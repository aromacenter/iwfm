"""Bizományosi kávé-raktár: termékek + partnerenkénti külső raktárkészlet.

A partner a "külső raktár": ide töltünk fel terméket (pl. kávét) kg-ban. A
termék gramm/adag beállításából a rendszer kiszámolja, hány adag készíthető.
Az elszámolás (settlements.py-ben) a fizikai leltár alapján számolja a fogyást
és adag × ár/adag alapon számláz.
"""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete, func as sa_func, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_role
from app.db import get_db
from app.models import (
    Partner,
    PartnerStock,
    Product,
    Settlement,
    SettlementLine,
    StockMovement,
    User,
)

products_router = APIRouter()  # /api/products
stock_router = APIRouter()  # /api/partners (kiegészíti az inventory partner-útvonalait)
settlements_router = APIRouter()  # /api/settlements

PAYMENT_METHODS = ("cash", "card", "transfer")


# ─── Termékek ────────────────────────────────────────────────────────────────


class ProductBody(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    unit: str = Field(default="kg", max_length=16)
    grams_per_portion: int = Field(default=7, ge=1, le=100000)
    price_per_portion: float = Field(default=0.0, ge=0)
    vat_percent: int = Field(default=27, ge=0, le=100)
    is_active: bool = True
    notes: str | None = None


class ProductOut(BaseModel):
    id: str
    name: str
    unit: str
    grams_per_portion: int
    price_per_portion: float
    vat_percent: int
    is_active: bool
    notes: str | None


def _product_out(p: Product) -> ProductOut:
    return ProductOut(
        id=str(p.id),
        name=p.name,
        unit=p.unit,
        grams_per_portion=p.grams_per_portion,
        price_per_portion=p.price_per_portion,
        vat_percent=p.vat_percent,
        is_active=p.is_active,
        notes=p.notes,
    )


async def _get_product_or_404(db: AsyncSession, product_id: str) -> Product:
    try:
        pid = uuid.UUID(product_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "product.not_found"})
    p = (await db.execute(select(Product).where(Product.id == pid))).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail={"code": "product.not_found"})
    return p


@products_router.get("", response_model=list[ProductOut])
async def list_products(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("manager")),
):
    rows = (await db.execute(select(Product).order_by(Product.name))).scalars().all()
    return [_product_out(p) for p in rows]


@products_router.post("", response_model=ProductOut, status_code=201)
async def create_product(
    body: ProductBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    p = Product(**body.model_dump())
    db.add(p)
    await db.flush()
    await record_audit(
        db, actor=actor, action="product.create", entity_type="product",
        entity_id=str(p.id), request=request,
    )
    await db.commit()
    return _product_out(p)


@products_router.patch("/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: str,
    body: ProductBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    p = await _get_product_or_404(db, product_id)
    for key, value in body.model_dump().items():
        setattr(p, key, value)
    await record_audit(
        db, actor=actor, action="product.update", entity_type="product",
        entity_id=str(p.id), request=request,
    )
    await db.commit()
    return _product_out(p)


class BulkDeleteBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=1000)


@products_router.post("/bulk-delete")
async def bulk_delete_products(
    body: BulkDeleteBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    """Termékek (tömeges) törlése. Ha egy termékből még van kint készlet
    valamelyik partnernél (>0), nem törölhető — előbb el kell számolni
    (blocked listában jelezzük, a többi törlődik). Az elszámolás-tételek
    pillanatkép-nevet őriznek, így a korábbi elszámolások olvashatók maradnak."""
    from app.models import SettlementLine

    deleted = 0
    blocked: list[dict] = []
    for raw in body.ids:
        try:
            pid = uuid.UUID(raw)
        except ValueError:
            continue
        product = (
            await db.execute(select(Product).where(Product.id == pid))
        ).scalar_one_or_none()
        if product is None:
            continue
        has_stock = (
            await db.execute(
                select(PartnerStock.id)
                .where(PartnerStock.product_id == pid, PartnerStock.quantity > 0)
                .limit(1)
            )
        ).first()
        if has_stock is not None:
            blocked.append({"id": str(pid), "name": product.name, "code": "product.has_stock"})
            continue
        # függő rekordok explicit takarítása (SQLite-on nincs FK-cascade garancia)
        await db.execute(sa_delete(PartnerStock).where(PartnerStock.product_id == pid))
        await db.execute(sa_delete(StockMovement).where(StockMovement.product_id == pid))
        await db.execute(
            sa_update(SettlementLine).where(SettlementLine.product_id == pid).values(product_id=None)
        )
        await db.delete(product)
        deleted += 1
    await record_audit(
        db, actor=actor, action="product.bulk_delete", entity_type="product",
        detail={"deleted": deleted, "blocked": len(blocked)}, request=request,
    )
    await db.commit()
    return {"deleted": deleted, "blocked": blocked}


# ─── Partner-készlet (külső raktár) ──────────────────────────────────────────


def portions_from(quantity_kg: float, grams_per_portion: int) -> float:
    """kg → adag (pontos). A gramm/adag alapján; 0 osztás ellen védve."""
    if grams_per_portion <= 0:
        return 0.0
    return quantity_kg * 1000.0 / grams_per_portion


class StockOut(BaseModel):
    partner_id: str
    product_id: str
    product_name: str
    unit: str
    quantity: float  # aktuális könyv szerinti készlet (kg)
    grams_per_portion: int
    price_per_portion: float
    portions_available: int  # hány adag készíthető (lefelé kerekítve)


class ReplenishBody(BaseModel):
    product_id: str
    quantity: float = Field(gt=0)  # feltöltendő mennyiség (kg)
    note: str | None = Field(default=None, max_length=512)


async def _get_partner_or_404(db: AsyncSession, partner_id: str) -> Partner:
    try:
        pid = uuid.UUID(partner_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "partner.not_found"})
    p = (await db.execute(select(Partner).where(Partner.id == pid))).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail={"code": "partner.not_found"})
    return p


