"""Árajánlat-kérés → szerződés → online aláírás → automatikus partner.

Publikus oldal (/ajanlat): a leendő vevő megadja a cégadatait, kiválasztja a
gépet a katalógusból (leírás + kiknek ajánljuk), és ajánlatot kér. Mi töltjük
ki az adagárat és a dátumokat, ebből készül a szerződés szövege (sablonból,
{{változókkal}}), majd aláíró-linket küldünk emailben. Az aláírás bármilyen
eszközön (mobil/tablet/gép) megy; aláírás után automatikusan létrejön a
Partner + a PartnerContract — minden szükséges adat az ajánlatkérésből jön.
"""

from __future__ import annotations

import collections
import secrets
import time as _time
import uuid
from datetime import UTC, date, datetime

import base64

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.core.config import get_settings
from app.db import get_db
from app.models import (
    Partner,
    PartnerContract,
    QuoteMachineType,
    QuoteRequest,
    QuoteSettings,
    User,
)
from app.services.wfm.contracts import apply_active_contract
from app.services.wfm.email_service import load_smtp_config, send_email
from app.services.wfm.geocode import schedule_geocode

router = APIRouter()  # /api/quotes — belső kezelés (perm: partners)
public_router = APIRouter()  # /api/public/quotes + /api/public/contract — auth nélkül

# Egyszerű IP-alapú rate limit a publikus végpontokra (mint a support-oldalon).
_WINDOW_SECONDS = 600
_hits: dict[str, list[float]] = collections.defaultdict(list)


def _rate_limited(key: str, limit: int) -> bool:
    now = _time.monotonic()
    hits = [t for t in _hits[key] if now - t < _WINDOW_SECONDS]
    if len(hits) >= limit:
        _hits[key] = hits
        return True
    hits.append(now)
    _hits[key] = hits
    return False


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


async def _next_serial(db: AsyncSession) -> str:
    year = datetime.now(UTC).year
    count = (
        await db.execute(
            select(sa_func.count()).select_from(QuoteRequest).where(
                QuoteRequest.serial.like(f"AJ-{year}-%")
            )
        )
    ).scalar_one()
    return f"AJ-{year}-{count + 1:04d}"


# ---------------------------------------------------------------------------
# Szerződés-sablon
# ---------------------------------------------------------------------------

# Ideiglenes alapértelmezés — a végleges szövegezést a megrendelő adja majd,
# a Beállítható sablon a {{változókkal}} bárhogy átírható.
DEFAULT_TEMPLATE = """BÉRLETI ÉS EGYÜTTMŰKÖDÉSI SZERZŐDÉS
Szerződésszám: {{szerzodes_szam}}

amely létrejött egyrészről az X-Presso Coffee Kft. mint Szolgáltató,
másrészről

Cégnév: {{ceg_nev}}
Adószám: {{adoszam}}
Székhely: {{szekhely}}
Számlázási cím: {{szamlazasi_cim}}
Kapcsolattartó: {{kapcsolattarto}} ({{email}}, {{telefon}})
Bankszámlaszám: {{bankszamla}}

mint Megrendelő között, az alábbi feltételekkel:

1. A Szolgáltató a Megrendelő részére kihelyezi a következő berendezést:
   {{gep}}

2. A kávé elszámolási egységára (adagár): {{adagar}} Ft/adag (nettó).

3. Havi minimum átvétel: {{min_adag}} adag. A minimum el nem érése esetén
   a különbözet elszámolása: {{min_alatti_ar}} Ft/adag (nettó).

4. A szerződés érvényessége: {{ervenyes_tol}} — {{ervenyes_ig}}.

5. A berendezés a Szolgáltató tulajdonában marad; a Megrendelő köteles azt
   rendeltetésszerűen használni.

Kelt: {{datum}}

A jelen szerződést a felek elolvasás és értelmezés után, mint akaratukkal
mindenben megegyezőt, elektronikus aláírással jóváhagyólag elfogadják."""

_HU_MONTHS = [
    "", "január", "február", "március", "április", "május", "június",
    "július", "augusztus", "szeptember", "október", "november", "december",
]


def _hu_date(d: date | None) -> str:
    if d is None:
        return "—"
    return f"{d.year}. {_HU_MONTHS[d.month]} {d.day}."


