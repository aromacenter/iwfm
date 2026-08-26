"""Raktárlánc: telephelyi és autó-raktárak készlete + beszerzési rendelések.

Áruút: beszerzési rendelés (szállító = Partner, type supplier/both) →
beérkeztetés a telephelyi raktárba → áthelyezés az üzletkötő autójára →
kiadás a partner külső raktárába (a partner-feltöltés vonja a forrás-raktárt).
Minden mozgás append-only a warehouse_movements táblában.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.db import get_db
from app.models import (
    Partner,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    User,
    Warehouse,
    WarehouseMovement,
    WarehouseStock,
    WarehouseTransfer,
)
from app.services.wfm.automation import fire_event

router = APIRouter()
po_router = APIRouter()

WAREHOUSE_KINDS = ("site", "van")


# ─── Segédek ────────────────────────────────────────────────────────────────


def _parse_uuid(raw: str, code: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": code})


async def _get_warehouse_or_404(db: AsyncSession, warehouse_id: str) -> Warehouse:
    wh = (
        await db.execute(
            select(Warehouse).where(Warehouse.id == _parse_uuid(warehouse_id, "warehouse.not_found"))
        )
    ).scalar_one_or_none()
    if wh is None:
        raise HTTPException(status_code=404, detail={"code": "warehouse.not_found"})
    return wh


async def _get_product_or_404(db: AsyncSession, product_id: str) -> Product:
    product = (
        await db.execute(
            select(Product).where(Product.id == _parse_uuid(product_id, "product.not_found"))
        )
    ).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail={"code": "product.not_found"})
    return product


async def _stock_row(
    db: AsyncSession, warehouse_id: uuid.UUID, product_id: uuid.UUID
) -> WarehouseStock:
    row = (
        await db.execute(
            select(WarehouseStock).where(
                WarehouseStock.warehouse_id == warehouse_id,
                WarehouseStock.product_id == product_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = WarehouseStock(warehouse_id=warehouse_id, product_id=product_id, quantity=0.0)
        db.add(row)
        await db.flush()
    return row


async def warehouse_issue(
    db: AsyncSession,
    *,
    warehouse_id: uuid.UUID,
    product: Product,
    quantity: float,
    partner_id: uuid.UUID,
    actor_id: uuid.UUID | None,
) -> None:
    """Kiadás egy raktárból partner-feltöltéshez. 422, ha nincs elég készlet.
    A consignment.replenish hívja, amikor forrás-raktár van megadva."""
    stock = await _stock_row(db, warehouse_id, product.id)
    if stock.quantity + 1e-9 < quantity:
        raise HTTPException(
            status_code=422,
            detail={"code": "warehouse.insufficient_stock", "available": stock.quantity},
        )
    stock.quantity -= quantity
    db.add(WarehouseMovement(
        warehouse_id=warehouse_id, product_id=product.id, action="issue",
        quantity_delta=-quantity, ref_id=partner_id, actor_user_id=actor_id,
    ))


async def warehouse_receive_return(
    db: AsyncSession,
    *,
    warehouse_id: uuid.UUID,
    product: Product,
    quantity: float,
    partner_id: uuid.UUID,
    actor_id: uuid.UUID | None,
) -> None:
    """Partnertől visszavett készlet bevételezése a raktárba (autóba).
    Ha a termék még nincs a raktár választékában, a sor létrejön — a fizikai
    áru megérkezett. A consignment stock/return hívja."""
    stock = (
        await db.execute(
            select(WarehouseStock).where(
                WarehouseStock.warehouse_id == warehouse_id,
                WarehouseStock.product_id == product.id,
            )
        )
    ).scalar_one_or_none()
    if stock is None:
        stock = WarehouseStock(
            warehouse_id=warehouse_id, product_id=product.id, quantity=0.0
        )
        db.add(stock)
    stock.quantity += quantity
    db.add(WarehouseMovement(
        warehouse_id=warehouse_id, product_id=product.id, action="receive",
        quantity_delta=quantity, ref_id=partner_id, actor_user_id=actor_id,
    ))


# ─── Raktárak ───────────────────────────────────────────────────────────────


class WarehouseBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: str = Field(default="site")
    user_id: str | None = None  # autónál: az üzletkötő
    notes: str | None = None
    is_active: bool = True

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, v: str) -> str:
        if v not in WAREHOUSE_KINDS:
            raise ValueError("warehouse.bad_kind")
        return v


class WarehouseOut(BaseModel):
    id: str
    name: str
    kind: str
    user_id: str | None
    user_name: str | None
    notes: str | None
    is_active: bool
    product_count: int
    total_quantity: float


async def _warehouse_out(db: AsyncSession, wh: Warehouse) -> WarehouseOut:
    rows = (
        await db.execute(
            select(WarehouseStock).where(WarehouseStock.warehouse_id == wh.id)
        )
    ).scalars().all()
    user_name = None
    if wh.user_id is not None:
        u = (await db.execute(select(User).where(User.id == wh.user_id))).scalar_one_or_none()
        user_name = u.display_name if u else None
    nonzero = [r for r in rows if abs(r.quantity) > 1e-9]
    return WarehouseOut(
        id=str(wh.id), name=wh.name, kind=wh.kind,
        user_id=str(wh.user_id) if wh.user_id else None, user_name=user_name,
        notes=wh.notes, is_active=wh.is_active,
        product_count=len(nonzero),
        total_quantity=round(sum(r.quantity for r in rows), 2),
    )


@router.get("", response_model=list[WarehouseOut])
async def list_warehouses(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    rows = (
        await db.execute(select(Warehouse).order_by(Warehouse.kind, Warehouse.name))
    ).scalars().all()
    return [await _warehouse_out(db, wh) for wh in rows]


@router.post("", response_model=WarehouseOut, status_code=201)
async def create_warehouse(
    body: WarehouseBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    data = body.model_dump()
    data["user_id"] = _parse_uuid(body.user_id, "user.not_found") if body.user_id else None
    wh = Warehouse(**data)
    db.add(wh)
    await db.flush()
    await record_audit(
        db, actor=actor, action="warehouse.create", entity_type="warehouse",
        entity_id=str(wh.id), detail={"name": wh.name, "kind": wh.kind}, request=request,
    )
    await db.commit()
    return await _warehouse_out(db, wh)


@router.patch("/{warehouse_id}", response_model=WarehouseOut)
async def update_warehouse(
    warehouse_id: str,
    body: WarehouseBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    wh = await _get_warehouse_or_404(db, warehouse_id)
    data = body.model_dump()
    data["user_id"] = _parse_uuid(body.user_id, "user.not_found") if body.user_id else None
    for key, value in data.items():
        setattr(wh, key, value)
    await record_audit(
        db, actor=actor, action="warehouse.update", entity_type="warehouse",
        entity_id=str(wh.id), request=request,
    )
    await db.commit()
    return await _warehouse_out(db, wh)


@router.delete("/{warehouse_id}")
async def delete_warehouse(
    warehouse_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("delete")),
):
    """Raktár/autó törlése. Védőkorlátok: nem üres készlettel vagy nyitott
    (piszkozat/megrendelt) beszerzési rendeléssel nem törölhető. A mozgás-
    történet a raktárral együtt törlődik (az audit-napló megmarad)."""
    wh = await _get_warehouse_or_404(db, warehouse_id)
    nonzero = [
        s for s in (
            await db.execute(
                select(WarehouseStock).where(WarehouseStock.warehouse_id == wh.id)
            )
        ).scalars()
        if abs(s.quantity) > 1e-9
    ]
    if nonzero:
        raise HTTPException(status_code=422, detail={"code": "warehouse.has_stock"})
    open_pos = (
        await db.execute(
            select(PurchaseOrder).where(
                PurchaseOrder.warehouse_id == wh.id,
                PurchaseOrder.status.in_(("draft", "ordered")),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if open_pos is not None:
        raise HTTPException(status_code=422, detail={"code": "warehouse.has_open_orders"})
    await record_audit(
        db, actor=actor, action="warehouse.delete", entity_type="warehouse",
        entity_id=str(wh.id), detail={"name": wh.name, "kind": wh.kind}, request=request,
    )
    # Gyerek-sorok explicit takarítása (SQLite-on nincs DB-szintű cascade).
    from sqlalchemy import delete as sa_delete, update as sa_update

    await db.execute(
        sa_delete(WarehouseMovement).where(WarehouseMovement.warehouse_id == wh.id)
    )
    await db.execute(
        sa_delete(WarehouseStock).where(WarehouseStock.warehouse_id == wh.id)
    )
    await db.execute(
        sa_update(PurchaseOrder)
        .where(PurchaseOrder.warehouse_id == wh.id)
        .values(warehouse_id=None)
    )
    await db.delete(wh)
    await db.commit()
    return {"ok": True}


class WarehouseStockOut(BaseModel):
    product_id: str
    product_name: str
    unit: str
    quantity: float
    min_quantity: float | None


@router.get("/{warehouse_id}/stock", response_model=list[WarehouseStockOut])
async def warehouse_stock(
    warehouse_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    """A raktárhoz KÉZZEL hozzárendelt termékek készlete. A katalógusból a
    /stock/add végponttal kerül be termék (a katalógus és a raktár-választék
    tudatosan eltérhet)."""
    wh = await _get_warehouse_or_404(db, warehouse_id)
    rows = (
        await db.execute(
            select(WarehouseStock, Product)
            .join(Product, Product.id == WarehouseStock.product_id)
            .where(WarehouseStock.warehouse_id == wh.id)
            .order_by(Product.name)
        )
    ).all()
    return [
        WarehouseStockOut(
            product_id=str(p.id), product_name=p.name, unit=p.unit,
            quantity=round(s.quantity, 2), min_quantity=s.min_quantity,
        )
        for s, p in rows
    ]


class StockAddBody(BaseModel):
    product_id: str


@router.post("/{warehouse_id}/stock/add", response_model=WarehouseStockOut, status_code=201)
async def add_stock_product(
    warehouse_id: str,
    body: StockAddBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Termék kézi hozzárendelése a raktárhoz (0 induló készlettel)."""
    wh = await _get_warehouse_or_404(db, warehouse_id)
    product = await _get_product_or_404(db, body.product_id)
    existing = (
        await db.execute(
            select(WarehouseStock).where(
                WarehouseStock.warehouse_id == wh.id,
                WarehouseStock.product_id == product.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=422, detail={"code": "warehouse.product_exists"})
    row = WarehouseStock(warehouse_id=wh.id, product_id=product.id, quantity=0.0)
    db.add(row)
    await record_audit(
        db, actor=actor, action="warehouse.stock_add", entity_type="warehouse",
        entity_id=str(wh.id), detail={"product": product.name}, request=request,
    )
    await db.commit()
    return WarehouseStockOut(
        product_id=str(product.id), product_name=product.name, unit=product.unit,
        quantity=0.0, min_quantity=None,
    )


@router.delete("/{warehouse_id}/stock/{product_id}")
async def remove_stock_product(
    warehouse_id: str,
    product_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Termék levétele a raktár választékáról — csak nulla készletnél."""
    wh = await _get_warehouse_or_404(db, warehouse_id)
    product = await _get_product_or_404(db, product_id)
    row = (
        await db.execute(
            select(WarehouseStock).where(
                WarehouseStock.warehouse_id == wh.id,
                WarehouseStock.product_id == product.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "product.not_found"})
    if abs(row.quantity) > 1e-9:
        raise HTTPException(status_code=422, detail={"code": "warehouse.stock_not_empty"})
    await db.delete(row)
    await record_audit(
        db, actor=actor, action="warehouse.stock_remove", entity_type="warehouse",
        entity_id=str(wh.id), detail={"product": product.name}, request=request,
    )
    await db.commit()
    return {"ok": True}


class MovementOut(BaseModel):
    id: str
    created_at: datetime
    action: str
    product_name: str
    unit: str
    quantity_delta: float
    unit_cost: float | None
    actor_name: str | None
    counterparty: str | None  # partner (kiadás), másik raktár (áthelyezés), PO
    note: str | None


@router.get("/{warehouse_id}/movements", response_model=list[MovementOut])
async def warehouse_movements(
    warehouse_id: str,
    days: int = 31,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    """Raktári mozgásnapló: ki, mikor, mit, mennyit vételezett / adott ki /
    vett be — a napi forgalom követéséhez."""
    from datetime import timedelta

    wh = await _get_warehouse_or_404(db, warehouse_id)
    days = max(1, min(days, 366))
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await db.execute(
            select(WarehouseMovement, Product)
            .join(Product, Product.id == WarehouseMovement.product_id)
            .where(
                WarehouseMovement.warehouse_id == wh.id,
                WarehouseMovement.created_at >= since,
            )
            .order_by(WarehouseMovement.created_at.desc())
            .limit(1000)
        )
    ).all()

    actor_ids = {m.actor_user_id for m, _p in rows if m.actor_user_id}
    actors: dict = {}
    if actor_ids:
        for u in (
            await db.execute(select(User).where(User.id.in_(actor_ids)))
        ).scalars():
            actors[u.id] = u.display_name

    ref_ids = {m.ref_id for m, _p in rows if m.ref_id}
    counterparties: dict = {}
    if ref_ids:
        for p in (
            await db.execute(select(Partner).where(Partner.id.in_(ref_ids)))
        ).scalars():
            counterparties[p.id] = p.name
        for w in (
            await db.execute(select(Warehouse).where(Warehouse.id.in_(ref_ids)))
        ).scalars():
            counterparties[w.id] = w.name
        for po in (
            await db.execute(select(PurchaseOrder).where(PurchaseOrder.id.in_(ref_ids)))
        ).scalars():
            counterparties[po.id] = po.order_no

    return [
        MovementOut(
            id=str(m.id), created_at=m.created_at, action=m.action,
            product_name=p.name, unit=p.unit,
            quantity_delta=round(m.quantity_delta, 3), unit_cost=m.unit_cost,
            actor_name=actors.get(m.actor_user_id),
            counterparty=counterparties.get(m.ref_id),
            note=m.note,
        )
        for m, p in rows
    ]


class AssignableUserOut(BaseModel):
    id: str
    display_name: str
    role: str


@router.get("/assignable-users", response_model=list[AssignableUserOut])
async def assignable_users(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    """Autó-raktárhoz rendelhető felhasználók (üzletkötők, vezetők)."""
    rows = (
        await db.execute(
            select(User).where(
                User.is_active.is_(True),
                User.role.in_(("admin", "manager", "uzletkoto")),
            ).order_by(User.display_name)
        )
    ).scalars().all()
    return [
        AssignableUserOut(id=str(u.id), display_name=u.display_name, role=u.role)
        for u in rows
    ]


class ReceiveBody(BaseModel):
    product_id: str
    quantity: float = Field(gt=0, le=100_000)
    unit_cost: float | None = Field(default=None, ge=0)  # Ft/egység, nettó
    note: str | None = Field(default=None, max_length=512)


@router.post("/{warehouse_id}/receive", response_model=WarehouseStockOut)
async def receive_stock(
    warehouse_id: str,
    body: ReceiveBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Közvetlen bevételezés a raktárba (beszerzési rendelés nélkül)."""
    wh = await _get_warehouse_or_404(db, warehouse_id)
    product = await _get_product_or_404(db, body.product_id)
    stock = await _stock_row(db, wh.id, product.id)
    stock.quantity += body.quantity
    db.add(WarehouseMovement(
        warehouse_id=wh.id, product_id=product.id, action="receive",
        quantity_delta=body.quantity, unit_cost=body.unit_cost,
        note=body.note, actor_user_id=actor.id,
    ))
    if body.unit_cost is not None:
        product.purchase_price = body.unit_cost
    await record_audit(
        db, actor=actor, action="warehouse.receive", entity_type="warehouse",
        entity_id=str(wh.id),
        detail={"product": product.name, "qty": body.quantity, "unit_cost": body.unit_cost},
        request=request,
    )
    await db.commit()
    return WarehouseStockOut(
        product_id=str(product.id), product_name=product.name, unit=product.unit,
        quantity=round(stock.quantity, 2), min_quantity=stock.min_quantity,
    )


class TransferBody(BaseModel):
    from_warehouse_id: str
    to_warehouse_id: str
    product_id: str
    quantity: float = Field(gt=0, le=100_000)
    note: str | None = Field(default=None, max_length=512)


@router.post("/transfer")
async def transfer_stock(
    body: TransferBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Áthelyezés raktárak között (tipikusan telephely → autó)."""
    if body.from_warehouse_id == body.to_warehouse_id:
        raise HTTPException(status_code=422, detail={"code": "warehouse.same_warehouse"})
    src = await _get_warehouse_or_404(db, body.from_warehouse_id)
    dst = await _get_warehouse_or_404(db, body.to_warehouse_id)
    product = await _get_product_or_404(db, body.product_id)

    src_stock = await _stock_row(db, src.id, product.id)
    if src_stock.quantity + 1e-9 < body.quantity:
        raise HTTPException(
            status_code=422,
            detail={"code": "warehouse.insufficient_stock", "available": src_stock.quantity},
        )

    # Autót érintő mozgás NEM azonnali: függő átadás lesz, amit a fogadó fél
    # (autónál a hozzárendelt üzletkötő, telephelynél a raktáros) hagy jóvá.
    # A forrásról már most lekerül a mennyiség (úton van).
    if src.kind == "van" or dst.kind == "van":
        src_stock.quantity -= body.quantity
        transfer = WarehouseTransfer(
            from_warehouse_id=src.id, to_warehouse_id=dst.id,
            product_id=product.id, quantity=body.quantity, note=body.note,
            status="pending", created_by=actor.id,
        )
        db.add(transfer)
        await db.flush()
        db.add(WarehouseMovement(
            warehouse_id=src.id, product_id=product.id, action="transfer_out",
            quantity_delta=-body.quantity, ref_id=dst.id,
            note=f"függő átadás → {dst.name}" + (f" ({body.note})" if body.note else ""),
            actor_user_id=actor.id,
        ))
        await record_audit(
            db, actor=actor, action="warehouse.transfer_pending", entity_type="warehouse",
            entity_id=str(src.id),
            detail={"to": dst.name, "product": product.name, "qty": body.quantity},
            request=request,
        )
        await db.commit()
        fire_event("warehouse.transfer_pending", {
            "termek_nev": product.name, "mennyiseg": body.quantity,
            "egyseg": product.unit, "honnan": src.name, "hova": dst.name,
            "inditotta": actor.display_name or actor.email,
        })
        return {
            "ok": True,
            "pending": True,
            "transfer_id": str(transfer.id),
            "from_quantity": round(src_stock.quantity, 2),
        }

    dst_stock = await _stock_row(db, dst.id, product.id)
    src_stock.quantity -= body.quantity
    dst_stock.quantity += body.quantity
    db.add(WarehouseMovement(
        warehouse_id=src.id, product_id=product.id, action="transfer_out",
        quantity_delta=-body.quantity, ref_id=dst.id, note=body.note,
        actor_user_id=actor.id,
    ))
    db.add(WarehouseMovement(
        warehouse_id=dst.id, product_id=product.id, action="transfer_in",
        quantity_delta=body.quantity, ref_id=src.id, note=body.note,
        actor_user_id=actor.id,
    ))
    await record_audit(
        db, actor=actor, action="warehouse.transfer", entity_type="warehouse",
        entity_id=str(src.id),
        detail={"to": dst.name, "product": product.name, "qty": body.quantity},
        request=request,
    )
    await db.commit()
    return {
        "ok": True,
        "pending": False,
        "from_quantity": round(src_stock.quantity, 2),
        "to_quantity": round(dst_stock.quantity, 2),
    }


# ─── Függő átadások (telephely↔autó jóváhagyással) ──────────────────────────


class TransferRowOut(BaseModel):
    id: str
    from_warehouse: str
    to_warehouse: str
    to_kind: str
    product_name: str
    unit: str
    quantity: float
    note: str | None
    status: str
    created_by_name: str | None
    created_at: datetime
    decided_at: datetime | None
    decision_note: str | None
    # A bejelentkezett felhasználó dönthet-e / vonhatja-e vissza
    can_decide: bool
    can_cancel: bool


def _can_decide(t: WarehouseTransfer, dst: Warehouse, actor: User) -> bool:
    """Autóra menőt csak az autó üzletkötője (vagy admin); telephelyre (vagy
    üzletkötő nélküli autóra) menőt bárki raktár-joggal, kivéve magát a
    küldőt (admin kivétel)."""
    if t.status != "pending":
        return False
    if actor.role == "admin":
        return True
    if dst.kind == "van" and dst.user_id is not None:
        return dst.user_id == actor.id
    return t.created_by != actor.id


async def _transfer_rows(
    db: AsyncSession, actor: User, status: str | None
) -> list[TransferRowOut]:
    q = select(WarehouseTransfer).order_by(WarehouseTransfer.created_at.desc()).limit(100)
    if status:
        q = q.where(WarehouseTransfer.status == status)
    rows = (await db.execute(q)).scalars().all()
    if not rows:
        return []
    wh_ids = {t.from_warehouse_id for t in rows} | {t.to_warehouse_id for t in rows}
    whs = {
        w.id: w for w in (
            await db.execute(select(Warehouse).where(Warehouse.id.in_(wh_ids)))
        ).scalars().all()
    }
    prods = {
        p.id: p for p in (
            await db.execute(select(Product).where(
                Product.id.in_({t.product_id for t in rows})
            ))
        ).scalars().all()
    }
    user_ids = {t.created_by for t in rows if t.created_by}
    users = {
        u.id: u for u in (
            await db.execute(select(User).where(User.id.in_(user_ids)))
        ).scalars().all()
    } if user_ids else {}
    out = []
    for t in rows:
        src, dst = whs.get(t.from_warehouse_id), whs.get(t.to_warehouse_id)
        p = prods.get(t.product_id)
        creator = users.get(t.created_by) if t.created_by else None
        out.append(TransferRowOut(
            id=str(t.id),
            from_warehouse=src.name if src else "?",
            to_warehouse=dst.name if dst else "?",
            to_kind=dst.kind if dst else "site",
            product_name=p.name if p else "?",
            unit=p.unit if p else "",
            quantity=round(t.quantity, 2),
            note=t.note,
            status=t.status,
            created_by_name=(creator.display_name or creator.email) if creator else None,
            created_at=t.created_at,
            decided_at=t.decided_at,
            decision_note=t.decision_note,
            can_decide=dst is not None and _can_decide(t, dst, actor),
            can_cancel=t.status == "pending" and (
                t.created_by == actor.id or actor.role == "admin"
            ),
        ))
    return out


@router.get("/transfers", response_model=list[TransferRowOut])
async def list_transfers(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    return await _transfer_rows(db, actor, status)


async def _pending_transfer_or_404(db: AsyncSession, transfer_id: str) -> WarehouseTransfer:
    tid = _parse_uuid(transfer_id, "warehouse.transfer_not_found")
    t = (
        await db.execute(select(WarehouseTransfer).where(WarehouseTransfer.id == tid))
    ).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "warehouse.transfer_not_found"})
    if t.status != "pending":
        raise HTTPException(status_code=422, detail={"code": "warehouse.transfer_decided"})
    return t


class DecisionBody(BaseModel):
    note: str | None = Field(default=None, max_length=512)


async def _transfer_ctx(db: AsyncSession, t: WarehouseTransfer, actor: User) -> dict:
    src = (await db.execute(select(Warehouse).where(Warehouse.id == t.from_warehouse_id))).scalar_one_or_none()
    dst = (await db.execute(select(Warehouse).where(Warehouse.id == t.to_warehouse_id))).scalar_one_or_none()
    p = (await db.execute(select(Product).where(Product.id == t.product_id))).scalar_one_or_none()
    return {
        "termek_nev": p.name if p else "?", "mennyiseg": t.quantity,
        "egyseg": p.unit if p else "", "honnan": src.name if src else "?",
        "hova": dst.name if dst else "?",
        "dontott": actor.display_name or actor.email,
    }


@router.post("/transfers/{transfer_id}/accept")
async def accept_transfer(
    transfer_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Az átadás átvételének igazolása — csak ekkor kerül a cél-raktárra."""
    t = await _pending_transfer_or_404(db, transfer_id)
    dst = (
        await db.execute(select(Warehouse).where(Warehouse.id == t.to_warehouse_id))
    ).scalar_one_or_none()
    if dst is None or not _can_decide(t, dst, actor):
        raise HTTPException(status_code=403, detail={"code": "warehouse.not_your_transfer"})
    dst_stock = await _stock_row(db, dst.id, t.product_id)
    dst_stock.quantity += t.quantity
    db.add(WarehouseMovement(
        warehouse_id=dst.id, product_id=t.product_id, action="transfer_in",
        quantity_delta=t.quantity, ref_id=t.from_warehouse_id,
        note="átadás elfogadva", actor_user_id=actor.id,
    ))
    t.status = "accepted"
    t.decided_by = actor.id
    t.decided_at = datetime.now(UTC)
    await record_audit(
        db, actor=actor, action="warehouse.transfer_accepted", entity_type="warehouse",
        entity_id=str(dst.id), detail={"transfer": str(t.id), "qty": t.quantity},
        request=request,
    )
    ctx = await _transfer_ctx(db, t, actor)
    await db.commit()
    fire_event("warehouse.transfer_accepted", ctx)
    return {"ok": True, "to_quantity": round(dst_stock.quantity, 2)}


@router.post("/transfers/{transfer_id}/reject")
async def reject_transfer(
    transfer_id: str,
    body: DecisionBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Az átadás elutasítása — a mennyiség visszakerül a forrás-raktárra."""
    t = await _pending_transfer_or_404(db, transfer_id)
    dst = (
        await db.execute(select(Warehouse).where(Warehouse.id == t.to_warehouse_id))
    ).scalar_one_or_none()
    if dst is None or not _can_decide(t, dst, actor):
        raise HTTPException(status_code=403, detail={"code": "warehouse.not_your_transfer"})
    src_stock = await _stock_row(db, t.from_warehouse_id, t.product_id)
    src_stock.quantity += t.quantity
    db.add(WarehouseMovement(
        warehouse_id=t.from_warehouse_id, product_id=t.product_id, action="transfer_in",
        quantity_delta=t.quantity, ref_id=t.to_warehouse_id,
        note="elutasított átadás visszavéve", actor_user_id=actor.id,
    ))
    t.status = "rejected"
    t.decided_by = actor.id
    t.decided_at = datetime.now(UTC)
    t.decision_note = body.note
    await record_audit(
        db, actor=actor, action="warehouse.transfer_rejected", entity_type="warehouse",
        entity_id=str(t.from_warehouse_id),
        detail={"transfer": str(t.id), "qty": t.quantity, "note": body.note},
        request=request,
    )
    ctx = await _transfer_ctx(db, t, actor)
    ctx["indok"] = body.note or ""
    await db.commit()
    fire_event("warehouse.transfer_rejected", ctx)
    return {"ok": True, "from_quantity": round(src_stock.quantity, 2)}


@router.post("/transfers/{transfer_id}/cancel")
async def cancel_transfer(
    transfer_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """A küldő visszavonja a saját függő átadását — a készlet visszakerül."""
    t = await _pending_transfer_or_404(db, transfer_id)
    if t.created_by != actor.id and actor.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "warehouse.not_your_transfer"})
    src_stock = await _stock_row(db, t.from_warehouse_id, t.product_id)
    src_stock.quantity += t.quantity
    db.add(WarehouseMovement(
        warehouse_id=t.from_warehouse_id, product_id=t.product_id, action="transfer_in",
        quantity_delta=t.quantity, ref_id=t.to_warehouse_id,
        note="visszavont átadás", actor_user_id=actor.id,
    ))
    t.status = "cancelled"
    t.decided_by = actor.id
    t.decided_at = datetime.now(UTC)
    await record_audit(
        db, actor=actor, action="warehouse.transfer_cancelled", entity_type="warehouse",
        entity_id=str(t.from_warehouse_id), detail={"transfer": str(t.id)},
        request=request,
    )
    await db.commit()
    return {"ok": True, "from_quantity": round(src_stock.quantity, 2)}


class AdjustBody(BaseModel):
    product_id: str
    counted_qty: float = Field(ge=0, le=100_000)  # leltár szerinti tényleges
    min_quantity: float | None = Field(default=None, ge=0)  # küszöb-állítás
    note: str | None = Field(default=None, max_length=512)


@router.post("/{warehouse_id}/adjust", response_model=WarehouseStockOut)
async def adjust_stock(
    warehouse_id: str,
    body: AdjustBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Leltár-korrekció: a készletet a számolt értékre állítja, az eltérés
    mozgásként rögzül."""
    wh = await _get_warehouse_or_404(db, warehouse_id)
    product = await _get_product_or_404(db, body.product_id)
    stock = await _stock_row(db, wh.id, product.id)
    delta = body.counted_qty - stock.quantity
    stock.quantity = body.counted_qty
    if body.min_quantity is not None:
        stock.min_quantity = body.min_quantity
    if abs(delta) > 1e-9:
        db.add(WarehouseMovement(
            warehouse_id=wh.id, product_id=product.id, action="adjust",
            quantity_delta=delta, note=body.note, actor_user_id=actor.id,
        ))
    await record_audit(
        db, actor=actor, action="warehouse.adjust", entity_type="warehouse",
        entity_id=str(wh.id),
        detail={"product": product.name, "counted": body.counted_qty, "delta": round(delta, 3)},
        request=request,
    )
    await db.commit()
    return WarehouseStockOut(
        product_id=str(product.id), product_name=product.name, unit=product.unit,
        quantity=round(stock.quantity, 2), min_quantity=stock.min_quantity,
    )


class ReorderSuggestionOut(BaseModel):
    warehouse_id: str
    warehouse_name: str
    product_id: str
    product_name: str
    unit: str
    quantity: float
    min_quantity: float
    suggested_qty: float


@router.get("/reorder-suggestions", response_model=list[ReorderSuggestionOut])
async def reorder_suggestions(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    """Küszöb alá csökkent raktárkészletek + javasolt rendelési mennyiség
    (a küszöb kétszereséig való feltöltés)."""
    rows = (
        await db.execute(
            select(WarehouseStock, Warehouse, Product)
            .join(Warehouse, Warehouse.id == WarehouseStock.warehouse_id)
            .join(Product, Product.id == WarehouseStock.product_id)
            .where(
                WarehouseStock.min_quantity.is_not(None),
                WarehouseStock.quantity <= WarehouseStock.min_quantity,
                Warehouse.is_active.is_(True),
            )
            .order_by(Warehouse.name, Product.name)
        )
    ).all()
    return [
        ReorderSuggestionOut(
            warehouse_id=str(wh.id), warehouse_name=wh.name,
            product_id=str(p.id), product_name=p.name, unit=p.unit,
            quantity=round(s.quantity, 2), min_quantity=s.min_quantity,
            suggested_qty=round(max(s.min_quantity * 2 - s.quantity, 0.0), 1),
        )
        for s, wh, p in rows
    ]


# ─── Beszerzési rendelések ──────────────────────────────────────────────────


class POLineIn(BaseModel):
    product_id: str
    quantity: float = Field(gt=0, le=100_000)
    unit_cost: float | None = Field(default=None, ge=0)


class POCreateBody(BaseModel):
    supplier_partner_id: str | None = None
    warehouse_id: str
    expected_at: str | None = None  # ÉÉÉÉ-HH-NN
    note: str | None = None
    lines: list[POLineIn] = Field(min_length=1, max_length=100)


class POLineOut(BaseModel):
    id: str
    product_id: str
    product_name: str
    unit: str
    quantity: float
    unit_cost: float | None
    received_qty: float


class POOut(BaseModel):
    id: str
    order_no: str
    supplier_partner_id: str | None
    supplier_name: str | None
    warehouse_id: str | None
    warehouse_name: str | None
    status: str
    expected_at: str | None
    ordered_at: datetime | None
    received_at: datetime | None
    note: str | None
    created_at: datetime
    lines: list[POLineOut]


async def _po_out(db: AsyncSession, po: PurchaseOrder) -> POOut:
    lines = (
        await db.execute(
            select(PurchaseOrderLine, Product)
            .join(Product, Product.id == PurchaseOrderLine.product_id)
            .where(PurchaseOrderLine.purchase_order_id == po.id)
            .order_by(Product.name)
        )
    ).all()
    supplier_name = warehouse_name = None
    if po.supplier_partner_id is not None:
        s = (
            await db.execute(select(Partner).where(Partner.id == po.supplier_partner_id))
        ).scalar_one_or_none()
        supplier_name = s.name if s else None
    if po.warehouse_id is not None:
        w = (
            await db.execute(select(Warehouse).where(Warehouse.id == po.warehouse_id))
        ).scalar_one_or_none()
        warehouse_name = w.name if w else None
    return POOut(
        id=str(po.id), order_no=po.order_no,
        supplier_partner_id=str(po.supplier_partner_id) if po.supplier_partner_id else None,
        supplier_name=supplier_name,
        warehouse_id=str(po.warehouse_id) if po.warehouse_id else None,
        warehouse_name=warehouse_name,
        status=po.status,
        expected_at=po.expected_at.isoformat() if po.expected_at else None,
        ordered_at=po.ordered_at, received_at=po.received_at,
        note=po.note, created_at=po.created_at,
        lines=[
            POLineOut(
                id=str(ln.id), product_id=str(p.id), product_name=p.name, unit=p.unit,
                quantity=ln.quantity, unit_cost=ln.unit_cost, received_qty=ln.received_qty,
            )
            for ln, p in lines
        ],
    )


async def _get_po_or_404(db: AsyncSession, po_id: str) -> PurchaseOrder:
    po = (
        await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == _parse_uuid(po_id, "po.not_found"))
        )
    ).scalar_one_or_none()
    if po is None:
        raise HTTPException(status_code=404, detail={"code": "po.not_found"})
    return po


@po_router.get("", response_model=list[POOut])
async def list_purchase_orders(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    rows = (
        await db.execute(select(PurchaseOrder).order_by(PurchaseOrder.created_at.desc()).limit(200))
    ).scalars().all()
    return [await _po_out(db, po) for po in rows]


@po_router.post("", response_model=POOut, status_code=201)
async def create_purchase_order(
    body: POCreateBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    from datetime import date as date_cls

    from app.services.wfm.codes import generate_po_number

    wh = await _get_warehouse_or_404(db, body.warehouse_id)
    supplier_id = None
    if body.supplier_partner_id:
        supplier = (
            await db.execute(
                select(Partner).where(
                    Partner.id == _parse_uuid(body.supplier_partner_id, "partner.not_found")
                )
            )
        ).scalar_one_or_none()
        if supplier is None:
            raise HTTPException(status_code=404, detail={"code": "partner.not_found"})
        supplier_id = supplier.id
    expected = None
    if body.expected_at:
        try:
            expected = date_cls.fromisoformat(body.expected_at)
        except ValueError:
            raise HTTPException(status_code=422, detail={"code": "po.bad_date"})

    po = PurchaseOrder(
        order_no=await generate_po_number(db),
        supplier_partner_id=supplier_id, warehouse_id=wh.id,
        expected_at=expected, note=body.note, created_by=actor.id,
    )
    db.add(po)
    await db.flush()
    for line in body.lines:
        product = await _get_product_or_404(db, line.product_id)
        db.add(PurchaseOrderLine(
            purchase_order_id=po.id, product_id=product.id,
            quantity=line.quantity, unit_cost=line.unit_cost,
        ))
    await record_audit(
        db, actor=actor, action="po.create", entity_type="purchase_order",
        entity_id=str(po.id), detail={"order_no": po.order_no, "lines": len(body.lines)},
        request=request,
    )
    await db.commit()
    return await _po_out(db, po)


@po_router.post("/{po_id}/order", response_model=POOut)
async def mark_ordered(
    po_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    po = await _get_po_or_404(db, po_id)
    if po.status != "draft":
        raise HTTPException(status_code=422, detail={"code": "po.bad_status"})
    po.status = "ordered"
    po.ordered_at = datetime.now(UTC)
    await record_audit(
        db, actor=actor, action="po.order", entity_type="purchase_order",
        entity_id=str(po.id), detail={"order_no": po.order_no}, request=request,
    )
    await db.commit()
    return await _po_out(db, po)


class POReceiveLineIn(BaseModel):
    line_id: str
    quantity: float = Field(gt=0, le=100_000)


class POReceiveBody(BaseModel):
    # Üresen: minden tétel hátralévő mennyisége beérkezik.
    lines: list[POReceiveLineIn] | None = None


@po_router.post("/{po_id}/receive", response_model=POOut)
async def receive_purchase_order(
    po_id: str,
    body: POReceiveBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Beérkeztetés a rendelés cél-raktárába. Részteljesítés támogatott; ha
    minden tétel beérkezett, a rendelés received státuszba kerül."""
    po = await _get_po_or_404(db, po_id)
    if po.status not in ("draft", "ordered"):
        raise HTTPException(status_code=422, detail={"code": "po.bad_status"})
    if po.warehouse_id is None:
        raise HTTPException(status_code=422, detail={"code": "po.no_warehouse"})

    all_lines = (
        await db.execute(
            select(PurchaseOrderLine).where(PurchaseOrderLine.purchase_order_id == po.id)
        )
    ).scalars().all()
    by_id = {ln.id: ln for ln in all_lines}

    if body.lines:
        to_receive = []
        for item in body.lines:
            ln = by_id.get(_parse_uuid(item.line_id, "po.line_not_found"))
            if ln is None:
                raise HTTPException(status_code=404, detail={"code": "po.line_not_found"})
            to_receive.append((ln, item.quantity))
    else:
        to_receive = [
            (ln, ln.quantity - ln.received_qty)
            for ln in all_lines
            if ln.quantity - ln.received_qty > 1e-9
        ]

    received_any = False
    for ln, qty in to_receive:
        if qty <= 1e-9:
            continue
        product = (
            await db.execute(select(Product).where(Product.id == ln.product_id))
        ).scalar_one()
        stock = await _stock_row(db, po.warehouse_id, product.id)
        stock.quantity += qty
        ln.received_qty = round(ln.received_qty + qty, 3)
        db.add(WarehouseMovement(
            warehouse_id=po.warehouse_id, product_id=product.id, action="receive",
            quantity_delta=qty, unit_cost=ln.unit_cost, ref_id=po.id,
            note=po.order_no, actor_user_id=actor.id,
        ))
        if ln.unit_cost is not None:
            product.purchase_price = ln.unit_cost
        received_any = True

    if received_any and all(
        ln.received_qty + 1e-9 >= ln.quantity for ln in all_lines
    ):
        po.status = "received"
        po.received_at = datetime.now(UTC)
    elif received_any and po.status == "draft":
        po.status = "ordered"
        po.ordered_at = po.ordered_at or datetime.now(UTC)

    await record_audit(
        db, actor=actor, action="po.receive", entity_type="purchase_order",
        entity_id=str(po.id),
        detail={"order_no": po.order_no, "status": po.status},
        request=request,
    )
    await db.commit()
    return await _po_out(db, po)


@po_router.post("/{po_id}/cancel", response_model=POOut)
async def cancel_purchase_order(
    po_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    po = await _get_po_or_404(db, po_id)
    if po.status not in ("draft", "ordered"):
        raise HTTPException(status_code=422, detail={"code": "po.bad_status"})
    po.status = "cancelled"
    await record_audit(
        db, actor=actor, action="po.cancel", entity_type="purchase_order",
        entity_id=str(po.id), detail={"order_no": po.order_no}, request=request,
    )
    await db.commit()
    return await _po_out(db, po)