def _stock_out(stock: PartnerStock, product: Product) -> StockOut:
    return StockOut(
        partner_id=str(stock.partner_id),
        product_id=str(product.id),
        product_name=product.name,
        unit=product.unit,
        quantity=stock.quantity,
        grams_per_portion=product.grams_per_portion,
        price_per_portion=product.price_per_portion,
        portions_available=int(math.floor(portions_from(stock.quantity, product.grams_per_portion))),
    )


@stock_router.get("/{partner_id}/stock", response_model=list[StockOut])
async def list_partner_stock(
    partner_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("manager")),
):
    await _get_partner_or_404(db, partner_id)
    rows = (
        await db.execute(
            select(PartnerStock, Product)
            .join(Product, Product.id == PartnerStock.product_id)
            .where(PartnerStock.partner_id == uuid.UUID(partner_id))
            .order_by(Product.name)
        )
    ).all()
    return [_stock_out(stock, product) for stock, product in rows]


@stock_router.post("/{partner_id}/stock/replenish", response_model=StockOut)
async def replenish_partner_stock(
    partner_id: str,
    body: ReplenishBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    """Termék feltöltése a partner külső raktárába (kg-ban)."""
    partner = await _get_partner_or_404(db, partner_id)
    product = await _get_product_or_404(db, body.product_id)

    stock = (
        await db.execute(
            select(PartnerStock).where(
                PartnerStock.partner_id == partner.id,
                PartnerStock.product_id == product.id,
            )
        )
    ).scalar_one_or_none()
    if stock is None:
        stock = PartnerStock(partner_id=partner.id, product_id=product.id, quantity=0.0)
        db.add(stock)
    stock.quantity += body.quantity

    db.add(
        StockMovement(
            partner_id=partner.id, product_id=product.id, action="replenish",
            quantity_delta=body.quantity, note=body.note, actor_user_id=actor.id,
        )
    )
    await record_audit(
        db, actor=actor, action="stock.replenish", entity_type="partner_stock",
        entity_id=str(partner.id), detail={"product": product.name, "qty": body.quantity},
        request=request,
    )
    await db.commit()
    return _stock_out(stock, product)


# ─── Elszámolás (settlement) ─────────────────────────────────────────────────


class SettlementLineIn(BaseModel):
    product_id: str
    physical_qty: float = Field(ge=0)  # fizikai leltár (kg)


class SettlementCreate(BaseModel):
    partner_id: str
    payment_method: str  # cash | card | transfer
    lines: list[SettlementLineIn] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=1000)


class SettlementLineOut(BaseModel):
    product_id: str | None
    product_name: str
    previous_qty: float
    physical_qty: float
    consumed_qty: float
    portions: float
    price_per_portion: float
    vat_percent: int
    amount_net: float
    amount_gross: float


class SettlementOut(BaseModel):
    id: str
    partner_id: str
    partner_name: str | None
    settled_by_name: str
    payment_method: str
    total_net: float
    total_gross: float
    invoiced: bool
    billingo_document_id: str | None
    billingo_status: str | None
    note: str | None
    created_at: datetime
    lines: list[SettlementLineOut] | None = None


