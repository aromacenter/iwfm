"""Készlet-nyilvántartás: partnerek + eszközök (gépek) egyedi vonalkóddal.

Eszköz életciklus: raktáron (in_stock) → kihelyezve partnerhez (deployed) →
visszavéve (in_stock). Minden mozgás az asset_movements táblába kerül
(kihelyezési előzmény + audit). A vonalkód egyedi, scannerrel kereshető.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import delete as sa_delete, func, or_, select, update as sa_update
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
    StockMovement,
    User,
)

router = APIRouter()  # /api/partners
assets_router = APIRouter()  # /api/assets

ASSET_STATUSES = ("in_stock", "deployed", "maintenance", "retired")


# ─── Partnerek ───────────────────────────────────────────────────────────────


PARTNER_TYPES = ("customer", "supplier", "both")
# Saját számlázó cégeink (a partner szerződött cége): xp | pc
INVOICING_COMPANIES = ("xp", "pc")


def compose_address(
    zip_: str | None, city: str | None, street: str | None, number: str | None
) -> str | None:
    """Strukturált részek → egysoros cím ('1051 Budapest, Fő utca 1.')."""
    left = " ".join(x.strip() for x in (zip_, city) if x and x.strip())
    right = " ".join(x.strip() for x in (street, number) if x and x.strip())
    if left and right:
        return f"{left}, {right}"
    return left or right or None


class PartnerBody(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    company_name: str | None = Field(default=None, max_length=256)
    invoicing_company: str | None = None  # xp | pc | None
    partner_type: str = Field(default="customer")
    tax_number: str | None = Field(default=None, max_length=32)
    eu_tax_number: str | None = Field(default=None, max_length=32)
    reg_number: str | None = Field(default=None, max_length=64)
    contact_name: str | None = Field(default=None, max_length=256)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=32)
    website: str | None = Field(default=None, max_length=256)
    address: str | None = Field(default=None, max_length=512)
    billing_address: str | None = Field(default=None, max_length=512)
    address_zip: str | None = Field(default=None, max_length=16)
    address_city: str | None = Field(default=None, max_length=128)
    address_street: str | None = Field(default=None, max_length=256)
    address_number: str | None = Field(default=None, max_length=32)
    billing_zip: str | None = Field(default=None, max_length=16)
    billing_city: str | None = Field(default=None, max_length=128)
    billing_street: str | None = Field(default=None, max_length=256)
    billing_number: str | None = Field(default=None, max_length=32)
    bank_account: str | None = Field(default=None, max_length=64)
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    # A szerződéses feltételek KÜLÖN entitásban élnek (/contracts) — a
    # partner-űrlap nem írja őket; a partneren lévő contract_* mezők a
    # mindenkor aktív szerződés tükrei.
    notes: str | None = None
    is_active: bool = True

    @field_validator("partner_type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        if v not in PARTNER_TYPES:
            raise ValueError("partner.bad_type")
        return v

    @field_validator("invoicing_company")
    @classmethod
    def _check_company(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        if v not in INVOICING_COMPANIES:
            raise ValueError("partner.bad_company")
        return v

    def resolved(self) -> dict:
        """Mezők úgy, hogy az egysoros címek a strukturált részekből álljanak
        össze, ha azok (bármelyike) ki van töltve."""
        data = self.model_dump()
        composed = compose_address(
            self.address_zip, self.address_city, self.address_street, self.address_number
        )
        if composed:
            data["address"] = composed
        composed_billing = compose_address(
            self.billing_zip, self.billing_city, self.billing_street, self.billing_number
        )
        if composed_billing:
            data["billing_address"] = composed_billing
        return data


class PartnerOut(BaseModel):
    id: str
    partner_code: str | None
    name: str
    company_name: str | None
    invoicing_company: str | None
    partner_type: str
    tax_number: str | None
    eu_tax_number: str | None
    reg_number: str | None
    contact_name: str | None
    contact_email: str | None
    contact_phone: str | None
    website: str | None
    address: str | None
    billing_address: str | None
    address_zip: str | None
    address_city: str | None
    address_street: str | None
    address_number: str | None
    billing_zip: str | None
    billing_city: str | None
    billing_street: str | None
    billing_number: str | None
    bank_account: str | None
    payment_terms_days: int | None
    contract_min_portions: int | None
    contract_below_min_price: float | None
    contract_min_kg: float | None
    contract_below_min_price_kg: float | None
    contract_no_min_until: date | None
    contract_rent_if_below_min: bool
    notes: str | None
    is_active: bool
    asset_count: int = 0


def _partner_out(p: Partner, asset_count: int = 0) -> PartnerOut:
    return PartnerOut(
        id=str(p.id),
        partner_code=p.partner_code,
        name=p.name,
        company_name=p.company_name,
        invoicing_company=p.invoicing_company,
        partner_type=p.partner_type,
        tax_number=p.tax_number,
        eu_tax_number=p.eu_tax_number,
        reg_number=p.reg_number,
        contact_name=p.contact_name,
        contact_email=p.contact_email,
        contact_phone=p.contact_phone,
        website=p.website,
        address=p.address,
        billing_address=p.billing_address,
        address_zip=p.address_zip,
        address_city=p.address_city,
        address_street=p.address_street,
        address_number=p.address_number,
        billing_zip=p.billing_zip,
        billing_city=p.billing_city,
        billing_street=p.billing_street,
        billing_number=p.billing_number,
        bank_account=p.bank_account,
        payment_terms_days=p.payment_terms_days,
        contract_min_portions=p.contract_min_portions,
        contract_below_min_price=p.contract_below_min_price,
        contract_min_kg=p.contract_min_kg,
        contract_below_min_price_kg=p.contract_below_min_price_kg,
        contract_no_min_until=p.contract_no_min_until,
        contract_rent_if_below_min=p.contract_rent_if_below_min,
        notes=p.notes,
        is_active=p.is_active,
        asset_count=asset_count,
    )


async def _get_partner_or_404(db: AsyncSession, partner_id: str) -> Partner:
    try:
        pid = uuid.UUID(partner_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "partner.not_found"})
    p = (await db.execute(select(Partner).where(Partner.id == pid))).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail={"code": "partner.not_found"})
    return p


class PartnerOverviewOut(BaseModel):
    partner: PartnerOut
    debt: float
    contracts: list[dict]
    machines: list[dict]
    stock: list[dict]
    recent_settlements: list[dict]
    open_deliveries: int
    open_deliveries_net: float
    open_tickets: int


@router.get("/{partner_id}/overview", response_model=PartnerOverviewOut)
async def partner_overview(
    partner_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("partners")),
):
    """Minden a partnerről egy nézetben — a listákból kattintva nyíló
    partner-adatlap adatai: törzsadatok, szerződések, kihelyezett gépek,
    készlet, tartozás, utolsó elszámolások, nyitott szállítólevelek."""
    from sqlalchemy import func as sa_func

    from app.api.consignment import _partner_debt
    from app.api.contracts import _out as contract_out
    from app.models import (
        Asset,
        DeliveryNote,
        DeliveryNoteLine,
        PartnerContract,
        PartnerStock,
        Product,
        ServiceTicket,
        Settlement,
    )

    p = await _get_partner_or_404(db, partner_id)

    assets = (
        await db.execute(
            select(Asset)
            .where(Asset.partner_id == p.id, Asset.status == "deployed")
            .order_by(Asset.barcode)
        )
    ).scalars().all()
    stock_rows = (
        await db.execute(
            select(PartnerStock, Product)
            .join(Product, Product.id == PartnerStock.product_id)
            .where(PartnerStock.partner_id == p.id)
            .order_by(Product.name)
        )
    ).all()
    contracts = (
        await db.execute(
            select(PartnerContract)
            .where(PartnerContract.partner_id == p.id)
            .order_by(PartnerContract.valid_from.desc())
        )
    ).scalars().all()
    settlements = (
        await db.execute(
            select(Settlement)
            .where(Settlement.partner_id == p.id)
            .order_by(Settlement.created_at.desc())
            .limit(5)
        )
    ).scalars().all()
    open_notes = (
        await db.execute(
            select(DeliveryNote.id).where(
                DeliveryNote.partner_id == p.id, DeliveryNote.status == "open"
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
    open_tickets = (
        await db.execute(
            select(sa_func.count()).select_from(ServiceTicket).where(
                ServiceTicket.partner_id == p.id,
                ServiceTicket.status.in_(("open", "in_progress")),
            )
        )
    ).scalar_one()

    return PartnerOverviewOut(
        partner=_partner_out(p, asset_count=len(assets)),
        debt=await _partner_debt(db, p.id),
        contracts=[contract_out(c).model_dump(mode="json") for c in contracts],
        machines=[
            {"id": str(a.id), "barcode": a.barcode, "name": a.name,
             "counter": a.counter, "customer_owned": a.customer_owned}
            for a in assets
        ],
        stock=[
            {"product_name": prod.name, "quantity": round(s.quantity, 2), "unit": prod.unit}
            for s, prod in stock_rows
        ],
        recent_settlements=[
            {"id": str(s.id), "created_at": s.created_at.isoformat(),
             "total_gross": s.total_gross, "paid_amount": s.paid_amount,
             "invoiced": s.invoiced, "payment_method": s.payment_method}
            for s in settlements
        ],
        open_deliveries=len(open_notes),
        open_deliveries_net=round(open_net, 2),
        open_tickets=open_tickets,
    )


@router.get("", response_model=list[PartnerOut])
async def list_partners(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("partners")),
):
    partners = (
        (await db.execute(select(Partner).order_by(Partner.name))).scalars().all()
    )
    counts = dict(
        (
            await db.execute(
                select(Asset.partner_id, func.count())
                .where(Asset.partner_id.is_not(None))
                .group_by(Asset.partner_id)
            )
        ).all()
    )
    return [_partner_out(p, int(counts.get(p.id, 0))) for p in partners]


@router.get("/tax-lookup")
async def tax_lookup(
    tax_number: str = Query(min_length=8, max_length=32),
    _: User = Depends(require_perm("partners")),
):
    """Közhiteles cégadat-lekérés adószám alapján (EU VIES). A magyar adószám
    első 8 jegyével kérdezünk; a válaszból hivatalos cégnév + székhely jön.
    Cégjegyzékszám/e-mail nincs ingyenes közhiteles forrásban."""
    import re as _re

    import httpx

    digits = _re.sub(r"\D", "", tax_number)
    if len(digits) < 8:
        raise HTTPException(status_code=422, detail={"code": "partner.bad_tax_number"})
    vat = digits[:8]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(
                f"https://ec.europa.eu/taxation_customs/vies/rest-api/ms/HU/vat/{vat}"
            )
            res.raise_for_status()
            data = res.json()
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail={"code": "partner.lookup_failed"})
    if not data.get("isValid"):
        return {"found": False}

    suffixes = {"kft": "Kft.", "kft.": "Kft.", "bt": "Bt.", "bt.": "Bt.",
                "zrt": "Zrt.", "zrt.": "Zrt.", "nyrt": "Nyrt.", "nyrt.": "Nyrt.",
                "kkt": "Kkt.", "kkt.": "Kkt."}
    lower_words = {"és", "a", "az"}

    def _pretty(raw: str) -> str:
        words = raw.strip().split()
        out = []
        for i, w in enumerate(words):
            lw = w.lower()
            if lw in suffixes:
                out.append(suffixes[lw])
            elif lw in lower_words and i > 0:
                out.append(lw)
            else:
                out.append(lw.capitalize())
        return " ".join(out)

    name = _pretty(data.get("name") or "") or None
    raw_address = (data.get("address") or "").replace("\n", " ").strip() or None
    # Cím best-effort bontása: "FŐ UTCA 1. 1011 BUDAPEST" → irsz + város + utca
    zip_code = city = street = None
    if raw_address:
        m = _re.search(r"\b(\d{4})\b\s+(.+)$", raw_address)
        if m:
            zip_code = m.group(1)
            city = m.group(2).strip().title() or None
            street = raw_address[: m.start()].strip(" ,") or None
        else:
            street = raw_address
    return {
        "found": True,
        "company_name": name,
        "address": raw_address,
        "address_zip": zip_code,
        "address_city": city,
        "address_street": street,
    }


@router.post("", response_model=PartnerOut, status_code=201)
async def create_partner(
    body: PartnerBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("partners")),
):
    from app.services.wfm.codes import generate_partner_code

    p = Partner(**body.resolved(), partner_code=await generate_partner_code(db))
    db.add(p)
    await db.flush()
    await record_audit(
        db, actor=actor, action="partner.create", entity_type="partner",
        entity_id=str(p.id), request=request,
    )
    await db.commit()

    from app.services.wfm.geocode import schedule_geocode

    schedule_geocode(p.id)

    from app.services.wfm.automation import fire_event

    fire_event("partner.created", {
        "_partner_id": p.id,
        "partner_nev": p.name,
        "partner_kod": p.partner_code,
        "varos": p.address_city or "",
        "ceg": p.invoicing_company or "",
    })
    return _partner_out(p)


@router.patch("/{partner_id}", response_model=PartnerOut)
async def update_partner(
    partner_id: str,
    body: PartnerBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("partners")),
):
    p = await _get_partner_or_404(db, partner_id)
    old_address = (p.address, p.address_zip, p.address_city, p.address_street, p.address_number)
    for key, value in body.resolved().items():
        setattr(p, key, value)
    address_changed = old_address != (
        p.address, p.address_zip, p.address_city, p.address_street, p.address_number
    )
    await record_audit(
        db, actor=actor, action="partner.update", entity_type="partner",
        entity_id=str(p.id), request=request,
    )
    await db.commit()
    if address_changed or (p.lat is None and p.address):
        from app.services.wfm.geocode import schedule_geocode

        schedule_geocode(p.id)
    return _partner_out(p)


class BulkDeleteBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=1000)


def _parse_uuid_list(ids: list[str]) -> list[uuid.UUID]:
    out = []
    for raw in ids:
        try:
            out.append(uuid.UUID(raw))
        except ValueError:
            continue  # érvénytelen id → egyszerűen kihagyjuk
    return out


@router.post("/bulk-delete")
async def bulk_delete_partners(
    body: BulkDeleteBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("delete")),
):
    """Partnerek (tömeges) törlése. Védőkorlátok: elszámolással rendelkező vagy
    kihelyezett géppel rendelkező partner nem törölhető (blocked listában
    jelezzük, a többi törlődik)."""
    ids = _parse_uuid_list(body.ids)
    deleted = 0
    blocked: list[dict] = []
    for pid in ids:
        partner = (
            await db.execute(select(Partner).where(Partner.id == pid))
        ).scalar_one_or_none()
        if partner is None:
            continue
        has_settlement = (
            await db.execute(select(Settlement.id).where(Settlement.partner_id == pid).limit(1))
        ).first()
        if has_settlement is not None:
            blocked.append({"id": str(pid), "name": partner.name, "code": "partner.has_settlements"})
            continue
        has_deployed = (
            await db.execute(
                select(Asset.id).where(Asset.partner_id == pid, Asset.status == "deployed").limit(1)
            )
        ).first()
        if has_deployed is not None:
            blocked.append({"id": str(pid), "name": partner.name, "code": "partner.has_assets"})
            continue
        # függő rekordok explicit takarítása (SQLite-on nincs FK-cascade garancia)
        await db.execute(sa_delete(PartnerStock).where(PartnerStock.partner_id == pid))
        await db.execute(sa_delete(StockMovement).where(StockMovement.partner_id == pid))
        await db.execute(sa_delete(PartnerPrice).where(PartnerPrice.partner_id == pid))
        await db.execute(
            sa_update(AssetMovement).where(AssetMovement.partner_id == pid).values(partner_id=None)
        )
        await db.execute(
            sa_update(Asset).where(Asset.partner_id == pid).values(partner_id=None)
        )
        await db.delete(partner)
        deleted += 1
    await record_audit(
        db, actor=actor, action="partner.bulk_delete", entity_type="partner",
        detail={"deleted": deleted, "blocked": len(blocked)}, request=request,
    )
    await db.commit()
    return {"deleted": deleted, "blocked": blocked}


# ─── Eszközök ────────────────────────────────────────────────────────────────


class AssetBody(BaseModel):
    barcode: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=256)  # Típus
    category: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=128)
    manufacturer: str | None = Field(default=None, max_length=128)
    article_number: str | None = Field(default=None, max_length=64)
    serial_number: str | None = Field(default=None, max_length=128)
    location_type: str | None = Field(default=None, max_length=64)  # figyelmen kívül — származtatott
    counter: int | None = Field(default=None, ge=0)
    counter_count: int = Field(default=1, ge=1, le=8)  # hány számláló van a gépen
    counters: list[int] | None = Field(default=None, max_length=8)  # állások (több számlálós)
    norm: float | None = Field(default=None, ge=0)
    # Számlálónkénti norma (g kávé/adag) több számlálós gépnél; 0 = nem használ kávét.
    norms: list[float] | None = Field(default=None, max_length=8)
    default_product_id: str | None = None  # melyik terméket (kávét) főzi
    tangible: bool = False
    customer_owned: bool = False  # az ügyfél saját gépe
    contract_min_portions: int | None = Field(default=None, ge=0, le=1_000_000)
    contract_below_min_price: float | None = Field(default=None, ge=0)
    rent_fee: float | None = Field(default=None, ge=0)
    notes: str | None = None


class AssetPatch(BaseModel):
    model_config = {"extra": "forbid"}
    barcode: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=256)
    category: str | None = None
    model: str | None = None
    manufacturer: str | None = None
    article_number: str | None = None
    serial_number: str | None = None
    location_type: str | None = None  # figyelmen kívül — származtatott
    counter: int | None = Field(default=None, ge=0)
    counter_count: int | None = Field(default=None, ge=1, le=8)
    counters: list[int] | None = Field(default=None, max_length=8)
    norm: float | None = Field(default=None, ge=0)
    norms: list[float] | None = Field(default=None, max_length=8)
    default_product_id: str | None = None  # "" → törlés
    tangible: bool | None = None
    customer_owned: bool | None = None
    contract_min_portions: int | None = Field(default=None, ge=0, le=1_000_000)
    contract_below_min_price: float | None = Field(default=None, ge=0)
    rent_fee: float | None = Field(default=None, ge=0)
    notes: str | None = None
    status: str | None = None  # csak in_stock|maintenance|retired (deploy külön)


class DeployBody(BaseModel):
    partner_id: str
    note: str | None = Field(default=None, max_length=512)


class ReturnBody(BaseModel):
    note: str | None = Field(default=None, max_length=512)


class MovementOut(BaseModel):
    id: str
    action: str
    partner_id: str | None
    partner_name: str | None
    detail: str | None
    created_at: datetime


class AssetOut(BaseModel):
    id: str
    barcode: str
    name: str
    category: str | None
    model: str | None
    manufacturer: str | None
    article_number: str | None
    serial_number: str | None
    location_type: str | None
    counter: int | None
    counter_count: int = 1
    counters: list[int] | None = None
    norm: float | None
    norms: list[float] | None = None
    default_product_id: str | None = None
    tangible: bool
    customer_owned: bool
    contract_min_portions: int | None
    contract_below_min_price: float | None
    rent_fee: float | None
    status: str
    partner_id: str | None
    partner_name: str | None
    deployed_at: datetime | None
    notes: str | None
    created_at: datetime
    movements: list[MovementOut] | None = None


def _derived_location(a: Asset) -> str:
    """A gép aktuális helye — nem kézzel töltött adat, a státuszból következik.
    Kód megy ki, a kliens i18n-nel fordítja (inv.loc_*)."""
    if a.status == "deployed":
        return "customer" if a.customer_owned else "partner"
    if a.status == "maintenance":
        return "service"
    if a.status == "retired":
        return "retired"
    return "warehouse"


def _asset_out(a: Asset, partner_name: str | None = None) -> AssetOut:
    return AssetOut(
        id=str(a.id),
        barcode=a.barcode,
        name=a.name,
        category=a.category,
        model=a.model,
        manufacturer=a.manufacturer,
        article_number=a.article_number,
        serial_number=a.serial_number,
        location_type=_derived_location(a),
        counter=a.counter,
        counter_count=a.counter_count or 1,
        counters=a.counters,
        norm=a.norm,
        norms=a.norms,
        default_product_id=str(a.default_product_id) if a.default_product_id else None,
        tangible=a.tangible,
        customer_owned=a.customer_owned,
        contract_min_portions=a.contract_min_portions,
        contract_below_min_price=a.contract_below_min_price,
        rent_fee=a.rent_fee,
        status=a.status,
        partner_id=str(a.partner_id) if a.partner_id else None,
        partner_name=partner_name,
        deployed_at=a.deployed_at,
        notes=a.notes,
        created_at=a.created_at,
    )


async def _partner_names(db: AsyncSession, partner_ids: set) -> dict:
    ids = {pid for pid in partner_ids if pid}
    if not ids:
        return {}
    rows = (await db.execute(select(Partner.id, Partner.name).where(Partner.id.in_(ids)))).all()
    return {pid: name for pid, name in rows}


async def _parse_default_product(db: AsyncSession, raw: str | None) -> uuid.UUID | None:
    """A gép „ebből főz" termék-hivatkozásának ellenőrzése. Üres → None."""
    if not raw:
        return None
    try:
        pid = uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "product.not_found"})
    exists = (await db.execute(select(Product.id).where(Product.id == pid))).first()
    if exists is None:
        raise HTTPException(status_code=404, detail={"code": "product.not_found"})
    return pid


