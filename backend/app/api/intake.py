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
from app.models import Asset, IntakePhoto, MachineIntake, Partner, User, WorksheetSettings
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
    # állapot-fotók a gépről (data-URL, a felület ~1600px-re kicsinyíti)
    photos: list[str] = Field(default_factory=list, max_length=8)


_PHOTO_MAX = 5 * 1024 * 1024  # 5 MB / kép


def _decode_photo(data_url: str) -> tuple[bytes, str]:
    import base64

    if not data_url.startswith("data:image/"):
        raise HTTPException(status_code=422, detail={"code": "intake.bad_photo"})
    try:
        header, payload = data_url.split(",", 1)
        mime = header.split(";")[0].removeprefix("data:")
        raw = base64.b64decode(payload)
    except Exception:
        raise HTTPException(status_code=422, detail={"code": "intake.bad_photo"})
    if len(raw) > _PHOTO_MAX:
        raise HTTPException(status_code=422, detail={"code": "intake.photo_too_large"})
    return raw, mime[:32]


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
    photo_count: int = 0
    # Már kiadott munkalap: az átvétel-listán a "Munkalap" gomb helyett a kész
    # munkalap (átadási papír) nyílik — nem adható ki még egyszer.
    task_id: str | None = None
    worksheet_serial: str | None = None
    worksheet_completed: bool = False


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
    photo_counts: dict[uuid.UUID, int] = {}
    if rows:
        photo_counts = {
            iid: cnt
            for iid, cnt in (
                await db.execute(
                    select(IntakePhoto.intake_id, sa_func.count())
                    .where(IntakePhoto.intake_id.in_([r.id for r in rows]))
                    .group_by(IntakePhoto.intake_id)
                )
            ).all()
        }
    # Az átvételből már kiadott munkalap-feladatok (a legutóbbi számít)
    tasks_by_intake: dict[uuid.UUID, tuple[uuid.UUID, str | None, bool]] = {}
    if rows:
        from app.models import Task, Worksheet

        task_rows = (
            await db.execute(
                select(Task.id, Task.intake_id, Worksheet.serial, Worksheet.work_description)
                .outerjoin(Worksheet, Worksheet.task_id == Task.id)
                .where(Task.intake_id.in_([r.id for r in rows]))
                .order_by(Task.created_at)
            )
        ).all()
        for tid, iid, ws_serial, ws_desc in task_rows:
            tasks_by_intake[iid] = (tid, ws_serial, bool((ws_desc or "").strip()))
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
            photo_count=photo_counts.get(r.id, 0),
            task_id=str(tasks_by_intake[r.id][0]) if r.id in tasks_by_intake else None,
            worksheet_serial=tasks_by_intake[r.id][1] if r.id in tasks_by_intake else None,
            worksheet_completed=tasks_by_intake[r.id][2] if r.id in tasks_by_intake else False,
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
    for data_url in body.photos[:8]:
        raw, mime = _decode_photo(data_url)
        db.add(IntakePhoto(intake_id=row.id, image=raw, mime=mime))
    await record_audit(
        db, actor=actor, action="intake.create", entity_type="intake",
        entity_id=row.serial, detail={"photos": len(body.photos)}, request=request,
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


async def _get_intake_or_404(db: AsyncSession, intake_id: str) -> MachineIntake:
    try:
        iid = uuid.UUID(intake_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "intake.not_found"})
    row = (
        await db.execute(select(MachineIntake).where(MachineIntake.id == iid))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "intake.not_found"})
    return row


async def _build_intake_pdf(db: AsyncSession, row: MachineIntake) -> bytes:
    """Az átvételi elismervény PDF-je — a letöltés és az irodai nyomtatási
    sor (nyomtató-ügynök) közös építője."""
    out = (await _out_rows(db, [row]))[0]

    from app.api.tasks import _worksheet_pdf_settings

    settings = await _worksheet_pdf_settings(db)
    clause = (settings or {}).get("intake_footer_text") or DEFAULT_INTAKE_FOOTER
    return build_intake_pdf(
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


@router.get("/{intake_id}/pdf")
async def intake_pdf(
    intake_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("service")),
):
    row = await _get_intake_or_404(db, intake_id)
    pdf = await _build_intake_pdf(db, row)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{row.serial}.pdf"'},
    )


@router.post("/{intake_id}/print")
async def intake_print(
    intake_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("service")),
):
    """Elismervény az irodai nyomtatóra: PDF a nyomtatási sorba — a helyi
    nyomtató-ügynök kinyomtatja, így TELEFONRÓL is megy a nyomtatás."""
    import base64

    from app.models import PrintJob

    row = await _get_intake_or_404(db, intake_id)
    pdf = await _build_intake_pdf(db, row)
    job = PrintJob(
        kind="pdf",
        label=f"Elismervény {row.serial}",
        payload=base64.b64encode(pdf).decode("ascii"),
        created_by=actor.id,
    )
    db.add(job)
    await record_audit(
        db, actor=actor, action="intake.print", entity_type="intake",
        entity_id=row.serial, request=request,
    )
    await db.commit()
    return {"ok": True, "job_id": str(job.id)}


@router.get("/{intake_id}/photos")
async def intake_photos(
    intake_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("service")),
):
    """Az átvételhez tartozó fotók azonosítói — a galéria ebből épül."""
    try:
        iid = uuid.UUID(intake_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "intake.not_found"})
    rows = (
        await db.execute(
            select(IntakePhoto.id)
            .where(IntakePhoto.intake_id == iid)
            .order_by(IntakePhoto.created_at)
        )
    ).scalars().all()
    return [{"id": str(pid)} for pid in rows]


@router.get("/{intake_id}/photos/{photo_id}")
async def intake_photo(
    intake_id: str,
    photo_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("service")),
):
    try:
        iid = uuid.UUID(intake_id)
        pid = uuid.UUID(photo_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "intake.not_found"})
    p = (
        await db.execute(
            select(IntakePhoto).where(
                IntakePhoto.id == pid, IntakePhoto.intake_id == iid
            )
        )
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail={"code": "intake.not_found"})
    return Response(content=bytes(p.image), media_type=p.mime or "image/jpeg")


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
    # fotók explicit takarítása (SQLite-on nincs FK-cascade garancia)
    from sqlalchemy import delete as sa_delete

    await db.execute(sa_delete(IntakePhoto).where(IntakePhoto.intake_id == row.id))
    await db.delete(row)
    await record_audit(
        db, actor=actor, action="intake.delete", entity_type="intake",
        entity_id=row.serial, request=request,
    )
    await db.commit()
    return Response(status_code=204)