def _money(value: float) -> float:
    return round(value, 2)


def _line_out(line: SettlementLine) -> SettlementLineOut:
    gross = _money(line.amount_net * (1 + line.vat_percent / 100))
    return SettlementLineOut(
        product_id=str(line.product_id) if line.product_id else None,
        product_name=line.product_name,
        previous_qty=line.previous_qty,
        physical_qty=line.physical_qty,
        consumed_qty=line.consumed_qty,
        portions=line.portions,
        price_per_portion=line.price_per_portion,
        vat_percent=line.vat_percent,
        amount_net=line.amount_net,
        amount_gross=gross,
    )


def _settlement_out(s: Settlement, partner_name: str | None = None) -> SettlementOut:
    return SettlementOut(
        id=str(s.id),
        partner_id=str(s.partner_id),
        partner_name=partner_name,
        settled_by_name=s.settled_by_name,
        payment_method=s.payment_method,
        total_net=s.total_net,
        total_gross=s.total_gross,
        invoiced=s.invoiced,
        billingo_document_id=s.billingo_document_id,
        billingo_status=s.billingo_status,
        note=s.note,
        created_at=s.created_at,
    )


async def _get_settlement_or_404(db: AsyncSession, settlement_id: str) -> Settlement:
    try:
        sid = uuid.UUID(settlement_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "settlement.not_found"})
    s = (await db.execute(select(Settlement).where(Settlement.id == sid))).scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail={"code": "settlement.not_found"})
    return s