async def _get_asset_or_404(db: AsyncSession, asset_id: str) -> Asset:
    try:
        aid = uuid.UUID(asset_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "asset.not_found"})
    a = (await db.execute(select(Asset).where(Asset.id == aid))).scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail={"code": "asset.not_found"})
    return a


async def _next_barcode(db: AsyncSession) -> str:
    """Generált eszközcímke: ESZ-NNNNNN (a meglévő ESZ- kódok max + 1)."""
    rows = (
        (await db.execute(select(Asset.barcode).where(Asset.barcode.like("ESZ-%"))))
        .scalars()
        .all()
    )
    nums = [int(b.split("-")[1]) for b in rows if b.split("-")[1].isdigit()]
    return f"ESZ-{(max(nums) + 1) if nums else 1:06d}"


@assets_router.get("/generate-barcode")
async def generate_barcode(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("machines")),
):
    return {"barcode": await _next_barcode(db)}


@assets_router.get("", response_model=list[AssetOut])
async def list_assets(
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    partner_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("machines")),
):
    query = select(Asset).order_by(Asset.created_at.desc())
    if q:
        like = f"%{q.strip()}%"
        query = query.where(
            or_(Asset.barcode.ilike(like), Asset.name.ilike(like),
                Asset.serial_number.ilike(like), Asset.model.ilike(like),
                Asset.manufacturer.ilike(like), Asset.article_number.ilike(like))
        )
    if status:
        # 'customer' = az ügyfél saját gépei (bármilyen állapotban);
        # 'deployed' = csak a SAJÁT kihelyezett gépeink.
        if status == "customer":
            query = query.where(Asset.customer_owned.is_(True))
        elif status == "deployed":
            query = query.where(Asset.status == "deployed", Asset.customer_owned.is_(False))
        elif status in ASSET_STATUSES:
            query = query.where(Asset.status == status)
        else:
            raise HTTPException(status_code=422, detail={"code": "asset.bad_status"})
    if partner_id:
        try:
            query = query.where(Asset.partner_id == uuid.UUID(partner_id))
        except ValueError:
            raise HTTPException(status_code=422, detail={"code": "asset.bad_partner"})

    assets = list((await db.execute(query.limit(1000))).scalars())
    names = await _partner_names(db, {a.partner_id for a in assets})
    return [_asset_out(a, names.get(a.partner_id)) for a in assets]


