"""Partner-portál: token-alapú, csak-olvasható nyilvános nézet.

A partner egy kitalálhatatlan (32 bájt entrópiájú) tokenes linket kap; a
linken belépés nélkül látja a saját külső raktár-készletét, elszámolásait és
kintlévőségeit. A token admin/manager által generálható és visszavonható —
visszavonás után a link azonnal érvénytelen. PII-t nem adunk ki, csak a
partner saját üzleti adatait.

129. kör — önkiszolgáló: a partner látja a nála lévő gépeket és számlálóikat,
a következő elszámolás várható idejét és becsült összegét, és a portálról
rendelhet terméket, illetve kérhet szervizt (ugyanoda fut be, mint a QR-oldali
bejelentés: ProductOrder / ServiceTicket + automatizálási esemény).
"""

from __future__ import annotations

import calendar
import math
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.consignment import _get_partner_or_404, portions_from
from app.api.deps import record_audit, require_perm
from app.db import get_db
from app.models import (
    Asset,
    Partner,
    PartnerContract,
    PartnerStock,
    Product,
    ProductOrder,
    ServiceTicket,
    Settlement,
    User,
)

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


@manage_router.post("/{partner_id}/portal-link/send")
async def send_portal_link(
    partner_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("partners")),
):
    """A partner-portál linkjének elküldése e-mailben a partner beállított
    címeire (26e6a4fc) — a felületen megerősítő kérdés után."""
    from app.core.config import get_settings
    from app.services.wfm.email_service import load_smtp_config, send_email, split_addresses

    partner = await _get_partner_or_404(db, partner_id)
    emails = [e for e in split_addresses(partner.contact_email) if "@" in e]
    if not emails:
        raise HTTPException(status_code=422, detail={"code": "portal.no_email"})
    config = await load_smtp_config(db)
    if config is None:
        raise HTTPException(status_code=422, detail={"code": "email.not_configured"})
    if not partner.portal_token:
        partner.portal_token = secrets.token_urlsafe(32)
    url = f"{get_settings().frontend_origin.rstrip('/')}/portal/{partner.portal_token}"
    body = (
        f"Tisztelt Partnerünk!\n\n"
        f"A(z) {partner.name} partner-oldalát ezen a linken érheti el — itt látható az aktuális "
        f"készlet, a korábbi elszámolások és a fizetésre váró számlák:\n{url}\n\n"
        "A link személyes, kérjük, ne adja tovább.\n\nÜdvözlettel"
    )
    ok = await send_email(config, ", ".join(emails), f"Partner-oldal — {partner.name}", body)
    if not ok:
        raise HTTPException(status_code=502, detail={"code": "email.send_failed"})
    await record_audit(
        db, actor=actor, action="partner.portal_link_sent", entity_type="partner",
        entity_id=str(partner.id), detail={"to": emails}, request=request,
    )
    await db.commit()
    return {"ok": True, "sent_to": emails}


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
    assets = (
        await db.execute(
            select(Asset)
            .where(Asset.partner_id == partner.id, Asset.status == "deployed")
            .order_by(Asset.name, Asset.barcode)
        )
    ).scalars().all()
    products = (
        await db.execute(
            select(Product).where(Product.is_active.is_(True)).order_by(Product.name)
        )
    ).scalars().all()
    orders = (
        await db.execute(
            select(ProductOrder)
            .where(ProductOrder.partner_id == partner.id)
            .order_by(ProductOrder.created_at.desc())
            .limit(10)
        )
    ).scalars().all()
    tickets = (
        await db.execute(
            select(ServiceTicket)
            .where(ServiceTicket.partner_id == partner.id)
            .order_by(ServiceTicket.created_at.desc())
            .limit(10)
        )
    ).scalars().all()

    return {
        "partner_name": partner.name,
        "partner_code": partner.partner_code,
        "assets": [
            {
                "name": a.name,
                "barcode": a.barcode,
                "serial_number": a.serial_number,
                "counter": a.counter,
                "counters": a.counters,
                "counter_names": a.counter_names,
                "support_path": f"/tamogatas/{a.qr_token}" if a.qr_token else None,
            }
            for a in assets
        ],
        "next_settlement": await _next_settlement(db, partner, settlements),
        "products": [
            {"id": str(p.id), "name": p.name, "unit": p.unit} for p in products
        ],
        "orders": [
            {
                "order_no": o.order_no,
                "status": o.status,
                "created_at": o.created_at.isoformat(),
                "items": o.items,
            }
            for o in orders
        ],
        "tickets": [
            {
                "ticket_no": t.ticket_no,
                "title": t.title,
                "status": t.status,
                "created_at": t.created_at.isoformat(),
            }
            for t in tickets
        ],
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


# ---------------------------------------------------------------------------
# 129. kör: következő elszámolás becslése + önkiszolgáló végpontok
# ---------------------------------------------------------------------------

def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)  # SQLite naiv datetime-ot ad vissza
    return dt