def _compose_address(zip_: str | None, city: str | None, street: str | None,
                     number: str | None) -> str:
    parts = " ".join(x for x in (street, number) if x)
    return ", ".join(x for x in (f"{zip_ or ''} {city or ''}".strip(), parts) if x)


def _fmt_num(v: float | int | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}".replace(",", " ") if float(v) == int(v) else f"{v:,.2f}".replace(",", " ")


def render_contract(template: str, q: QuoteRequest) -> str:
    address = _compose_address(q.address_zip, q.address_city, q.address_street, q.address_number)
    billing = _compose_address(q.billing_zip, q.billing_city, q.billing_street, q.billing_number)
    values = {
        "szerzodes_szam": q.serial,
        "datum": _hu_date(date.today()),
        "ceg_nev": q.company_name,
        "adoszam": q.tax_number,
        "szekhely": address or "—",
        "szamlazasi_cim": billing or address or "—",
        "kapcsolattarto": q.contact_name,
        "email": q.contact_email,
        "telefon": q.contact_phone,
        "bankszamla": q.bank_account or "—",
        "gep": q.machine_type_name or "—",
        "adagar": _fmt_num(q.price_per_portion),
        "min_adag": _fmt_num(q.min_portions),
        "min_alatti_ar": _fmt_num(q.below_min_price),
        "ervenyes_tol": _hu_date(q.valid_from),
        "ervenyes_ig": _hu_date(q.valid_to) if q.valid_to else "határozatlan idejű",
    }
    text = template
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


async def _get_template(db: AsyncSession) -> str:
    row = (
        await db.execute(select(QuoteSettings).where(QuoteSettings.id == 1))
    ).scalar_one_or_none()
    if row is not None and (row.contract_template or "").strip():
        return row.contract_template
    return DEFAULT_TEMPLATE


# ---------------------------------------------------------------------------
# Kimeneti sémák
# ---------------------------------------------------------------------------


class MachineTypeOut(BaseModel):
    id: str
    name: str
    description: str | None
    ideal_for: str | None
    sort_order: int
    active: bool
    has_image: bool


def _mtype_out(m: QuoteMachineType) -> MachineTypeOut:
    return MachineTypeOut(
        id=str(m.id), name=m.name, description=m.description,
        ideal_for=m.ideal_for, sort_order=m.sort_order, active=m.active,
        has_image=m.image_data is not None,
    )


_IMAGE_MIME = {"image/png", "image/jpeg", "image/webp"}
_MAX_IMAGE_BYTES = 2 * 1024 * 1024


def _decode_image(data_url: str) -> tuple[bytes, str]:
    if not data_url.startswith("data:"):
        raise HTTPException(status_code=422, detail={"code": "quote.bad_image"})
    try:
        header, b64 = data_url.split(",", 1)
        mime = header.split(";")[0].removeprefix("data:").lower()
        raw = base64.b64decode(b64)
    except Exception:
        raise HTTPException(status_code=422, detail={"code": "quote.bad_image"})
    if mime not in _IMAGE_MIME or len(raw) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=422, detail={"code": "quote.bad_image"})
    return raw, mime


class QuoteOut(BaseModel):
    id: str
    serial: str
    status: str
    company_name: str
    contact_name: str
    contact_email: str
    contact_phone: str
    tax_number: str
    address_zip: str
    address_city: str
    address_street: str
    address_number: str | None
    billing_zip: str | None
    billing_city: str | None
    billing_street: str | None
    billing_number: str | None
    bank_account: str | None
    machine_type_id: str | None
    machine_type_name: str | None
    message: str | None
    price_per_portion: float | None
    min_portions: int | None
    below_min_price: float | None
    rent_if_below_min: bool
    valid_from: date | None
    valid_to: date | None
    admin_note: str | None
    contract_text: str | None
    sign_url: str | None
    sent_at: datetime | None
    signed_at: datetime | None
    signed_name: str | None
    partner_id: str | None
    created_at: datetime


def _sign_url(q: QuoteRequest) -> str | None:
    if not q.sign_token:
        return None
    return f"{get_settings().frontend_origin.rstrip('/')}/szerzodes/{q.sign_token}"


