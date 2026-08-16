"""Digitális szállítólevelek: áru kiszállítása elszámolás nélkül.

Bármelyik képviselő vihet/küldhet árut bármelyik partnernek — a tételek a
partner KÖVETKEZŐ elszámolásán automatikusan betöltődnek és kiszámlázódnak
(SZL-hivatkozással a bizonylaton/számlán). Ha forrás-raktár van megadva, a
készlet kiadásként levonódik; visszavonáskor visszakerül.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.db import get_db
from app.models import (
    DeliveryNote,
    DeliveryNoteLine,
    Partner,
    Product,
    User,
    Warehouse,
    WarehouseMovement,
    WarehouseStock,
)

router = APIRouter()


async def _next_serial(db: AsyncSession) -> str:
    year = datetime.now(UTC).year
    count = (
        await db.execute(
            select(sa_func.count()).select_from(DeliveryNote).where(
                DeliveryNote.serial.like(f"SZL-{year}-%")
            )
        )
    ).scalar_one()
    return f"SZL-{year}-{count + 1:04d}"


class DeliveryLineIn(BaseModel):
    product_id: str
    quantity: float = Field(gt=0, le=100_000)
    unit_price: float = Field(ge=0)  # Ft/egység, nettó


class DeliveryCreateBody(BaseModel):
    partner_id: str
    source_warehouse_id: str | None = None
    note: str | None = Field(default=None, max_length=512)
    lines: list[DeliveryLineIn] = Field(min_length=1, max_length=50)


class DeliveryLineOut(BaseModel):
    product_id: str | None
    product_name: str
    unit: str
    quantity: float
    unit_price: float
    amount_net: float


class DeliveryOut(BaseModel):
    id: str
    serial: str
    partner_id: str
    partner_name: str | None
    status: str
    source_warehouse_name: str | None
    note: str | None
    created_by_name: str | None
    created_at: datetime
    total_net: float
    lines: list[DeliveryLineOut]


async def _out(db: AsyncSession, dn: DeliveryNote) -> DeliveryOut:
    lines = (
        await db.execute(
            select(DeliveryNoteLine).where(DeliveryNoteLine.delivery_note_id == dn.id)
        )
    ).scalars().all()
    partner = (
        await db.execute(select(Partner).where(Partner.id == dn.partner_id))
    ).scalar_one_or_none()
    wh_name = None
    if dn.source_warehouse_id:
        wh = (
            await db.execute(select(Warehouse).where(Warehouse.id == dn.source_warehouse_id))
        ).scalar_one_or_none()
        wh_name = wh.name if wh else None
    return DeliveryOut(
        id=str(dn.id), serial=dn.serial, partner_id=str(dn.partner_id),
        partner_name=partner.name if partner else None,
        status=dn.status, source_warehouse_name=wh_name, note=dn.note,
        created_by_name=dn.created_by_name, created_at=dn.created_at,
        total_net=round(sum(ln.quantity * ln.unit_price for ln in lines), 2),
        lines=[
            DeliveryLineOut(
                product_id=str(ln.product_id) if ln.product_id else None,
                product_name=ln.product_name, unit=ln.unit,
                quantity=ln.quantity, unit_price=ln.unit_price,
                amount_net=round(ln.quantity * ln.unit_price, 2),
            )
            for ln in lines
        ],
    )


@router.get("", response_model=list[DeliveryOut])
async def list_deliveries(
    partner_id: str | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    query = select(DeliveryNote).order_by(DeliveryNote.created_at.desc()).limit(200)
    if partner_id:
        try:
            query = query.where(DeliveryNote.partner_id == uuid.UUID(partner_id))
        except ValueError:
            raise HTTPException(status_code=404, detail={"code": "partner.not_found"})
    if status:
        query = query.where(DeliveryNote.status == status)
    rows = (await db.execute(query)).scalars().all()
    return [await _out(db, dn) for dn in rows]


@router.post("", response_model=DeliveryOut, status_code=201)
async def create_delivery(
    body: DeliveryCreateBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    try:
        pid = uuid.UUID(body.partner_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "partner.not_found"})
    partner = (await db.execute(select(Partner).where(Partner.id == pid))).scalar_one_or_none()
    if partner is None:
        raise HTTPException(status_code=404, detail={"code": "partner.not_found"})

    src: Warehouse | None = None
    if body.source_warehouse_id:
        from app.api.warehouse import _get_warehouse_or_404

        src = await _get_warehouse_or_404(db, body.source_warehouse_id)

    dn = DeliveryNote(
        serial=await _next_serial(db), partner_id=partner.id,
        source_warehouse_id=src.id if src else None, note=body.note,
        created_by=actor.id, created_by_name=actor.display_name,
    )
    db.add(dn)
    await db.flush()

    for item in body.lines:
        try:
            prod_id = uuid.UUID(item.product_id)
        except ValueError:
            raise HTTPException(status_code=404, detail={"code": "product.not_found"})
        product = (
            await db.execute(select(Product).where(Product.id == prod_id))
        ).scalar_one_or_none()
        if product is None:
            raise HTTPException(status_code=404, detail={"code": "product.not_found"})
        db.add(DeliveryNoteLine(
            delivery_note_id=dn.id, product_id=product.id, product_name=product.name,
            unit=product.unit, quantity=item.quantity, unit_price=item.unit_price,
            vat_percent=product.vat_percent,
        ))
        if src is not None:
            from app.api.warehouse import warehouse_issue

            await warehouse_issue(
                db, warehouse_id=src.id, product=product, quantity=item.quantity,
                partner_id=partner.id, actor_id=actor.id,
            )

    await record_audit(
        db, actor=actor, action="delivery.create", entity_type="delivery_note",
        entity_id=dn.serial,
        detail={"partner": partner.name, "lines": len(body.lines)},
        request=request,
    )
    await db.commit()
    return await _out(db, dn)


@router.post("/{delivery_id}/cancel", response_model=DeliveryOut)
async def cancel_delivery(
    delivery_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Nyitott szállítólevél visszavonása — a forrás-raktár készlete
    visszakerül, a tételek nem számlázódnak."""
    try:
        did = uuid.UUID(delivery_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "delivery.not_found"})
    dn = (
        await db.execute(select(DeliveryNote).where(DeliveryNote.id == did))
    ).scalar_one_or_none()
    if dn is None:
        raise HTTPException(status_code=404, detail={"code": "delivery.not_found"})
    if dn.status != "open":
        raise HTTPException(status_code=422, detail={"code": "delivery.bad_status"})

    if dn.source_warehouse_id is not None:
        lines = (
            await db.execute(
                select(DeliveryNoteLine).where(DeliveryNoteLine.delivery_note_id == dn.id)
            )
        ).scalars().all()
        for ln in lines:
            if ln.product_id is None:
                continue
            stock = (
                await db.execute(
                    select(WarehouseStock).where(
                        WarehouseStock.warehouse_id == dn.source_warehouse_id,
                        WarehouseStock.product_id == ln.product_id,
                    )
                )
            ).scalar_one_or_none()
            if stock is None:
                stock = WarehouseStock(
                    warehouse_id=dn.source_warehouse_id, product_id=ln.product_id,
                    quantity=0.0,
                )
                db.add(stock)
            stock.quantity += ln.quantity
            db.add(WarehouseMovement(
                warehouse_id=dn.source_warehouse_id, product_id=ln.product_id,
                action="adjust", quantity_delta=ln.quantity,
                note=f"{dn.serial} visszavonva", actor_user_id=actor.id,
            ))

    dn.status = "cancelled"
    await record_audit(
        db, actor=actor, action="delivery.cancel", entity_type="delivery_note",
        entity_id=dn.serial, request=request,
    )
    await db.commit()
    return await _out(db, dn)
