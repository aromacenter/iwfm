"""Gép-kihelyezési / visszavételi / csere jegyzőkönyvek (121. kör, x-presso jegy
f8e12fda). A deploy / return / swap végpont hozza létre (inventory.py),
innen listázható, letölthető (A4 PDF) és küldhető a partnernek e-mailben.
Helyszíni készítésnél a partner mobilon aláír: a név és az időbélyeg a lapra kerül."""

from __future__ import annotations

import logging
import unicodedata
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.db import get_db
from app.models import Asset, AssetProtocol, Partner, User

logger = logging.getLogger(__name__)
router = APIRouter()  # /api/asset-protocols

PREFIX = {"deploy": "KH", "return": "VV", "swap": "CS"}


class ProtocolFields(BaseModel):
    """A deploy / return / swap kérés opcionális jegyzőkönyv-adatai."""

    on_site: bool = False
    signer_name: str | None = Field(default=None, max_length=128)
    signature: str | None = None  # PNG data URL (partner, mobilon)
    employee_signature: str | None = None
    location: str | None = Field(default=None, max_length=512)
    counters: list[int] | None = Field(default=None, max_length=99)  # átadáskori állások (deploy/return)
    note: str | None = Field(default=None, max_length=2000)
    send_email: bool = False
    # 152. kör: az átadás módja — személyesen vagy futárszolgálattal (+ futár / csomagszám)
    delivery_mode: str | None = Field(default=None, pattern="^(personal|courier)$")
    delivery_ref: str | None = Field(default=None, max_length=128)


class ProtocolOut(BaseModel):
    id: str
    serial: str
    kind: str
    asset_id: str
    asset_barcode: str | None
    asset_name: str | None
    replacement_barcode: str | None = None
    partner_id: str | None
    partner_name: str | None
    location: str | None
    on_site: bool
    signer_name: str | None
    signed_at: datetime | None
    created_by_name: str | None
    created_at: datetime
    emailed_at: datetime | None
    emailed_to: str | None
    note: str | None
    # 152. kör: gépcsere-lista
    asset_serial: str | None = None
    replacement_name: str | None = None
    replacement_serial: str | None = None
    delivery_mode: str | None = None  # personal | courier | None (nincs jelölve)
    delivery_ref: str | None = None


def _local(dt: datetime | None) -> str:
    if dt is None:
        return ""
    from zoneinfo import ZoneInfo

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ZoneInfo("Europe/Budapest")).strftime("%Y-%m-%d %H:%M")


async def next_serial(db: AsyncSession, kind: str) -> str:
    year = datetime.now(UTC).year
    prefix = f"{PREFIX.get(kind, 'JK')}-{year}-"
    n = (
        await db.execute(
            select(func.count()).select_from(AssetProtocol).where(AssetProtocol.serial.like(prefix + "%"))
        )
    ).scalar_one()
    return f"{prefix}{int(n) + 1:04d}"


def _partner_address(p: Partner | None) -> str | None:
    if p is None:
        return None
    return p.address or " ".join(x for x in (p.address_zip, p.address_city, p.address_street, p.address_number) if x) or None