class TypeDefaultsOut(BaseModel):
    manufacturer: str | None
    article_number: str | None
    norm: float | None
    norms: list[float] | None
    counter_count: int


@assets_router.get("/type-defaults", response_model=TypeDefaultsOut | None)
async def asset_type_defaults(
    name: str = Query(min_length=2, max_length=256),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("machines")),
):
    """Új gép felvételekor: ha van már ilyen nevű (típusú) gép, annak adatai
    előtölthetők (cikkszám, gyártó, norma, számláló-szám)."""
    a = (
        await db.execute(
            select(Asset)
            .where(func.lower(Asset.name) == name.strip().lower())
            .order_by(Asset.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if a is None:
        return None
    return TypeDefaultsOut(
        manufacturer=a.manufacturer, article_number=a.article_number,
        norm=a.norm, norms=a.norms, counter_count=a.counter_count or 1,
    )


@assets_router.get("/by-barcode/{barcode}", response_model=AssetOut)
async def asset_by_barcode(
    barcode: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("machines")),
):
    a = (
        await db.execute(select(Asset).where(Asset.barcode == barcode.strip()))
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail={"code": "asset.barcode_not_found"})
    names = await _partner_names(db, {a.partner_id})
    return _asset_out(a, names.get(a.partner_id))


MAX_ASSET_PHOTOS = 10
_PHOTO_MIMES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


class AssetPhotosBody(BaseModel):
    photos: list[str] = Field(min_length=1, max_length=5)
    note: str | None = Field(default=None, max_length=256)


@assets_router.get("/photos/{photo_id}")
async def get_asset_photo(
    photo_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("machines")),
):
    from fastapi import Response

    from app.models import AssetPhoto

    try:
        pid = uuid.UUID(photo_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "asset.photo_not_found"})
    photo = (
        await db.execute(select(AssetPhoto).where(AssetPhoto.id == pid))
    ).scalar_one_or_none()
    if photo is None:
        raise HTTPException(status_code=404, detail={"code": "asset.photo_not_found"})
    return Response(content=photo.data, media_type=photo.mime)


