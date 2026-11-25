"""Tételek (sarzsok) lejárattal és FIFO (177. kör).

Bevételezéskor a mennyiség tételként is rögzül (raktár, termék, tárhely,
lejárat). A tételek TANÁCSADÓ réteg: a készlet mestere továbbra is a
`warehouse_stock` sor. Minden kiadás/átadás/leltár a tételeket a
`trim_lots` egyeztetéssel fogyasztja FIFO-ban (előbb a legkorábbi lejárat,
azon belül a legrégebben érkezett) — ugyanúgy, ahogy a tárhelyeket a
`trim_located`. Így egyetlen kiadási útvonalhoz sem kellett hozzányúlni, és
a lista mindig a valós készlettel egyezik, amikor ránéznek.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, Warehouse, WarehouseLocation, WarehouseLot, WarehouseStock

EPS = 1e-9
SOON_DAYS = 7


def default_expiry(product: Product, today: date | None = None) -> date | None:
    """Alapértelmezett lejárat a termék eltarthatóságából (nap)."""
    if not product.shelf_life_days:
        return None
    from datetime import timedelta

    return (today or datetime.now(UTC).date()) + timedelta(days=int(product.shelf_life_days))


def lot_order(lot: WarehouseLot) -> tuple:
    return (lot.expires_at is None, lot.expires_at or date.max, lot.received_at or datetime.min.replace(tzinfo=UTC))


async def add_lot(
    db: AsyncSession, warehouse_id: uuid.UUID, product_id: uuid.UUID, quantity: float,
    *, expires_at: date | None, location_id: uuid.UUID | None = None, note: str | None = None,
) -> WarehouseLot:
    """Új tétel; azonos (tárhely, lejárat) tételbe olvad, hogy ne szaporodjon."""
    row = (
        await db.execute(
            select(WarehouseLot).where(
                WarehouseLot.warehouse_id == warehouse_id,
                WarehouseLot.product_id == product_id,
                WarehouseLot.location_id.is_(None) if location_id is None else WarehouseLot.location_id == location_id,
                WarehouseLot.expires_at.is_(None) if expires_at is None else WarehouseLot.expires_at == expires_at,
            )
        )
    ).scalars().first()
    if row is None:
        row = WarehouseLot(
            warehouse_id=warehouse_id, product_id=product_id, location_id=location_id,
            expires_at=expires_at, quantity=0.0, note=note, received_at=datetime.now(UTC),
        )
        db.add(row)
    row.quantity += quantity
    await db.flush()
    return row


async def _total(db: AsyncSession, warehouse_id: uuid.UUID, product_id: uuid.UUID) -> float:
    row = (
        await db.execute(
            select(WarehouseStock.quantity).where(
                WarehouseStock.warehouse_id == warehouse_id, WarehouseStock.product_id == product_id
            )
        )
    ).scalar_one_or_none()
    return float(row or 0.0)


async def trim_lots(db: AsyncSession, warehouse_id: uuid.UUID, product_id: uuid.UUID) -> list[WarehouseLot]:
    """Egyeztetés a készlettel: ha a tételek összege több, mint a termék
    raktári összese, a többletet FIFO-ban (legkorábbi lejárat, legrégebbi
    érkezés) levonjuk — „ami elment, az a legrégebbi volt”. Visszaadja a
    megmaradt tételeket FIFO-sorrendben."""
    rows = (
        await db.execute(
            select(WarehouseLot).where(WarehouseLot.warehouse_id == warehouse_id, WarehouseLot.product_id == product_id)
        )
    ).scalars().all()
    rows = sorted(rows, key=lot_order)
    total = await _total(db, warehouse_id, product_id)
    excess = sum(r.quantity for r in rows) - total
    changed = False
    if excess > EPS:
        for r in rows:
            if excess <= EPS:
                break
            take = min(r.quantity, excess)
            r.quantity -= take
            excess -= take
            changed = True
    keep: list[WarehouseLot] = []
    for r in rows:
        if r.quantity <= EPS:
            await db.delete(r)
            changed = True
        else:
            keep.append(r)
    if changed:
        await db.flush()
    return keep


def days_left(expires_at: date | None, today: date) -> int | None:
    return None if expires_at is None else (expires_at - today).days


def lot_status(expires_at: date | None, today: date) -> str:
    d = days_left(expires_at, today)
    if d is None:
        return "ok"
    if d < 0:
        return "expired"
    return "soon" if d <= SOON_DAYS else "ok"


async def lots_for_warehouse(db: AsyncSession, warehouse_id: uuid.UUID, product_id: uuid.UUID | None = None) -> list[dict]:
    """Egy raktár tételei (egyeztetve), FIFO-sorrendben, tárhely-kóddal."""
    q = select(WarehouseLot.product_id).where(WarehouseLot.warehouse_id == warehouse_id).distinct()
    if product_id is not None:
        q = q.where(WarehouseLot.product_id == product_id)
    pids = [p for (p,) in (await db.execute(q)).all()]
    today = datetime.now(UTC).date()
    out: list[dict] = []
    for pid in pids:
        lots = await trim_lots(db, warehouse_id, pid)
        if not lots:
            continue
        product = await db.get(Product, pid)
        for lot in lots:
            loc = await db.get(WarehouseLocation, lot.location_id) if lot.location_id else None
            out.append({
                "id": str(lot.id), "product_id": str(pid), "product_name": product.name if product else "?",
                "unit": product.unit if product else "", "location_id": str(lot.location_id) if lot.location_id else None,
                "location_code": loc.code if loc else None, "expires_at": lot.expires_at.isoformat() if lot.expires_at else None,
                "quantity": round(lot.quantity, 3), "received_at": lot.received_at.isoformat() if lot.received_at else None,
                "days_left": days_left(lot.expires_at, today), "status": lot_status(lot.expires_at, today),
            })
    out.sort(key=lambda x: (x["product_name"], x["expires_at"] is None, x["expires_at"] or "", x["received_at"] or ""))
    return out


async def next_expiry_map(db: AsyncSession, warehouse_id: uuid.UUID) -> dict[uuid.UUID, tuple[date, str | None]]:
    """Termékenként a legkorábbi lejárat (és annak tárhelye) a raktárban."""
    out: dict[uuid.UUID, tuple[date, str | None]] = {}
    for lot in await lots_for_warehouse(db, warehouse_id):
        if lot["expires_at"] is None:
            continue
        pid = uuid.UUID(lot["product_id"])
        d = date.fromisoformat(lot["expires_at"])
        if pid not in out or d < out[pid][0]:
            out[pid] = (d, lot["location_code"])
    return out


async def expiring(db: AsyncSession, days: int = 30, warehouse_ids: list[uuid.UUID] | None = None) -> list[dict]:
    """Minden (látható) raktár lejárt vagy ``days`` napon belül lejáró
    tétele, egyeztetve, lejárat szerint."""
    from datetime import timedelta

    today = datetime.now(UTC).date()
    limit = today + timedelta(days=days)
    q = (
        select(WarehouseLot.warehouse_id, WarehouseLot.product_id)
        .where(WarehouseLot.expires_at.is_not(None), WarehouseLot.expires_at <= limit)
        .distinct()
    )
    if warehouse_ids is not None:
        q = q.where(WarehouseLot.warehouse_id.in_(warehouse_ids))
    pairs = (await db.execute(q)).all()
    out: list[dict] = []
    for wid, pid in pairs:
        lots = await trim_lots(db, wid, pid)
        wh = await db.get(Warehouse, wid)
        product = await db.get(Product, pid)
        for lot in lots:
            if lot.expires_at is None or lot.expires_at > limit:
                continue
            loc = await db.get(WarehouseLocation, lot.location_id) if lot.location_id else None
            out.append({
                "id": str(lot.id), "warehouse_id": str(wid), "warehouse_name": wh.name if wh else "?",
                "product_id": str(pid), "product_name": product.name if product else "?", "unit": product.unit if product else "",
                "location_code": loc.code if loc else None, "expires_at": lot.expires_at.isoformat(),
                "quantity": round(lot.quantity, 3), "days_left": days_left(lot.expires_at, today),
                "status": lot_status(lot.expires_at, today),
            })
    out.sort(key=lambda x: (x["expires_at"], x["warehouse_name"], x["product_name"]))
    return out