async def create_protocol(
    db: AsyncSession,
    *,
    kind: str,
    asset: Asset,
    partner: Partner | None,
    actor: User,
    fields: ProtocolFields | None,
    replacement: Asset | None = None,
    counters: list[int] | None = None,
    replacement_counters: list[int] | None = None,
    note: str | None = None,
) -> AssetProtocol:
    """Jegyzőkönyv-sor a mozgáshoz (pillanatképekkel) — a hívó commitol."""
    f = fields or ProtocolFields()
    now = datetime.now(UTC)
    signed = bool(f.on_site and (f.signature or f.employee_signature))
    row = AssetProtocol(
        serial=await next_serial(db, kind),
        kind=kind,
        asset_id=asset.id,
        replacement_asset_id=replacement.id if replacement is not None else None,
        partner_id=partner.id if partner is not None else None,
        partner_name=partner.name if partner is not None else None,
        partner_address=_partner_address(partner),
        partner_tax_number=partner.tax_number if partner is not None else None,
        partner_contact=(partner.contact_name if partner is not None else None),
        location=(f.location or "").strip() or _partner_address(partner),
        counters=list(counters if counters is not None else (f.counters or asset.counters or ([asset.counter] if asset.counter is not None else []))),
        counter_names=list(asset.counter_names or []),
        replacement_counters=list(replacement_counters or (replacement.counters if replacement is not None and replacement.counters else [])),
        replacement_counter_names=list(replacement.counter_names or []) if replacement is not None else None,
        asset_snapshot={
            "name": asset.name, "manufacturer": asset.manufacturer, "model": asset.model,
            "serial_number": asset.serial_number, "barcode": asset.barcode,
        },
        replacement_snapshot=(
            {"name": replacement.name, "manufacturer": replacement.manufacturer, "model": replacement.model,
             "serial_number": replacement.serial_number, "barcode": replacement.barcode}
            if replacement is not None else None
        ),
        on_site=bool(f.on_site),
        signer_name=(f.signer_name or "").strip()[:128] or None,
        signature=f.signature if f.on_site else None,
        employee_signature=f.employee_signature if f.on_site else None,
        signed_at=now if signed else None,
        note=((f.note or note or "").strip() or None),
        delivery_mode=f.delivery_mode,
        delivery_ref=(f.delivery_ref or "").strip()[:128] or None,
        created_by_user_id=actor.id,
        created_by_name=actor.display_name or actor.email,
        created_at=now,
    )
    db.add(row)
    await db.flush()
    return row


def _out(p: AssetProtocol) -> ProtocolOut:
    snap = p.asset_snapshot or {}
    rsnap = p.replacement_snapshot or {}
    return ProtocolOut(
        id=str(p.id), serial=p.serial, kind=p.kind, asset_id=str(p.asset_id),
        asset_barcode=snap.get("barcode"), asset_name=snap.get("name"),
        replacement_barcode=rsnap.get("barcode") or None,
        partner_id=str(p.partner_id) if p.partner_id else None, partner_name=p.partner_name,
        location=p.location, on_site=bool(p.on_site), signer_name=p.signer_name, signed_at=p.signed_at,
        created_by_name=p.created_by_name, created_at=p.created_at, emailed_at=p.emailed_at,
        emailed_to=p.emailed_to, note=p.note,
        asset_serial=snap.get("serial_number") or None,
        replacement_name=rsnap.get("name") or None,
        replacement_serial=rsnap.get("serial_number") or None,
        delivery_mode=p.delivery_mode, delivery_ref=p.delivery_ref,
    )


def _fold(text: str | None) -> str:
    """Kereséshez: kisbetű, ékezet nélkül."""
    norm = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(ch for ch in norm if unicodedata.category(ch) != "Mn")


async def _get_or_404(db: AsyncSession, protocol_id: str) -> AssetProtocol:
    try:
        pid = uuid.UUID(protocol_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "protocol.not_found"})
    row = (await db.execute(select(AssetProtocol).where(AssetProtocol.id == pid))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "protocol.not_found"})
    return row