@assets_router.delete("/photos/{photo_id}")
async def delete_asset_photo(
    photo_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("machines")),
):
    from app.models import AssetPhoto

    try:
        pid = uuid.UUID(photo_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "asset.photo_not_found"})
    photo = (
        await db.execute(select(AssetPhoto).where(AssetPhoto.id == pid))
    ).scalar_one_or_none()
    if photo is None:
        raise HTTPException(status_code=404, detail={"code": "asset.photo_not_found"})
    await db.delete(photo)
    await record_audit(
        db, actor=actor, action="asset.photo_delete", entity_type="asset",
        entity_id=str(photo.asset_id), request=request,
    )
    await db.commit()
    return {"ok": True}


@assets_router.get("/{asset_id}/photos")
async def list_asset_photos(
    asset_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("machines")),
):
    from app.models import AssetPhoto

    a = await _get_asset_or_404(db, asset_id)
    rows = (
        await db.execute(
            select(AssetPhoto.id, AssetPhoto.filename, AssetPhoto.note, AssetPhoto.created_at)
            .where(AssetPhoto.asset_id == a.id)
            .order_by(AssetPhoto.created_at.desc())
        )
    ).all()
    return [
        {"id": str(pid), "filename": fn, "note": note, "created_at": created}
        for pid, fn, note, created in rows
    ]