@settlements_router.post("", response_model=SettlementOut, status_code=201)
async def create_settlement(
    body: SettlementCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    """Elszámolás rögzítése: a fizikai leltár alapján kiszámolja a fogyást
    (adag) és az összeget (adag × ár/adag), levonja a partner készletéből.
    A számlázás külön lépés (POST /{id}/invoice)."""
    if body.payment_method not in PAYMENT_METHODS:
        raise HTTPException(status_code=422, detail={"code": "settlement.bad_payment"})
    partner = await _get_partner_or_404(db, body.partner_id)

    settlement = Settlement(
        partner_id=partner.id,
        settled_by_user_id=actor.id,
        settled_by_name=actor.display_name,
        payment_method=body.payment_method,
        total_net=0.0,
        total_gross=0.0,
        note=body.note,
    )
    db.add(settlement)
    await db.flush()

    total_net = 0.0
    total_gross = 0.0
    for line_in in body.lines:
        product = await _get_product_or_404(db, line_in.product_id)
        stock = (
            await db.execute(
                select(PartnerStock).where(
                    PartnerStock.partner_id == partner.id,
                    PartnerStock.product_id == product.id,
                )
            )
        ).scalar_one_or_none()
        previous = stock.quantity if stock is not None else 0.0
        consumed = max(previous - line_in.physical_qty, 0.0)
        portions = portions_from(consumed, product.grams_per_portion)
        amount_net = _money(portions * product.price_per_portion)
        amount_gross = _money(amount_net * (1 + product.vat_percent / 100))
        total_net += amount_net
        total_gross += amount_gross

        db.add(
            SettlementLine(
                settlement_id=settlement.id,
                product_id=product.id,
                product_name=product.name,
                previous_qty=previous,
                physical_qty=line_in.physical_qty,
                consumed_qty=consumed,
                portions=portions,
                price_per_portion=product.price_per_portion,
                vat_percent=product.vat_percent,
                amount_net=amount_net,
            )
        )
        # A könyv szerinti készlet a leltár utáni fizikai mennyiségre áll.
        if stock is None:
            stock = PartnerStock(
                partner_id=partner.id, product_id=product.id, quantity=line_in.physical_qty
            )
            db.add(stock)
        else:
            stock.quantity = line_in.physical_qty
        if consumed > 0:
            db.add(
                StockMovement(
                    partner_id=partner.id, product_id=product.id, action="settlement",
                    quantity_delta=-consumed, settlement_id=settlement.id, actor_user_id=actor.id,
                )
            )

    settlement.total_net = _money(total_net)
    settlement.total_gross = _money(total_gross)

    await record_audit(
        db, actor=actor, action="settlement.create", entity_type="settlement",
        entity_id=str(settlement.id),
        detail={"partner": partner.name, "gross": settlement.total_gross},
        request=request,
    )
    await db.commit()
    out = _settlement_out(settlement, partner.name)
    out.lines = [
        _line_out(line)
        for line in (
            await db.execute(
                select(SettlementLine).where(SettlementLine.settlement_id == settlement.id)
            )
        ).scalars()
    ]
    return out


@settlements_router.get("", response_model=list[SettlementOut])
async def list_settlements(
    partner_id: str | None = Query(default=None),
    settled_by: str | None = Query(default=None),  # user_id
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("manager")),
):
    query = (
        select(Settlement, Partner.name)
        .join(Partner, Partner.id == Settlement.partner_id)
        .order_by(Settlement.created_at.desc())
    )
    if partner_id:
        try:
            query = query.where(Settlement.partner_id == uuid.UUID(partner_id))
        except ValueError:
            raise HTTPException(status_code=422, detail={"code": "settlement.bad_partner"})
    if settled_by:
        try:
            query = query.where(Settlement.settled_by_user_id == uuid.UUID(settled_by))
        except ValueError:
            raise HTTPException(status_code=422, detail={"code": "settlement.bad_user"})
    if date_from:
        query = query.where(Settlement.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.where(Settlement.created_at <= datetime.combine(date_to, datetime.max.time()))

    rows = (await db.execute(query.limit(1000))).all()
    return [_settlement_out(s, name) for s, name in rows]


@settlements_router.post("/bulk-delete")
async def bulk_delete_settlements(
    body: BulkDeleteBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    """Elszámolások (tömeges) törlése. Már kiszámlázott elszámolás nem
    törölhető (a Billingó-bizonylat létezik). Törléskor a levont készlet
    visszakerül a partner külső raktárába (revert mozgással), így az
    elszámolás megismételhető."""
    deleted = 0
    blocked: list[dict] = []
    for raw in body.ids:
        try:
            sid = uuid.UUID(raw)
        except ValueError:
            continue
        settlement = (
            await db.execute(select(Settlement).where(Settlement.id == sid))
        ).scalar_one_or_none()
        if settlement is None:
            continue
        partner_name = (
            await db.execute(select(Partner.name).where(Partner.id == settlement.partner_id))
        ).scalar_one_or_none()
        label = f"{partner_name or '?'} ({settlement.created_at:%Y-%m-%d})"
        if settlement.invoiced:
            blocked.append({"id": str(sid), "name": label, "code": "settlement.invoiced"})
            continue

        lines = (
            (
                await db.execute(
                    select(SettlementLine).where(SettlementLine.settlement_id == sid)
                )
            )
            .scalars()
            .all()
        )
        # Készlet-visszaállítás: a fogyás visszakerül a partner raktárába.
        for line in lines:
            if line.product_id is None or line.consumed_qty <= 0:
                continue  # törölt termék fogyása nem állítható vissza
            stock = (
                await db.execute(
                    select(PartnerStock).where(
                        PartnerStock.partner_id == settlement.partner_id,
                        PartnerStock.product_id == line.product_id,
                    )
                )
            ).scalar_one_or_none()
            if stock is None:
                stock = PartnerStock(
                    partner_id=settlement.partner_id, product_id=line.product_id, quantity=0.0
                )
                db.add(stock)
            stock.quantity += line.consumed_qty
            db.add(
                StockMovement(
                    partner_id=settlement.partner_id, product_id=line.product_id,
                    action="revert", quantity_delta=line.consumed_qty,
                    note=f"elszámolás törölve ({label})", actor_user_id=actor.id,
                )
            )
        # az eredeti fogyás-mozgások megmaradnak, csak a hivatkozás oldódik
        await db.execute(
            sa_update(StockMovement)
            .where(StockMovement.settlement_id == sid)
            .values(settlement_id=None)
        )
        await db.execute(sa_delete(SettlementLine).where(SettlementLine.settlement_id == sid))
        await db.delete(settlement)
        deleted += 1
    await record_audit(
        db, actor=actor, action="settlement.bulk_delete", entity_type="settlement",
        detail={"deleted": deleted, "blocked": len(blocked)}, request=request,
    )
    await db.commit()
    return {"deleted": deleted, "blocked": blocked}


# ─── Üzletkötő-elszámolás (Phase 4) ──────────────────────────────────────────
# FONTOS: a statikus útvonalak (/agents, /summary) a /{settlement_id} ELŐTT
# legyenek, különben a path-paraméter elnyelné őket.


class AgentOut(BaseModel):
    user_id: str | None
    name: str
    count: int


@settlements_router.get("/agents", response_model=list[AgentOut])
async def settlement_agents(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("manager")),
):
    """Az elszámolást végzők (üzletkötők) listája — a szűrő legördülőhöz."""
    rows = (
        await db.execute(
            select(
                Settlement.settled_by_user_id,
                Settlement.settled_by_name,
                sa_func.count(),
            )
            .group_by(Settlement.settled_by_user_id, Settlement.settled_by_name)
            .order_by(Settlement.settled_by_name)
        )
    ).all()
    return [
        AgentOut(user_id=str(uid) if uid else None, name=name, count=int(cnt))
        for uid, name, cnt in rows
    ]


def _payment_filters(query, settled_by, date_from, date_to):
    if settled_by:
        try:
            query = query.where(Settlement.settled_by_user_id == uuid.UUID(settled_by))
        except ValueError:
            raise HTTPException(status_code=422, detail={"code": "settlement.bad_user"})
    if date_from:
        query = query.where(Settlement.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.where(Settlement.created_at <= datetime.combine(date_to, datetime.max.time()))
    return query


@settlements_router.get("/summary")
async def settlement_summary(
    settled_by: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("manager")),
):
    """Fizetési módonkénti összesítés (üzletkötő-elszámoláshoz): darab + nettó +
    bruttó, valamint a teljes összeg."""
    query = _payment_filters(select(Settlement), settled_by, date_from, date_to)
    settlements = (await db.execute(query)).scalars().all()

    by_payment = {m: {"count": 0, "net": 0.0, "gross": 0.0} for m in PAYMENT_METHODS}
    for s in settlements:
        bucket = by_payment.get(s.payment_method)
        if bucket is not None:
            bucket["count"] += 1
            bucket["net"] += s.total_net
            bucket["gross"] += s.total_gross

    for bucket in by_payment.values():
        bucket["net"] = _money(bucket["net"])
        bucket["gross"] = _money(bucket["gross"])

    return {
        "by_payment": by_payment,
        "total_net": _money(sum(s.total_net for s in settlements)),
        "total_gross": _money(sum(s.total_gross for s in settlements)),
        "count": len(settlements),
    }


@settlements_router.get("/{settlement_id}", response_model=SettlementOut)
async def get_settlement(
    settlement_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("manager")),
):
    s = await _get_settlement_or_404(db, settlement_id)
    partner = (
        await db.execute(select(Partner.name).where(Partner.id == s.partner_id))
    ).scalar_one_or_none()
    out = _settlement_out(s, partner)
    out.lines = [
        _line_out(line)
        for line in (
            await db.execute(
                select(SettlementLine).where(SettlementLine.settlement_id == s.id)
            )
        ).scalars()
    ]
    return out