def _tue_thu(d: date) -> date:
    """Kedd–csütörtök ablakba igazítás (ugyanaz, mint az esedékes-listán)."""
    shift = {0: 1, 4: -1, 5: -2, 6: 2}.get(d.weekday(), 0)
    return d + timedelta(days=shift)


def _next_fixed_due(c: PartnerContract, today: date) -> date | None:
    """Fix bérleti szerződés következő automata elszámolási napja."""
    from app.services.wfm.rental_billing import CYCLE_DAYS

    cycle = c.billing_cycle or "monthly"
    last = c.rental_last_billed
    if cycle == "monthly":
        day = int(c.billing_day or 1)

        def _on(y: int, m: int) -> date:
            return date(y, m, min(day, calendar.monthrange(y, m)[1]))

        cand = _on(today.year, today.month)
        billed_this_month = bool(last and last.year == today.year and last.month == today.month)
        if cand < today or billed_this_month:
            y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
            cand = _on(y, m)
        return cand
    period = CYCLE_DAYS.get(cycle, 7)
    if last is not None:
        cand = last + timedelta(days=period)
        while cand < today:
            cand += timedelta(days=period)
        return cand
    weekday = int(c.billing_weekday if c.billing_weekday is not None else 0)
    return today + timedelta(days=(weekday - today.weekday()) % 7)


async def _next_settlement(db: AsyncSession, partner: Partner, settlements: list) -> dict:
    """A következő elszámolás várható napja és becsült bruttó összege.

    - fix bérleti szerződésnél: a szerződés ciklusa szerinti nap + a bérleti díj;
    - bizományi/számlálós partnernél: utolsó elszámolás + ciklus (kedd–csütörtökre
      igazítva), az összeg az utolsó 180 nap átlagos napi bruttó forgalmából
      vetítve a két elszámolás közti napokra. Nincs előzmény → nincs becslés.
    """
    from app.services.wfm.contracts import active_contract

    today = date.today()
    c = await active_contract(db, partner.id)
    if c is not None and c.fixed_rental and (c.rent_fee or 0) > 0:
        due = _next_fixed_due(c, today)
        return {
            "kind": "fixed",
            "due_date": due.isoformat() if due else None,
            "estimated_gross": round(float(c.rent_fee) * 1.27, 2),
            "cycle": c.billing_cycle or "monthly",
        }
    last = _aware(settlements[0].created_at) if settlements else None
    weeks = int(partner.contract_settlement_weeks or (c.settlement_weeks if c else 0) or 4)
    due = _tue_thu((last + timedelta(days=weeks * 7)).date()) if last else None
    window_start = datetime.now(UTC) - timedelta(days=180)
    row = (
        await db.execute(
            select(
                sa_func.sum(Settlement.total_gross),
                sa_func.min(Settlement.created_at),
                sa_func.max(Settlement.created_at),
                sa_func.count(),
            ).where(Settlement.partner_id == partner.id, Settlement.created_at >= window_start)
        )
    ).one()
    estimated = None
    total, first, clast, cnt = float(row[0] or 0), _aware(row[1]), _aware(row[2]), int(row[3] or 0)
    if total > 0 and cnt >= 1 and first and clast:
        span = max((clast - first).days, 14)
        avg_daily = total / span
        days = (due - last.date()).days if (due and last) else weeks * 7
        estimated = round(avg_daily * max(days, 1), 0)
    return {
        "kind": "consumption",
        "due_date": due.isoformat() if due else None,
        "estimated_gross": estimated,
        "cycle": f"{weeks}w",
    }


async def _partner_by_token(db: AsyncSession, token: str) -> Partner:
    if not token or len(token) < 16:
        raise HTTPException(status_code=404, detail={"code": "portal.not_found"})
    partner = (
        await db.execute(select(Partner).where(Partner.portal_token == token))
    ).scalar_one_or_none()
    if partner is None or not partner.is_active:
        raise HTTPException(status_code=404, detail={"code": "portal.not_found"})
    return partner


class PortalOrderItem(BaseModel):
    product_id: str
    quantity: float = Field(gt=0, le=1000)


class PortalOrderBody(BaseModel):
    items: list[PortalOrderItem] = Field(min_length=1, max_length=10)
    note: str | None = Field(default=None, max_length=1000)
    contact_name: str | None = Field(default=None, max_length=256)
    contact_phone: str | None = Field(default=None, max_length=64)