def _quote_out(q: QuoteRequest) -> QuoteOut:
    return QuoteOut(
        id=str(q.id), serial=q.serial, status=q.status,
        company_name=q.company_name, contact_name=q.contact_name,
        contact_email=q.contact_email, contact_phone=q.contact_phone,
        tax_number=q.tax_number, address_zip=q.address_zip,
        address_city=q.address_city, address_street=q.address_street,
        address_number=q.address_number, billing_zip=q.billing_zip,
        billing_city=q.billing_city, billing_street=q.billing_street,
        billing_number=q.billing_number, bank_account=q.bank_account,
        machine_type_id=str(q.machine_type_id) if q.machine_type_id else None,
        machine_type_name=q.machine_type_name, message=q.message,
        price_per_portion=q.price_per_portion, min_portions=q.min_portions,
        below_min_price=q.below_min_price, rent_if_below_min=q.rent_if_below_min,
        valid_from=q.valid_from, valid_to=q.valid_to, admin_note=q.admin_note,
        contract_text=q.contract_text, sign_url=_sign_url(q),
        sent_at=q.sent_at, signed_at=q.signed_at, signed_name=q.signed_name,
        partner_id=str(q.partner_id) if q.partner_id else None,
        created_at=q.created_at,
    )


async def _get_quote_or_404(db: AsyncSession, quote_id: str) -> QuoteRequest:
    try:
        qid = uuid.UUID(quote_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "quote.not_found"})
    q = (
        await db.execute(select(QuoteRequest).where(QuoteRequest.id == qid))
    ).scalar_one_or_none()
    if q is None:
        raise HTTPException(status_code=404, detail={"code": "quote.not_found"})
    return q


# ---------------------------------------------------------------------------
# PUBLIKUS: gép-katalógus + ajánlatkérés beadása
# ---------------------------------------------------------------------------


@public_router.get("/quotes/machine-types", response_model=list[MachineTypeOut])
async def public_machine_types(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(QuoteMachineType)
            .where(QuoteMachineType.active.is_(True))
            .order_by(QuoteMachineType.sort_order, QuoteMachineType.name)
        )
    ).scalars().all()
    return [_mtype_out(m) for m in rows]


@public_router.get("/quotes/machine-types/{type_id}/image")
async def public_machine_type_image(type_id: str, db: AsyncSession = Depends(get_db)):
    try:
        mid = uuid.UUID(type_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "quote.machine_type_not_found"})
    m = (
        await db.execute(select(QuoteMachineType).where(QuoteMachineType.id == mid))
    ).scalar_one_or_none()
    if m is None or m.image_data is None:
        raise HTTPException(status_code=404, detail={"code": "quote.machine_type_not_found"})
    return Response(
        content=bytes(m.image_data),
        media_type=m.image_mime or "image/jpeg",
        headers={"Cache-Control": "public, max-age=3600"},
    )


class PublicQuoteBody(BaseModel):
    company_name: str = Field(min_length=2, max_length=256)
    contact_name: str = Field(min_length=2, max_length=256)
    contact_email: EmailStr
    contact_phone: str = Field(min_length=6, max_length=32)
    tax_number: str = Field(min_length=8, max_length=32)
    address_zip: str = Field(min_length=4, max_length=16)
    address_city: str = Field(min_length=2, max_length=128)
    address_street: str = Field(min_length=2, max_length=256)
    address_number: str | None = Field(default=None, max_length=32)
    billing_zip: str | None = Field(default=None, max_length=16)
    billing_city: str | None = Field(default=None, max_length=128)
    billing_street: str | None = Field(default=None, max_length=256)
    billing_number: str | None = Field(default=None, max_length=32)
    bank_account: str | None = Field(default=None, max_length=64)
    machine_type_id: str | None = None
    message: str | None = Field(default=None, max_length=2000)


