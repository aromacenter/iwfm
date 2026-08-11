"""Bizományosi kávé-raktár: termékek + partnerenkénti külső raktárkészlet.

A partner a "külső raktár": ide töltünk fel terméket (pl. kávét) kg-ban. A
termék gramm/adag beállításából a rendszer kiszámolja, hány adag készíthető.
Az elszámolás (settlements.py-ben) a fizikai leltár alapján számolja a fogyást
és adag × ár/adag alapon számláz.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete as sa_delete, func as sa_func, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.db import get_db
from app.models import (
    Partner,
    PartnerPrice,
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
    low_stock_threshold: float | None = Field(default=None, ge=0)
    is_active: bool = True
    notes: str | None = None


class ProductOut(BaseModel):
    id: str
    name: str
    unit: str
    grams_per_portion: int
    price_per_portion: float
    vat_percent: int
    low_stock_threshold: float | None
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
        low_stock_threshold=p.low_stock_threshold,
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


class LowStockOut(BaseModel):
    partner_id: str
    partner_name: str
    product_id: str
    product_name: str
    unit: str
    quantity: float
    threshold: float


@products_router.get("/low-stock", response_model=list[LowStockOut])
async def low_stock(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    """Küszöb alatti partner-készletek: hol fogy ki hamarosan a termék.
    A legkisebb (küszöbhöz mért) készletek elöl."""
    rows = (
        await db.execute(
            select(PartnerStock, Product, Partner.name)
            .join(Product, Product.id == PartnerStock.product_id)
            .join(Partner, Partner.id == PartnerStock.partner_id)
            .where(
                Product.low_stock_threshold.is_not(None),
                Product.is_active.is_(True),
                Partner.is_active.is_(True),
                PartnerStock.quantity <= Product.low_stock_threshold,
            )
        )
    ).all()
    out = [
        LowStockOut(
            partner_id=str(stock.partner_id),
            partner_name=partner_name,
            product_id=str(product.id),
            product_name=product.name,
            unit=product.unit,
            quantity=stock.quantity,
            threshold=product.low_stock_threshold or 0.0,
        )
        for stock, product, partner_name in rows
    ]
    out.sort(key=lambda r: r.quantity - r.threshold)
    return out


@products_router.get("", response_model=list[ProductOut])
async def list_products(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("products")),
):
    rows = (await db.execute(select(Product).order_by(Product.name))).scalars().all()
    return [_product_out(p) for p in rows]


@products_router.post("", response_model=ProductOut, status_code=201)
async def create_product(
    body: ProductBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("products")),
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
    actor: User = Depends(require_perm("products")),
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
    actor: User = Depends(require_perm("delete")),
):
    """Termékek (tömeges) törlése. A kint lévő partner-készlet nyilvántartása
    és a mozgástörténet is törlődik a termékkel együtt (a törlés-megerősítő
    figyelmeztet erre); az elszámolás-tételek pillanatkép-nevet őriznek, így a
    korábbi elszámolások olvashatók maradnak."""
    from app.models import SettlementLine

    deleted = 0
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
        # függő rekordok explicit takarítása (SQLite-on nincs FK-cascade garancia)
        await db.execute(sa_delete(PartnerStock).where(PartnerStock.product_id == pid))
        await db.execute(sa_delete(StockMovement).where(StockMovement.product_id == pid))
        await db.execute(sa_delete(PartnerPrice).where(PartnerPrice.product_id == pid))
        await db.execute(
            sa_update(SettlementLine).where(SettlementLine.product_id == pid).values(product_id=None)
        )
        await db.delete(product)
        deleted += 1
    await record_audit(
        db, actor=actor, action="product.bulk_delete", entity_type="product",
        detail={"deleted": deleted}, request=request,
    )
    await db.commit()
    return {"deleted": deleted, "blocked": []}


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
    price_per_portion: float  # érvényes ár (partner-felülírás, ha van)
    base_price_per_portion: float  # a termék alapára
    has_price_override: bool = False
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


async def _partner_price_map(db: AsyncSession, partner_id: uuid.UUID) -> dict[uuid.UUID, float]:
    """{product_id: felülírt ár} a partnerhez."""
    return {
        pid: price
        for pid, price in (
            await db.execute(
                select(PartnerPrice.product_id, PartnerPrice.price_per_portion).where(
                    PartnerPrice.partner_id == partner_id
                )
            )
        ).all()
    }


def _effective_price(product: Product, overrides: dict[uuid.UUID, float]) -> float:
    return overrides.get(product.id, product.price_per_portion)


def _stock_out(
    stock: PartnerStock, product: Product, overrides: dict[uuid.UUID, float] | None = None
) -> StockOut:
    overrides = overrides or {}
    return StockOut(
        partner_id=str(stock.partner_id),
        product_id=str(product.id),
        product_name=product.name,
        unit=product.unit,
        quantity=stock.quantity,
        grams_per_portion=product.grams_per_portion,
        price_per_portion=_effective_price(product, overrides),
        base_price_per_portion=product.price_per_portion,
        has_price_override=product.id in overrides,
        portions_available=int(math.floor(portions_from(stock.quantity, product.grams_per_portion))),
    )


@stock_router.get("/{partner_id}/stock", response_model=list[StockOut])
async def list_partner_stock(
    partner_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    partner = await _get_partner_or_404(db, partner_id)
    rows = (
        await db.execute(
            select(PartnerStock, Product)
            .join(Product, Product.id == PartnerStock.product_id)
            .where(PartnerStock.partner_id == uuid.UUID(partner_id))
            .order_by(Product.name)
        )
    ).all()
    overrides = await _partner_price_map(db, partner.id)
    return [_stock_out(stock, product, overrides) for stock, product in rows]


@stock_router.post("/{partner_id}/stock/replenish", response_model=StockOut)
async def replenish_partner_stock(
    partner_id: str,
    body: ReplenishBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
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
    return _stock_out(stock, product, await _partner_price_map(db, partner.id))


# ─── Partner-specifikus árak ─────────────────────────────────────────────────


class PartnerPriceOut(BaseModel):
    product_id: str
    product_name: str
    base_price_per_portion: float
    price_per_portion: float | None  # None = nincs felülírás (alapár érvényes)


class PartnerPriceBody(BaseModel):
    price_per_portion: float = Field(ge=0)


@stock_router.get("/{partner_id}/prices", response_model=list[PartnerPriceOut])
async def list_partner_prices(
    partner_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("products")),
):
    """Minden aktív termék a partner-felülírással (ha van)."""
    partner = await _get_partner_or_404(db, partner_id)
    overrides = await _partner_price_map(db, partner.id)
    products = (
        await db.execute(select(Product).where(Product.is_active.is_(True)).order_by(Product.name))
    ).scalars().all()
    return [
        PartnerPriceOut(
            product_id=str(p.id),
            product_name=p.name,
            base_price_per_portion=p.price_per_portion,
            price_per_portion=overrides.get(p.id),
        )
        for p in products
    ]


@stock_router.put("/{partner_id}/prices/{product_id}", response_model=PartnerPriceOut)
async def set_partner_price(
    partner_id: str,
    product_id: str,
    body: PartnerPriceBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("products")),
):
    partner = await _get_partner_or_404(db, partner_id)
    product = await _get_product_or_404(db, product_id)
    row = (
        await db.execute(
            select(PartnerPrice).where(
                PartnerPrice.partner_id == partner.id, PartnerPrice.product_id == product.id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = PartnerPrice(
            partner_id=partner.id, product_id=product.id,
            price_per_portion=body.price_per_portion,
        )
        db.add(row)
    else:
        row.price_per_portion = body.price_per_portion
    await record_audit(
        db, actor=actor, action="partner_price.set", entity_type="partner_price",
        entity_id=str(partner.id),
        detail={"product": product.name, "price": body.price_per_portion}, request=request,
    )
    await db.commit()
    return PartnerPriceOut(
        product_id=str(product.id),
        product_name=product.name,
        base_price_per_portion=product.price_per_portion,
        price_per_portion=row.price_per_portion,
    )


@stock_router.delete("/{partner_id}/prices/{product_id}", response_model=PartnerPriceOut)
async def clear_partner_price(
    partner_id: str,
    product_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("products")),
):
    """A felülírás törlése — visszaáll a termék alapára."""
    partner = await _get_partner_or_404(db, partner_id)
    product = await _get_product_or_404(db, product_id)
    await db.execute(
        sa_delete(PartnerPrice).where(
            PartnerPrice.partner_id == partner.id, PartnerPrice.product_id == product.id
        )
    )
    await record_audit(
        db, actor=actor, action="partner_price.clear", entity_type="partner_price",
        entity_id=str(partner.id), detail={"product": product.name}, request=request,
    )
    await db.commit()
    return PartnerPriceOut(
        product_id=str(product.id),
        product_name=product.name,
        base_price_per_portion=product.price_per_portion,
        price_per_portion=None,
    )


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
    invoicing_company: str | None = None  # xp | pc
    payment_method: str
    total_net: float
    total_gross: float
    invoiced: bool
    billingo_document_id: str | None
    billingo_status: str | None
    has_signature: bool = False
    receipt_sent_at: datetime | None = None
    payment_status: str = "none"
    due_date: date | None = None
    paid_at: datetime | None = None
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
        invoicing_company=s.invoicing_company,
        payment_method=s.payment_method,
        total_net=s.total_net,
        total_gross=s.total_gross,
        invoiced=s.invoiced,
        billingo_document_id=s.billingo_document_id,
        billingo_status=s.billingo_status,
        has_signature=bool(s.partner_signature),
        receipt_sent_at=s.receipt_sent_at,
        payment_status=s.payment_status,
        due_date=s.due_date,
        paid_at=s.paid_at,
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
    actor: User = Depends(require_perm("settlements")),
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
        invoicing_company=partner.invoicing_company,
        payment_method=body.payment_method,
        total_net=0.0,
        total_gross=0.0,
        note=body.note,
    )
    db.add(settlement)
    await db.flush()

    price_overrides = await _partner_price_map(db, partner.id)
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
        unit_price = _effective_price(product, price_overrides)
        amount_net = _money(portions * unit_price)
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
                price_per_portion=unit_price,
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
    company: str | None = Query(default=None),  # xp | pc — cégenkénti bontás
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    query = (
        select(Settlement, Partner.name)
        .join(Partner, Partner.id == Settlement.partner_id)
        .order_by(Settlement.created_at.desc())
    )
    if company:
        if company not in ("xp", "pc"):
            raise HTTPException(status_code=422, detail={"code": "settlement.bad_company"})
        query = query.where(Settlement.invoicing_company == company)
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
    actor: User = Depends(require_perm("delete")),
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
    _: User = Depends(require_perm("agent_report")),
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
    company: str | None = Query(default=None),  # xp | pc — cégenkénti bontás
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("agent_report")),
):
    """Fizetési módonkénti összesítés (üzletkötő-elszámoláshoz): darab + nettó +
    bruttó, a teljes összeg, valamint számlázó cégenkénti bontás."""
    query = _payment_filters(select(Settlement), settled_by, date_from, date_to)
    if company:
        if company not in ("xp", "pc"):
            raise HTTPException(status_code=422, detail={"code": "settlement.bad_company"})
        query = query.where(Settlement.invoicing_company == company)
    settlements = (await db.execute(query)).scalars().all()

    by_payment = {m: {"count": 0, "net": 0.0, "gross": 0.0} for m in PAYMENT_METHODS}
    by_company = {c: {"count": 0, "net": 0.0, "gross": 0.0} for c in ("xp", "pc", "none")}
    for s in settlements:
        bucket = by_payment.get(s.payment_method)
        if bucket is not None:
            bucket["count"] += 1
            bucket["net"] += s.total_net
            bucket["gross"] += s.total_gross
        cbucket = by_company[s.invoicing_company if s.invoicing_company in ("xp", "pc") else "none"]
        cbucket["count"] += 1
        cbucket["net"] += s.total_net
        cbucket["gross"] += s.total_gross

    for bucket in (*by_payment.values(), *by_company.values()):
        bucket["net"] = _money(bucket["net"])
        bucket["gross"] = _money(bucket["gross"])

    return {
        "by_payment": by_payment,
        "by_company": by_company,
        "total_net": _money(sum(s.total_net for s in settlements)),
        "total_gross": _money(sum(s.total_gross for s in settlements)),
        "count": len(settlements),
    }


class DuePartnerOut(BaseModel):
    partner_id: str
    partner_code: str | None
    name: str
    contact_phone: str | None
    address: str | None = None  # útvonal-tervezéshez
    city: str | None = None  # földrajzi csoportosításhoz
    last_settlement_at: datetime | None  # None = még sosem volt elszámolva
    days_since: int | None
    stock_products: int  # hány termékből van kint készlete
    # Fogyás-előrejelzés az elszámolás-előzményekből (utolsó 180 nap):
    avg_daily_kg: float | None = None  # átlagos napi fogyás (kg)
    days_left: int | None = None  # várhatóan ennyi nap múlva fogy ki
    suggested_kg: float | None = None  # javasolt vinnivaló (30 napra)


@settlements_router.get("/due", response_model=list[DuePartnerOut])
async def due_settlements(
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    """Esedékes elszámolások (látogatási terv): azok az aktív partnerek, akiknél
    van kint készlet vagy volt már elszámolás, és az utolsó elszámolásuk N+
    napja történt (vagy még sosem) VAGY a fogyás-előrejelzés szerint 7 napon
    belül kifogynak. A legrégebben elszámoltak elöl."""
    last_at = {
        pid: at
        for pid, at in (
            await db.execute(
                select(Settlement.partner_id, sa_func.max(Settlement.created_at))
                .group_by(Settlement.partner_id)
            )
        ).all()
    }
    stock_counts = {
        pid: int(cnt)
        for pid, cnt in (
            await db.execute(
                select(PartnerStock.partner_id, sa_func.count())
                .where(PartnerStock.quantity > 0)
                .group_by(PartnerStock.partner_id)
            )
        ).all()
    }
    stock_kg = {
        pid: float(total or 0)
        for pid, total in (
            await db.execute(
                select(PartnerStock.partner_id, sa_func.sum(PartnerStock.quantity))
                .group_by(PartnerStock.partner_id)
            )
        ).all()
    }

    # Fogyás az utolsó 180 nap elszámolásaiból: összes fogyás (kg) + időablak.
    now = datetime.now(UTC)
    window_start = now - timedelta(days=180)
    consumption = {
        pid: (float(kg or 0), first, last)
        for pid, kg, first, last in (
            await db.execute(
                select(
                    Settlement.partner_id,
                    sa_func.sum(SettlementLine.consumed_qty),
                    sa_func.min(Settlement.created_at),
                    sa_func.max(Settlement.created_at),
                )
                .join(SettlementLine, SettlementLine.settlement_id == Settlement.id)
                .where(Settlement.created_at >= window_start)
                .group_by(Settlement.partner_id)
            )
        ).all()
    }

    def _aware(dt: datetime | None) -> datetime | None:
        if dt is not None and dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)  # SQLite naiv datetime-ot ad vissza
        return dt

    relevant = set(last_at) | set(stock_counts)
    if not relevant:
        return []
    partners = (
        await db.execute(
            select(Partner).where(Partner.id.in_(relevant), Partner.is_active.is_(True))
        )
    ).scalars().all()

    out: list[DuePartnerOut] = []
    for p in partners:
        last = _aware(last_at.get(p.id))
        since = (now - last).days if last is not None else None

        avg_daily = days_left = suggested = None
        cons = consumption.get(p.id)
        if cons and cons[0] > 0:
            kg, first, clast = cons[0], _aware(cons[1]), _aware(cons[2])
            span = max((clast - first).days if first and clast else 0, 14)
            avg_daily = round(kg / span, 3)
            if avg_daily > 0:
                remaining = stock_kg.get(p.id, 0.0)
                days_left = max(int(remaining / avg_daily), 0)
                suggested = round(max(avg_daily * 30 - remaining, 0), 1)

        runs_out_soon = days_left is not None and days_left <= 7
        if since is not None and since < days and not runs_out_soon:
            continue
        out.append(
            DuePartnerOut(
                partner_id=str(p.id),
                partner_code=p.partner_code,
                name=p.name,
                contact_phone=p.contact_phone,
                address=p.address,
                city=p.address_city,
                last_settlement_at=last,
                days_since=since,
                stock_products=stock_counts.get(p.id, 0),
                avg_daily_kg=avg_daily,
                days_left=days_left,
                suggested_kg=suggested,
            )
        )
    # hamarosan kifogyók legelöl, aztán a sosem elszámoltak, majd a legrégebbiek
    out.sort(
        key=lambda d: (
            d.days_left if d.days_left is not None and d.days_left <= 7 else 999,
            d.days_since is not None,
            -(d.days_since or 0),
        )
    )
    return out


class ReceivableOut(BaseModel):
    id: str
    partner_id: str
    partner_name: str | None
    payment_method: str
    total_gross: float
    billingo_document_id: str | None
    billingo_status: str | None
    due_date: date | None
    days_overdue: int  # 0, ha még nem járt le
    created_at: datetime


@settlements_router.get("/receivables", response_model=list[ReceivableOut])
async def list_receivables(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    """Kintlévőségek: kiszámlázott, de még nem fizetett elszámolások.
    A lejártak elöl, a legrégebb óta lejárt legfelül."""
    rows = (
        await db.execute(
            select(Settlement, Partner.name)
            .join(Partner, Partner.id == Settlement.partner_id)
            .where(Settlement.invoiced.is_(True), Settlement.payment_status == "outstanding")
            .order_by(Settlement.due_date.asc().nulls_last())
        )
    ).all()
    today = date.today()
    out = []
    for s, partner_name in rows:
        overdue = (today - s.due_date).days if s.due_date and s.due_date < today else 0
        out.append(
            ReceivableOut(
                id=str(s.id),
                partner_id=str(s.partner_id),
                partner_name=partner_name,
                payment_method=s.payment_method,
                total_gross=s.total_gross,
                billingo_document_id=s.billingo_document_id,
                billingo_status=s.billingo_status,
                due_date=s.due_date,
                days_overdue=overdue,
                created_at=s.created_at,
            )
        )
    out.sort(key=lambda r: -r.days_overdue)
    return out


@settlements_router.post("/{settlement_id}/mark-paid", response_model=SettlementOut)
async def mark_settlement_paid(
    settlement_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("invoicing")),
):
    """Kézi „fizetve” jelölés (pl. beérkezett utalás alapján)."""
    s = await _get_settlement_or_404(db, settlement_id)
    if not s.invoiced:
        raise HTTPException(status_code=422, detail={"code": "settlement.not_invoiced"})
    if s.payment_status == "paid":
        raise HTTPException(status_code=409, detail={"code": "settlement.already_paid"})
    s.payment_status = "paid"
    s.paid_at = datetime.now(UTC)
    partner_name = (
        await db.execute(select(Partner.name).where(Partner.id == s.partner_id))
    ).scalar_one_or_none()
    await record_audit(
        db, actor=actor, action="settlement.mark_paid", entity_type="settlement",
        entity_id=str(s.id), request=request,
    )
    await db.commit()
    return _settlement_out(s, partner_name)


@settlements_router.post("/{settlement_id}/sync-payment")
async def sync_settlement_payment(
    settlement_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("invoicing")),
):
    """Fizetési státusz frissítése a Billingóból. Ha ott már fizetve, itt is
    fizetettre áll."""
    from app.services.wfm.billingo_service import fetch_payment_status

    s = await _get_settlement_or_404(db, settlement_id)
    if not s.invoiced or not s.billingo_document_id:
        raise HTTPException(status_code=422, detail={"code": "settlement.not_invoiced"})
    try:
        status = await fetch_payment_status(db, s.billingo_document_id, s.invoicing_company)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "settings.billingo_not_configured"})
    except Exception:
        raise HTTPException(status_code=502, detail={"code": "settlement.sync_failed"})

    changed = False
    if status == "paid" and s.payment_status != "paid":
        s.payment_status = "paid"
        s.paid_at = datetime.now(UTC)
        changed = True
    if changed:
        await record_audit(
            db, actor=actor, action="settlement.payment_synced", entity_type="settlement",
            entity_id=str(s.id), detail={"billingo_status": status}, request=request,
        )
        await db.commit()
    return {"billingo_payment_status": status, "payment_status": s.payment_status}


@settlements_router.get("/{settlement_id}", response_model=SettlementOut)
async def get_settlement(
    settlement_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
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
    actor: User = Depends(require_perm("invoicing")),
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
        document_id, mode, due = await create_invoice_for_settlement(db, s, partner)
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
    # Kintlévőség: készpénz/kártya azonnal fizetve; utalásnál határidő fut.
    if s.payment_method in ("cash", "card"):
        s.payment_status = "paid"
        s.paid_at = datetime.now(UTC)
    else:
        s.payment_status = "outstanding"
        s.due_date = due
    await record_audit(
        db, actor=actor, action="settlement.invoice", entity_type="settlement",
        entity_id=str(s.id), detail={"billingo_id": document_id, "mode": mode}, request=request,
    )
    await db.commit()
    return _settlement_out(s, partner.name)


# ─── Bizonylat: PDF + aláírás + email ────────────────────────────────────────


def _receipt_no(s: Settlement) -> str:
    return f"ELSZ-{s.created_at:%Y%m%d}-{str(s.id)[:8].upper()}"


async def _build_settlement_pdf(db: AsyncSession, s: Settlement) -> tuple[bytes, str]:
    """A bizonylat PDF-je — (pdf_bytes, bizonylatszám)."""
    from app.models import WorksheetSettings
    from app.services.wfm.settlement_pdf import build_settlement_pdf

    partner = (
        await db.execute(select(Partner).where(Partner.id == s.partner_id))
    ).scalar_one_or_none()
    lines = (
        await db.execute(select(SettlementLine).where(SettlementLine.settlement_id == s.id))
    ).scalars().all()

    branding = (
        await db.execute(select(WorksheetSettings).where(WorksheetSettings.id == 1))
    ).scalar_one_or_none()
    settings = None
    if branding is not None:
        settings = {
            "company_name": branding.company_name,
            "company_address": branding.company_address,
            "footer_text": branding.footer_text,
            "accent_color": branding.accent_color,
            "logo_bytes": bytes(branding.logo_data) if branding.logo_data else None,
        }

    receipt_no = _receipt_no(s)
    pdf = build_settlement_pdf(
        {
            "receipt_no": receipt_no,
            "created_at": f"{s.created_at:%Y-%m-%d %H:%M}",
            "partner_name": partner.name if partner else "—",
            "partner_code": partner.partner_code if partner else None,
            "partner_address": partner.address if partner else None,
            "partner_tax_number": partner.tax_number if partner else None,
            "settled_by_name": s.settled_by_name,
            "payment_method": s.payment_method,
            "invoiced": s.invoiced,
            "billingo_document_id": s.billingo_document_id,
            "note": s.note,
            "lines": [
                {
                    "product_name": line.product_name,
                    "previous_qty": line.previous_qty,
                    "physical_qty": line.physical_qty,
                    "consumed_qty": line.consumed_qty,
                    "portions": line.portions,
                    "price_per_portion": line.price_per_portion,
                    "amount_net": line.amount_net,
                    "amount_gross": _money(line.amount_net * (1 + line.vat_percent / 100)),
                }
                for line in lines
            ],
            "total_net": s.total_net,
            "total_gross": s.total_gross,
            "partner_signature": s.partner_signature,
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
        },
        settings,
    )
    return pdf, receipt_no


@settlements_router.get("/{settlement_id}/pdf")
async def settlement_pdf(
    settlement_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    s = await _get_settlement_or_404(db, settlement_id)
    pdf, receipt_no = await _build_settlement_pdf(db, s)
    await record_audit(
        db, actor=actor, action="settlement.pdf", entity_type="settlement",
        entity_id=str(s.id), request=request,
    )
    await db.commit()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{receipt_no}.pdf"'},
    )


class SignatureBody(BaseModel):
    signature: str = Field(min_length=30, max_length=500_000)
    # Opcionális e-mail-gyűjtés aláíráskor: csak akkor mentjük a partnerre,
    # ha még nincs e-mail címe (kézi értéket nem írunk felül).
    partner_email: EmailStr | None = None


@settlements_router.post("/{settlement_id}/signature", response_model=SettlementOut)
async def sign_settlement(
    settlement_id: str,
    body: SignatureBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """A partner képernyős aláírásának rögzítése a bizonylathoz."""
    if not body.signature.startswith("data:image/png;base64,"):
        raise HTTPException(status_code=422, detail={"code": "settlement.bad_signature"})
    s = await _get_settlement_or_404(db, settlement_id)
    s.partner_signature = body.signature
    partner = (
        await db.execute(select(Partner).where(Partner.id == s.partner_id))
    ).scalar_one_or_none()
    if body.partner_email and partner is not None and not partner.contact_email:
        partner.contact_email = str(body.partner_email)
        await record_audit(
            db, actor=actor, action="partner.email_collected", entity_type="partner",
            entity_id=str(partner.id), request=request,
        )
    await record_audit(
        db, actor=actor, action="settlement.sign", entity_type="settlement",
        entity_id=str(s.id), request=request,
    )
    await db.commit()
    return _settlement_out(s, partner.name if partner else None)


class ReceiptEmailBody(BaseModel):
    to: EmailStr | None = None  # None → a partner kapcsolattartói email-címe


@settlements_router.post("/{settlement_id}/receipt-email", response_model=SettlementOut)
async def email_settlement_receipt(
    settlement_id: str,
    body: ReceiptEmailBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """A bizonylat PDF elküldése emailben a partnernek (vagy megadott címre)."""
    from app.services.wfm.email_service import load_smtp_config, send_email

    s = await _get_settlement_or_404(db, settlement_id)
    partner = (
        await db.execute(select(Partner).where(Partner.id == s.partner_id))
    ).scalar_one_or_none()
    to = body.to or (partner.contact_email if partner else None)
    if not to:
        raise HTTPException(status_code=422, detail={"code": "settlement.no_email"})

    smtp = await load_smtp_config(db)
    if smtp is None:
        raise HTTPException(status_code=422, detail={"code": "settings.email_not_configured"})

    pdf, receipt_no = await _build_settlement_pdf(db, s)
    ok = await send_email(
        smtp,
        to,
        f"Elszámolási bizonylat — {receipt_no}",
        (
            f"Tisztelt Partnerünk!\n\nMellékelve küldjük a(z) {receipt_no} számú "
            f"elszámolási bizonylatot.\n\nÜdvözlettel:\n{s.settled_by_name}"
        ),
        attachments=[(f"{receipt_no}.pdf", pdf, "application", "pdf")],
    )
    if not ok:
        raise HTTPException(status_code=502, detail={"code": "settings.email_send_failed"})

    s.receipt_sent_at = datetime.now(UTC)
    await record_audit(
        db, actor=actor, action="settlement.receipt_email", entity_type="settlement",
        entity_id=str(s.id), detail={"to": to}, request=request,
    )
    await db.commit()
    return _settlement_out(s, partner.name if partner else None)
