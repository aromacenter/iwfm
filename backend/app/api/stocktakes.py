"""Leltár modul (stocktake): raktári leltár-ívek.

Folyamat: nyitás (a raktár termékeiről könyv szerinti pillanatkép készül) →
számolt mennyiségek rögzítése (mentés közben is) → zárás: a számolt értékek
leltár-korrekciós (adjust) mozgással a készletre állnak, az eltérések az
Átírás-riportban is látszanak. Üresen hagyott sor = nem számolták, nem változik.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.db import get_db
from app.models import (
    Product,
    Stocktake,
    StocktakeLine,
    User,
    Warehouse,
    WarehouseMovement,
    WarehouseStock,
)
from app.services.wfm.license import require_module

router = APIRouter(dependencies=[Depends(require_module("stocktake"))])


class StocktakeLineOut(BaseModel):
    id: str
    product_id: str
    product_name: str
    unit: str
    system_qty: float
    current_qty: float  # a MOSTANI könyv szerinti készlet (zárás ehhez képest korrigál)
    counted_qty: float | None


class StocktakeOut(BaseModel):
    id: str
    warehouse_id: str
    warehouse_name: str
    status: str
    note: str | None
    opened_by_name: str | None
    closed_by_name: str | None
    opened_at: datetime
    closed_at: datetime | None
    line_count: int = 0
    counted_count: int = 0
    diff_count: int = 0  # eltéréses sorok (zárt leltárnál végleges)
    lines: list[StocktakeLineOut] = []


class StocktakeCreateBody(BaseModel):
    warehouse_id: str
    note: str | None = Field(default=None, max_length=512)


class LineUpdate(BaseModel):
    id: str
    counted_qty: float | None = Field(default=None, ge=0, le=1_000_000)


class LinesBody(BaseModel):
    lines: list[LineUpdate] = Field(max_length=2000)


async def _get_or_404(db: AsyncSession, stocktake_id: str) -> Stocktake:
    try:
        sid = uuid.UUID(stocktake_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "stocktake.not_found"})
    row = (
        await db.execute(select(Stocktake).where(Stocktake.id == sid))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "stocktake.not_found"})
    return row


async def _current_stock(db: AsyncSession, warehouse_id: uuid.UUID) -> dict[uuid.UUID, float]:
    rows = (
        await db.execute(
            select(WarehouseStock.product_id, WarehouseStock.quantity).where(
                WarehouseStock.warehouse_id == warehouse_id
            )
        )
    ).all()
    return {pid: float(q or 0.0) for pid, q in rows}


async def _out(db: AsyncSession, st: Stocktake, with_lines: bool) -> StocktakeOut:
    lines = (
        await db.execute(
            select(StocktakeLine)
            .where(StocktakeLine.stocktake_id == st.id)
            .order_by(StocktakeLine.product_name)
        )
    ).scalars().all()
    current = await _current_stock(db, st.warehouse_id) if with_lines else {}
    counted = [ln for ln in lines if ln.counted_qty is not None]
    diffs = 0
    out_lines: list[StocktakeLineOut] = []
    for ln in lines:
        cur = current.get(ln.product_id, ln.system_qty)
        if ln.counted_qty is not None and abs(ln.counted_qty - (cur if st.status == "open" else ln.system_qty)) > 1e-9:
            diffs += 1
        if with_lines:
            out_lines.append(StocktakeLineOut(
                id=str(ln.id), product_id=str(ln.product_id),
                product_name=ln.product_name, unit=ln.unit,
                system_qty=round(ln.system_qty, 2),
                current_qty=round(cur, 2),
                counted_qty=ln.counted_qty,
            ))
    return StocktakeOut(
        id=str(st.id), warehouse_id=str(st.warehouse_id),
        warehouse_name=st.warehouse_name, status=st.status, note=st.note,
        opened_by_name=st.opened_by_name, closed_by_name=st.closed_by_name,
        opened_at=st.opened_at, closed_at=st.closed_at,
        line_count=len(lines), counted_count=len(counted), diff_count=diffs,
        lines=out_lines,
    )


@router.get("", response_model=list[StocktakeOut])
async def list_stocktakes(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    rows = (
        await db.execute(select(Stocktake).order_by(Stocktake.opened_at.desc()).limit(200))
    ).scalars().all()
    return [await _out(db, st, with_lines=False) for st in rows]


@router.post("", response_model=StocktakeOut, status_code=201)
async def create_stocktake(
    body: StocktakeCreateBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Leltár nyitása: pillanatkép a raktár (kézzel felvett) termékeiről."""
    try:
        wid = uuid.UUID(body.warehouse_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "warehouse.not_found"})
    wh = (
        await db.execute(select(Warehouse).where(Warehouse.id == wid))
    ).scalar_one_or_none()
    if wh is None:
        raise HTTPException(status_code=404, detail={"code": "warehouse.not_found"})
    open_exists = (
        await db.execute(
            select(Stocktake.id).where(
                Stocktake.warehouse_id == wh.id, Stocktake.status == "open"
            )
        )
    ).scalars().first()
    if open_exists:
        raise HTTPException(status_code=422, detail={"code": "stocktake.already_open"})
    st = Stocktake(
        id=uuid.uuid4(), warehouse_id=wh.id, warehouse_name=wh.name,
        status="open", note=(body.note or "").strip() or None,
        opened_by_name=actor.display_name, opened_at=datetime.now(UTC),
    )
    db.add(st)
    stock_rows = (
        await db.execute(
            select(WarehouseStock, Product)
            .join(Product, Product.id == WarehouseStock.product_id)
            .where(WarehouseStock.warehouse_id == wh.id)
            .order_by(Product.name)
        )
    ).all()
    for srow, product in stock_rows:
        db.add(StocktakeLine(
            id=uuid.uuid4(), stocktake_id=st.id, product_id=product.id,
            product_name=product.name, unit=product.unit,
            system_qty=float(srow.quantity or 0.0), counted_qty=None,
        ))
    await record_audit(
        db, actor=actor, action="stocktake.open", entity_type="stocktake",
        entity_id=str(st.id),
        detail={"warehouse": wh.name, "lines": len(stock_rows)}, request=request,
    )
    await db.commit()
    return await _out(db, st, with_lines=True)


