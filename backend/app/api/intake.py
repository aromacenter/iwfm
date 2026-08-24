"""Gép-átvétel (Átvétel menü): az ügyfél behozott gépének rögzítése
tartozékokkal/hibákkal + nyomtatható átvételi elismervény (AT-ÉÉÉÉ-NNNN).

A lap alján a 60 napos tárolási záradék KÖTELEZŐEN megjelenik — a szövege a
Beállítások → Munkalap kártyán szerkeszthető (üresen a beépített alapszöveg).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.db import get_db
from app.models import Asset, MachineIntake, Partner, User, WorksheetSettings
from app.services.wfm.worksheet_pdf import DEFAULT_INTAKE_FOOTER, build_intake_pdf

router = APIRouter()


class IntakeBody(BaseModel):
    asset_id: str
    partner_id: str | None = None
    client_name: str | None = Field(default=None, max_length=256)
    client_company: str | None = Field(default=None, max_length=256)
    client_phone: str | None = Field(default=None, max_length=64)
    client_email: str | None = Field(default=None, max_length=320)
    client_address: str | None = Field(default=None, max_length=512)
    accessories: str | None = Field(default=None, max_length=4000)
    faults: str | None = Field(default=None, max_length=8000)
    note: str | None = Field(default=None, max_length=4000)


class IntakeOut(BaseModel):
    id: str
    serial: str
    asset_id: str | None
    asset_name: str | None
    asset_manufacturer: str | None
    asset_serial: str | None
    asset_barcode: str | None
    partner_name: str | None
    client_name: str | None
    client_company: str | None
    client_phone: str | None
    client_email: str | None
    client_address: str | None
    accessories: str | None
    faults: str | None
    note: str | None
    received_by_name: str | None
    received_at: datetime


async def _next_serial(db: AsyncSession) -> str:
    year = datetime.now(UTC).year
    count = (
        await db.execute(
            select(sa_func.count()).select_from(MachineIntake).where(
                MachineIntake.serial.like(f"AT-{year}-%")
            )
        )
    ).scalar_one()
    return f"AT-{year}-{count + 1:04d}"


async def _out_rows(db: AsyncSession, rows: list[MachineIntake]) -> list[IntakeOut]:
    asset_ids = {r.asset_id for r in rows if r.asset_id}
    assets: dict[uuid.UUID, Asset] = {}
    if asset_ids:
        assets = {
            a.id: a
            for a in (
                await db.execute(select(Asset).where(Asset.id.in_(asset_ids)))
            ).scalars()
        }
    partner_ids = {r.partner_id for r in rows if r.partner_id}
    partners: dict[uuid.UUID, str] = {}
    if partner_ids:
        partners = {
            pid: name
            for pid, name in (
                await db.execute(
                    select(Partner.id, Partner.name).where(Partner.id.in_(partner_ids))
                )
            ).all()
        }
    out = []
    for r in rows:
        a = assets.get(r.asset_id) if r.asset_id else None
        out.append(IntakeOut(
            id=str(r.id),
            serial=r.serial,
            asset_id=str(r.asset_id) if r.asset_id else None,
            asset_name=a.name if a else None,
            asset_manufacturer=a.manufacturer if a else None,
            asset_serial=a.serial_number if a else None,
            asset_barcode=a.barcode if a else None,
            partner_name=partners.get(r.partner_id) if r.partner_id else None,
            client_name=r.client_name,
            client_company=r.client_company,
            client_phone=r.client_phone,
            client_email=r.client_email,
            client_address=r.client_address,
            accessories=r.accessories,
            faults=r.faults,
            note=r.note,
            received_by_name=r.received_by_name,
            received_at=r.received_at,
        ))
    return out


@router.get("", response_model=list[IntakeOut])
async def list_intakes(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("service")),
):
    rows = list(
        (
            await db.execute(
                select(MachineIntake).order_by(MachineIntake.received_at.desc()).limit(200)
            )
        ).scalars()
    )
    return await _out_rows(db, rows)


@router.post("", response_model=IntakeOut, status_code=201)
async def create_intake(
    body: IntakeBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("service")),
):
    try:
        aid = uuid.UUID(body.asset_id)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "intake.bad_asset"})
    asset = (await db.execute(select(Asset).where(Asset.id == aid))).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail={"code": "intake.asset_not_found"})
    partner_id = None
    if body.partner_id:
        try:
            partner_id = uuid.UUID(body.partner_id)
        except ValueError:
            raise HTTPException(status_code=422, detail={"code": "intake.bad_partner"})
    row = MachineIntake(
        id=uuid.uuid4(),
        serial=await _next_serial(db),
        asset_id=asset.id,
        partner_id=partner_id or asset.partner_id,
        client_name=(body.client_name or "").strip() or None,
        client_company=(body.client_company or "").strip() or None,
        client_phone=(body.client_phone or "").strip() or None,
        client_email=(body.client_email or "").strip() or None,
        client_address=(body.client_address or "").strip() or None,
        accessories=(body.accessories or "").strip() or None,
        faults=(body.faults or "").strip() or None,
        note=(body.note or "").strip() or None,
        received_by_name=actor.display_name,
        received_at=datetime.now(UTC),
        created_by=actor.id,
    )
    db.add(row)
    await db.flush()
    await record_audit(
        db, actor=actor, action="intake.create", entity_type="intake",
        entity_id=row.serial, request=request,
    )
    await db.commit()
    out = (await _out_rows(db, [row]))[0]
    # Automatizálás-trigger: "Gép átvéve" (tűz-és-felejt).
    try:
        from app.services.wfm.automation import fire_event

        fire_event("intake.created", {
            "sorszam": out.serial,
            "ugyfel": out.client_name or out.partner_name or "",
            "gep_nev": out.asset_name or "",
            "gep_vonalkod": out.asset_barcode or "",
            "hibak": out.faults or "",
            "atvette": out.received_by_name or "",
        })
    except Exception:
        pass
    return out


@router.get("/{intake_id}/pdf")
async def intake_pdf(
    intake_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("service")),
):
    try:
        iid = uuid.UUID(intake_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "intake.not_found"})
    row = (
        await db.execute(select(MachineIntake).where(MachineIntake.id == iid))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "intake.not_found"})
    out = (await _out_rows(db, [row]))[0]

    from app.api.tasks import _worksheet_pdf_settings

    settings = await _worksheet_pdf_settings(db)
    clause = (settings or {}).get("intake_footer_text") or DEFAULT_INTAKE_FOOTER
    pdf = build_intake_pdf(
        {
            "serial": row.serial,
            "received_at": f"{row.received_at:%Y-%m-%d %H:%M}",
            "client_name": out.client_name or out.partner_name,
            "client_company": out.client_company,
            "client_phone": out.client_phone,
            "client_email": out.client_email,
            "client_address": out.client_address,
            "asset_name": out.asset_name,
            "asset_manufacturer": out.asset_manufacturer,
            "asset_serial": out.asset_serial,
            "asset_barcode": out.asset_barcode,
            "accessories": out.accessories,
            "faults": out.faults,
            "note": out.note,
            "received_by_name": out.received_by_name,
            "footer_clause": clause,
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
        },
        settings,
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{row.serial}.pdf"'},
    )


@router.delete("/{intake_id}", status_code=204)
async def delete_intake(
    intake_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("delete")),
):
    try:
        iid = uuid.UUID(intake_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "intake.not_found"})
    row = (
        await db.execute(select(MachineIntake).where(MachineIntake.id == iid))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "intake.not_found"})
    await db.delete(row)
    await record_audit(
        db, actor=actor, action="intake.delete", entity_type="intake",
        entity_id=row.serial, request=request,
    )
    await db.commit()
    return Response(status_code=204)