@public_router.post("/quotes", status_code=201)
async def public_create_quote(
    body: PublicQuoteBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ip = _client_ip(request)
    if _rate_limited(f"quote:{ip}", 5):
        raise HTTPException(status_code=429, detail={"code": "quote.rate_limited"})

    mtype = None
    if body.machine_type_id:
        try:
            mid = uuid.UUID(body.machine_type_id)
        except ValueError:
            raise HTTPException(status_code=422, detail={"code": "quote.bad_machine_type"})
        mtype = (
            await db.execute(
                select(QuoteMachineType).where(
                    QuoteMachineType.id == mid, QuoteMachineType.active.is_(True)
                )
            )
        ).scalar_one_or_none()
        if mtype is None:
            raise HTTPException(status_code=422, detail={"code": "quote.bad_machine_type"})

    q = QuoteRequest(
        serial=await _next_serial(db),
        company_name=body.company_name.strip(),
        contact_name=body.contact_name.strip(),
        contact_email=str(body.contact_email),
        contact_phone=body.contact_phone.strip(),
        tax_number=body.tax_number.strip(),
        address_zip=body.address_zip.strip(),
        address_city=body.address_city.strip(),
        address_street=body.address_street.strip(),
        address_number=(body.address_number or "").strip() or None,
        billing_zip=(body.billing_zip or "").strip() or None,
        billing_city=(body.billing_city or "").strip() or None,
        billing_street=(body.billing_street or "").strip() or None,
        billing_number=(body.billing_number or "").strip() or None,
        bank_account=(body.bank_account or "").strip() or None,
        machine_type_id=mtype.id if mtype else None,
        machine_type_name=mtype.name if mtype else None,
        message=(body.message or "").strip() or None,
    )
    db.add(q)
    await db.flush()
    await record_audit(
        db, actor=None, action="quote.create", entity_type="quote_request",
        entity_id=str(q.id),
        detail={"serial": q.serial, "company": q.company_name, "ip": ip},
    )
    await db.commit()
    return {"ok": True, "serial": q.serial}


# ---------------------------------------------------------------------------
# PUBLIKUS: szerződés megtekintése + aláírása (sign_token)
# ---------------------------------------------------------------------------


async def _quote_by_token(db: AsyncSession, token: str) -> QuoteRequest:
    if not token or len(token) < 16:
        raise HTTPException(status_code=404, detail={"code": "quote.not_found"})
    q = (
        await db.execute(select(QuoteRequest).where(QuoteRequest.sign_token == token))
    ).scalar_one_or_none()
    if q is None:
        raise HTTPException(status_code=404, detail={"code": "quote.not_found"})
    return q


@public_router.get("/contract/{token}")
async def public_contract(token: str, db: AsyncSession = Depends(get_db)):
    q = await _quote_by_token(db, token)
    return {
        "serial": q.serial,
        "status": q.status,
        "company_name": q.company_name,
        "contact_name": q.contact_name,
        "machine_type_name": q.machine_type_name,
        "contract_text": q.contract_text,
        "signed_at": q.signed_at,
        "signed_name": q.signed_name,
    }


class SignBody(BaseModel):
    signature: str = Field(min_length=100)  # PNG data URL
    signed_name: str = Field(min_length=2, max_length=256)


@public_router.post("/contract/{token}/sign")
async def public_sign_contract(
    token: str,
    body: SignBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ip = _client_ip(request)
    if _rate_limited(f"sign:{ip}", 10):
        raise HTTPException(status_code=429, detail={"code": "quote.rate_limited"})
    if not body.signature.startswith("data:image/png;base64,"):
        raise HTTPException(status_code=422, detail={"code": "quote.bad_signature"})

    q = await _quote_by_token(db, token)
    if q.status == "signed":
        raise HTTPException(status_code=409, detail={"code": "quote.already_signed"})
    if q.status != "sent" or not q.contract_text:
        raise HTTPException(status_code=409, detail={"code": "quote.not_signable"})

    partner = await _create_partner_from_quote(db, q)
    q.status = "signed"
    q.signed_at = datetime.now(UTC)
    q.signature = body.signature
    q.signed_name = body.signed_name.strip()
    q.signed_ip = ip
    q.partner_id = partner.id

    await record_audit(
        db, actor=None, action="quote.signed", entity_type="quote_request",
        entity_id=str(q.id),
        detail={
            "serial": q.serial, "company": q.company_name,
            "signed_name": q.signed_name, "ip": ip,
            "partner_id": str(partner.id), "partner_code": partner.partner_code,
        },
    )
    await db.commit()
    schedule_geocode(partner.id)
    return {"ok": True, "serial": q.serial, "partner_code": partner.partner_code}


async def _create_partner_from_quote(db: AsyncSession, q: QuoteRequest) -> Partner:
    """Aláírt szerződésből Partner + PartnerContract — minden adat az
    ajánlatkérésből jön, a partner-kód sorfolytonosan generálódik."""
    from app.services.wfm.codes import generate_partner_code

    address = _compose_address(q.address_zip, q.address_city, q.address_street, q.address_number)
    has_billing = any((q.billing_zip, q.billing_city, q.billing_street))
    billing = (
        _compose_address(q.billing_zip, q.billing_city, q.billing_street, q.billing_number)
        if has_billing else address
    )
    note_bits = [f"Online ajánlatkérésből: {q.serial}"]
    if q.machine_type_name:
        note_bits.append(f"Kért gép: {q.machine_type_name}")
    if q.price_per_portion is not None:
        note_bits.append(f"Szerződött adagár: {_fmt_num(q.price_per_portion)} Ft/adag (nettó)")
    if q.message:
        note_bits.append(f"Üzenet: {q.message}")

    partner = Partner(
        partner_code=await generate_partner_code(db),
        name=q.company_name,
        company_name=q.company_name,
        partner_type="customer",
        tax_number=q.tax_number,
        contact_name=q.contact_name,
        contact_email=q.contact_email,
        contact_phone=q.contact_phone,
        address=address or None,
        address_zip=q.address_zip,
        address_city=q.address_city,
        address_street=q.address_street,
        address_number=q.address_number,
        billing_address=billing or None,
        billing_zip=q.billing_zip if has_billing else q.address_zip,
        billing_city=q.billing_city if has_billing else q.address_city,
        billing_street=q.billing_street if has_billing else q.address_street,
        billing_number=q.billing_number if has_billing else q.address_number,
        bank_account=q.bank_account,
        notes="\n".join(note_bits),
    )
    db.add(partner)
    await db.flush()

    contract = PartnerContract(
        partner_id=partner.id,
        valid_from=q.valid_from or date.today(),
        valid_to=q.valid_to,
        min_portions=q.min_portions,
        below_min_price=q.below_min_price,
        rent_if_below_min=q.rent_if_below_min,
        note=f"Online aláírt szerződés ({q.serial})",
    )
    db.add(contract)
    await db.flush()
    await apply_active_contract(db, partner)
    return partner


# ---------------------------------------------------------------------------
# BELSŐ: ajánlatok kezelése (perm: partners)
# ---------------------------------------------------------------------------


@router.get("", response_model=list[QuoteOut])
async def list_quotes(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("partners")),
):
    stmt = select(QuoteRequest).order_by(QuoteRequest.created_at.desc())
    if status:
        stmt = stmt.where(QuoteRequest.status == status)
    rows = (await db.execute(stmt)).scalars().all()
    return [_quote_out(q) for q in rows]


class TemplateBody(BaseModel):
    contract_template: str | None = None


@router.get("/template")
async def get_template(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("partners")),
):
    row = (
        await db.execute(select(QuoteSettings).where(QuoteSettings.id == 1))
    ).scalar_one_or_none()
    saved = row.contract_template if row else None
    return {
        "contract_template": saved,
        "default_template": DEFAULT_TEMPLATE,
        "effective": saved if (saved or "").strip() else DEFAULT_TEMPLATE,
    }