@settlements_router.post("/{settlement_id}/invoice", response_model=SettlementOut)
async def invoice_settlement(
    settlement_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    """„Kiszámlázott” gomb: a fogyás kiszámlázása a Billingón keresztül.
    Teszt-módban díjbekérő (proforma) készül, éles módban számla."""
    from app.services.wfm.billingo_service import create_invoice_for_settlement

    s = await _get_settlement_or_404(db, settlement_id)
    if s.invoiced:
        raise HTTPException(status_code=409, detail={"code": "settlement.already_invoiced"})
    partner = (
        await db.execute(select(Partner).where(Partner.id == s.partner_id))
    ).scalar_one_or_none()
    if partner is None:
        raise HTTPException(status_code=404, detail={"code": "partner.not_found"})

    try:
        document_id, mode = await create_invoice_for_settlement(db, s, partner)
    except ValueError as exc:
        code = str(exc)
        if code == "billingo_not_configured":
            raise HTTPException(status_code=422, detail={"code": "settings.billingo_not_configured"})
        if code == "billingo_no_items":
            raise HTTPException(status_code=422, detail={"code": "settlement.no_items"})
        raise HTTPException(status_code=502, detail={"code": "settlement.invoice_failed"})
    except Exception:
        s.billingo_status = "error"
        await db.commit()
        raise HTTPException(status_code=502, detail={"code": "settlement.invoice_failed"})

    s.invoiced = True
    s.billingo_document_id = document_id
    s.billingo_status = mode  # 'proforma' (teszt) | 'invoice' (éles)
    await record_audit(
        db, actor=actor, action="settlement.invoice", entity_type="settlement",
        entity_id=str(s.id), detail={"billingo_id": document_id, "mode": mode}, request=request,
    )
    await db.commit()
    return _settlement_out(s, partner.name)