@router.post("/{token}/order", status_code=201)
async def portal_order(
    token: str,
    body: PortalOrderBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Termékrendelés a partner-portálról → ugyanaz a rendelés-sor, mint a
    QR-oldalról (az üzletkötő az Elszámolás oldalon látja)."""
    from app.api.support import _next_order_no, _rate_limited

    ip = request.client.host if request.client else "?"
    if _rate_limited(f"portal-order:{ip}", 5):
        raise HTTPException(status_code=429, detail={"code": "support.rate_limited"})
    partner = await _partner_by_token(db, token)

    items = []
    for item in body.items:
        try:
            pid = uuid.UUID(item.product_id)
        except ValueError:
            raise HTTPException(status_code=422, detail={"code": "support.bad_product"})
        product = (
            await db.execute(
                select(Product).where(Product.id == pid, Product.is_active.is_(True))
            )
        ).scalar_one_or_none()
        if product is None:
            raise HTTPException(status_code=422, detail={"code": "support.bad_product"})
        items.append({"name": product.name, "quantity": item.quantity, "unit": product.unit})

    order = ProductOrder(
        order_no=await _next_order_no(db),
        partner_id=partner.id,
        partner_label=partner.name,
        asset_id=None,
        items=items,
        note=(body.note or "").strip() or None,
        contact_name=(body.contact_name or "").strip() or None,
        contact_phone=(body.contact_phone or "").strip() or None,
    )
    db.add(order)
    await db.flush()
    await record_audit(
        db, actor=None, action="portal.order", entity_type="product_order",
        entity_id=order.order_no, detail={"partner": partner.name, "items": len(items)},
        request=request,
    )
    await db.commit()

    from app.services.wfm.automation import fire_event

    fire_event("order.created", {
        "_partner_id": partner.id,
        "rendeles_szam": order.order_no,
        "partner_nev": partner.name,
        "tetel_lista": ", ".join(f"{i['name']} ({i['quantity']:g} {i['unit']})" for i in items),
    })
    return {"order_no": order.order_no}


class PortalTicketBody(BaseModel):
    description: str = Field(min_length=5, max_length=4000)
    asset_barcode: str | None = Field(default=None, max_length=64)
    contact_name: str | None = Field(default=None, max_length=256)
    contact_phone: str | None = Field(default=None, max_length=64)
    contact_email: EmailStr | None = None


@router.post("/{token}/ticket", status_code=201)
async def portal_ticket(
    token: str,
    body: PortalTicketBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Szervizkérés a partner-portálról → szervizjegy (opcionálisan a partner
    egyik gépéhez kötve) + ticket.created automatizálási esemény."""
    from app.api.service import _next_ticket_no
    from app.api.support import _rate_limited

    ip = request.client.host if request.client else "?"
    if _rate_limited(f"portal-ticket:{ip}", 5):
        raise HTTPException(status_code=429, detail={"code": "support.rate_limited"})
    partner = await _partner_by_token(db, token)

    asset = None
    if body.asset_barcode:
        asset = (
            await db.execute(
                select(Asset).where(
                    Asset.partner_id == partner.id, Asset.barcode == body.asset_barcode.strip()
                )
            )
        ).scalar_one_or_none()
        if asset is None:
            raise HTTPException(status_code=422, detail={"code": "portal.bad_asset"})

    contact_bits = " · ".join(
        x for x in (body.contact_name, body.contact_phone, body.contact_email) if x
    )
    description = body.description.strip()
    if contact_bits:
        description = f"{description}\n\nBejelentő (portál): {contact_bits}"
    # A bejelentő e-mailje csak a jegyen marad: a portál-tokent bárki
    # továbbadhatja, abból nem lesz partner-kapcsolattartó (oda megy a portál-link).

    asset_label =f"{asset.name} ({asset.barcode})" if asset else None
    ticket = ServiceTicket(
        ticket_no=await _next_ticket_no(db),
        kind="repair",
        priority="normal",
        title=f"Partner-portál bejelentés — {asset_label or partner.name}",
        description=description,
        asset_id=asset.id if asset else None,
        asset_label=asset_label,
        partner_id=partner.id,
        partner_label=partner.name,
    )
    db.add(ticket)
    await db.flush()
    await record_audit(
        db, actor=None, action="portal.ticket", entity_type="service_ticket",
        entity_id=ticket.ticket_no,
        detail={"partner": partner.name, "asset": asset.barcode if asset else None},
        request=request,
    )
    await db.commit()

    from app.services.wfm.automation import fire_event

    fire_event("ticket.created", {
        "_partner_id": partner.id,
        "cim": ticket.title,
        "prioritas": ticket.priority,
        "fajta": ticket.kind,
        "gep_vonalkod": asset.barcode if asset else "",
        "gep_nev": asset_label or "",
        "partner_nev": partner.name,
        "bejelento": "Partner-portál",
    })
    return {"ticket_no": ticket.ticket_no}