@router.put("/template")
async def put_template(
    body: TemplateBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("partners")),
):
    row = (
        await db.execute(select(QuoteSettings).where(QuoteSettings.id == 1))
    ).scalar_one_or_none()
    if row is None:
        row = QuoteSettings(id=1)
        db.add(row)
    row.contract_template = (body.contract_template or "").strip() or None
    await record_audit(
        db, actor=actor, action="quote.template_update", entity_type="quote_settings",
        entity_id="1", request=request,
    )
    await db.commit()
    return {"ok": True}


# Gép-katalógus kezelése


class MachineTypeBody(BaseModel):
    name: str = Field(min_length=2, max_length=256)
    description: str | None = Field(default=None, max_length=4000)
    ideal_for: str | None = Field(default=None, max_length=4000)
    sort_order: int = Field(default=0, ge=0, le=10_000)
    active: bool = True
    image: str | None = None  # data URL — csak megadva változtat
    remove_image: bool = False


@router.get("/machine-types", response_model=list[MachineTypeOut])
async def list_machine_types(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("partners")),
):
    rows = (
        await db.execute(
            select(QuoteMachineType).order_by(
                QuoteMachineType.sort_order, QuoteMachineType.name
            )
        )
    ).scalars().all()
    return [_mtype_out(m) for m in rows]