@assets_router.post("/{asset_id}/photos", status_code=201)
async def upload_asset_photos(
    asset_id: str,
    body: AssetPhotosBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("machines")),
):
    """Fotók a gépről (max 10/gép, 4MB/kép, PNG/JPEG/WebP data URL-ként)."""
    import base64

    from app.models import AssetPhoto

    a = await _get_asset_or_404(db, asset_id)
    existing = (
        await db.execute(
            select(func.count()).select_from(AssetPhoto).where(AssetPhoto.asset_id == a.id)
        )
    ).scalar_one()
    if existing + len(body.photos) > MAX_ASSET_PHOTOS:
        raise HTTPException(status_code=422, detail={"code": "asset.too_many_photos"})

    for i, data_url in enumerate(body.photos):
        try:
            header, b64 = data_url.split(",", 1)
            mime = header.split(";")[0].removeprefix("data:")
            ext = _PHOTO_MIMES[mime]
            raw = base64.b64decode(b64)
        except (ValueError, KeyError):
            raise HTTPException(status_code=422, detail={"code": "asset.bad_photo"})
        if len(raw) > 4 * 1024 * 1024:
            raise HTTPException(status_code=422, detail={"code": "asset.photo_too_large"})
        db.add(AssetPhoto(
            asset_id=a.id,
            filename=f"{a.barcode}-{datetime.now(UTC):%Y%m%d%H%M%S}-{i}.{ext}",
            mime=mime,
            data=raw,
            note=body.note,
            created_by=actor.id,
        ))
    await record_audit(
        db, actor=actor, action="asset.photo_upload", entity_type="asset",
        entity_id=a.barcode, detail={"count": len(body.photos)}, request=request,
    )
    await db.commit()
    return {"uploaded": len(body.photos)}


