"""Alkatrész-raktár — a munkalap-oldalról is hívott, útvonal-független rész
(B fázis): a munkalapra beépített alkatrészek listája a mozgásnaplóból.

A beépítés ``use`` mozgás negatív mennyiséggel (worksheet_id-val); a
visszavonás szintén ``use`` mozgás pozitív mennyiséggel, a ``document_no``
mezőben az eredeti mozgás azonosítójával (append-only napló, nincs törlés)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Part, PartsMovement, PartsWarehouse


class UsedPartOut(BaseModel):
    movement_id: str
    part_id: str
    sku: str
    name: str
    unit: str
    quantity: float
    price_net: float | None  # a MI ügyfél-árunk (pillanatkép)
    cost_net: float | None  # beszerzési ár (belső)
    warehouse_name: str
    created_at: datetime


async def used_parts_for_worksheet(db: AsyncSession, worksheet_id: uuid.UUID) -> list[UsedPartOut]:
    rows = (
        await db.execute(
            select(PartsMovement, Part, PartsWarehouse.name)
            .join(Part, Part.id == PartsMovement.part_id)
            .join(PartsWarehouse, PartsWarehouse.id == PartsMovement.warehouse_id)
            .where(PartsMovement.worksheet_id == worksheet_id, PartsMovement.kind == "use")
            .order_by(PartsMovement.created_at)
        )
    ).all()
    reversed_ids = {m.document_no for m, _, _ in rows if m.quantity_delta > 0 and m.document_no}
    return [
        UsedPartOut(
            movement_id=str(m.id), part_id=str(p.id), sku=p.sku, name=p.name, unit=p.unit,
            quantity=round(-m.quantity_delta, 4), price_net=m.price_net, cost_net=m.unit_cost,
            warehouse_name=whn, created_at=m.created_at,
        )
        for m, p, whn in rows
        if m.quantity_delta < 0 and str(m.id) not in reversed_ids
    ]


def used_parts_as_materials(items: list[UsedPartOut]) -> list[dict]:
    """A beépített alkatrészek anyag-sorként (munkalap PDF / ajánlat)."""
    return [
        {
            "name": f"{u.name} ({u.sku})", "qty": f"{u.quantity:g}", "unit": u.unit,
            "price_net": u.price_net, "cost_net": u.cost_net,
        }
        for u in items
    ]
