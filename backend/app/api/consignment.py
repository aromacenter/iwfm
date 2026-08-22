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

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import (
    DateTime,
    delete as sa_delete,
    func as sa_func,
    select,
    type_coerce,
    update as sa_update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.db import get_db
from app.models import (
    Asset,
    AssetMovement,
    Partner,
    PartnerPrice,
    PartnerStock,
    Product,
    Settlement,
    SettlementLine,
    SettlementMachine,
    StockMovement,
    User,
)

products_router = APIRouter()  # /api/products
stock_router = APIRouter()  # /api/partners (kiegészíti az inventory partner-útvonalait)
settlements_router = APIRouter()  # /api/settlements

PAYMENT_METHODS = ("cash", "card", "transfer", "cod")


# ─── Termékek ────────────────────────────────────────────────────────────────


class ProductBody(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    # Csoport (pl. Kávék, Kávégépek, Alkatrészek, Kellékek) — szabad szöveg.
    category: str | None = Field(default=None, max_length=64)
    unit: str = Field(default="kg", max_length=16)
    grams_per_portion: int = Field(default=7, ge=1, le=100000)
    price_per_portion: float = Field(default=0.0, ge=0)
    vat_percent: int = Field(default=27, ge=0, le=100)
    low_stock_threshold: float | None = Field(default=None, ge=0)
    purchase_price: float | None = Field(default=None, ge=0)  # Ft/kg (nettó)
    is_active: bool = True
    notes: str | None = None


class ProductOut(BaseModel):
    id: str
    name: str
    category: str | None
    unit: str
    grams_per_portion: int
    price_per_portion: float
    vat_percent: int
    low_stock_threshold: float | None
    purchase_price: float | None
    is_active: bool
    notes: str | None


def _product_out(p: Product) -> ProductOut:
    return ProductOut(
        id=str(p.id),
        name=p.name,
        category=p.category,
        unit=p.unit,
        grams_per_portion=p.grams_per_portion,
        price_per_portion=p.price_per_portion,
        vat_percent=p.vat_percent,
        low_stock_threshold=p.low_stock_threshold,
        purchase_price=p.purchase_price,
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
    # Küszöb: partnerenkénti felülírás (partner_stock.min_quantity), ha nincs,
    # a termék globális low_stock_threshold-ja.
    threshold = sa_func.coalesce(PartnerStock.min_quantity, Product.low_stock_threshold)
    rows = (
        await db.execute(
            select(PartnerStock, Product, Partner.name)
            .join(Product, Product.id == PartnerStock.product_id)
            .join(Partner, Partner.id == PartnerStock.partner_id)
            .where(
                threshold.is_not(None),
                Product.is_active.is_(True),
                Partner.is_active.is_(True),
                PartnerStock.quantity <= threshold,
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
            threshold=(
                stock.min_quantity
                if stock.min_quantity is not None
                else (product.low_stock_threshold or 0.0)
            ),
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
    min_quantity: float | None = None  # partnerenkénti riasztási küszöb (kg)


class ReplenishBody(BaseModel):
    product_id: str
    quantity: float = Field(gt=0)  # feltöltendő mennyiség (kg)
    unit_cost: float | None = Field(default=None, ge=0)  # beszerzési ár (Ft/kg, nettó)
    note: str | None = Field(default=None, max_length=512)
    # Melyik saját raktárból (telephely/autó) megy ki az áru — ha meg van
    # adva, a raktárkészlet csökken (elégtelen készletnél 422).
    source_warehouse_id: str | None = None


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
        min_quantity=stock.min_quantity,
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
    """Termék feltöltése a partner külső raktárába (kg-ban). Ha forrás-raktár
    van megadva, az áru onnan kerül kiadásra (raktárkészlet-csökkenéssel)."""
    partner = await _get_partner_or_404(db, partner_id)
    product = await _get_product_or_404(db, body.product_id)

    if body.source_warehouse_id:
        from app.api.warehouse import _get_warehouse_or_404, warehouse_issue

        src = await _get_warehouse_or_404(db, body.source_warehouse_id)
        await warehouse_issue(
            db, warehouse_id=src.id, product=product, quantity=body.quantity,
            partner_id=partner.id, actor_id=actor.id,
        )

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
            quantity_delta=body.quantity, unit_cost=body.unit_cost,
            note=body.note, actor_user_id=actor.id,
        )
    )
    if body.unit_cost is not None:
        product.purchase_price = body.unit_cost  # utolsó beszerzési ár
    await record_audit(
        db, actor=actor, action="stock.replenish", entity_type="partner_stock",
        entity_id=str(partner.id),
        detail={"product": product.name, "qty": body.quantity, "unit_cost": body.unit_cost},
        request=request,
    )
    await db.commit()
    return _stock_out(stock, product, await _partner_price_map(db, partner.id))


@stock_router.delete("/{partner_id}/stock/{product_id}")
async def delete_partner_stock(
    partner_id: str,
    product_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Készlet-sor törlése a partner külső raktárából (pl. tévesen felvett
    termék). A könyv szerinti mennyiség kivezetésre kerül (remove mozgás)."""
    partner = await _get_partner_or_404(db, partner_id)
    product = await _get_product_or_404(db, product_id)
    stock = (
        await db.execute(
            select(PartnerStock).where(
                PartnerStock.partner_id == partner.id,
                PartnerStock.product_id == product.id,
            )
        )
    ).scalar_one_or_none()
    if stock is None:
        raise HTTPException(status_code=404, detail={"code": "stock.not_found"})
    qty = stock.quantity
    if qty:
        db.add(StockMovement(
            partner_id=partner.id, product_id=product.id, action="remove",
            quantity_delta=-qty, note="Készlet-sor törölve",
            actor_user_id=actor.id,
        ))
    await db.delete(stock)
    await record_audit(
        db, actor=actor, action="stock.delete", entity_type="partner_stock",
        entity_id=str(partner.id),
        detail={"product": product.name, "qty": qty}, request=request,
    )
    await db.commit()
    return {"ok": True}


class StockReturnItem(BaseModel):
    product_id: str
    quantity: float = Field(gt=0, le=100_000)


class StockReturnBody(BaseModel):
    target_warehouse_id: str  # a képviselő autója / raktár
    items: list[StockReturnItem] | None = None
    all: bool = False  # True: a partner TELJES készletének visszavétele
    note: str | None = Field(default=None, max_length=512)


@stock_router.post("/{partner_id}/stock/return")
async def return_partner_stock(
    partner_id: str,
    body: StockReturnBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Ki nem fizetett termékek visszavétele a partnertől a képviselő
    raktárába (autójába) — szerződés-lezáráskor a teljes készlet (all=true),
    egyébként (pl. kávéváltás) kiválasztott termékek/mennyiségek.

    A partner-készlet csökken (return mozgás), a cél-raktár készlete nő
    (receive mozgás, ellenoldal: a partner) — a lánc követhető marad."""
    from app.api.warehouse import _get_warehouse_or_404, warehouse_receive_return

    partner = await _get_partner_or_404(db, partner_id)
    warehouse = await _get_warehouse_or_404(db, body.target_warehouse_id)

    stock_rows = (
        await db.execute(
            select(PartnerStock, Product)
            .join(Product, Product.id == PartnerStock.product_id)
            .where(PartnerStock.partner_id == partner.id)
        )
    ).all()
    by_pid = {product.id: (stock, product) for stock, product in stock_rows}

    worklist: list[tuple[PartnerStock, Product, float]] = []
    if body.all:
        for stock, product in stock_rows:
            if stock.quantity > 1e-9:
                worklist.append((stock, product, stock.quantity))
    else:
        if not body.items:
            raise HTTPException(status_code=422, detail={"code": "stock.return_empty"})
        for item in body.items:
            try:
                pid = uuid.UUID(item.product_id)
            except ValueError:
                raise HTTPException(status_code=404, detail={"code": "product.not_found"})
            if pid not in by_pid:
                raise HTTPException(status_code=404, detail={"code": "stock.not_found"})
            stock, product = by_pid[pid]
            if item.quantity > stock.quantity + 1e-9:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "stock.return_too_much",
                            "product": product.name, "available": stock.quantity},
                )
            worklist.append((stock, product, min(item.quantity, stock.quantity)))

    if not worklist:
        raise HTTPException(status_code=422, detail={"code": "stock.return_empty"})

    returned = []
    for stock, product, qty in worklist:
        stock.quantity -= qty
        db.add(StockMovement(
            partner_id=partner.id, product_id=product.id, action="return",
            quantity_delta=-qty,
            note=body.note or f"Visszavét → {warehouse.name}",
            actor_user_id=actor.id,
        ))
        await warehouse_receive_return(
            db, warehouse_id=warehouse.id, product=product, quantity=qty,
            partner_id=partner.id, actor_id=actor.id,
        )
        returned.append({"product_name": product.name, "quantity": round(qty, 2),
                         "unit": product.unit})

    await record_audit(
        db, actor=actor, action="stock.return", entity_type="partner_stock",
        entity_id=str(partner.id),
        detail={"warehouse": warehouse.name, "all": body.all,
                "items": [{"p": r["product_name"], "q": r["quantity"]} for r in returned]},
        request=request,
    )
    await db.commit()
    return {"ok": True, "returned": returned, "warehouse_name": warehouse.name}


class StockMinBody(BaseModel):
    product_id: str
    min_quantity: float | None = Field(default=None, ge=0)  # None = globális küszöb


@stock_router.patch("/{partner_id}/stock/min", response_model=StockOut)
async def set_stock_min(
    partner_id: str,
    body: StockMinBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Partnerenkénti minimum készlet (riasztási küszöb) beállítása egy
    termékre — None visszaáll a termék globális küszöbére."""
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
    stock.min_quantity = body.min_quantity
    await record_audit(
        db, actor=actor, action="stock.min_set", entity_type="partner_stock",
        entity_id=str(partner.id),
        detail={"product": product.name, "min_quantity": body.min_quantity},
        request=request,
    )
    await db.commit()
    return _stock_out(stock, product, await _partner_price_map(db, partner.id))


# ─── Elszámolási kontextus (gép-soros elszámoláshoz) ─────────────────────────


class SettlementCtxMachine(BaseModel):
    asset_id: str
    barcode: str
    name: str
    counter_count: int
    counters: list[int] | None  # több számlálós gép aktuális állásai
    prev_counter: int  # az utolsó elszámoláskori állás (vagy a gép aktuális)
    last_settled_at: datetime | None
    default_product_id: str | None
    product_name: str | None


class DebtItemOut(BaseModel):
    """Egy kifizetetlen elszámolás — a tartozás tételes bontásához."""

    settlement_id: str
    created_at: datetime
    total_gross: float
    paid_amount: float
    remaining: float
    due_date: date | None
    invoiced: bool


class SettlementContextOut(BaseModel):
    machines: list[SettlementCtxMachine]
    debt: float  # nyitott egyenleg (Σ bruttó − fizetett)
    debt_items: list[DebtItemOut] = []  # melyik elszámolásokból, dátumokkal
    active_contracts: int
    last_visit: datetime | None
    single_product_id: str | None  # ha a partnernek pontosan 1 készlet-terméke van
    # Nyitott szállítólevelek — a következő elszámoláson automatikusan
    # kiszámlázódnak.
    open_deliveries: int = 0
    open_deliveries_net: float = 0.0


@stock_router.get("/{partner_id}/settlement-context", response_model=SettlementContextOut)
async def settlement_context(
    partner_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    """A gép-soros elszámoló nézet adatai: kihelyezett gépek előző számlálóval
    és termék-hozzárendeléssel, nyitott tartozás, aktív szerződések száma."""
    partner = await _get_partner_or_404(db, partner_id)

    # A tükör-mezők frissítése az aktuálisan érvényes szerződésből (jövőbeli
    # szerződés élesedik, lejárt kikapcsol).
    from app.services.wfm.contracts import apply_active_contract

    await apply_active_contract(db, partner)
    await db.commit()

    assets = (
        await db.execute(
            select(Asset)
            .where(Asset.partner_id == partner.id, Asset.status == "deployed")
            .order_by(Asset.barcode)
        )
    ).scalars().all()
    product_names = {
        p.id: p.name
        for p in (await db.execute(select(Product.id, Product.name).select_from(Product))).all()
    } if assets else {}

    machines = []
    for a in assets:
        prev, last_at = await _machine_prev_counter(db, a)
        machines.append(SettlementCtxMachine(
            asset_id=str(a.id),
            barcode=a.barcode,
            name=a.name,
            counter_count=a.counter_count,
            counters=a.counters,
            prev_counter=prev,
            last_settled_at=last_at,
            default_product_id=str(a.default_product_id) if a.default_product_id else None,
            product_name=product_names.get(a.default_product_id),
        ))

    stock_pids = (
        await db.execute(
            select(PartnerStock.product_id).where(PartnerStock.partner_id == partner.id)
        )
    ).scalars().all()
    single_pid = str(stock_pids[0]) if len(stock_pids) == 1 else None

    contracts = sum(
        1 for a in assets
        if a.rent_fee or a.contract_min_portions
    )
    if (partner.contract_min_portions or partner.contract_min_kg
            or partner.contract_rent_if_below_min):
        contracts += 1

    last_visit = (
        await db.execute(
            select(sa_func.max(Settlement.created_at)).where(
                Settlement.partner_id == partner.id
            )
        )
    ).scalar_one_or_none()

    from app.models import DeliveryNote, DeliveryNoteLine

    open_notes = (
        await db.execute(
            select(DeliveryNote.id).where(
                DeliveryNote.partner_id == partner.id, DeliveryNote.status == "open"
            )
        )
    ).scalars().all()
    open_net = 0.0
    if open_notes:
        open_net = (
            await db.execute(
                select(
                    sa_func.coalesce(
                        sa_func.sum(DeliveryNoteLine.quantity * DeliveryNoteLine.unit_price),
                        0.0,
                    )
                ).where(DeliveryNoteLine.delivery_note_id.in_(open_notes))
            )
        ).scalar_one()

    # Tartozás tételesen: mely elszámolásokból áll, dátummal és határidővel —
    # a képviselő a helyszínen látja, mit kell behajtania.
    unpaid = (
        await db.execute(
            select(Settlement)
            .where(Settlement.partner_id == partner.id)
            .order_by(Settlement.created_at.desc())
        )
    ).scalars().all()
    debt_items = []
    for s in unpaid:
        paid = s.paid_amount if s.paid_amount is not None else s.total_gross
        remaining = _money(s.total_gross - paid)
        if remaining > 0.5:
            debt_items.append(DebtItemOut(
                settlement_id=str(s.id), created_at=s.created_at,
                total_gross=s.total_gross, paid_amount=_money(paid),
                remaining=remaining, due_date=s.due_date, invoiced=s.invoiced,
            ))

    return SettlementContextOut(
        machines=machines,
        debt=await _partner_debt(db, partner.id),
        debt_items=debt_items,
        active_contracts=contracts,
        last_visit=last_visit,
        single_product_id=single_pid,
        open_deliveries=len(open_notes),
        open_deliveries_net=_money(open_net),
    )


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
    # A gép számlálója szerinti adagszám az időszakban (opcionális). Ha meg
    # van adva, a számlázás EZ alapján megy; a kg-leltár keresztellenőrzés:
    # a számláló szerint vártnál kisebb fizikai készlet hiánya kg-áron kerül
    # felszámításra.
    counter_portions: float | None = Field(default=None, ge=0)
    # Kézi egységár-felülírás ERRE az elszámolásra (Ft/adag, nettó) — az
    # átírás audit-naplóba kerül (settlement.override).
    price_per_portion: float | None = Field(default=None, ge=0)


class SettlementMachineIn(BaseModel):
    """Gép-soros elszámolás: egy gép új számláló-állása + levonások."""

    asset_id: str
    new_counter: int = Field(default=0, ge=0)
    new_counters: list[int] | None = None  # több számlálós gép: állásonként
    service_portions: int = Field(default=0, ge=0)  # szerviz közben főzött, nem számlázandó
    discount_pct: float = Field(default=0.0, ge=0, le=100)
    # Termék-felülírás erre az elszámolásra — ha a gépen nincs beállítva
    # „ebből főz", az itt választott termék kerül a gépre is (megjegyezzük).
    product_id: str | None = None
    # Kézi egységár-felülírás (Ft/adag, nettó) — audit-naplózva.
    price_per_portion: float | None = Field(default=None, ge=0)


class HandoverIn(BaseModel):
    """Elszámoláskor átadott áru (a leltár UTÁN növeli a készletet)."""

    product_id: str
    quantity: float = Field(gt=0)  # kg
    unit_cost: float | None = Field(default=None, ge=0)  # beszerzési ár (Ft/kg)


class SettlementCreate(BaseModel):
    partner_id: str
    payment_method: str  # cash | card | transfer
    no_vat: bool = False  # ÁFA és Billingó-számla nélkül (a megadott ár fizetendő)
    lines: list[SettlementLineIn] = Field(default_factory=list)
    machines: list[SettlementMachineIn] = Field(default_factory=list)
    handovers: list[HandoverIn] = Field(default_factory=list)
    # A helyszínen ténylegesen fizetett bruttó összeg — None: nem rögzítjük
    # (a saját bruttó rendezettnek számít, tartozás nem képződik).
    paid_amount: float | None = Field(default=None, ge=0)
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


class SettlementMachineOut(BaseModel):
    asset_id: str | None
    barcode: str
    asset_name: str
    product_name: str | None
    prev_counter: int
    new_counter: int
    service_portions: int
    discount_pct: float
    portions_billed: float
    price_per_portion: float
    amount_net: float


class SettlementOut(BaseModel):
    id: str
    partner_id: str
    partner_name: str | None
    settled_by_name: str
    invoicing_company: str | None = None  # xp | pc
    payment_method: str
    no_vat: bool = False
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
    debt_before: float | None = None  # nyitott egyenleg az elszámolás előtt
    paid_amount: float | None = None  # a helyszínen fizetett bruttó összeg
    previous_at: datetime | None = None  # a partner ELŐZŐ elszámolása (időszak-kezdet)
    note: str | None
    created_at: datetime
    lines: list[SettlementLineOut] | None = None
    machines: list[SettlementMachineOut] | None = None


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
        no_vat=s.no_vat,
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
        debt_before=s.debt_before,
        paid_amount=s.paid_amount,
        note=s.note,
        created_at=s.created_at,
    )


def _machine_out(m: SettlementMachine) -> SettlementMachineOut:
    return SettlementMachineOut(
        asset_id=str(m.asset_id) if m.asset_id else None,
        barcode=m.barcode,
        asset_name=m.asset_name,
        product_name=m.product_name,
        prev_counter=m.prev_counter,
        new_counter=m.new_counter,
        service_portions=m.service_portions,
        discount_pct=m.discount_pct,
        portions_billed=m.portions_billed,
        price_per_portion=m.price_per_portion,
        amount_net=m.amount_net,
    )


async def _partner_debt(
    db: AsyncSession, partner_id: uuid.UUID, exclude_id: uuid.UUID | None = None
) -> float:
    """A partner nyitott egyenlege: Σ(bruttó − fizetett). A paid_amount=None
    sorok (régi elszámolások) rendezettnek számítanak."""
    expr = sa_func.sum(
        Settlement.total_gross - sa_func.coalesce(Settlement.paid_amount, Settlement.total_gross)
    )
    q = select(expr).where(Settlement.partner_id == partner_id)
    if exclude_id is not None:
        q = q.where(Settlement.id != exclude_id)
    return _money((await db.execute(q)).scalar_one() or 0.0)


async def _machine_prev_counter(
    db: AsyncSession, asset: Asset
) -> tuple[int, datetime | None]:
    """A gép előző elszámolási számlálója: az utolsó gép-soros elszámolásból;
    ha még nem volt, a gép aktuális számlálója az alapvonal."""
    row = (
        await db.execute(
            select(SettlementMachine.new_counter, SettlementMachine.created_at)
            .where(SettlementMachine.asset_id == asset.id)
            .order_by(SettlementMachine.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is not None:
        return int(row[0]), row[1]
    return int(asset.counter or 0), None


async def apply_contract_lines(
    db: AsyncSession,
    partner: Partner,
    settlement: Settlement,
    total_portions: float,
    total_consumed_kg: float = 0.0,
) -> tuple[float, float]:
    """Szerződéses sorok az elszámoláshoz.

    Minimum-forrás (erősorrend):
      1. partner kg-alapú minimuma (contract_min_kg + contract_below_min_price_kg),
      2. partner adag-alapú minimuma (contract_min_portions + contract_below_min_price),
      3. a kihelyezett gépeken beállított adag-minimumok összege.
    Türelmi időszak (contract_no_min_until, pl. új partner első 3 hónapja):
    a minimum-elvárás szünetel.

    Bérleti díj: alapesetben mindig jár (időarányosan, a gépek rent_fee-je).
    Ha a partneren contract_rent_if_below_min igaz: a bérleti díj CSAK akkor
    kerül felszámításra, ha a minimum nem teljesült — és ilyenkor különbözetet
    nem számlázunk (a bérleti díj maga a szankció); minimum felett semmi.

    Az időszak az előző elszámolás óta eltelt napok száma (első elszámolásnál
    30 nap, 1–90 nap közé szorítva). Visszaadja a hozzáadott (nettó, bruttó)
    összeget."""
    assets = (
        await db.execute(
            select(Asset).where(
                Asset.partner_id == partner.id,
                Asset.status == "deployed",
            )
        )
    ).scalars().all()
    rent_total = sum(a.rent_fee or 0 for a in assets)

    # Adag-alapú minimum: gépek összege, partner-felülírással
    min_portions = sum(a.contract_min_portions or 0 for a in assets)
    below_prices = [a.contract_below_min_price for a in assets if a.contract_below_min_price]
    if partner.contract_min_portions is not None:
        min_portions = partner.contract_min_portions
    if partner.contract_below_min_price is not None:
        below_prices = [partner.contract_below_min_price]

    # Kg-alapú minimum (csak partner-szinten) — ha él, az adag-alapút kiváltja
    kg_mode = bool(partner.contract_min_kg)

    # Türelmi időszak: minden minimum-elvárás szünetel
    grace = (
        partner.contract_no_min_until is not None
        and date.today() <= partner.contract_no_min_until
    )
    if grace:
        min_portions = 0
        kg_mode = False

    conditional_rent = bool(partner.contract_rent_if_below_min)
    has_min = kg_mode or (min_portions > 0 and bool(below_prices))
    if rent_total <= 0 and not has_min:
        return 0.0, 0.0

    prev = (
        await db.execute(
            select(sa_func.max(Settlement.created_at)).where(
                Settlement.partner_id == partner.id, Settlement.id != settlement.id
            )
        )
    ).scalar_one_or_none()
    if prev is not None and prev.tzinfo is None:
        prev = prev.replace(tzinfo=UTC)
    days = 30 if prev is None else (datetime.now(UTC) - prev).days
    days = min(max(days, 1), 90)

    # Minimum-teljesülés kiszámítása (a bérleti díj feltételéhez is kell)
    shortfall_portions = 0.0
    shortfall_kg = 0.0
    min_due_portions = min_due_kg = 0.0
    if kg_mode:
        min_due_kg = (partner.contract_min_kg or 0) * days / 30.0
        shortfall_kg = max(min_due_kg - total_consumed_kg, 0.0)
    elif min_portions > 0:
        min_due_portions = min_portions * days / 30.0
        shortfall_portions = max(min_due_portions - total_portions, 0.0)
    below_minimum = shortfall_kg >= 0.1 or shortfall_portions >= 1

    added_net = added_gross = 0.0
    vat_pct = 0 if settlement.no_vat else 27
    vat_mult = 1.0 if settlement.no_vat else 1.27

    # Bérleti díj: feltételes módban csak minimum alatt, egyébként mindig
    charge_rent = rent_total > 0 and (
        (below_minimum and has_min) if conditional_rent else True
    )
    if charge_rent:
        rent_net = _money(rent_total * days / 30)
        label = f"Bérleti díj ({days} nap, {sum(1 for a in assets if a.rent_fee)} gép)"
        if conditional_rent:
            label += " — minimum alatt"
        db.add(SettlementLine(
            settlement_id=settlement.id,
            product_name=label,
            previous_qty=0.0, physical_qty=0.0, consumed_qty=0.0,
            portions=1.0, price_per_portion=rent_net, vat_percent=vat_pct,
            amount_net=rent_net,
        ))
        added_net += rent_net
        added_gross += _money(rent_net * vat_mult)

    # Különbözet-számlázás: csak ha NEM a feltételes bérleti díj a szankció
    if not conditional_rent and below_minimum:
        if kg_mode and partner.contract_below_min_price_kg:
            price_kg = partner.contract_below_min_price_kg
            amount = _money(shortfall_kg * price_kg)
            db.add(SettlementLine(
                settlement_id=settlement.id,
                product_name=(
                    f"Minimum átvétel különbözet ({shortfall_kg:.1f} kg — "
                    f"{days} napra {min_due_kg:.1f} kg a minimum)"
                ),
                previous_qty=0.0, physical_qty=0.0, consumed_qty=0.0,
                portions=1.0, price_per_portion=amount, vat_percent=vat_pct,
                amount_net=amount,
            ))
            added_net += amount
            added_gross += _money(amount * vat_mult)
        elif not kg_mode and below_prices:
            price = max(below_prices)
            amount = _money(shortfall_portions * price)
            db.add(SettlementLine(
                settlement_id=settlement.id,
                product_name=(
                    f"Minimum adag különbözet ({shortfall_portions:.0f} adag — "
                    f"{days} napra {min_due_portions:.0f} adag a minimum)"
                ),
                previous_qty=0.0, physical_qty=0.0, consumed_qty=0.0,
                portions=round(shortfall_portions), price_per_portion=price,
                vat_percent=vat_pct,
                amount_net=amount,
            ))
            added_net += amount
            added_gross += _money(amount * vat_mult)

    return _money(added_net), _money(added_gross)


async def _previous_settlement_at(
    db: AsyncSession, s: Settlement
) -> datetime | None:
    """A partner megelőző elszámolásának időpontja (az időszak kezdete)."""
    return (
        await db.execute(
            select(sa_func.max(Settlement.created_at)).where(
                Settlement.partner_id == s.partner_id,
                Settlement.created_at < s.created_at,
                Settlement.id != s.id,
            )
        )
    ).scalar_one_or_none()


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

    if not body.lines and not body.machines:
        # Üres leltár/gép-lista is elfogadható, ha van betöltendő nyitott
        # szállítólevél — az elszámolás csak azokat számlázza ki.
        from app.models import DeliveryNote as _DN

        has_open = (
            await db.execute(
                select(_DN.id).where(
                    _DN.partner_id == partner.id, _DN.status == "open"
                ).limit(1)
            )
        ).scalar_one_or_none()
        if has_open is None:
            raise HTTPException(status_code=422, detail={"code": "settlement.empty"})

    # Az elszámolás a MA érvényes szerződés feltételeivel számol — a partner
    # tükör-mezőit frissítjük az aktív szerződésből.
    from app.services.wfm.contracts import apply_active_contract

    await apply_active_contract(db, partner)

    settlement = Settlement(
        partner_id=partner.id,
        settled_by_user_id=actor.id,
        settled_by_name=actor.display_name,
        invoicing_company=partner.invoicing_company,
        payment_method=body.payment_method,
        no_vat=body.no_vat,
        total_net=0.0,
        total_gross=0.0,
        note=body.note,
    )
    db.add(settlement)
    await db.flush()

    price_overrides = await _partner_price_map(db, partner.id)
    # Kézi átírások gyűjtése (egységár, szerviz-adag) — riporthoz auditálva.
    manual_overrides: list[dict] = []

    # ── Gép-soros elszámolás: gépenként lefőzött adag (számláló-különbség) −
    # szerviz-adag, kedvezménnyel árazva; termékenként összegződik és a
    # termék-sor számlázási alapja lesz. A gép számlálója frissül.
    machine_billed: dict[uuid.UUID, float] = {}   # termék → számlázott adag
    machine_brewed: dict[uuid.UUID, float] = {}   # termék → lefőzött adag (kg-ellenőrzés)
    machine_amount: dict[uuid.UUID, float] = {}   # termék → nettó összeg (kedvezménnyel)
    if body.machines:
        stock_pids = (
            await db.execute(
                select(PartnerStock.product_id).where(PartnerStock.partner_id == partner.id)
            )
        ).scalars().all()
        fallback_pid = stock_pids[0] if len(stock_pids) == 1 else None
        for m_in in body.machines:
            try:
                aid = uuid.UUID(m_in.asset_id)
            except ValueError:
                raise HTTPException(status_code=404, detail={"code": "asset.not_found"})
            asset = (
                await db.execute(select(Asset).where(Asset.id == aid))
            ).scalar_one_or_none()
            if asset is None:
                raise HTTPException(status_code=404, detail={"code": "asset.not_found"})
            if asset.partner_id != partner.id:
                raise HTTPException(
                    status_code=422, detail={"code": "settlement.machine_wrong_partner"}
                )
            prev_counter, _ = await _machine_prev_counter(db, asset)
            new_counter = (
                sum(m_in.new_counters) if m_in.new_counters else m_in.new_counter
            )
            if new_counter < prev_counter:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "settlement.counter_below_prev",
                            "barcode": asset.barcode},
                )
            brewed = new_counter - prev_counter
            # Számlálónkénti különbség a norma-súlyozott kg-ellenőrzéshez
            # (a korábbi állások a gépen tárolt `counters` pillanatképből).
            per_counter_diffs: list[float] | None = None
            if (
                m_in.new_counters
                and isinstance(asset.counters, list)
                and len(asset.counters) == len(m_in.new_counters)
            ):
                per_counter_diffs = [
                    max(new_c - (asset.counters[i] or 0), 0)
                    for i, new_c in enumerate(m_in.new_counters)
                ]
            billed = max(brewed - m_in.service_portions, 0)
            # Termék (erősorrend): kérésbeli felülírás → a gép beállítása →
            # a partner egyetlen készlet-terméke.
            override_pid: uuid.UUID | None = None
            if m_in.product_id:
                try:
                    override_pid = uuid.UUID(m_in.product_id)
                except ValueError:
                    raise HTTPException(
                        status_code=404, detail={"code": "product.not_found"}
                    )
            pid = override_pid or asset.default_product_id or fallback_pid
            if pid is None:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "settlement.machine_no_product",
                            "barcode": asset.barcode},
                )
            product = await _get_product_or_404(db, str(pid))
            # Beállítás nélküli gép: a most választott terméket megjegyezzük
            if asset.default_product_id is None:
                asset.default_product_id = product.id
            unit_price = _effective_price(product, price_overrides)
            if m_in.price_per_portion is not None:
                if abs(m_in.price_per_portion - unit_price) > 1e-9:
                    manual_overrides.append({
                        "field": "unit_price", "target": asset.barcode,
                        "product": product.name,
                        "from": unit_price, "to": m_in.price_per_portion,
                    })
                unit_price = m_in.price_per_portion
            if m_in.service_portions:
                manual_overrides.append({
                    "field": "service_portions", "target": asset.barcode,
                    "product": product.name, "from": 0, "to": m_in.service_portions,
                })
            amount = _money(billed * unit_price * (1 - m_in.discount_pct / 100.0))
            db.add(SettlementMachine(
                settlement_id=settlement.id,
                asset_id=asset.id,
                barcode=asset.barcode,
                asset_name=asset.name,
                product_id=product.id,
                product_name=product.name,
                prev_counter=prev_counter,
                new_counter=new_counter,
                service_portions=m_in.service_portions,
                discount_pct=m_in.discount_pct,
                portions_billed=billed,
                price_per_portion=unit_price,
                amount_net=amount,
            ))
            # Kg-keresztellenőrzés: számlálónkénti normával (g/adag) súlyozva —
            # a 0 normájú számláló (pl. forró csoki) nem fogyaszt kávét.
            cross_brewed = float(brewed)
            norms = asset.norms if isinstance(asset.norms, list) else None
            if (
                per_counter_diffs is not None
                and norms
                and product.grams_per_portion
            ):
                grams = sum(
                    diff * (
                        norms[i]
                        if i < len(norms) and norms[i] is not None
                        else product.grams_per_portion
                    )
                    for i, diff in enumerate(per_counter_diffs)
                )
                cross_brewed = grams / product.grams_per_portion
            machine_billed[product.id] = machine_billed.get(product.id, 0.0) + billed
            machine_brewed[product.id] = machine_brewed.get(product.id, 0.0) + cross_brewed
            machine_amount[product.id] = machine_amount.get(product.id, 0.0) + amount
            old_counter = asset.counter
            asset.counter = new_counter
            if m_in.new_counters and asset.counter_count > 1:
                asset.counters = list(m_in.new_counters)
            db.add(AssetMovement(
                asset_id=asset.id, action="counter", partner_id=partner.id,
                actor_user_id=actor.id,
                detail=f"Elszámolás: {old_counter if old_counter is not None else '?'} → {new_counter}"[:512],
            ))

    total_net = 0.0
    total_gross = 0.0
    total_portions = 0.0
    total_consumed_kg = 0.0
    settled_pids: set[uuid.UUID] = set()
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
        # Számlázott adagszám (erősorrend): a gép-sorok összege erre a
        # termékre → a kézzel megadott számláló-adag → a kg-fogyásból számolva.
        # A kg-keresztellenőrzés alapja a LEFŐZÖTT adag (szerviz-adagokkal
        # együtt — azok is fogyasztottak kávét, csak nem számlázzuk őket).
        unit_price = _effective_price(product, price_overrides)
        if line_in.price_per_portion is not None:
            if abs(line_in.price_per_portion - unit_price) > 1e-9:
                manual_overrides.append({
                    "field": "unit_price", "target": "leltár",
                    "product": product.name,
                    "from": unit_price, "to": line_in.price_per_portion,
                })
            unit_price = line_in.price_per_portion
        if product.id in machine_billed:
            portions = machine_billed[product.id]
            amount_net = _money(machine_amount[product.id])
            cross_portions: float | None = machine_brewed[product.id]
        elif line_in.counter_portions is not None:
            portions = line_in.counter_portions
            amount_net = _money(portions * unit_price)
            cross_portions = line_in.counter_portions
        else:
            portions = portions_from(consumed, product.grams_per_portion)
            amount_net = _money(portions * unit_price)
            cross_portions = None
        line_vat = 0 if body.no_vat else product.vat_percent
        amount_gross = _money(amount_net * (1 + line_vat / 100))
        total_net += amount_net
        total_gross += amount_gross
        total_portions += portions
        total_consumed_kg += consumed
        settled_pids.add(product.id)

        db.add(
            SettlementLine(
                settlement_id=settlement.id,
                product_id=product.id,
                product_name=product.name,
                previous_qty=previous,
                physical_qty=line_in.physical_qty,
                consumed_qty=consumed,
                portions=portions,
                counter_portions=cross_portions,
                price_per_portion=unit_price,
                vat_percent=line_vat,
                amount_net=amount_net,
            )
        )

        # Kg-keresztellenőrzés: a számláló szerint ennyi készletnek KELLENE
        # lennie — ha a mért ennél kevesebb, a hiányt kg-áron számlázzuk
        # (beszerzési ár, ha van; különben az adagár kg-egyenértéke).
        if cross_portions is not None:
            expected_remaining = previous - (
                cross_portions * product.grams_per_portion / 1000.0
            )
            shortage_kg = expected_remaining - line_in.physical_qty
            if shortage_kg >= 0.05:
                kg_price = product.purchase_price or (
                    unit_price * 1000.0 / max(product.grams_per_portion, 1)
                )
                short_net = _money(shortage_kg * kg_price)
                db.add(
                    SettlementLine(
                        settlement_id=settlement.id,
                        product_id=product.id,
                        product_name=(
                            f"Készlethiány — {product.name} "
                            f"({shortage_kg:.2f} kg, számláló-ellenőrzés)"
                        ),
                        previous_qty=0.0, physical_qty=0.0,
                        consumed_qty=0.0, portions=1.0,
                        price_per_portion=short_net, vat_percent=line_vat,
                        amount_net=short_net,
                    )
                )
                total_net += short_net
                total_gross += _money(short_net * (1 + line_vat / 100))
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

    # Gép-sorokból számlázott termékek, amelyekhez NEM érkezett leltár-sor:
    # önálló tételként kerülnek be (készlet-módosítás nélkül).
    for pid, billed in machine_billed.items():
        if pid in settled_pids:
            continue
        product = await _get_product_or_404(db, str(pid))
        unit_price = _effective_price(product, price_overrides)
        line_vat = 0 if body.no_vat else product.vat_percent
        amount_net = _money(machine_amount[pid])
        db.add(SettlementLine(
            settlement_id=settlement.id,
            product_id=product.id,
            product_name=product.name,
            previous_qty=0.0, physical_qty=0.0, consumed_qty=0.0,
            portions=billed,
            counter_portions=machine_brewed[pid],
            price_per_portion=unit_price,
            vat_percent=line_vat,
            amount_net=amount_net,
        ))
        total_net += amount_net
        total_gross += _money(amount_net * (1 + line_vat / 100))
        total_portions += billed

    # Átadott áru (a leltár UTÁN növeli a készletet — a következő időszaké).
    for h in body.handovers:
        product = await _get_product_or_404(db, h.product_id)
        h_stock = (
            await db.execute(
                select(PartnerStock).where(
                    PartnerStock.partner_id == partner.id,
                    PartnerStock.product_id == product.id,
                )
            )
        ).scalar_one_or_none()
        if h_stock is None:
            h_stock = PartnerStock(partner_id=partner.id, product_id=product.id, quantity=0.0)
            db.add(h_stock)
        h_stock.quantity += h.quantity
        db.add(StockMovement(
            partner_id=partner.id, product_id=product.id, action="replenish",
            quantity_delta=h.quantity, unit_cost=h.unit_cost,
            settlement_id=settlement.id, note="Elszámoláskor átadva",
            actor_user_id=actor.id,
        ))
        if h.unit_cost is not None:
            product.purchase_price = h.unit_cost

    # Nyitott szállítólevelek betöltése: a korábban (elszámolás nélkül)
    # kiszállított áru itt számlázódik ki, SZL-hivatkozással.
    from app.models import DeliveryNote, DeliveryNoteLine

    open_notes = (
        await db.execute(
            select(DeliveryNote).where(
                DeliveryNote.partner_id == partner.id, DeliveryNote.status == "open"
            ).order_by(DeliveryNote.created_at)
        )
    ).scalars().all()
    for dn in open_notes:
        dn_lines = (
            await db.execute(
                select(DeliveryNoteLine).where(DeliveryNoteLine.delivery_note_id == dn.id)
            )
        ).scalars().all()
        for ln in dn_lines:
            line_vat = 0 if body.no_vat else ln.vat_percent
            amount_net = _money(ln.quantity * ln.unit_price)
            db.add(SettlementLine(
                settlement_id=settlement.id,
                product_id=ln.product_id,
                product_name=f"{ln.product_name} ({dn.serial})",
                previous_qty=0.0, physical_qty=0.0, consumed_qty=ln.quantity,
                portions=ln.quantity,
                counter_portions=None,
                price_per_portion=ln.unit_price,
                vat_percent=line_vat,
                amount_net=amount_net,
            ))
            total_net += amount_net
            total_gross += _money(amount_net * (1 + line_vat / 100))
        dn.status = "settled"
        dn.settlement_id = settlement.id

    # Szerződéses tételek: bérleti díj + minimum különbözet (automatikus).
    # total_portions/total_consumed_kg a termék-sorokból gyűlt a ciklusban.
    extra_net, extra_gross = await apply_contract_lines(
        db, partner, settlement, total_portions, total_consumed_kg
    )
    total_net += extra_net
    total_gross += extra_gross

    settlement.total_net = _money(total_net)
    settlement.total_gross = _money(total_gross)

    # Tartozás-görgetés: nyitott egyenleg pillanatképe + a fizetett összeg.
    settlement.debt_before = await _partner_debt(db, partner.id, exclude_id=settlement.id)
    settlement.paid_amount = body.paid_amount

    await record_audit(
        db, actor=actor, action="settlement.create", entity_type="settlement",
        entity_id=str(settlement.id),
        detail={"partner": partner.name, "gross": settlement.total_gross},
        request=request,
    )
    if manual_overrides:
        # Kézi átírások (egységár, szerviz-adag) — riport a Naplóból
        # (action = settlement.override) szűrhető.
        await record_audit(
            db, actor=actor, action="settlement.override", entity_type="settlement",
            entity_id=str(settlement.id),
            detail={"partner": partner.name, "overrides": manual_overrides[:50]},
            request=request,
        )
    await db.commit()

    # Automatizálások (háttérben, saját sessionnel)
    from app.services.wfm.automation import fire_event

    fire_event("settlement.created", {
        "_partner_id": partner.id,
        "partner_nev": partner.name,
        "partner_kod": partner.partner_code,
        "partner_email": partner.contact_email,
        "vegosszeg_brutto": settlement.total_gross,
        "vegosszeg_netto": settlement.total_net,
        "fizetes_mod": settlement.payment_method,
        "elszamolo": settlement.settled_by_name,
        "ceg": settlement.invoicing_company or "",
        "afa_nelkul": "igen" if settlement.no_vat else "nem",
    })
    # Alacsony készlet: a leltár utáni mennyiség a küszöb alatt van-e
    for line_in in body.lines:
        try:
            prod = await _get_product_or_404(db, line_in.product_id)
        except HTTPException:
            continue
        st = (
            await db.execute(
                select(PartnerStock).where(
                    PartnerStock.partner_id == partner.id,
                    PartnerStock.product_id == prod.id,
                )
            )
        ).scalar_one_or_none()
        threshold = (st.min_quantity if st and st.min_quantity is not None
                     else prod.low_stock_threshold)
        if threshold is not None and line_in.physical_qty <= threshold:
            fire_event("stock.low", {
                "_partner_id": partner.id,
                "partner_nev": partner.name,
                "termek_nev": prod.name,
                "keszlet_kg": line_in.physical_qty,
                "kuszob_kg": threshold,
            })

    out = _settlement_out(settlement, partner.name)
    out.lines = [
        _line_out(line)
        for line in (
            await db.execute(
                select(SettlementLine).where(SettlementLine.settlement_id == settlement.id)
            )
        ).scalars()
    ]
    out.machines = [
        _machine_out(m)
        for m in (
            await db.execute(
                select(SettlementMachine)
                .where(SettlementMachine.settlement_id == settlement.id)
                .order_by(SettlementMachine.barcode)
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
    # Az előző elszámolás időpontja partnerenként (időszak-kezdet) — a teljes
    # állományon számolt ablak-függvény, hogy a szűrők ne csonkolják.
    prev_sub = (
        select(
            Settlement.id.label("sid"),
            type_coerce(
                sa_func.lag(Settlement.created_at).over(
                    partition_by=Settlement.partner_id,
                    order_by=Settlement.created_at,
                ),
                DateTime(timezone=True),
            ).label("prev_at"),
        )
    ).subquery()
    query = (
        select(Settlement, Partner.name, prev_sub.c.prev_at)
        .join(Partner, Partner.id == Settlement.partner_id)
        .join(prev_sub, prev_sub.c.sid == Settlement.id)
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
    out = []
    for s, name, prev_at in rows:
        item = _settlement_out(s, name)
        item.previous_at = prev_at
        out.append(item)
    return out


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


@settlements_router.get("/monthly-report")
async def monthly_report(
    year: int = Query(ge=2000, le=2100),
    month: int = Query(ge=1, le=12),
    company: str | None = Query(default=None),  # xp | pc | None (összes)
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("agent_report")),
):
    """Havi zárás PDF a könyvelőnek — cégenként vagy összesítve."""
    from fastapi import Response

    from app.services.wfm.billingo_service import COMPANIES
    from app.services.wfm.monthly_report import build_monthly_report_pdf

    if company is not None and company not in ("xp", "pc"):
        raise HTTPException(status_code=422, detail={"code": "settlement.bad_company"})
    start = datetime(year, month, 1, tzinfo=UTC)
    end = datetime(year + 1, 1, 1, tzinfo=UTC) if month == 12 else datetime(year, month + 1, 1, tzinfo=UTC)

    query = (
        select(Settlement, Partner.name)
        .join(Partner, Partner.id == Settlement.partner_id)
        .where(Settlement.created_at >= start, Settlement.created_at < end)
        .order_by(Settlement.created_at)
    )
    if company:
        query = query.where(Settlement.invoicing_company == company)
    rows = [
        {
            "date": f"{s.created_at:%m.%d. %H:%M}",
            "partner": name or "—",
            "method": s.payment_method,
            "net": s.total_net,
            "gross": s.total_gross,
            "invoiced": s.invoiced,
            "payment_status": s.payment_status,
        }
        for s, name in (await db.execute(query)).all()
    ]
    pdf = build_monthly_report_pdf(
        company_label=COMPANIES.get(company, "Mindkét cég összesítve") if company else "Mindkét cég összesítve",
        period_label=f"{year}. {month:02d}. hónap",
        rows=rows,
    )
    fn = f"zaras-{company or 'osszes'}-{year}-{month:02d}.pdf"
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fn}"'},
    )


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
    out.previous_at = await _previous_settlement_at(db, s)
    out.lines = [
        _line_out(line)
        for line in (
            await db.execute(
                select(SettlementLine).where(SettlementLine.settlement_id == s.id)
            )
        ).scalars()
    ]
    out.machines = [
        _machine_out(m)
        for m in (
            await db.execute(
                select(SettlementMachine)
                .where(SettlementMachine.settlement_id == s.id)
                .order_by(SettlementMachine.barcode)
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
    if s.no_vat:
        # ÁFA/számla nélküli elszámolás — Billingó-számla nem készülhet hozzá.
        raise HTTPException(status_code=409, detail={"code": "settlement.no_vat_no_invoice"})
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


def _budapest(dt: datetime | None) -> datetime | None:
    """UTC (vagy naiv-UTC) időbélyeg budapesti helyi időre — a bizonylaton a
    helyi idő szerepel."""
    from zoneinfo import ZoneInfo

    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ZoneInfo("Europe/Budapest"))


def _receipt_no(s: Settlement) -> str:
    return f"ELSZ-{_budapest(s.created_at):%Y%m%d}-{str(s.id)[:8].upper()}"


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
    machines = (
        await db.execute(
            select(SettlementMachine)
            .where(SettlementMachine.settlement_id == s.id)
            .order_by(SettlementMachine.barcode)
        )
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

    previous_at = await _previous_settlement_at(db, s)
    receipt_no = _receipt_no(s)
    pdf = build_settlement_pdf(
        {
            "receipt_no": receipt_no,
            "created_at": f"{_budapest(s.created_at):%Y-%m-%d %H:%M}",
            "previous_at": f"{_budapest(previous_at):%Y-%m-%d %H:%M}" if previous_at else None,
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
            "machines": [
                {
                    "barcode": m.barcode,
                    "asset_name": m.asset_name,
                    "prev_counter": m.prev_counter,
                    "new_counter": m.new_counter,
                    "service_portions": m.service_portions,
                    "discount_pct": m.discount_pct,
                    "portions_billed": m.portions_billed,
                    "amount_net": m.amount_net,
                }
                for m in machines
            ],
            "total_net": s.total_net,
            "total_gross": s.total_gross,
            "debt_before": s.debt_before,
            "paid_amount": s.paid_amount,
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
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """A partner képernyős aláírásának rögzítése a bizonylathoz. Ha az
    Értesítéseknél be van kapcsolva, a bizonylat automatikusan kimegy
    e-mailben a partnernek aláírás után."""
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

    # Automatikus bizonylat-küldés (best-effort, háttérben)
    from app.services.wfm.notifier import get_or_create_settings as _notif

    notif = await _notif(db)
    await db.commit()
    partner_email = partner.contact_email if partner else None
    if notif.auto_receipt and partner_email:
        background_tasks.add_task(_auto_send_receipt, str(s.id), partner_email)

    from app.services.wfm.automation import fire_event

    fire_event("settlement.signed", {
        "_partner_id": partner.id if partner else None,
        "partner_nev": partner.name if partner else "",
        "partner_kod": partner.partner_code if partner else "",
        "partner_email": partner_email,
        "vegosszeg_brutto": s.total_gross,
        "elszamolo": s.settled_by_name,
    })
    return _settlement_out(s, partner.name if partner else None)


async def _auto_send_receipt(settlement_id: str, to: str) -> None:
    """Háttérfeladat: bizonylat-PDF küldése aláírás után (saját session)."""
    from app.db import get_session_factory
    from app.services.wfm.email_service import load_smtp_config, send_email

    try:
        async with get_session_factory()() as db:
            s = (
                await db.execute(
                    select(Settlement).where(Settlement.id == uuid.UUID(settlement_id))
                )
            ).scalar_one_or_none()
            if s is None:
                return
            smtp = await load_smtp_config(db)
            if smtp is None:
                return
            pdf, receipt_no = await _build_settlement_pdf(db, s)
            ok = await send_email(
                smtp, to,
                f"Elszámolási bizonylat — {receipt_no}",
                (
                    f"Tisztelt Partnerünk!\n\nMellékelve küldjük a(z) {receipt_no} számú "
                    f"elszámolási bizonylatot.\n\nÜdvözlettel:\n{s.settled_by_name}"
                ),
                attachments=[(f"{receipt_no}.pdf", pdf, "application", "pdf")],
            )
            if ok:
                s.receipt_sent_at = datetime.now(UTC)
                await db.commit()
    except Exception:  # noqa: BLE001 — háttérfeladat, nem dobhat a kérésre
        import logging

        logging.getLogger(__name__).warning("auto receipt send failed", exc_info=True)


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