@assets_router.get("/{asset_id}", response_model=AssetOut)
async def get_asset(
    asset_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("machines")),
):
    a = await _get_asset_or_404(db, asset_id)
    names = await _partner_names(db, {a.partner_id})
    out = _asset_out(a, names.get(a.partner_id))

    moves = (
        (
            await db.execute(
                select(AssetMovement)
                .where(AssetMovement.asset_id == a.id)
                .order_by(AssetMovement.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    move_names = await _partner_names(db, {m.partner_id for m in moves})
    out.movements = [
        MovementOut(
            id=str(m.id),
            action=m.action,
            partner_id=str(m.partner_id) if m.partner_id else None,
            partner_name=move_names.get(m.partner_id),
            detail=m.detail,
            created_at=m.created_at,
        )
        for m in moves
    ]
    return out


@assets_router.post("", response_model=AssetOut, status_code=201)
async def create_asset(
    body: AssetBody,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("machines")),
):
    barcode = body.barcode.strip()
    existing = (
        await db.execute(select(Asset.id).where(Asset.barcode == barcode))
    ).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail={"code": "asset.barcode_taken"})

    a = Asset(
        barcode=barcode,
        name=body.name.strip(),
        category=(body.category or "").strip() or None,
        model=(body.model or "").strip() or None,
        manufacturer=(body.manufacturer or "").strip() or None,
        article_number=(body.article_number or "").strip() or None,
        serial_number=(body.serial_number or "").strip() or None,
        # location_type nem kézzel töltött — a kimenetben a státuszból származik
        # Több számlálós gép: a `counter` mindig az állások összege
        counter=sum(body.counters) if body.counters else body.counter,
        counter_count=body.counter_count,
        counters=body.counters,
        norm=body.norm,
        norms=body.norms,
        default_product_id=await _parse_default_product(db, body.default_product_id),
        tangible=body.tangible,
        customer_owned=body.customer_owned,
        contract_min_portions=body.contract_min_portions,
        contract_below_min_price=body.contract_below_min_price,
        rent_fee=body.rent_fee,
        notes=body.notes,
        created_by=actor.id,
    )
    db.add(a)
    await db.flush()
    db.add(AssetMovement(asset_id=a.id, action="created", actor_user_id=actor.id))
    await record_audit(
        db, actor=actor, action="asset.create", entity_type="asset",
        entity_id=a.barcode, request=request,
    )
    await db.commit()
    # Új géptípusnál a tudásbázis automatikus bővítése a háttérben (best-effort)
    from app.services.wfm.kb_auto import ensure_machine_kb

    background_tasks.add_task(ensure_machine_kb, a.manufacturer, a.name)
    return _asset_out(a)


@assets_router.patch("/{asset_id}", response_model=AssetOut)
async def update_asset(
    asset_id: str,
    body: AssetPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("machines")),
):
    a = await _get_asset_or_404(db, asset_id)
    data = body.model_dump(exclude_unset=True)
    data.pop("location_type", None)  # származtatott — kézzel nem írható

    if "status" in data:
        if data["status"] not in ("in_stock", "maintenance", "retired"):
            # 'deployed'-ot csak a /deploy végpont állíthat (partner kell hozzá)
            raise HTTPException(status_code=422, detail={"code": "asset.bad_status"})
        if data["status"] != a.status:
            db.add(
                AssetMovement(
                    asset_id=a.id, action="status", detail=data["status"],
                    actor_user_id=actor.id,
                )
            )
        # állapotváltáskor (pl. szervizbe) a kihelyezés megszűnik
        if data["status"] != "deployed":
            a.partner_id = None
            a.deployed_at = None

    if "barcode" in data:
        new_bc = (data["barcode"] or "").strip()
        clash = (
            await db.execute(
                select(Asset.id).where(Asset.barcode == new_bc, Asset.id != a.id)
            )
        ).first()
        if clash is not None:
            raise HTTPException(status_code=409, detail={"code": "asset.barcode_taken"})
        data["barcode"] = new_bc

    if "default_product_id" in data:
        data["default_product_id"] = await _parse_default_product(
            db, data["default_product_id"]
        )

    for key, value in data.items():
        setattr(a, key, value.strip() if isinstance(value, str) and key != "notes" else value)
    # Több számlálós gép: az egyes állásokból a `counter` mindig az összeg
    if data.get("counters"):
        a.counter = sum(data["counters"])
    await record_audit(
        db, actor=actor, action="asset.update", entity_type="asset",
        entity_id=a.barcode, detail={"fields": sorted(data)}, request=request,
    )
    await db.commit()
    names = await _partner_names(db, {a.partner_id})
    return _asset_out(a, names.get(a.partner_id))


@assets_router.post("/{asset_id}/deploy", response_model=AssetOut)
async def deploy_asset(
    asset_id: str,
    body: DeployBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("machines")),
):
    a = await _get_asset_or_404(db, asset_id)
    if a.status == "retired":
        raise HTTPException(status_code=422, detail={"code": "asset.retired"})
    partner = await _get_partner_or_404(db, body.partner_id)

    a.status = "deployed"
    a.partner_id = partner.id
    a.deployed_at = datetime.now(UTC)
    db.add(
        AssetMovement(
            asset_id=a.id, action="deploy", partner_id=partner.id,
            detail=body.note, actor_user_id=actor.id,
        )
    )
    await record_audit(
        db, actor=actor, action="asset.deploy", entity_type="asset",
        entity_id=a.barcode, detail={"partner": partner.name}, request=request,
    )
    await db.commit()
    return _asset_out(a, partner.name)


