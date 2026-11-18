"""Tárhelyezett raktár (161. kör): sor → állás → szint → fakk.

A raktár-készlet (warehouse_stock) marad a termékenkénti ÖSSZES mennyiség —
minden meglévő folyamat (átadás, kiadás, leltár, beszerzés) változatlanul
azt mozgatja. A tárhely-bontás (warehouse_stock_locations) ezen belül
mondja meg, hogy a mennyiség hol van. Ami nincs tárhelyre téve, az a
„nem tárhelyezett” (unplaced = összes − tárhelyeken lévő).

Átrendezés: a tárhely saját azonosítójú sor, a készlet az azonosítóhoz
kötődik, ezért a sor/állás/szint/fakk koordináták szabadon átírhatók — a
kód újragenerálódik, a készlet „velemegy”.
"""

from __future__ import annotations

import re
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Warehouse, WarehouseLocation, WarehouseStock, WarehouseStockLocation

EPS = 1e-9
_ROW_LETTERS = re.compile(r"^[A-Z]{1,3}$")
_ROW_NUMBERS = re.compile(r"^[1-9][0-9]{0,2}$")


def location_code(row_code: str, bay: int, level: int, slot: int) -> str:
    """Tárhely-kód: A-03-2 (sor-állás-szint), fakkal A-03-2-1."""
    base = f"{row_code}-{bay:02d}-{level}"
    return f"{base}-{slot}" if slot else base


def normalize_row_code(raw: str, scheme: str) -> str:
    """A sor-kód a raktár sémája szerint: betű (A, B, … AA) vagy szám (1, 2, …)."""
    code = (raw or "").strip().upper()
    pattern = _ROW_NUMBERS if scheme == "numbers" else _ROW_LETTERS
    if not pattern.match(code):
        raise HTTPException(status_code=422, detail={"code": "warehouse.bad_row_code", "scheme": scheme})
    return code


def is_located(wh: Warehouse) -> bool:
    return (getattr(wh, "layout", None) or "simple") == "located"


async def located_rows(
    db: AsyncSession, warehouse_id: uuid.UUID, product_id: uuid.UUID
) -> list[WarehouseStockLocation]:
    return list((
        await db.execute(
            select(WarehouseStockLocation).where(
                WarehouseStockLocation.warehouse_id == warehouse_id,
                WarehouseStockLocation.product_id == product_id,
            )
        )
    ).scalars().all())


async def located_sum(db: AsyncSession, warehouse_id: uuid.UUID, product_id: uuid.UUID) -> float:
    return sum(r.quantity for r in await located_rows(db, warehouse_id, product_id))


async def _total(db: AsyncSession, warehouse_id: uuid.UUID, product_id: uuid.UUID) -> float:
    row = (
        await db.execute(
            select(WarehouseStock).where(
                WarehouseStock.warehouse_id == warehouse_id,
                WarehouseStock.product_id == product_id,
            )
        )
    ).scalar_one_or_none()
    return row.quantity if row is not None else 0.0


async def located_add(
    db: AsyncSession, warehouse_id: uuid.UUID, product_id: uuid.UUID,
    location_id: uuid.UUID, quantity: float,
) -> WarehouseStockLocation:
    """Mennyiség RÁTÉTELE egy tárhelyre (a termékenkénti összes NEM változik —
    azt a hívó kezeli). Bevételezésnél tárhellyel, illetve berakásnál."""
    row = (
        await db.execute(
            select(WarehouseStockLocation).where(
                WarehouseStockLocation.warehouse_id == warehouse_id,
                WarehouseStockLocation.product_id == product_id,
                WarehouseStockLocation.location_id == location_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = WarehouseStockLocation(
            warehouse_id=warehouse_id, product_id=product_id,
            location_id=location_id, quantity=0.0,
        )
        db.add(row)
    row.quantity += quantity
    await db.flush()
    return row


async def located_take(
    db: AsyncSession, warehouse_id: uuid.UUID, product_id: uuid.UUID,
    location_id: uuid.UUID, quantity: float,
) -> float:
    """Mennyiség LEVÉTELE egy adott tárhelyről. 422, ha ott nincs ennyi."""
    row = (
        await db.execute(
            select(WarehouseStockLocation).where(
                WarehouseStockLocation.warehouse_id == warehouse_id,
                WarehouseStockLocation.product_id == product_id,
                WarehouseStockLocation.location_id == location_id,
            )
        )
    ).scalar_one_or_none()
    have = row.quantity if row is not None else 0.0
    if have + EPS < quantity:
        raise HTTPException(
            status_code=422,
            detail={"code": "warehouse.location_insufficient", "available": round(have, 3)},
        )
    row.quantity -= quantity
    if row.quantity < EPS:
        await db.delete(row)
    await db.flush()
    return have - quantity


async def trim_located(db: AsyncSession, warehouse_id: uuid.UUID, product_id: uuid.UUID) -> float:
    """Egyeztetés az összes készlettel: ha a tárhelyeken több van, mint a
    termék raktári összese (kiadás/átadás/leltár vitte el tárhely nélkül),
    a többletet a legkisebb tárhelyektől kezdve levesszük — „ami elment, az
    a legkisebb kupacból ment el”. Visszaadja a nem tárhelyezett mennyiséget."""
    total = await _total(db, warehouse_id, product_id)
    rows = await located_rows(db, warehouse_id, product_id)
    placed = sum(r.quantity for r in rows)
    excess = placed - total
    if excess > EPS:
        for r in sorted(rows, key=lambda x: x.quantity):
            if excess <= EPS:
                break
            take = min(r.quantity, excess)
            r.quantity -= take
            excess -= take
            if r.quantity < EPS:
                await db.delete(r)
        await db.flush()
        placed = total
    if total < 0:
        return 0.0
    return max(0.0, total - placed)


async def get_location_or_404(
    db: AsyncSession, warehouse_id: uuid.UUID, location_id: str | uuid.UUID
) -> WarehouseLocation:
    try:
        lid = location_id if isinstance(location_id, uuid.UUID) else uuid.UUID(str(location_id))
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "warehouse.location_not_found"})
    loc = (
        await db.execute(
            select(WarehouseLocation).where(
                WarehouseLocation.id == lid, WarehouseLocation.warehouse_id == warehouse_id
            )
        )
    ).scalar_one_or_none()
    if loc is None:
        raise HTTPException(status_code=404, detail={"code": "warehouse.location_not_found"})
    return loc
