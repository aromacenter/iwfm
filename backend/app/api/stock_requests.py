"""Saját raktárkészlet + áru-igénylés a központi raktárból (7bd7dbd8).

Folyamat: a dolgozó a saját (hozzárendelt autó-)raktárába igényel terméket →
az admin/manager Telegram-értesítést kap, és a vezérlőpulton jóváhagyja →
függő raktár-átadás jön létre (a forrásról lekerül a készlet), amit a dolgozó
a Saját raktár oldalon ELFOGAD — csak ekkor kerül a raktárába. A meglévő
átadás-gépezet gondoskodik róla, hogy a készlet ne mehessen negatívba.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, record_audit, require_perm
from app.db import get_db
from app.models import (
    Product,
    StockRequest,
    User,
    Warehouse,
    WarehouseMovement,
    WarehouseStock,
    WarehouseTransfer,
)

router = APIRouter()


class MyStockRow(BaseModel):
    product_id: str
    product_name: str
    unit: str
    quantity: float


class IncomingTransferOut(BaseModel):
    id: str
    product_name: str
    quantity: float
    unit: str
    from_name: str
    note: str | None
    created_at: datetime


class MyWarehouseOut(BaseModel):
    warehouse_id: str | None
    warehouse_name: str | None
    stock: list[MyStockRow] = []
    incoming: list[IncomingTransferOut] = []  # elfogadásra váró átadások


class RequestBody(BaseModel):
    product_id: str
    quantity: float = Field(gt=0, le=100_000)
    note: str | None = Field(default=None, max_length=512)


class RequestOut(BaseModel):
    id: str
    product_id: str
    product_name: str | None
    unit: str | None
    quantity: float
    note: str | None
    status: str
    requester_name: str | None
    decided_by_name: str | None
    created_at: datetime
    decided_at: datetime | None


async def _own_van(db: AsyncSession, user_id: uuid.UUID) -> Warehouse | None:
    return (
        await db.execute(
            select(Warehouse).where(Warehouse.user_id == user_id).limit(1)
        )
    ).scalars().first()


async def _request_out(db: AsyncSession, rows: list[StockRequest]) -> list[RequestOut]:
    pids = {r.product_id for r in rows}
    products: dict[uuid.UUID, Product] = {}
    if pids:
        products = {
            p.id: p
            for p in (
                await db.execute(select(Product).where(Product.id.in_(pids)))
            ).scalars()
        }
    return [
        RequestOut(
            id=str(r.id), product_id=str(r.product_id),
            product_name=products[r.product_id].name if r.product_id in products else None,
            unit=products[r.product_id].unit if r.product_id in products else None,
            quantity=r.quantity, note=r.note, status=r.status,
            requester_name=r.requester_name, decided_by_name=r.decided_by_name,
            created_at=r.created_at, decided_at=r.decided_at,
        )
        for r in rows
    ]


@router.get("/my-warehouse", response_model=MyWarehouseOut)
async def my_warehouse(
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """A bejelentkezett dolgozó saját raktára: készlet + elfogadásra váró
    beérkező átadások. Akinek nincs hozzárendelt raktára, üreset kap."""
    wh = await _own_van(db, actor.id)
    if wh is None:
        return MyWarehouseOut(warehouse_id=None, warehouse_name=None)
    stock_rows = (
        await db.execute(
            select(WarehouseStock, Product)
            .join(Product, Product.id == WarehouseStock.product_id)
            .where(WarehouseStock.warehouse_id == wh.id)
            .order_by(Product.name)
        )
    ).all()
    incoming_rows = (
        await db.execute(
            select(WarehouseTransfer, Product, Warehouse)
            .join(Product, Product.id == WarehouseTransfer.product_id)
            .join(Warehouse, Warehouse.id == WarehouseTransfer.from_warehouse_id)
            .where(
                WarehouseTransfer.to_warehouse_id == wh.id,
                WarehouseTransfer.status == "pending",
            )
            .order_by(WarehouseTransfer.created_at)
        )
    ).all()
    return MyWarehouseOut(
        warehouse_id=str(wh.id),
        warehouse_name=wh.name,
        stock=[
            MyStockRow(
                product_id=str(p.id), product_name=p.name, unit=p.unit,
                quantity=round(srow.quantity, 2),
            )
            for srow, p in stock_rows
        ],
        incoming=[
            IncomingTransferOut(
                id=str(tr.id), product_name=p.name, quantity=tr.quantity,
                unit=p.unit, from_name=src.name, note=tr.note, created_at=tr.created_at,
            )
            for tr, p, src in incoming_rows
        ],
    )


@router.post("", response_model=RequestOut, status_code=201)
async def create_request(
    body: RequestBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """Áru-igénylés a központi raktárból a SAJÁT raktárba — a vezetők
    Telegram-értesítést kapnak, és a vezérlőpulton hagyják jóvá."""
    wh = await _own_van(db, actor.id)
    if wh is None:
        raise HTTPException(status_code=422, detail={"code": "stockreq.no_warehouse"})
    try:
        pid = uuid.UUID(body.product_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "product.not_found"})
    product = (
        await db.execute(select(Product).where(Product.id == pid))
    ).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail={"code": "product.not_found"})
    req = StockRequest(
        id=uuid.uuid4(), user_id=actor.id, warehouse_id=wh.id, product_id=product.id,
        quantity=body.quantity, note=(body.note or "").strip() or None,
        status="pending", requester_name=actor.display_name,
        created_at=datetime.now(UTC),
    )
    db.add(req)
    await record_audit(
        db, actor=actor, action="stockreq.create", entity_type="stock_request",
        entity_id=str(req.id),
        detail={"product": product.name, "qty": body.quantity}, request=request,
    )
    await db.commit()
    # Telegram a vezetőknek kattintható linkkel — best effort
    try:
        from app.core.config import get_settings as _gs
        from app.models import Employee
        from app.services.wfm.telegram import send_personal

        link = f"{_gs().frontend_origin.rstrip('/')}/vezerlopult"
        msg = (
            f"📦 Áru-igénylés: {actor.display_name} — "
            f"{body.quantity:g} {product.unit} {product.name}"
            + (f"\nMegjegyzés: {req.note}" if req.note else "")
            + f"\n🔗 {link}"
        )
        boss_ids = (
            await db.execute(select(User.id).where(User.role.in_(("admin", "manager"))))
        ).scalars().all()
        for emp in (
            await db.execute(select(Employee).where(Employee.user_id.in_(boss_ids)))
        ).scalars().all():
            await send_personal(db, emp, msg)
    except Exception:  # pragma: no cover
        import logging

        logging.getLogger(__name__).warning("stock request notify failed", exc_info=True)
    return (await _request_out(db, [req]))[0]


@router.get("", response_model=list[RequestOut])
async def list_requests(
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """A saját igénylések (mindenkinek); invoicing joggal az ÖSSZES."""
    from app.api.deps import get_permission_matrix, permissions_for

    matrix = await get_permission_matrix(db)
    q = select(StockRequest).order_by(StockRequest.created_at.desc()).limit(100)
    if "invoicing" not in permissions_for(actor.role, matrix):
        q = q.where(StockRequest.user_id == actor.id)
    rows = (await db.execute(q)).scalars().all()
    return await _request_out(db, rows)


class ApproveBody(BaseModel):
    source_warehouse_id: str | None = None  # None = az egyetlen telephely


@router.post("/{request_id}/approve", response_model=RequestOut)
async def approve_request(
    request_id: str,
    body: ApproveBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("invoicing")),
):
    """Jóváhagyás: az áru AZONNAL a dolgozó raktárába kerül (e2a7fb7a: ő
    kérte, nem kell még egyszer elfogadnia) — a készlet a forrásról levonva
    (nem mehet negatívba). Minden más raktár-mozgásnál marad az elfogadás."""
    req = await _get_req_or_404(db, request_id)
    if req.status != "pending":
        raise HTTPException(status_code=422, detail={"code": "stockreq.decided"})
    # forrás: megadott vagy az egyetlen telephely-raktár
    if body.source_warehouse_id:
        try:
            src_id = uuid.UUID(body.source_warehouse_id)
        except ValueError:
            raise HTTPException(status_code=404, detail={"code": "warehouse.not_found"})
        src = (
            await db.execute(select(Warehouse).where(Warehouse.id == src_id))
        ).scalar_one_or_none()
    else:
        sites = (
            await db.execute(select(Warehouse).where(Warehouse.kind == "site"))
        ).scalars().all()
        src = sites[0] if len(sites) == 1 else None
    if src is None:
        raise HTTPException(status_code=422, detail={"code": "stockreq.pick_source"})

    src_stock = (
        await db.execute(
            select(WarehouseStock).where(
                WarehouseStock.warehouse_id == src.id,
                WarehouseStock.product_id == req.product_id,
            )
        )
    ).scalar_one_or_none()
    if src_stock is None or src_stock.quantity + 1e-9 < req.quantity:
        raise HTTPException(
            status_code=422,
            detail={"code": "warehouse.insufficient_stock",
                    "available": src_stock.quantity if src_stock else 0},
        )
    product = (
        await db.execute(select(Product).where(Product.id == req.product_id))
    ).scalar_one()
    dst = (
        await db.execute(select(Warehouse).where(Warehouse.id == req.warehouse_id))
    ).scalar_one()

    # Azonnali átadás: a transfer rögtön elfogadott állapotú, a cél-raktár
    # jóváíródik — az igénylő kérte, nem kell külön elfogadnia (e2a7fb7a).
    src_stock.quantity -= req.quantity
    transfer = WarehouseTransfer(
        from_warehouse_id=src.id, to_warehouse_id=dst.id,
        product_id=req.product_id, quantity=req.quantity,
        note=f"Igénylés jóváhagyva — {req.requester_name or ''}".strip(" —"),
        status="accepted", created_by=actor.id,
        decided_by=actor.id, decided_at=datetime.now(UTC),
    )
    db.add(transfer)
    await db.flush()
    db.add(WarehouseMovement(
        warehouse_id=src.id, product_id=req.product_id, action="transfer_out",
        quantity_delta=-req.quantity, ref_id=dst.id,
        note=f"igénylés jóváhagyva → {dst.name}",
        actor_user_id=actor.id,
    ))
    dst_stock = (
        await db.execute(
            select(WarehouseStock).where(
                WarehouseStock.warehouse_id == dst.id,
                WarehouseStock.product_id == req.product_id,
            )
        )
    ).scalar_one_or_none()
    if dst_stock is None:
        dst_stock = WarehouseStock(
            warehouse_id=dst.id, product_id=req.product_id, quantity=0.0
        )
        db.add(dst_stock)
    dst_stock.quantity += req.quantity
    db.add(WarehouseMovement(
        warehouse_id=dst.id, product_id=req.product_id, action="transfer_in",
        quantity_delta=req.quantity, ref_id=src.id,
        note="igénylés jóváhagyva — automatikusan átvéve",
        actor_user_id=actor.id,
    ))
    req.status = "approved"
    req.decided_at = datetime.now(UTC)
    req.decided_by_name = actor.display_name
    req.transfer_id = transfer.id
    await record_audit(
        db, actor=actor, action="stockreq.approve", entity_type="stock_request",
        entity_id=str(req.id),
        detail={"product": product.name, "qty": req.quantity, "source": src.name},
        request=request,
    )
    await db.commit()
    # értesítés az igénylőnek — best effort
    try:
        from app.core.config import get_settings as _gs2
        from app.models import Employee
        from app.services.wfm.telegram import send_personal

        emp = (
            await db.execute(select(Employee).where(Employee.user_id == req.user_id))
        ).scalars().first()
        if emp is not None:
            link = f"{_gs2().frontend_origin.rstrip('/')}/sajat-raktar"
            await send_personal(
                db, emp,
                f"✅ Igénylésed jóváhagyva: {req.quantity:g} {product.unit} "
                f"{product.name} — már a raktáradban van.\n🔗 {link}",
            )
    except Exception:  # pragma: no cover
        pass
    return (await _request_out(db, [req]))[0]


@router.post("/{request_id}/decline", response_model=RequestOut)
async def decline_request(
    request_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("invoicing")),
):
    req = await _get_req_or_404(db, request_id)
    if req.status != "pending":
        raise HTTPException(status_code=422, detail={"code": "stockreq.decided"})
    req.status = "declined"
    req.decided_at = datetime.now(UTC)
    req.decided_by_name = actor.display_name
    await record_audit(
        db, actor=actor, action="stockreq.decline", entity_type="stock_request",
        entity_id=str(req.id), detail={}, request=request,
    )
    await db.commit()
    return (await _request_out(db, [req]))[0]


async def _get_req_or_404(db: AsyncSession, request_id: str) -> StockRequest:
    try:
        rid = uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "stockreq.not_found"})
    req = (
        await db.execute(select(StockRequest).where(StockRequest.id == rid))
    ).scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail={"code": "stockreq.not_found"})
    return req