class SwapBody(BaseModel):
    replacement_asset_id: str
    note: str | None = Field(default=None, max_length=512)


@assets_router.post("/{asset_id}/swap", response_model=AssetOut)
async def swap_asset(
    asset_id: str,
    body: SwapBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("machines")),
):
    """Gépcsere egy lépésben: a kihelyezett gép szervizre kerül, a cseregép
    ugyanahhoz a partnerhez kihelyeződik, és ÖRÖKLI a szerződéses feltételeket
    (min. adag, min. alatti adagár, bérleti díj). Mindkét gép előzményébe
    bekerül a csere."""
    old = await _get_asset_or_404(db, asset_id)
    if old.status != "deployed" or old.partner_id is None:
        raise HTTPException(status_code=422, detail={"code": "asset.not_deployed"})
    new = await _get_asset_or_404(db, body.replacement_asset_id)
    if new.status != "in_stock":
        raise HTTPException(status_code=422, detail={"code": "asset.replacement_not_in_stock"})
    partner = (
        await db.execute(select(Partner).where(Partner.id == old.partner_id))
    ).scalar_one_or_none()

    # cseregép: kihelyezés + szerződéses feltételek öröklése
    new.status = "deployed"
    new.partner_id = old.partner_id
    new.deployed_at = datetime.now(UTC)
    new.contract_min_portions = old.contract_min_portions
    new.contract_below_min_price = old.contract_below_min_price
    new.rent_fee = old.rent_fee
    # régi gép: szervizre, kihelyezés lezárva
    prev_partner = old.partner_id
    old.status = "maintenance"
    old.partner_id = None
    old.deployed_at = None

    note = (body.note or "").strip()
    db.add(AssetMovement(
        asset_id=old.id, action="return", partner_id=prev_partner,
        detail=f"Gépcsere → {new.barcode} került a helyére. {note}".strip(),
        actor_user_id=actor.id,
    ))
    db.add(AssetMovement(
        asset_id=new.id, action="deploy", partner_id=prev_partner,
        detail=f"Gépcsere: {old.barcode} helyett. {note}".strip(),
        actor_user_id=actor.id,
    ))
    await record_audit(
        db, actor=actor, action="asset.swap", entity_type="asset",
        entity_id=old.barcode,
        detail={"replacement": new.barcode, "partner": partner.name if partner else None},
        request=request,
    )
    await db.commit()
    return _asset_out(new, partner.name if partner else None)


