"""Partner-szerződések kezelése — a partnertől külön entitás, érvényességgel.

Egy partnernek több szerződése lehet (történet + jövőbeli); az elszámolás a
mindenkor aktívat alkalmazza (services/wfm/contracts.apply_active_contract).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.db import get_db
from app.models import Partner, PartnerContract, User
from app.services.wfm.contracts import apply_active_contract

router = APIRouter()

# /api/contracts — minden szerződés egy listában (áttekintő oldal)
overview_router = APIRouter()


async def _get_partner_or_404(db: AsyncSession, partner_id: str) -> Partner:
    try:
        pid = uuid.UUID(partner_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "partner.not_found"})
    p = (await db.execute(select(Partner).where(Partner.id == pid))).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail={"code": "partner.not_found"})
    return p


async def _get_contract_or_404(
    db: AsyncSession, partner: Partner, contract_id: str
) -> PartnerContract:
    try:
        cid = uuid.UUID(contract_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "contract.not_found"})
    c = (
        await db.execute(
            select(PartnerContract).where(
                PartnerContract.id == cid, PartnerContract.partner_id == partner.id
            )
        )
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail={"code": "contract.not_found"})
    return c


class ContractBody(BaseModel):
    valid_from: date
    valid_to: date | None = None
    min_portions: int | None = Field(default=None, ge=0, le=1_000_000)
    below_min_price: float | None = Field(default=None, ge=0)  # Ft/adag
    min_kg: float | None = Field(default=None, ge=0, le=100_000)
    below_min_price_kg: float | None = Field(default=None, ge=0)  # Ft/kg
    rent_if_below_min: bool = False
    # Elszámolás gyakorisága: 1, 2 vagy 4 hetente (alapértelmezés 4).
    settlement_weeks: int = 4
    # Szerződéses fizetési mód + határidő — az elszámolás/számlázás
    # alapértelmezése, ott csak felülírható.
    payment_method: str | None = None
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    # Nincs minimum: a partner mindig pontosan a lefőzöttet fizeti (a
    # gép-szintű minimumokat is kikapcsolja — korlátlan türelmi időszak).
    no_minimum: bool = False
    # Automata számlázás: a diktált számláló-állásokból mentett elszámolás
    # után a számla azonnal, emberi kattintás nélkül kiállítódik.
    auto_billing: bool = False
    # Telefonos elszámolás: az útvonal-tervezés kihagyja a partnert;
    # visit_weeks megadásával ritkább (karbantartó) látogatás mégis ütemeződik.
    phone_settlement: bool = False
    visit_weeks: int | None = Field(default=None, ge=1, le=52)
    # Számlára kerülő norma (adag/kg) — csak a számla-megjegyzés lefőzhető
    # adag kijelzését és a 0 Ft-os kávésort vezérli.
    invoice_norma: int | None = Field(default=None, ge=1, le=1000)
    # Alapértelmezett kávéfajta a partnernek — az elszámolás átadott-kávé
    # sora ezt ajánlja, de a képviselő helyben mást is adhat.
    default_product_id: str | None = None
    # Sávos adagárazás (tiered modul): az elszámolt adagszám sávja adja az
    # adagárat. qty_to None = nincs felső határ (utolsó sáv).
    price_tiers: list[dict] | None = None
    note: str | None = None

    @field_validator("price_tiers")
    @classmethod
    def _check_tiers(cls, v: list[dict] | None) -> list[dict] | None:
        if not v:
            return None
        if len(v) > 20:
            raise ValueError("contract.too_many_tiers")
        out = []
        prev_to: float | None = None
        for t in v:
            try:
                lo = float(t.get("qty_from") or 0)
                hi = t.get("qty_to")
                hi = float(hi) if hi not in (None, "") else None
                price = float(t.get("price"))
            except (TypeError, ValueError):
                raise ValueError("contract.bad_tier")
            if lo < 0 or price < 0 or (hi is not None and hi < lo):
                raise ValueError("contract.bad_tier")
            if prev_to is not None and lo < prev_to:
                raise ValueError("contract.tier_overlap")
            prev_to = hi if hi is not None else float("inf")
            out.append({"qty_from": lo, "qty_to": hi, "price": price})
        return out

    @field_validator("settlement_weeks")
    @classmethod
    def _check_weeks(cls, v: int) -> int:
        if v not in (1, 2, 4):
            raise ValueError("contract.bad_settlement_weeks")
        return v

    @field_validator("payment_method")
    @classmethod
    def _check_method(cls, v: str | None) -> str | None:
        if v is not None and v not in ("cash", "card", "transfer", "cod"):
            raise ValueError("contract.bad_payment_method")
        return v


class ContractOut(BaseModel):
    id: str
    valid_from: date
    valid_to: date | None
    min_portions: int | None
    below_min_price: float | None
    min_kg: float | None
    below_min_price_kg: float | None
    rent_if_below_min: bool
    settlement_weeks: int
    payment_method: str | None
    payment_terms_days: int | None
    no_minimum: bool
    auto_billing: bool = False
    phone_settlement: bool = False
    visit_weeks: int | None = None
    price_tiers: list[dict] | None = None
    invoice_norma: int | None = None
    default_product_id: str | None = None
    default_product_name: str | None = None
    note: str | None
    status: str  # active | future | expired
    created_at: datetime


def _parse_product_id(raw) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


async def _product_name(db: AsyncSession, pid: uuid.UUID | None) -> str | None:
    if pid is None:
        return None
    from app.models import Product

    return (
        await db.execute(select(Product.name).where(Product.id == pid))
    ).scalar_one_or_none()


def _status(c: PartnerContract, today: date) -> str:
    if c.valid_from > today:
        return "future"
    if c.valid_to is not None and c.valid_to < today:
        return "expired"
    return "active"


def _out(c: PartnerContract, product_name: str | None = None) -> ContractOut:
    return ContractOut(
        id=str(c.id), valid_from=c.valid_from, valid_to=c.valid_to,
        min_portions=c.min_portions, below_min_price=c.below_min_price,
        min_kg=c.min_kg, below_min_price_kg=c.below_min_price_kg,
        rent_if_below_min=c.rent_if_below_min,
        settlement_weeks=c.settlement_weeks,
        payment_method=c.payment_method,
        payment_terms_days=c.payment_terms_days,
        no_minimum=c.no_minimum,
        auto_billing=c.auto_billing,
        phone_settlement=c.phone_settlement,
        visit_weeks=c.visit_weeks,
        price_tiers=c.price_tiers,
        invoice_norma=c.invoice_norma,
        default_product_id=str(c.default_product_id) if c.default_product_id else None,
        default_product_name=product_name,
        note=c.note,
        status=_status(c, date.today()), created_at=c.created_at,
    )


def _check_dates(body: ContractBody) -> None:
    if body.valid_to is not None and body.valid_to < body.valid_from:
        raise HTTPException(status_code=422, detail={"code": "contract.bad_dates"})


@router.get("/{partner_id}/contracts", response_model=list[ContractOut])
async def list_contracts(
    partner_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("partners")),
):
    partner = await _get_partner_or_404(db, partner_id)
    rows = (
        await db.execute(
            select(PartnerContract)
            .where(PartnerContract.partner_id == partner.id)
            .order_by(PartnerContract.valid_from.desc())
        )
    ).scalars().all()
    names = {c.id: await _product_name(db, c.default_product_id) for c in rows}
    return [_out(c, names.get(c.id)) for c in rows]


@router.post("/{partner_id}/contracts", response_model=ContractOut, status_code=201)
async def create_contract(
    partner_id: str,
    body: ContractBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("partners")),
):
    partner = await _get_partner_or_404(db, partner_id)
    _check_dates(body)
    values = body.model_dump()
    values["default_product_id"] = _parse_product_id(values.get("default_product_id"))
    c = PartnerContract(partner_id=partner.id, created_by=actor.id, **values)
    db.add(c)
    await db.flush()
    await apply_active_contract(db, partner)
    await record_audit(
        db, actor=actor, action="contract.create", entity_type="partner_contract",
        entity_id=str(c.id), detail={"partner": partner.name, "from": str(body.valid_from)},
        request=request,
    )
    await db.commit()
    return _out(c, await _product_name(db, c.default_product_id))


@router.patch("/{partner_id}/contracts/{contract_id}", response_model=ContractOut)
async def update_contract(
    partner_id: str,
    contract_id: str,
    body: ContractBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("partners")),
):
    partner = await _get_partner_or_404(db, partner_id)
    c = await _get_contract_or_404(db, partner, contract_id)
    _check_dates(body)
    values = body.model_dump()
    values["default_product_id"] = _parse_product_id(values.get("default_product_id"))
    for key, value in values.items():
        setattr(c, key, value)
    await apply_active_contract(db, partner)
    await record_audit(
        db, actor=actor, action="contract.update", entity_type="partner_contract",
        entity_id=str(c.id), detail={"partner": partner.name}, request=request,
    )
    await db.commit()
    return _out(c, await _product_name(db, c.default_product_id))


@router.delete("/{partner_id}/contracts/{contract_id}")
async def delete_contract(
    partner_id: str,
    contract_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("partners")),
):
    partner = await _get_partner_or_404(db, partner_id)
    c = await _get_contract_or_404(db, partner, contract_id)
    await record_audit(
        db, actor=actor, action="contract.delete", entity_type="partner_contract",
        entity_id=str(c.id),
        detail={"partner": partner.name, "from": str(c.valid_from)},
        request=request,
    )
    await db.delete(c)
    await db.flush()
    await apply_active_contract(db, partner)
    await db.commit()
    return {"ok": True}


class ContractRowOut(BaseModel):
    """Egy sor az áttekintőben: szerződés + partner + kihelyezett gépek árral."""

    id: str
    partner_id: str
    partner_name: str
    partner_active: bool
    status: str  # active | future | expired
    valid_from: date
    valid_to: date | None
    min_portions: int | None
    below_min_price: float | None
    min_kg: float | None
    below_min_price_kg: float | None
    rent_if_below_min: bool
    settlement_weeks: int
    payment_method: str | None
    payment_terms_days: int | None
    no_minimum: bool
    note: str | None
    machines: list[dict]


class NoContractRowOut(BaseModel):
    """Partner kihelyezett géppel, de partner-szerződés REKORD nélkül.

    has_machine_terms: a gépeken vannak szerződéses feltételek (bérleti díj,
    minimum, számlálónkénti adagár) — az elszámolás így is jól számol, csak a
    partner-szintű szerződés-rekord (érvényesség, ütemezés) hiányzik. Az
    importált állomány tipikusan ilyen."""

    partner_id: str
    partner_name: str
    partner_active: bool
    has_machine_terms: bool = False
    machines: list[dict]


class ContractsOverviewOut(BaseModel):
    contracts: list[ContractRowOut]
    no_contract: list[NoContractRowOut]


@overview_router.get("", response_model=ContractsOverviewOut)
async def contracts_overview(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("partners")),
):
    """Szerződések oldal adatai: ki, milyen gép, mikortól meddig, milyen áron.

    A gép adagára a gép termékének partner-ára (felülírás), különben a termék
    alapára — ugyanaz a feloldás, mint az elszámolásban. Külön lista azokról a
    partnerekről, akiknél van kihelyezett gép, de nincs egyetlen szerződés sem.
    """
    from app.models import Asset, PartnerPrice, PartnerStock, Product

    contracts = (
        await db.execute(
            select(PartnerContract, Partner)
            .join(Partner, Partner.id == PartnerContract.partner_id)
            .order_by(Partner.name, PartnerContract.valid_from.desc())
        )
    ).all()
    assets = (
        await db.execute(
            select(Asset)
            .where(Asset.partner_id.is_not(None), Asset.status == "deployed")
            .order_by(Asset.barcode)
        )
    ).scalars().all()
    products = {
        p.id: p for p in (await db.execute(select(Product))).scalars().all()
    }
    overrides = {
        (pp.partner_id, pp.product_id): pp.price_per_portion
        for pp in (await db.execute(select(PartnerPrice))).scalars().all()
    }
    # Elszámolás-fallback: ha a gépnek nincs terméke, a partner EGYETLEN
    # készlet-terméke érvényes (ha pont egy van).
    stock_products: dict = {}
    for pid, prod_id in (
        await db.execute(select(PartnerStock.partner_id, PartnerStock.product_id))
    ).all():
        stock_products.setdefault(pid, set()).add(prod_id)

    def machine_dict(a: Asset) -> dict:
        prod_id = a.default_product_id
        if prod_id is None:
            single = stock_products.get(a.partner_id) or set()
            if len(single) == 1:
                prod_id = next(iter(single))
        prod = products.get(prod_id)
        price = None
        if prod is not None:
            price = overrides.get((a.partner_id, prod.id), prod.price_per_portion)
        return {
            "id": str(a.id), "barcode": a.barcode, "name": a.name,
            "counter": a.counter, "customer_owned": a.customer_owned,
            "deployed_at": a.deployed_at.date().isoformat() if a.deployed_at else None,
            "product_name": prod.name if prod else None,
            "price_per_portion": price,
            "rent_fee": a.rent_fee,
            "counter_count": a.counter_count or 1,
            "counter_prices": a.counter_prices,
        }

    machines_by_partner: dict = {}
    for a in assets:
        machines_by_partner.setdefault(a.partner_id, []).append(machine_dict(a))

    today = date.today()
    rows = [
        ContractRowOut(
            id=str(c.id), partner_id=str(p.id), partner_name=p.name,
            partner_active=p.is_active, status=_status(c, today),
            valid_from=c.valid_from, valid_to=c.valid_to,
            min_portions=c.min_portions, below_min_price=c.below_min_price,
            min_kg=c.min_kg, below_min_price_kg=c.below_min_price_kg,
            rent_if_below_min=c.rent_if_below_min,
            settlement_weeks=c.settlement_weeks,
            payment_method=c.payment_method,
            payment_terms_days=c.payment_terms_days,
            no_minimum=c.no_minimum, note=c.note,
            machines=machines_by_partner.get(p.id, []),
        )
        for c, p in contracts
    ]

    contracted_ids = {c.partner_id for c, _p in contracts}
    orphan_ids = [pid for pid in machines_by_partner if pid not in contracted_ids]
    # Gép-szintű szerződéses feltételek (bérleti díj / minimum / számlálónkénti
    # adagár) — ilyenkor az elszámolás rendben számol partner-szerződés nélkül is.
    terms_by_partner: set = set()
    for a in assets:
        if a.partner_id is None:
            continue
        has_cps = isinstance(a.counter_prices, list) and any(
            p is not None for p in a.counter_prices
        )
        if a.rent_fee or a.contract_min_portions or has_cps:
            terms_by_partner.add(a.partner_id)
    orphans = []
    if orphan_ids:
        orphan_partners = (
            await db.execute(
                select(Partner).where(Partner.id.in_(orphan_ids)).order_by(Partner.name)
            )
        ).scalars().all()
        orphans = [
            NoContractRowOut(
                partner_id=str(p.id), partner_name=p.name, partner_active=p.is_active,
                has_machine_terms=p.id in terms_by_partner,
                machines=machines_by_partner[p.id],
            )
            for p in orphan_partners
        ]

    return ContractsOverviewOut(contracts=rows, no_contract=orphans)
