"""Készlet-nyilvántartás: partnerek + eszközök (gépek) egyedi vonalkóddal.

Eszköz életciklus: raktáron (in_stock) → kihelyezve partnerhez (deployed) →
visszavéve (in_stock). Minden mozgás az asset_movements táblába kerül
(kihelyezési előzmény + audit). A vonalkód egyedi, scannerrel kereshető.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import delete as sa_delete, func, or_, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_role
from app.db import get_db
from app.models import (
    Asset,
    AssetMovement,
    Partner,
    PartnerStock,
    Settlement,
    StockMovement,
    User,
)

router = APIRouter()  # /api/partners
assets_router = APIRouter()  # /api/assets

ASSET_STATUSES = ("in_stock", "deployed", "maintenance", "retired")


# ─── Partnerek ───────────────────────────────────────────────────────────────


PARTNER_TYPES = ("customer", "supplier", "both")


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
    notes: str | None = None
    is_active: bool = True

    @field_validator("partner_type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        if v not in PARTNER_TYPES:
            raise ValueError("partner.bad_type")
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
    notes: str | None
    is_active: bool
    asset_count: int = 0


def _partner_out(p: Partner, asset_count: int = 0) -> PartnerOut:
    return PartnerOut(
        id=str(p.id),
        partner_code=p.partner_code,
        name=p.name,
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


@router.get("", response_model=list[PartnerOut])
async def list_partners(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("manager")),
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


@router.post("", response_model=PartnerOut, status_code=201)
async def create_partner(
    body: PartnerBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
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
    return _partner_out(p)


@router.patch("/{partner_id}", response_model=PartnerOut)
async def update_partner(
    partner_id: str,
    body: PartnerBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    p = await _get_partner_or_404(db, partner_id)
    for key, value in body.resolved().items():
        setattr(p, key, value)
    await record_audit(
        db, actor=actor, action="partner.update", entity_type="partner",
        entity_id=str(p.id), request=request,
    )
    await db.commit()
    return _partner_out(p)


class BulkDeleteBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


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
    actor: User = Depends(require_role("manager")),
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
    location_type: str | None = Field(default=None, max_length=64)
    counter: int | None = Field(default=None, ge=0)
    norm: float | None = Field(default=None, ge=0)
    tangible: bool = False
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
    location_type: str | None = None
    counter: int | None = Field(default=None, ge=0)
    norm: float | None = Field(default=None, ge=0)
    tangible: bool | None = None
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
    norm: float | None
    tangible: bool
    status: str
    partner_id: str | None
    partner_name: str | None
    deployed_at: datetime | None
    notes: str | None
    created_at: datetime
    movements: list[MovementOut] | None = None


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
        location_type=a.location_type,
        counter=a.counter,
        norm=a.norm,
        tangible=a.tangible,
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
    _: User = Depends(require_role("manager")),
):
    return {"barcode": await _next_barcode(db)}


@assets_router.get("", response_model=list[AssetOut])
async def list_assets(
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    partner_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("manager")),
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
        if status not in ASSET_STATUSES:
            raise HTTPException(status_code=422, detail={"code": "asset.bad_status"})
        query = query.where(Asset.status == status)
    if partner_id:
        try:
            query = query.where(Asset.partner_id == uuid.UUID(partner_id))
        except ValueError:
            raise HTTPException(status_code=422, detail={"code": "asset.bad_partner"})

    assets = list((await db.execute(query.limit(1000))).scalars())
    names = await _partner_names(db, {a.partner_id for a in assets})
    return [_asset_out(a, names.get(a.partner_id)) for a in assets]


@assets_router.get("/by-barcode/{barcode}", response_model=AssetOut)
async def asset_by_barcode(
    barcode: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("manager")),
):
    a = (
        await db.execute(select(Asset).where(Asset.barcode == barcode.strip()))
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail={"code": "asset.barcode_not_found"})
    names = await _partner_names(db, {a.partner_id})
    return _asset_out(a, names.get(a.partner_id))


@assets_router.get("/{asset_id}", response_model=AssetOut)
async def get_asset(
    asset_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("manager")),
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
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
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
        location_type=(body.location_type or "").strip() or None,
        counter=body.counter,
        norm=body.norm,
        tangible=body.tangible,
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
    return _asset_out(a)


@assets_router.patch("/{asset_id}", response_model=AssetOut)
async def update_asset(
    asset_id: str,
    body: AssetPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    a = await _get_asset_or_404(db, asset_id)
    data = body.model_dump(exclude_unset=True)

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

    for key, value in data.items():
        setattr(a, key, value.strip() if isinstance(value, str) and key != "notes" else value)
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
    actor: User = Depends(require_role("manager")),
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


@assets_router.post("/{asset_id}/return", response_model=AssetOut)
async def return_asset(
    asset_id: str,
    body: ReturnBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
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
    actor: User = Depends(require_role("manager")),
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