async def build_pdf(db: AsyncSession, p: AssetProtocol) -> bytes:
    from app.api.tasks import _worksheet_pdf_settings
    from app.services.wfm.protocol_pdf import build_protocol_pdf

    settings = await _worksheet_pdf_settings(db) or {}
    return build_protocol_pdf(
        {
            "serial": p.serial, "kind": p.kind, "created_at": _local(p.created_at),
            "company_name": settings.get("company_name"), "company_address": settings.get("company_address"),
            "partner_name": p.partner_name, "partner_address": p.partner_address,
            "partner_tax_number": p.partner_tax_number, "partner_contact": p.partner_contact,
            "asset": p.asset_snapshot or {}, "counters": p.counters, "counter_names": p.counter_names,
            "replacement": p.replacement_snapshot, "replacement_counters": p.replacement_counters,
            "replacement_counter_names": p.replacement_counter_names,
            "location": p.location, "note": p.note, "created_by_name": p.created_by_name,
            "on_site": bool(p.on_site), "signer_name": p.signer_name, "signature": p.signature,
            "employee_signature": p.employee_signature, "signed_at": _local(p.signed_at) if p.signed_at else None,
            "delivery_mode": p.delivery_mode, "delivery_ref": p.delivery_ref,
            "generated_at": _local(datetime.now(UTC)),
        },
        settings,
    )


async def send_protocol_email(db: AsyncSession, p: AssetProtocol, to: list[str] | None = None) -> list[str]:
    """A jegyzőkönyv PDF-je a partner beállított címeire (vagy a megadottra).
    Visszaadja, hova ment; üres lista = nem ment (nincs SMTP / nincs cím)."""
    from app.api.tasks import _worksheet_pdf_settings
    from app.services.wfm.email_service import load_smtp_config, send_email, split_addresses

    recipients = list(to or [])
    if not recipients and p.partner_id:
        partner = (await db.execute(select(Partner).where(Partner.id == p.partner_id))).scalar_one_or_none()
        if partner is not None:
            recipients = [e for e in split_addresses(partner.contact_email) if "@" in e]
    if not recipients:
        return []
    config = await load_smtp_config(db)
    if config is None:
        return []
    pdf = await build_pdf(db, p)
    settings = await _worksheet_pdf_settings(db) or {}
    company = settings.get("company_name") or "X-admin"
    from app.services.wfm.protocol_pdf import KIND_LABEL

    body = (
        f"Tisztelt Partnerünk!\n\n"
        f"Mellékelten küldjük a(z) {p.serial} számú {KIND_LABEL.get(p.kind, '')} jegyzőkönyvet "
        f"({(p.asset_snapshot or {}).get('name') or ''} · {(p.asset_snapshot or {}).get('barcode') or ''}), "
        f"amely a kihelyezési / bérleti szerződés melléklete.\n"
        + (f"A jegyzőkönyvet {p.signer_name or 'a partner képviselője'} a helyszínen, {_local(p.signed_at)}-kor írta alá.\n" if p.signed_at else "")
        + f"\nÜdvözlettel,\n{company}"
    )
    sent: list[str] = []
    for addr in recipients:
        if await send_email(config, addr, f"{TITLE_SUBJECT.get(p.kind, 'Jegyzőkönyv')} – {p.serial}", body,
                            attachments=[(f"{p.serial}.pdf", pdf, "application", "pdf")]):
            sent.append(addr)
    if sent:
        p.emailed_at = datetime.now(UTC)
        p.emailed_to = ", ".join(sent)[:320]
    return sent


TITLE_SUBJECT = {"deploy": "Kihelyezési jegyzőkönyv", "return": "Visszavételi jegyzőkönyv", "swap": "Gépcsere jegyzőkönyv"}