@router.get("/{stocktake_id}", response_model=StocktakeOut)
async def get_stocktake(
    stocktake_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    st = await _get_or_404(db, stocktake_id)
    return await _out(db, st, with_lines=True)


@router.put("/{stocktake_id}/lines", response_model=StocktakeOut)
async def save_lines(
    stocktake_id: str,
    body: LinesBody,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Számolt mennyiségek mentése (nyitott leltáron, részletekben is)."""
    st = await _get_or_404(db, stocktake_id)
    if st.status != "open":
        raise HTTPException(status_code=422, detail={"code": "stocktake.closed"})
    by_id: dict[uuid.UUID, float | None] = {}
    for u in body.lines:
        try:
            by_id[uuid.UUID(u.id)] = u.counted_qty
        except ValueError:
            continue
    if by_id:
        rows = (
            await db.execute(
                select(StocktakeLine).where(
                    StocktakeLine.stocktake_id == st.id,
                    StocktakeLine.id.in_(by_id.keys()),
                )
            )
        ).scalars().all()
        for ln in rows:
            ln.counted_qty = by_id.get(ln.id)
    await db.commit()
    return await _out(db, st, with_lines=True)


@router.post("/{stocktake_id}/close", response_model=StocktakeOut)
async def close_stocktake(
    stocktake_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Zárás: a számolt értékek a készletre állnak, az eltérés adjust-mozgás.
    A system_qty a záráskori könyv szerinti értékre frissül (ahhoz képest
    végleges az eltérés); a nem számolt sorok változatlanok maradnak."""
    st = await _get_or_404(db, stocktake_id)
    if st.status != "open":
        raise HTTPException(status_code=422, detail={"code": "stocktake.closed"})
    lines = (
        await db.execute(select(StocktakeLine).where(StocktakeLine.stocktake_id == st.id))
    ).scalars().all()
    stock_rows = {
        row.product_id: row
        for row in (
            await db.execute(
                select(WarehouseStock).where(WarehouseStock.warehouse_id == st.warehouse_id)
            )
        ).scalars().all()
    }
    adjusted = 0
    for ln in lines:
        srow = stock_rows.get(ln.product_id)
        current = float(srow.quantity or 0.0) if srow else 0.0
        ln.system_qty = current  # a zárás pillanatának könyv szerinti értéke
        if ln.counted_qty is None:
            continue
        delta = ln.counted_qty - current
        if abs(delta) <= 1e-9:
            continue
        if srow is None:
            srow = WarehouseStock(
                warehouse_id=st.warehouse_id, product_id=ln.product_id, quantity=0.0
            )
            db.add(srow)
            stock_rows[ln.product_id] = srow
        srow.quantity = ln.counted_qty
        db.add(WarehouseMovement(
            warehouse_id=st.warehouse_id, product_id=ln.product_id, action="adjust",
            quantity_delta=delta,
            note=f"Leltár ({st.opened_at.date().isoformat()}) — {ln.product_name}"[:512],
            actor_user_id=actor.id,
        ))
        adjusted += 1
    st.status = "closed"
    st.closed_at = datetime.now(UTC)
    st.closed_by_name = actor.display_name
    await record_audit(
        db, actor=actor, action="stocktake.close", entity_type="stocktake",
        entity_id=str(st.id),
        detail={"warehouse": st.warehouse_name, "adjusted": adjusted,
                "counted": sum(1 for ln in lines if ln.counted_qty is not None)},
        request=request,
    )
    await db.commit()
    return await _out(db, st, with_lines=True)
