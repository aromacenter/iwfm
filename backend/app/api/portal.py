"""Partner-portál: token-alapú, csak-olvasható nyilvános nézet.

A partner egy kitalálhatatlan (32 bájt entrópiájú) tokenes linket kap; a
linken belépés nélkül látja a saját külső raktár-készletét, elszámolásait és
kintlévőségeit. A token admin/manager által generálható és visszavonható —
visszavonás után a link azonnal érvénytelen. PII-t nem adunk ki, csak a
partner saját üzleti adatait.
"""

from __future__ import annotations

import math
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.consignment import _get_partner_or_404, portions_from
from app.api.deps import record_audit, require_perm
from app.db import get_db
from app.models import Partner, PartnerStock, Product, Settlement, User

router = APIRouter()  # /api/portal (nyilvános GET + kezelő végpontok)
manage_router = APIRouter()  # /api/partners/{id}/portal-link


@manage_router.post("/{partner_id}/portal-link")
async def create_portal_link(
    partner_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("partners")),
):
    """Portál-link generálása (ha már van, azt adja vissza)."""
    partner = await _get_partner_or_404(db, partner_id)
    if not partner.portal_token:
        partner.portal_token = secrets.token_urlsafe(32)
        await record_audit(
            db, actor=actor, action="partner.portal_link", entity_type="partner",
            entity_id=str(partner.id), request=request,
        )
        await db.commit()
    return {"token": partner.portal_token, "path": f"/portal/{partner.portal_token}"}


@manage_router.delete("/{partner_id}/portal-link")
async def revoke_portal_link(
    partner_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("partners")),
):
    """A portál-link visszavonása — a korábbi URL érvénytelenné válik."""
    partner = await _get_partner_or_404(db, partner_id)
    partner.portal_token = None
    await record_audit(
        db, actor=actor, action="partner.portal_revoke", entity_type="partner",
        entity_id=str(partner.id), request=request,
    )
    await db.commit()
    return {"ok": True}


@router.get("/{token}")
async def portal_view(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """A partner nyilvános nézete (nincs bejelentkezés — a token a kulcs)."""
    if not token or len(token) < 16:
        raise HTTPException(status_code=404, detail={"code": "portal.not_found"})
    partner = (
        await db.execute(select(Partner).where(Partner.portal_token == token))
    ).scalar_one_or_none()
    if partner is None or not partner.is_active:
        raise HTTPException(status_code=404, detail={"code": "portal.not_found"})

    stock_rows = (
        await db.execute(
            select(PartnerStock, Product)
            .join(Product, Product.id == PartnerStock.product_id)
            .where(PartnerStock.partner_id == partner.id)
            .order_by(Product.name)
        )
    ).all()
    settlements = (
        await db.execute(
            select(Settlement)
            .where(Settlement.partner_id == partner.id)
            .order_by(Settlement.created_at.desc())
            .limit(20)
        )
    ).scalars().all()
    outstanding = [
        s for s in settlements if s.invoiced and s.payment_status == "outstanding"
    ]

    return {
        "partner_name": partner.name,
        "partner_code": partner.partner_code,
        "stock": [
            {
                "product_name": product.name,
                "unit": product.unit,
                "quantity": stock.quantity,
                "portions_available": int(
                    math.floor(portions_from(stock.quantity, product.grams_per_portion))
                ),
            }
            for stock, product in stock_rows
        ],
        "settlements": [
            {
                "created_at": s.created_at.isoformat(),
                "payment_method": s.payment_method,
                "total_gross": s.total_gross,
                "invoiced": s.invoiced,
                "payment_status": s.payment_status,
                "due_date": s.due_date.isoformat() if s.due_date else None,
            }
            for s in settlements
        ],
        "outstanding_total": round(sum(s.total_gross for s in outstanding), 2),
    }