@router.get("", response_model=list[ProtocolOut])
async def list_protocols(
    partner_id: str | None = Query(default=None),
    asset_id: str | None = Query(default=None),
    kind: str | None = Query(default=None, pattern="^(deploy|return|swap)$"),
    search: str | None = Query(default=None, max_length=128, alias="q"),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    delivery_mode: str | None = Query(default=None, pattern="^(personal|courier|unset)$"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("machines")),
):
    """Jegyzőkönyvek listája. 152. kör: típus-, dátum- és átadási mód szűrő +
    szabad szavas keresés (sorszám, partner, gép vonalkódja / neve / gyári
    száma, rögzítő, megjegyzés, futár) — a Gépcserék oldal ebből dolgozik."""
    q = select(AssetProtocol).order_by(AssetProtocol.created_at.desc())
    for col, val in ((AssetProtocol.partner_id, partner_id), (AssetProtocol.asset_id, asset_id)):
        if val:
            try:
                q = q.where(col == uuid.UUID(val))
            except ValueError:
                raise HTTPException(status_code=422, detail={"code": "protocol.bad_filter"})
    if kind:
        q = q.where(AssetProtocol.kind == kind)
    if date_from:
        q = q.where(AssetProtocol.created_at >= datetime(date_from.year, date_from.month, date_from.day, tzinfo=UTC))
    if date_to:
        end = datetime(date_to.year, date_to.month, date_to.day, tzinfo=UTC) + timedelta(days=1)
        q = q.where(AssetProtocol.created_at < end)
    if delivery_mode == "unset":
        q = q.where(AssetProtocol.delivery_mode.is_(None))
    elif delivery_mode:
        q = q.where(AssetProtocol.delivery_mode == delivery_mode)
    rows = [_out(p) for p in (await db.execute(q.limit(3000))).scalars().all()]
    needle = _fold(search).strip()
    if needle:
        words = needle.split()

        def hit(r: ProtocolOut) -> bool:
            hay = _fold(" ".join(str(x or "") for x in (
                r.serial, r.partner_name, r.asset_barcode, r.asset_name, r.asset_serial,
                r.replacement_barcode, r.replacement_name, r.replacement_serial,
                r.created_by_name, r.note, r.delivery_ref, r.location, r.signer_name,
            )))
            return all(w in hay for w in words)

        rows = [r for r in rows if hit(r)]
    return rows[:500]


class ProtocolPatch(BaseModel):
    delivery_mode: str | None = Field(default=None, pattern="^(personal|courier)$")
    delivery_ref: str | None = Field(default=None, max_length=128)


@router.patch("/{protocol_id}", response_model=ProtocolOut)
async def patch_protocol(
    protocol_id: str,
    body: ProtocolPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("machines")),
):
    """Az átadás módjának utólagos jelölése / javítása (a korábbi, jelöletlen
    gépcseréknél is). A jegyzőkönyv többi adata pillanatkép, nem módosítható."""
    p = await _get_or_404(db, protocol_id)
    sent = body.model_fields_set
    if "delivery_mode" in sent:
        p.delivery_mode = body.delivery_mode
    if "delivery_ref" in sent:
        p.delivery_ref = (body.delivery_ref or "").strip()[:128] or None
    await record_audit(db, actor=actor, action="asset.protocol_delivery", entity_type="asset_protocol",
                       entity_id=p.serial, detail={"mode": p.delivery_mode, "ref": p.delivery_ref}, request=request)
    await db.commit()
    return _out(p)


@router.get("/{protocol_id}", response_model=ProtocolOut)
async def get_protocol(protocol_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(require_perm("machines"))):
    return _out(await _get_or_404(db, protocol_id))


@router.get("/{protocol_id}/pdf")
async def protocol_pdf(protocol_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(require_perm("machines"))):
    p = await _get_or_404(db, protocol_id)
    pdf = await build_pdf(db, p)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{p.serial}.pdf"'})


class SendBody(BaseModel):
    to: EmailStr | None = None  # None → a partner beállított címei


@router.post("/{protocol_id}/send", response_model=ProtocolOut)
async def protocol_send(
    protocol_id: str,
    body: SendBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("machines")),
):
    p = await _get_or_404(db, protocol_id)
    sent = await send_protocol_email(db, p, [str(body.to)] if body.to else None)
    if not sent:
        raise HTTPException(status_code=422, detail={"code": "protocol.not_sent"})
    await record_audit(db, actor=actor, action="asset.protocol_sent", entity_type="asset_protocol",
                       entity_id=p.serial, detail={"to": sent}, request=request)
    await db.commit()
    return _out(p)