@router.post("/machine-types", response_model=MachineTypeOut, status_code=201)
async def create_machine_type(
    body: MachineTypeBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("partners")),
):
    data = body.model_dump(exclude={"image", "remove_image"})
    m = QuoteMachineType(**data)
    if body.image:
        m.image_data, m.image_mime = _decode_image(body.image)
    db.add(m)
    await db.flush()
    await record_audit(
        db, actor=actor, action="quote.machine_type_create",
        entity_type="quote_machine_type", entity_id=str(m.id),
        detail={"name": m.name}, request=request,
    )
    await db.commit()
    return _mtype_out(m)


async def _get_mtype_or_404(db: AsyncSession, type_id: str) -> QuoteMachineType:
    try:
        mid = uuid.UUID(type_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "quote.machine_type_not_found"})
    m = (
        await db.execute(select(QuoteMachineType).where(QuoteMachineType.id == mid))
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail={"code": "quote.machine_type_not_found"})
    return m


@router.patch("/machine-types/{type_id}", response_model=MachineTypeOut)
async def update_machine_type(
    type_id: str,
    body: MachineTypeBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("partners")),
):
    m = await _get_mtype_or_404(db, type_id)
    for key, value in body.model_dump(exclude={"image", "remove_image"}).items():
        setattr(m, key, value)
    if body.remove_image:
        m.image_data = None
        m.image_mime = None
    elif body.image:
        m.image_data, m.image_mime = _decode_image(body.image)
    await record_audit(
        db, actor=actor, action="quote.machine_type_update",
        entity_type="quote_machine_type", entity_id=str(m.id),
        detail={"name": m.name}, request=request,
    )
    await db.commit()
    return _mtype_out(m)


@router.delete("/machine-types/{type_id}")
async def delete_machine_type(
    type_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("partners")),
):
    m = await _get_mtype_or_404(db, type_id)
    await record_audit(
        db, actor=actor, action="quote.machine_type_delete",
        entity_type="quote_machine_type", entity_id=str(m.id),
        detail={"name": m.name}, request=request,
    )
    await db.delete(m)
    await db.commit()
    return {"ok": True}


# Ajánlat részletei + feltételek + küldés


@router.get("/{quote_id}", response_model=QuoteOut)
async def get_quote(
    quote_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("partners")),
):
    return _quote_out(await _get_quote_or_404(db, quote_id))


class QuotePatchBody(BaseModel):
    # Vevő-adatok javítása (elgépelés stb.) + a mi feltételeink.
    company_name: str | None = Field(default=None, min_length=2, max_length=256)
    contact_name: str | None = Field(default=None, min_length=2, max_length=256)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, min_length=6, max_length=32)
    tax_number: str | None = Field(default=None, min_length=8, max_length=32)
    address_zip: str | None = Field(default=None, min_length=4, max_length=16)
    address_city: str | None = Field(default=None, min_length=2, max_length=128)
    address_street: str | None = Field(default=None, min_length=2, max_length=256)
    address_number: str | None = Field(default=None, max_length=32)
    billing_zip: str | None = Field(default=None, max_length=16)
    billing_city: str | None = Field(default=None, max_length=128)
    billing_street: str | None = Field(default=None, max_length=256)
    billing_number: str | None = Field(default=None, max_length=32)
    bank_account: str | None = Field(default=None, max_length=64)
    machine_type_name: str | None = Field(default=None, max_length=256)
    price_per_portion: float | None = Field(default=None, ge=0)
    min_portions: int | None = Field(default=None, ge=0, le=1_000_000)
    below_min_price: float | None = Field(default=None, ge=0)
    rent_if_below_min: bool | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    admin_note: str | None = Field(default=None, max_length=4000)
    contract_text: str | None = None