@assets_router.post("/{asset_id}/return", response_model=AssetOut)
async def return_asset(
    asset_id: str,
    body: ReturnBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("machines")),
):
    a = await _get_asset_or_404(db, asset_id)
    if a.status != "deployed":
        raise HTTPException(status_code=422, detail={"code": "asset.not_deployed"})
    prev_partner = a.partner_id
    a.status = "in_stock"
    a.partner_id = None
    a.deployed_at = None
    db.add(
        AssetMovement(
            asset_id=a.id, action="return", partner_id=prev_partner,
            detail=body.note, actor_user_id=actor.id,
        )
    )
    await record_audit(
        db, actor=actor, action="asset.return", entity_type="asset",
        entity_id=a.barcode, request=request,
    )
    await db.commit()
    return _asset_out(a)


@assets_router.post("/bulk-delete")
async def bulk_delete_assets(
    body: BulkDeleteBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("delete")),
):
    """Gépek (tömeges) törlése. Kihelyezett gép nem törölhető — előbb vissza
    kell venni (blocked listában jelezzük, a többi törlődik)."""
    ids = _parse_uuid_list(body.ids)
    deleted = 0
    blocked: list[dict] = []
    for aid in ids:
        asset = (
            await db.execute(select(Asset).where(Asset.id == aid))
        ).scalar_one_or_none()
        if asset is None:
            continue
        if asset.status == "deployed":
            blocked.append({"id": str(aid), "name": asset.barcode, "code": "asset.deployed"})
            continue
        await db.execute(sa_delete(AssetMovement).where(AssetMovement.asset_id == aid))
        await db.delete(asset)
        deleted += 1
    await record_audit(
        db, actor=actor, action="asset.bulk_delete", entity_type="asset",
        detail={"deleted": deleted, "blocked": len(blocked)}, request=request,
    )
    await db.commit()
    return {"deleted": deleted, "blocked": blocked}
