"""Beérkezett termékrendelések (a QR-oldalról) kezelése.

Az üzletkötő az Elszámolás oldalon látja a nyitott rendeléseket, és
teljesítéskor (vagy elvetéskor) lezárja őket. Törlés a törlés-joghoz kötött.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.db import get_db
from app.models import ProductOrder, User

router = APIRouter()

STATUSES = ("open", "done", "cancelled")


class OrderOut(BaseModel):
    id: str
    order_no: str
    partner_id: str | None
    partner_label: str | None
    items: list
    note: str | None
    contact_name: str | None
    contact_phone: str | None
    status: str
    done_at: datetime | None
    created_at: datetime


def _out(o: ProductOrder) -> OrderOut:
    return OrderOut(
        id=str(o.id),
        order_no=o.order_no,
        partner_id=str(o.partner_id) if o.partner_id else None,
        partner_label=o.partner_label,
        items=o.items or [],
        note=o.note,
        contact_name=o.contact_name,
        contact_phone=o.contact_phone,
        status=o.status,
        done_at=o.done_at,
        created_at=o.created_at,
    )


@router.get("", response_model=list[OrderOut])
async def list_orders(
    status: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    query = select(ProductOrder).order_by(ProductOrder.created_at.desc())
    if status:
        if status not in STATUSES:
            raise HTTPException(status_code=422, detail={"code": "order.bad_status"})
        query = query.where(ProductOrder.status == status)
    rows = (await db.execute(query.limit(500))).scalars().all()
    return [_out(o) for o in rows]


class OrderStatusBody(BaseModel):
    status: str


@router.patch("/{order_id}", response_model=OrderOut)
async def update_order(
    order_id: str,
    body: OrderStatusBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    if body.status not in STATUSES:
        raise HTTPException(status_code=422, detail={"code": "order.bad_status"})
    try:
        oid = uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "order.not_found"})
    o = (
        await db.execute(select(ProductOrder).where(ProductOrder.id == oid))
    ).scalar_one_or_none()
    if o is None:
        raise HTTPException(status_code=404, detail={"code": "order.not_found"})
    o.status = body.status
    o.done_at = datetime.now(UTC) if body.status in ("done", "cancelled") else None
    await record_audit(
        db, actor=actor, action="order.status", entity_type="product_order",
        entity_id=o.order_no, detail={"status": body.status}, request=request,
    )
    await db.commit()
    return _out(o)