@router.patch("/{quote_id}", response_model=QuoteOut)
async def update_quote(
    quote_id: str,
    body: QuotePatchBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("partners")),
):
    q = await _get_quote_or_404(db, quote_id)
    if q.status == "signed":
        raise HTTPException(status_code=409, detail={"code": "quote.already_signed"})
    data = body.model_dump(exclude_unset=True)
    if (
        data.get("valid_to") is not None
        and (data.get("valid_from") or q.valid_from) is not None
        and data["valid_to"] < (data.get("valid_from") or q.valid_from)
    ):
        raise HTTPException(status_code=422, detail={"code": "contract.bad_dates"})
    for key, value in data.items():
        setattr(q, key, value)
    await record_audit(
        db, actor=actor, action="quote.update", entity_type="quote_request",
        entity_id=str(q.id), detail={"serial": q.serial}, request=request,
    )
    await db.commit()
    return _quote_out(q)


@router.post("/{quote_id}/prepare", response_model=QuoteOut)
async def prepare_contract(
    quote_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("partners")),
):
    """A sablonból legenerálja (újragenerálja) a szerződés szövegét — a
    küldés előtt még szabadon szerkeszthető (PATCH contract_text)."""
    q = await _get_quote_or_404(db, quote_id)
    if q.status == "signed":
        raise HTTPException(status_code=409, detail={"code": "quote.already_signed"})
    if q.price_per_portion is None or q.valid_from is None:
        raise HTTPException(status_code=422, detail={"code": "quote.missing_terms"})
    q.contract_text = render_contract(await _get_template(db), q)
    await record_audit(
        db, actor=actor, action="quote.prepare", entity_type="quote_request",
        entity_id=str(q.id), detail={"serial": q.serial}, request=request,
    )
    await db.commit()
    return _quote_out(q)


@router.post("/{quote_id}/send")
async def send_contract(
    quote_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("partners")),
):
    """Aláíró-link kiküldése emailben. SMTP híján is visszaadja a linket,
    hogy kézzel (más csatornán) el lehessen küldeni."""
    q = await _get_quote_or_404(db, quote_id)
    if q.status == "signed":
        raise HTTPException(status_code=409, detail={"code": "quote.already_signed"})
    if q.status == "cancelled":
        raise HTTPException(status_code=409, detail={"code": "quote.cancelled"})
    if not (q.contract_text or "").strip():
        raise HTTPException(status_code=422, detail={"code": "quote.no_contract"})
    if q.price_per_portion is None or q.valid_from is None:
        raise HTTPException(status_code=422, detail={"code": "quote.missing_terms"})

    if not q.sign_token:
        q.sign_token = secrets.token_urlsafe(32)
    q.status = "sent"
    q.sent_at = datetime.now(UTC)
    sign_url = _sign_url(q)

    email_sent = False
    config = await load_smtp_config(db)
    if config is not None:
        body = (
            f"Tisztelt {q.contact_name}!\n\n"
            f"Elkészült a(z) {q.serial} számú szerződés a(z) {q.company_name} részére.\n"
            "A szerződést az alábbi hivatkozáson tekintheti meg és írhatja alá\n"
            "elektronikusan — telefonon, tableten vagy számítógépen:\n\n"
            f"{sign_url}\n\n"
            "Kérdés esetén válaszoljon erre a levélre.\n\n"
            "Üdvözlettel:\nX-Presso Coffee Kft."
        )
        email_sent = await send_email(
            config, q.contact_email, f"Szerződés aláírása — {q.serial}", body
        )

    await record_audit(
        db, actor=actor, action="quote.send", entity_type="quote_request",
        entity_id=str(q.id),
        detail={"serial": q.serial, "email_sent": email_sent, "to": q.contact_email},
        request=request,
    )
    await db.commit()
    return {"ok": True, "sign_url": sign_url, "email_sent": email_sent}


@router.post("/{quote_id}/cancel", response_model=QuoteOut)
async def cancel_quote(
    quote_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("partners")),
):
    q = await _get_quote_or_404(db, quote_id)
    if q.status == "signed":
        raise HTTPException(status_code=409, detail={"code": "quote.already_signed"})
    q.status = "cancelled"
    q.sign_token = None  # a kiküldött link érvénytelenné válik
    await record_audit(
        db, actor=actor, action="quote.cancel", entity_type="quote_request",
        entity_id=str(q.id), detail={"serial": q.serial}, request=request,
    )
    await db.commit()
    return _quote_out(q)
