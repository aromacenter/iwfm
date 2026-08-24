"""Feladatok (kiosztás + dolgozói visszajelzés).

Manager oldal: CRUD + kiosztás (opcionális skill-követelménnyel — a későbbi
AI-alapú kiosztás előkészítése). Dolgozói oldal (me): saját feladatok,
komment írása, státusz: 'done' (befejezett) / 'needs_more_work'
(további munkát igényel).
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    get_own_employee,
    record_audit,
    require_perm,
)
from app.db import get_db
from app.models import (
    Asset,
    AuditEvent,
    Employee,
    Partner,
    Skill,
    Task,
    TaskComment,
    User,
    Worksheet,
    WorksheetSettings,
)
from app.services.wfm.ai_assign import suggest_assignee
from app.services.wfm.ai_service import generate_from_image
from app.services.wfm.email_service import load_smtp_config, send_email
from app.services.wfm.worksheet_pdf import DEFAULT_CUSTOMER_FOOTER, build_worksheet_pdf

router = APIRouter()
me_router = APIRouter()  # /api/me/tasks alá kerül
quote_public_router = APIRouter()  # /api/public/worksheet-quote — auth nélkül

EMPLOYEE_STATUSES = ("done", "needs_more_work")
ALL_STATUSES = ("open", "done", "needs_more_work")


# ─── Schemas ─────────────────────────────────────────────────────────────────


class TaskCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=4000)
    # None = az AI automatikusan jelöli ki a legalkalmasabb dolgozót
    employee_id: str | None = None
    due_date: date
    required_skill_id: int | None = None
    # Online munkalap fejléc-adatai — a feladattal együtt áll ki a munkalap.
    client_name: str | None = Field(default=None, max_length=256)
    client_location: str | None = Field(default=None, max_length=512)
    # Külső szerviznek átadott gép munkalapja (KSZ-sorszám, külön nézet;
    # a visszaigazolt nettó költségek belsők, az ügyfél a -1-es példányt
    # kapja a mi szerviz-árainkkal).
    external_service: bool = False
    # A munkalap tárgy-gépe: ebből jön a karbantartási díj alapértéke és az
    # aláírás utáni auto-számlázás partnere.
    asset_id: str | None = None


class TaskPatchBody(BaseModel):
    model_config = {"extra": "forbid"}
    title: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None
    employee_id: str | None = None
    due_date: date | None = None
    required_skill_id: int | None = None
    status: str | None = None  # manager bármit állíthat (pl. visszanyitás)


class CommentBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class StatusBody(BaseModel):
    status: str  # done | needs_more_work
    comment: str | None = Field(default=None, max_length=1000)


class CommentOut(BaseModel):
    id: str
    author_name: str | None
    text: str
    created_at: datetime


class TaskOut(BaseModel):
    id: str
    title: str
    description: str | None
    employee_id: str
    employee_name: str | None
    due_date: date
    required_skill: dict | None
    status: str
    comments: list[CommentOut]
    created_at: datetime
    worksheet_serial: str | None = None  # ML-2026-0001, ha van munkalap
    worksheet_completed: bool = False  # kitöltötte-e már a dolgozó
    worksheet_external: bool = False  # külső szerviznek átadott gép munkalapja
    asset: dict | None = None  # a munkalaphoz kötött gép adatai (KSZ)
    ai_reason: str | None = None  # csak létrehozáskor, ha az AI jelölte ki


MAX_SIGNATURE_BYTES = 300_000  # ~300KB data URL-enként


class MaterialItem(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    qty: str = Field(default="", max_length=32)
    unit: str = Field(default="db", max_length=16)
    # Külső szervizes munkalapon: a szerviz nettó költsége (BELSŐ adat) és a
    # mi ügyfél-áraink (ez kerül az ügyfél -1-es példányára).
    cost_net: float | None = Field(default=None, ge=0)
    price_net: float | None = Field(default=None, ge=0)


class WorkItem(BaseModel):
    """Elvégzett munka egy sora. KSZ-en a cost_net a szerviz belső díja
    (SOHA nem kerül az ügyfél elé), a price_net a MI árunk az ügyfél-példányra
    — utóbbit a képviselő állítja be az ár-szerkesztőben."""

    name: str = Field(min_length=1, max_length=256)
    cost_net: float | None = Field(default=None, ge=0)
    price_net: float | None = Field(default=None, ge=0)


class WorksheetBody(BaseModel):
    work_description: str = Field(default="", max_length=8000)
    works: list[WorkItem] = Field(default_factory=list, max_length=25)
    # Javítási konstrukciók: alternatív ajánlatok árral (nem összegződnek).
    repair_options: list[WorkItem] = Field(default_factory=list, max_length=25)
    materials: list[MaterialItem] = Field(default_factory=list, max_length=25)
    hours_spent: float | None = Field(default=None, ge=0, le=1000)
    client_name: str | None = Field(default=None, max_length=256)
    client_location: str | None = Field(default=None, max_length=512)
    employee_signature: str | None = None
    client_signature: str | None = None
    # Karbantartási díj (nettó, a gép díjából előtöltve — átírható) és a
    # kedvezmény-pipa (elengedve → nem készül automatikus számla).
    maintenance_fee: float | None = Field(default=None, ge=0)
    fee_discount: bool | None = None  # None = nem nyúlunk hozzá
    # Az ügyfél-példány (−1) megjegyzése — None = nem nyúlunk hozzá.
    customer_note: str | None = Field(default=None, max_length=8000)


class WorksheetOut(BaseModel):
    serial: str
    external_service: bool = False
    work_description: str
    works: list[WorkItem] = []
    repair_options: list[WorkItem] = []
    materials: list[MaterialItem]
    hours_spent: float | None
    client_name: str | None
    client_location: str | None
    has_employee_signature: bool
    has_client_signature: bool
    # A mentett aláírás-képek (data URL) — újranyitáskor a felület megjeleníti
    # őket, hogy ne tűnjenek "elveszettnek".
    employee_signature: str | None
    client_signature: str | None
    maintenance_fee: float | None = None
    fee_discount: bool = False
    invoiced: bool = False  # az auto-számla már kiment
    customer_note: str | None = None
    # Ügyfél-árajánlat állapota (none → sent → accepted)
    quote_status: str = "none"
    quote_email: str | None = None
    quote_sent_at: datetime | None = None
    quote_accepted_at: datetime | None = None
    quote_selected_name: str | None = None
    picked_up_at: datetime | None = None  # a gépet elhoztuk a szerelőtől
    updated_at: datetime


def _validate_signature(value: str | None) -> str | None:
    if not value:
        return None
    if not value.startswith("data:image/png;base64,") or len(value) > MAX_SIGNATURE_BYTES:
        raise HTTPException(status_code=422, detail={"code": "worksheet.bad_signature"})
    return value


def _strip_price(rows: list[dict]) -> list[dict]:
    return [{**r, "price_net": None} for r in rows]


def _worksheet_out(ws: Worksheet, *, for_worker: bool = False) -> WorksheetOut:
    """A munkalap API-képe. ``for_worker=True`` (dolgozói/me-végpontok) esetén
    KSZ-munkalapon az ÜGYFÉLNEK szánt áraink (price_net) és az ügyfél-
    megjegyzés SOSEM kerülnek a válaszba — a külsős szerviz nem láthatja őket."""
    hide = for_worker and ws.external_service
    works = list(ws.works or [])
    repair_options = list(ws.repair_options or [])
    materials = list(ws.materials or [])
    if hide:
        works = _strip_price(works)
        repair_options = _strip_price(repair_options)
        materials = _strip_price(materials)
    return WorksheetOut(
        serial=ws.serial,
        external_service=ws.external_service,
        work_description=ws.work_description,
        works=[WorkItem(**w) for w in works],
        repair_options=[WorkItem(**w) for w in repair_options],
        materials=[MaterialItem(**m) for m in materials],
        hours_spent=ws.hours_spent,
        client_name=ws.client_name,
        client_location=ws.client_location,
        has_employee_signature=ws.employee_signature is not None,
        has_client_signature=ws.client_signature is not None,
        employee_signature=ws.employee_signature,
        client_signature=ws.client_signature,
        # A karbantartási díj is a MI ügyfél-árunk — a külsős szerviz nem
        # láthatja és nem is állíthatja (csak a képviselő, az ár-szerkesztőben).
        maintenance_fee=None if hide else ws.maintenance_fee,
        fee_discount=False if hide else ws.fee_discount,
        invoiced=ws.billingo_document_id is not None,
        customer_note=None if hide else ws.customer_note,
        quote_status=ws.quote_status or "none",
        quote_email=None if hide else ws.quote_email,
        quote_sent_at=ws.quote_sent_at,
        quote_accepted_at=ws.quote_accepted_at,
        quote_selected_name=ws.quote_selected_name,
        picked_up_at=ws.picked_up_at,
        updated_at=ws.updated_at,
    )


async def _next_worksheet_serial(db: AsyncSession, external: bool = False) -> str:
    """Sima munkalap: ML-ÉÉÉÉ-NNNN; külső szervizes: KSZ-ÉÉÉÉ-NNNN — a két
    tartomány külön számozódik."""
    prefix = "KSZ" if external else "ML"
    year = datetime.now(UTC).year
    count = (
        await db.execute(
            select(sa_func.count()).select_from(Worksheet).where(
                Worksheet.serial.like(f"{prefix}-{year}-%")
            )
        )
    ).scalar_one()
    return f"{prefix}-{year}-{count + 1:04d}"


async def _get_worksheet(db: AsyncSession, task_id: uuid.UUID) -> Worksheet | None:
    return (
        await db.execute(select(Worksheet).where(Worksheet.task_id == task_id))
    ).scalar_one_or_none()


async def _upsert_worksheet(
    db: AsyncSession, task: Task, body: WorksheetBody, actor: User, request: Request,
    preserve_prices: bool = False,
) -> Worksheet:
    ws = await _get_worksheet(db, task.id)
    created = ws is None
    had_both_signatures = (
        ws is not None and ws.employee_signature is not None and ws.client_signature is not None
    )
    if ws is None:
        ws = Worksheet(task_id=task.id, serial=await _next_worksheet_serial(db), created_by=actor.id)
        db.add(ws)
    # Leírás VAGY legalább egy tételes munka kell — mindkettő üresen 422.
    if not body.work_description.strip() and not body.works:
        raise HTTPException(status_code=422, detail={"code": "worksheet.empty"})
    ws.work_description = body.work_description.strip()
    works = [w.model_dump() for w in body.works]
    repair_options = [w.model_dump() for w in body.repair_options]
    materials = [m.model_dump() for m in body.materials]
    if preserve_prices and ws.external_service:
        # A munkadíjak/konstrukciók ügyfél-árát a képviselő állítja — a
        # szervizes mentés nem írhatja felül/törölheti (név szerint örökítjük).
        for new_rows, old_rows in ((works, ws.works), (repair_options, ws.repair_options)):
            old_prices_by_name = {
                str(w.get("name", "")).strip().lower(): w.get("price_net")
                for w in (old_rows or [])
                if w.get("price_net") is not None
            }
            for w in new_rows:
                if w.get("price_net") is None:
                    w["price_net"] = old_prices_by_name.get(str(w.get("name", "")).strip().lower())
    ws.works = works
    ws.repair_options = repair_options
    if preserve_prices and ws.external_service:
        # Az ügyfél-árakat a KÉPVISELŐ állítja be — a dolgozói (szervizes)
        # mentés nem írhatja felül/törölheti: név szerint átörökítjük.
        old_prices = {
            str(m.get("name", "")).strip().lower(): m.get("price_net")
            for m in (ws.materials or [])
            if m.get("price_net") is not None
        }
        for m in materials:
            if m.get("price_net") is None:
                m["price_net"] = old_prices.get(str(m.get("name", "")).strip().lower())
    ws.materials = materials
    ws.hours_spent = body.hours_spent
    ws.client_name = (body.client_name or "").strip() or None
    ws.client_location = (body.client_location or "").strip() or None
    # Karbantartási díj: a MI ügyfél-árunk — KSZ-en a dolgozói (szervizes)
    # mentés nem nyúlhat hozzá, csak a képviselő állítja.
    worker_on_external = preserve_prices and ws.external_service
    if body.maintenance_fee is not None and not worker_on_external:
        ws.maintenance_fee = body.maintenance_fee
    if body.fee_discount is not None and not worker_on_external:
        ws.fee_discount = body.fee_discount
    if body.customer_note is not None:
        ws.customer_note = body.customer_note.strip() or None
    # Csak KITÖLTÖTT aláírás ír felül — üres/None sosem törli a mentettet,
    # így az újranyitott munkalap mentése nem "tünteti el" az aláírásokat.
    if body.employee_signature:
        ws.employee_signature = _validate_signature(body.employee_signature)
    if body.client_signature:
        ws.client_signature = _validate_signature(body.client_signature)
    await db.flush()
    await record_audit(
        db, actor=actor, action="worksheet.create" if created else "worksheet.update",
        entity_type="worksheet", entity_id=ws.serial, request=request,
    )
    await db.commit()
    await db.refresh(ws)  # az onupdate-es updated_at lejárt attribútum lenne

    # Automatizálás-trigger: "Munkalap aláírva" — amikor MINDKÉT aláírás
    # először kerül fel (tűz-és-felejt).
    if (
        not had_both_signatures
        and ws.employee_signature is not None
        and ws.client_signature is not None
    ):
        try:
            from app.services.wfm.automation import fire_event

            emp = (
                await db.execute(select(Employee).where(Employee.id == task.employee_id))
            ).scalar_one_or_none()
            gep_nev = ""
            if ws.asset_id is not None:
                a = (
                    await db.execute(select(Asset).where(Asset.id == ws.asset_id))
                ).scalar_one_or_none()
                gep_nev = a.name if a else ""
            fire_event("worksheet.signed", {
                "sorszam": ws.serial,
                "cim": task.title,
                "dolgozo": f"{emp.last_name} {emp.first_name}" if emp else "",
                "ugyfel": ws.client_name or "",
                "gep_nev": gep_nev,
            })
        except Exception:
            import logging

            logging.getLogger(__name__).warning("worksheet.signed event failed", exc_info=True)

    # KSZ-karbantartás: mindkét aláírás megvan és nincs kedvezmény → a díjról
    # automatikusan számla készül és e-mailben kimegy az ügyfélnek a
    # munkalap-példánnyal együtt (best-effort, egyszer).
    await _maybe_autoinvoice_maintenance(db, task, ws, actor, request)
    return ws


async def _maybe_autoinvoice_maintenance(
    db: AsyncSession, task: Task, ws: Worksheet, actor: User, request: Request
) -> None:
    """A karbantartási díj automatikus kiszámlázása a KSZ-munkalap mindkét
    aláírása UTÁN — ha van díj, nincs kedvezmény, és még nem történt meg.
    Hibája sosem akasztja meg a munkalap mentését."""
    if not (
        ws.external_service
        and (ws.maintenance_fee or 0) > 0
        and not ws.fee_discount
        and ws.employee_signature
        and ws.client_signature
        and ws.billingo_document_id is None
        and ws.asset_id is not None
    ):
        return
    try:
        from app.models import Asset

        asset = (
            await db.execute(select(Asset).where(Asset.id == ws.asset_id))
        ).scalar_one_or_none()
        if asset is None or asset.partner_id is None:
            return
        partner = (
            await db.execute(select(Partner).where(Partner.id == asset.partner_id))
        ).scalar_one_or_none()
        if partner is None:
            return

        from app.services.wfm.billingo_service import create_maintenance_invoice

        try:
            doc_id, mode, due = await create_maintenance_invoice(
                db, partner,
                serial=ws.serial,
                asset_label=f"{asset.name} ({asset.barcode})",
                amount_net=float(ws.maintenance_fee),
            )
        except ValueError:
            return  # Billingó nincs beállítva — kihagyjuk, nincs jelölés
        ws.billingo_document_id = doc_id

        # E-mail az ügyfélnek: munkalap (ügyfél-példány) + számla-értesítés.
        if partner.contact_email:
            try:
                pdf, serial = await _build_worksheet_pdf(db, task, "customer")
                smtp = await load_smtp_config(db)
                if smtp is not None:
                    sent = await send_email(
                        smtp, partner.contact_email,
                        f"Karbantartás elvégezve — {serial}",
                        (
                            "Tisztelt Partnerünk!\n\n"
                            f"A(z) {asset.name} ({asset.barcode}) gép karbantartása "
                            "elkészült; a munkalapot mellékeljük. A karbantartási "
                            f"díjról ({ws.maintenance_fee:.0f} Ft + ÁFA) "
                            f"{'díjbekérőt' if mode == 'proforma' else 'számlát'} "
                            f"állítottunk ki, fizetési határidő: {due.isoformat()}.\n\n"
                            "Üdvözlettel"
                        ),
                        attachments=[(f"{serial}.pdf", pdf, "application", "pdf")],
                    )
                    if sent:
                        ws.invoice_sent_at = datetime.now(UTC)
            except Exception:
                import logging

                logging.getLogger(__name__).warning(
                    "maintenance invoice email failed", exc_info=True
                )
        await record_audit(
            db, actor=actor, action="worksheet.autoinvoice", entity_type="worksheet",
            entity_id=ws.serial,
            detail={"document_id": doc_id, "amount_net": ws.maintenance_fee,
                    "partner": partner.name},
            request=request,
        )
        await db.commit()
    except Exception:
        import logging

        logging.getLogger(__name__).warning("maintenance autoinvoice failed", exc_info=True)


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _comments_map(
    db: AsyncSession, task_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[CommentOut]]:
    if not task_ids:
        return {}
    rows = (
        await db.execute(
            select(TaskComment, User.display_name)
            .outerjoin(User, User.id == TaskComment.author_user_id)
            .where(TaskComment.task_id.in_(task_ids))
            .order_by(TaskComment.created_at)
        )
    ).all()
    out: dict[uuid.UUID, list[CommentOut]] = {}
    for c, author in rows:
        out.setdefault(c.task_id, []).append(
            CommentOut(id=str(c.id), author_name=author, text=c.text, created_at=c.created_at)
        )
    return out


async def _tasks_out(db: AsyncSession, tasks: list[Task]) -> list[TaskOut]:
    emp_rows = (
        await db.execute(
            select(Employee.id, Employee.last_name, Employee.first_name).where(
                Employee.id.in_({t.employee_id for t in tasks})
            )
        )
    ).all()
    names = {eid: f"{ln} {fn}" for eid, ln, fn in emp_rows}
    skill_rows = (
        await db.execute(
            select(Skill.id, Skill.name).where(
                Skill.id.in_({t.required_skill_id for t in tasks if t.required_skill_id})
            )
        )
    ).all()
    skills = {sid: name for sid, name in skill_rows}
    comments = await _comments_map(db, [t.id for t in tasks])
    ws_rows = (
        await db.execute(
            select(
                Worksheet.task_id, Worksheet.serial, Worksheet.work_description,
                Worksheet.external_service, Worksheet.asset_id,
            ).where(Worksheet.task_id.in_([t.id for t in tasks]))
        )
    ).all()
    worksheet_serials = {tid: serial for tid, serial, _, _, _ in ws_rows}
    worksheet_done = {tid: bool((desc or "").strip()) for tid, _, desc, _, _ in ws_rows}
    worksheet_external = {tid: ext for tid, _, _, ext, _ in ws_rows}
    ws_asset_ids = {tid: aid for tid, _, _, _, aid in ws_rows if aid}
    task_assets: dict[uuid.UUID, dict] = {}
    if ws_asset_ids:
        asset_rows = (
            await db.execute(
                select(Asset, Partner.name)
                .outerjoin(Partner, Partner.id == Asset.partner_id)
                .where(Asset.id.in_(set(ws_asset_ids.values())))
            )
        ).all()
        by_id = {
            a.id: {
                "name": a.name,
                "barcode": a.barcode,
                "serial_number": a.serial_number,
                "category": a.category,
                "partner_name": pname,
                "counter": a.counter,
                "maintenance_fee": a.maintenance_fee,
            }
            for a, pname in asset_rows
        }
        task_assets = {
            tid: by_id[aid] for tid, aid in ws_asset_ids.items() if aid in by_id
        }
    return [
        TaskOut(
            id=str(t.id),
            title=t.title,
            description=t.description,
            employee_id=str(t.employee_id),
            employee_name=names.get(t.employee_id),
            due_date=t.due_date,
            required_skill=(
                {"id": t.required_skill_id, "name": skills[t.required_skill_id]}
                if t.required_skill_id and t.required_skill_id in skills
                else None
            ),
            status=t.status,
            comments=comments.get(t.id, []),
            created_at=t.created_at,
            worksheet_serial=worksheet_serials.get(t.id),
            worksheet_completed=worksheet_done.get(t.id, False),
            worksheet_external=worksheet_external.get(t.id, False),
            asset=task_assets.get(t.id),
        )
        for t in tasks
    ]


async def _get_task_or_404(db: AsyncSession, task_id: str) -> Task:
    try:
        tid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "tasks.not_found"})
    task = (await db.execute(select(Task).where(Task.id == tid))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail={"code": "tasks.not_found"})
    return task


# ─── Manager endpoints ───────────────────────────────────────────────────────


@router.get("", response_model=list[TaskOut])
async def list_tasks(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    status: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("tasks")),
):
    query = select(Task).order_by(Task.due_date.desc(), Task.created_at.desc())
    if date_from:
        query = query.where(Task.due_date >= date_from)
    if date_to:
        query = query.where(Task.due_date <= date_to)
    if status:
        if status not in ALL_STATUSES:
            raise HTTPException(status_code=422, detail={"code": "tasks.bad_status"})
        query = query.where(Task.status == status)
    tasks = list((await db.execute(query.limit(500))).scalars())
    return await _tasks_out(db, tasks)


@router.post("", response_model=TaskOut, status_code=201)
async def create_task(
    body: TaskCreateBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("tasks")),
):
    ai_reason: str | None = None
    employee_id_str = body.employee_id

    # Dolgozó nélkül érkező feladat: az AI jelöli ki a legalkalmasabbat
    # (skill + aznapi beosztás + szabadság + terhelés alapján).
    if employee_id_str is None:
        try:
            suggestion = await suggest_assignee(
                db,
                title=body.title.strip(),
                description=body.description,
                due_date=body.due_date,
                required_skill_id=body.required_skill_id,
            )
        except ValueError as exc:
            code = str(exc)
            if code == "ai_not_configured":
                raise HTTPException(
                    status_code=422, detail={"code": "settings.ai_not_configured"}
                )
            if code == "no_candidates":
                raise HTTPException(status_code=422, detail={"code": "tasks.no_candidates"})
            raise HTTPException(status_code=502, detail={"code": "tasks.ai_suggest_failed"})
        except Exception:
            raise HTTPException(status_code=502, detail={"code": "tasks.ai_suggest_failed"})
        employee_id_str = suggestion["employee_id"]
        ai_reason = suggestion["reason"]

    try:
        emp_id = uuid.UUID(employee_id_str)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "tasks.bad_employee"})
    emp = (
        await db.execute(
            select(Employee).where(Employee.id == emp_id, Employee.status == "active")
        )
    ).scalar_one_or_none()
    if emp is None:
        raise HTTPException(status_code=422, detail={"code": "tasks.bad_employee"})

    task = Task(
        title=body.title.strip(),
        description=body.description,
        employee_id=emp_id,
        due_date=body.due_date,
        required_skill_id=body.required_skill_id,
        created_by=actor.id,
    )
    db.add(task)
    await db.flush()

    # A munkalap tárgy-gépe (opcionális): a karbantartási díj alapértéke és
    # az auto-számlázás partnere ebből jön.
    ws_asset = None
    if body.asset_id:
        from app.models import Asset

        try:
            aid = uuid.UUID(body.asset_id)
        except ValueError:
            raise HTTPException(status_code=404, detail={"code": "asset.not_found"})
        ws_asset = (
            await db.execute(select(Asset).where(Asset.id == aid))
        ).scalar_one_or_none()
        if ws_asset is None:
            raise HTTPException(status_code=404, detail={"code": "asset.not_found"})

    # A feladattal együtt azonnal kiáll az online munkalap (ML-sorszámmal);
    # az elvégzett munkát/anyagokat/aláírást a dolgozó tölti ki később.
    ws = Worksheet(
        task_id=task.id,
        serial=await _next_worksheet_serial(db, body.external_service),
        external_service=body.external_service,
        asset_id=ws_asset.id if ws_asset else None,
        maintenance_fee=ws_asset.maintenance_fee if ws_asset else None,
        work_description="",
        materials=[],
        client_name=(body.client_name or "").strip() or None,
        client_location=(body.client_location or "").strip() or None,
        created_by=actor.id,
    )
    db.add(ws)
    await db.flush()

    await record_audit(
        db, actor=actor, action="task.create", entity_type="task",
        entity_id=str(task.id),
        detail={"worksheet": ws.serial, "ai_assigned": ai_reason is not None},
        request=request,
    )
    await db.commit()

    # Személyes Telegram-értesítés a kiosztott dolgozónak (best-effort).
    await _notify_assignee(db, task, emp, ws.serial)

    out = (await _tasks_out(db, [task]))[0]
    out.ai_reason = ai_reason
    return out


async def _notify_assignee(db: AsyncSession, task: Task, emp: Employee, serial: str | None) -> None:
    """Privát Telegram a dolgozónak az új/átosztott feladatáról — ha
    összekapcsolta magát a bottal. Hibája sosem akasztja meg a mentést."""
    try:
        from app.services.wfm.telegram import send_personal

        parts = [f"📋 Új feladat: {task.title}"]
        if serial:
            parts.append(f"munkalap: {serial}")
        if task.due_date:
            parts.append(f"határidő: {task.due_date.isoformat()}")
        await send_personal(db, emp, " · ".join(parts))
    except Exception:
        import logging

        logging.getLogger(__name__).warning("task assign notify failed", exc_info=True)
    # Automatizálás-trigger: "Feladat kiosztva" (tűz-és-felejt).
    try:
        from app.services.wfm.automation import fire_event

        gep_nev = gep_vonalkod = ""
        ws = await _get_worksheet(db, task.id)
        if ws is not None and ws.asset_id is not None:
            a = (
                await db.execute(select(Asset).where(Asset.id == ws.asset_id))
            ).scalar_one_or_none()
            if a is not None:
                gep_nev, gep_vonalkod = a.name, a.barcode or ""
        fire_event("task.assigned", {
            "cim": task.title,
            "dolgozo": f"{emp.last_name} {emp.first_name}",
            "hatarido": task.due_date.isoformat() if task.due_date else "",
            "sorszam": serial or "",
            "ugyfel": (ws.client_name if ws else None) or "",
            "gep_nev": gep_nev,
            "gep_vonalkod": gep_vonalkod,
        })
    except Exception:
        import logging

        logging.getLogger(__name__).warning("task.assigned event failed", exc_info=True)


@router.patch("/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: str,
    body: TaskPatchBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("tasks")),
):
    task = await _get_task_or_404(db, task_id)
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in ALL_STATUSES:
        raise HTTPException(status_code=422, detail={"code": "tasks.bad_status"})
    if "employee_id" in data:
        try:
            data["employee_id"] = uuid.UUID(data["employee_id"])
        except ValueError:
            raise HTTPException(status_code=422, detail={"code": "tasks.bad_employee"})
    reassigned_to = (
        data["employee_id"]
        if "employee_id" in data and data["employee_id"] != task.employee_id
        else None
    )
    for key, value in data.items():
        setattr(task, key, value)
    await record_audit(
        db, actor=actor, action="task.update", entity_type="task",
        entity_id=str(task.id), detail={"fields": sorted(data)}, request=request,
    )
    await db.commit()

    if reassigned_to is not None:
        new_emp = (
            await db.execute(select(Employee).where(Employee.id == reassigned_to))
        ).scalar_one_or_none()
        if new_emp is not None:
            ws = (
                await db.execute(select(Worksheet).where(Worksheet.task_id == task.id))
            ).scalar_one_or_none()
            await _notify_assignee(db, task, new_emp, ws.serial if ws else None)
    return (await _tasks_out(db, [task]))[0]


@router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("tasks")),
):
    task = await _get_task_or_404(db, task_id)
    await db.delete(task)
    await record_audit(
        db, actor=actor, action="task.delete", entity_type="task",
        entity_id=str(task.id), request=request,
    )
    await db.commit()
    return {"ok": True}


STATUS_LABELS_HU = {
    "open": "Nyitott",
    "done": "Befejezett",
    "needs_more_work": "További munkát igényel",
}


@router.get("/{task_id}/worksheet", response_model=WorksheetOut)
async def get_task_worksheet(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("tasks")),
):
    task = await _get_task_or_404(db, task_id)
    ws = await _get_worksheet(db, task.id)
    if ws is None:
        raise HTTPException(status_code=404, detail={"code": "worksheet.not_found"})
    return _worksheet_out(ws)


@router.put("/{task_id}/worksheet", response_model=WorksheetOut)
async def manager_upsert_worksheet(
    task_id: str,
    body: WorksheetBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("tasks")),
):
    """Vezetői kitöltés/javítás — ugyanaz a folyamat, mint a dolgozói."""
    task = await _get_task_or_404(db, task_id)
    return _worksheet_out(await _upsert_worksheet(db, task, body, actor, request))


async def _build_worksheet_pdf(
    db: AsyncSession, task: Task, variant: str = "internal"
) -> tuple[bytes, str]:
    """A munkalap PDF-jét állítja elő — (pdf_bytes, sorszám). 404, ha nincs ML.

    Külső szervizes (KSZ) munkalapnál két példány van:
    - internal: a szerviz visszaigazolása a NETTÓ költségekkel — csak belső;
    - customer: a "-1"-es ügyfél-példány a MI szerviz-árainkkal (a költségek
      nem szerepelnek rajta)."""
    ws = await _get_worksheet(db, task.id)
    if ws is None:
        raise HTTPException(status_code=404, detail={"code": "worksheet.not_found"})

    emp = (
        await db.execute(select(Employee).where(Employee.id == task.employee_id))
    ).scalar_one_or_none()
    comments = (await _comments_map(db, [task.id])).get(task.id, [])

    serial = ws.serial
    title = None
    price_column = None
    materials = list(ws.materials or [])
    # Tételes munkadíjak: belső példányon a szerviz költsége (cost_net),
    # ügyfél-példányon KIZÁRÓLAG a mi áraink (price_net) — a cost_net-et
    # lehúzzuk, hogy véletlenül se kerülhessen az ügyfél elé.
    works = list(ws.works or [])
    repair_options = list(ws.repair_options or [])
    works_price_field = "price_net"
    works_price_label = "Munkadíj (Ft, nettó)"
    if ws.external_service:
        if variant == "customer":
            works = [
                {k: v for k, v in w.items() if k != "cost_net"} for w in works
            ]
            repair_options = [
                {k: v for k, v in w.items() if k != "cost_net"} for w in repair_options
            ]
        else:
            works_price_field = "cost_net"
            works_price_label = "Munkadíj — nettó költség (Ft)"
    if ws.external_service:
        if variant == "customer":
            serial = f"{ws.serial}-1"
            title = "SZERVIZ MUNKALAP"
            price_column = {"field": "price_net", "label": "Ár (Ft, nettó)"}
            # az ügyfél-példányra a belső költség SOSEM kerülhet rá
            materials = [
                {k: v for k, v in m.items() if k != "cost_net"} for m in materials
            ]
        else:
            title = "KÜLSŐ SZERVIZ MUNKALAP (belső példány)"
            price_column = {"field": "cost_net", "label": "Nettó költség (Ft)"}
        # Karbantartási díj sora (mindkét példányon): kedvezménynél elengedve.
        if ws.fee_discount:
            materials.append({"name": "Karbantartási díj — KEDVEZMÉNY (elengedve)",
                              "qty": "1", "unit": "alkalom"})
        elif (ws.maintenance_fee or 0) > 0:
            fee_row = {"name": "Karbantartási díj", "qty": "1", "unit": "alkalom"}
            if variant == "customer":
                fee_row["price_net"] = ws.maintenance_fee
            elif variant == "worker":
                pass  # a szervizes példányán az összeg (a mi árunk) nem szerepel
            else:
                fee_row["name"] = f"Karbantartási díj — {ws.maintenance_fee:.0f} Ft (nettó)"
            materials.append(fee_row)

    # Az ügyfél-példányra a szervizes BELSŐ leírása és a belső kommentek nem
    # kerülnek rá — helyettük a képviselő ügyfél-megjegyzése (ha van).
    work_description = ws.work_description
    pdf_comments = [{"author": c.author_name, "text": c.text} for c in comments]
    pdf_settings = await _worksheet_pdf_settings(db)
    extra_footer = None
    handover_url = None
    if ws.external_service and variant == "customer":
        work_description = ws.customer_note or ""
        pdf_comments = []
        # Garanciális feltételek — MINDIG rákerül az ügyfél-példány aljára
        # (a Beállításokban átírható, üresen a beépített alapszöveg).
        extra_footer = (pdf_settings or {}).get("customer_footer_text") or DEFAULT_CUSTOMER_FOOTER
        # Átadás-QR: a boltban beolvasva az Átadás menü nyílik ezzel a
        # munkalappal (fizetendő összeg, fizetési mód, számla).
        from app.core.config import get_settings

        handover_url = (
            f"{get_settings().frontend_origin.rstrip('/')}/atadas?task={task.id}"
        )

    pdf = build_worksheet_pdf(
        {
            "extra_footer": extra_footer,
            "handover_url": handover_url,
            "serial": serial,
            "title": title,
            "price_column": price_column,
            "task_title": task.title,
            "task_description": task.description,
            "due_date": task.due_date.isoformat(),
            "status_label": STATUS_LABELS_HU.get(task.status, task.status),
            "employee_name": f"{emp.last_name} {emp.first_name}" if emp else "—",
            "employee_code": emp.employee_code if emp else None,
            "job_title": emp.job_title if emp else None,
            "work_description": work_description,
            "works": works,
            "repair_options": repair_options,
            "works_price_field": works_price_field,
            "works_price_label": works_price_label,
            "materials": materials,
            "hours_spent": ws.hours_spent,
            "client_name": ws.client_name,
            "client_location": ws.client_location,
            "employee_signature": ws.employee_signature,
            "client_signature": ws.client_signature,
            "comments": pdf_comments,
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
        },
        pdf_settings,
    )
    return pdf, serial


async def _worksheet_pdf_settings(db: AsyncSession) -> dict | None:
    """A munkalap-PDF testreszabási beállításai (logó, fejléc, szín, kapcsolók)."""
    row = (
        await db.execute(select(WorksheetSettings).where(WorksheetSettings.id == 1))
    ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "company_name": row.company_name,
        "company_address": row.company_address,
        "footer_text": row.footer_text,
        "accent_color": row.accent_color,
        "logo_bytes": bytes(row.logo_data) if row.logo_data else None,
        "customer_footer_text": row.customer_footer_text,
        "intake_footer_text": row.intake_footer_text,
        "show_materials": row.show_materials,
        "show_hours": row.show_hours,
        "show_client_signature": row.show_client_signature,
        "show_comments": row.show_comments,
    }


def _pdf_response(pdf: bytes, serial: str) -> Response:
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{serial}.pdf"'},
    )


async def _email_worksheet_pdf(db: AsyncSession, to: str, pdf: bytes, serial: str) -> None:
    """A munkalap PDF-jének elküldése emailben (csatolmányként). 422/502 hibakód."""
    smtp = await load_smtp_config(db)
    if smtp is None:
        raise HTTPException(status_code=422, detail={"code": "settings.email_not_configured"})
    ok = await send_email(
        smtp,
        to,
        f"Iwfm — munkalap {serial}",
        f"Mellékelve a(z) {serial} sorszámú munkalap PDF formátumban.\n\nIwfm",
        attachments=[(f"{serial}.pdf", pdf, "application", "pdf")],
    )
    if not ok:
        raise HTTPException(status_code=502, detail={"code": "settings.email_send_failed"})


class WorksheetEmailBody(BaseModel):
    to: EmailStr


@router.get("/{task_id}/worksheet/pdf")
async def worksheet_pdf(
    task_id: str,
    request: Request,
    variant: str = "internal",  # internal | customer (KSZ-nél a -1-es példány)
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("tasks")),
):
    task = await _get_task_or_404(db, task_id)
    pdf, serial = await _build_worksheet_pdf(
        db, task, "customer" if variant == "customer" else "internal"
    )
    await record_audit(
        db, actor=actor, action="worksheet.pdf", entity_type="worksheet",
        entity_id=serial, request=request,
    )
    await db.commit()
    return _pdf_response(pdf, serial)


@router.post("/{task_id}/worksheet/email")
async def email_worksheet(
    task_id: str,
    body: WorksheetEmailBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("tasks")),
):
    task = await _get_task_or_404(db, task_id)
    # Emailben az ügyfél felé KSZ-munkalapnál mindig a -1-es (ügyfél-áras)
    # példány megy — a belső költségek nem hagyhatják el a céget.
    pdf, serial = await _build_worksheet_pdf(db, task, "customer")
    await _email_worksheet_pdf(db, body.to, pdf, serial)
    await record_audit(
        db, actor=actor, action="worksheet.email", entity_type="worksheet",
        entity_id=serial, detail={"to": body.to}, request=request,
    )
    await db.commit()
    return {"ok": True}


# ─── AI: munkalap-előkitöltés fotóból ───────────────────────────────────────

_PHOTO_MIME = {"image/png", "image/jpeg", "image/jpg"}
_MAX_PHOTO_BYTES = 8 * 1024 * 1024  # 8 MB

WORKSHEET_PHOTO_PROMPT = (
    "Egy szerelési/karbantartási munkalap vagy kézzel írt jegyzet fotóját kapod. "
    "Olvasd ki a tartalmát és add vissza KIZÁRÓLAG érvényes JSON-ként, magyarul, "
    "pontosan ezzel a sémával:\n"
    '{"work_description": "az elvégzett munka összefoglalója szövegként", '
    '"materials": [{"name": "anyag neve", "qty": "mennyiség", "unit": "egység pl. db/m/kg"}], '
    '"hours_spent": ráfordított órák száma vagy null}\n'
    "Amit nem tudsz kiolvasni, azt hagyd üresen vagy null. Csak a JSON-t add vissza."
)


class PhotoFillBody(BaseModel):
    image: str  # data URL (image/png|jpeg)


class PhotoFillOut(BaseModel):
    work_description: str | None = None
    materials: list[dict] = []
    hours_spent: float | None = None


def _parse_json_object(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


async def _extract_worksheet_from_photo(db: AsyncSession, image_data_url: str) -> PhotoFillOut:
    if not image_data_url.startswith("data:"):
        raise HTTPException(status_code=422, detail={"code": "tasks.bad_photo"})
    try:
        header, b64 = image_data_url.split(",", 1)
        mime = header.split(";")[0].removeprefix("data:").lower()
        raw = base64.b64decode(b64)
    except Exception:
        raise HTTPException(status_code=422, detail={"code": "tasks.bad_photo"})
    if mime not in _PHOTO_MIME:
        raise HTTPException(status_code=422, detail={"code": "tasks.bad_photo"})
    if len(raw) > _MAX_PHOTO_BYTES:
        raise HTTPException(status_code=422, detail={"code": "tasks.photo_too_large"})
    norm_mime = "image/jpeg" if mime in ("image/jpg", "image/jpeg") else "image/png"

    try:
        text = await generate_from_image(
            db, WORKSHEET_PHOTO_PROMPT, image_b64=b64, mime=norm_mime
        )
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "settings.ai_not_configured"})
    except Exception:
        raise HTTPException(status_code=502, detail={"code": "tasks.ai_photo_failed"})

    parsed = _parse_json_object(text or "")
    if parsed is None:
        raise HTTPException(status_code=502, detail={"code": "tasks.ai_photo_failed"})

    materials: list[dict] = []
    for item in (parsed.get("materials") or [])[:30]:
        if isinstance(item, dict) and item.get("name"):
            materials.append(
                {
                    "name": str(item.get("name"))[:120],
                    "qty": str(item.get("qty") or ""),
                    "unit": str(item.get("unit") or ""),
                }
            )
    hours = parsed.get("hours_spent")
    try:
        hours = float(hours) if hours is not None else None
    except (TypeError, ValueError):
        hours = None
    return PhotoFillOut(
        work_description=(str(parsed.get("work_description") or "").strip() or None),
        materials=materials,
        hours_spent=hours,
    )


@router.post("/{task_id}/worksheet/from-photo", response_model=PhotoFillOut)
async def worksheet_from_photo(
    task_id: str,
    body: PhotoFillBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("tasks")),
):
    task = await _get_task_or_404(db, task_id)
    result = await _extract_worksheet_from_photo(db, body.image)
    await record_audit(
        db, actor=actor, action="worksheet.photo_fill", entity_type="task",
        entity_id=str(task.id), request=request,
    )
    await db.commit()
    return result


class SuggestBody(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=4000)
    due_date: date
    required_skill_id: int | None = None


@router.post("/suggest-assignee")
async def ai_suggest_assignee(
    body: SuggestBody,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("tasks")),
):
    """AI-javaslat a legalkalmasabb dolgozóra (skill + szabadság + terhelés)."""
    try:
        return await suggest_assignee(
            db,
            title=body.title.strip(),
            description=body.description,
            due_date=body.due_date,
            required_skill_id=body.required_skill_id,
        )
    except ValueError as exc:
        code = str(exc)
        if code == "ai_not_configured":
            raise HTTPException(status_code=422, detail={"code": "settings.ai_not_configured"})
        if code == "no_candidates":
            raise HTTPException(status_code=422, detail={"code": "tasks.no_candidates"})
        raise HTTPException(status_code=502, detail={"code": "tasks.ai_suggest_failed"})
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail={"code": "tasks.ai_suggest_failed"})


# ─── Employee (self-service) endpoints — /api/me/tasks ──────────────────────


@me_router.get("", response_model=list[TaskOut])
async def my_tasks(
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
):
    tasks = list(
        (
            await db.execute(
                select(Task)
                .where(Task.employee_id == emp.id)
                .order_by(Task.status == "done", Task.due_date.desc())
                .limit(100)
            )
        ).scalars()
    )
    return await _tasks_out(db, tasks)


async def _own_task_or_404(db: AsyncSession, emp: Employee, task_id: str) -> Task:
    task = await _get_task_or_404(db, task_id)
    if task.employee_id != emp.id:  # idegen feladat: nem látható, 404
        raise HTTPException(status_code=404, detail={"code": "tasks.not_found"})
    return task


@me_router.post("/{task_id}/comment", response_model=TaskOut)
async def my_comment(
    task_id: str,
    body: CommentBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
    user: User = Depends(get_current_user),
):
    task = await _own_task_or_404(db, emp, task_id)
    db.add(TaskComment(task_id=task.id, author_user_id=user.id, text=body.text.strip()))
    await record_audit(
        db, actor=user, action="task.self_comment", entity_type="task",
        entity_id=str(task.id), request=request,
    )
    await db.commit()
    return (await _tasks_out(db, [task]))[0]


@me_router.get("/{task_id}/worksheet", response_model=WorksheetOut)
async def my_worksheet(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
):
    task = await _own_task_or_404(db, emp, task_id)
    ws = await _get_worksheet(db, task.id)
    if ws is None:
        raise HTTPException(status_code=404, detail={"code": "worksheet.not_found"})
    return _worksheet_out(ws, for_worker=True)


@me_router.put("/{task_id}/worksheet", response_model=WorksheetOut)
async def my_upsert_worksheet(
    task_id: str,
    body: WorksheetBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
    user: User = Depends(get_current_user),
):
    """A dolgozó kitölti (vagy frissíti) a saját feladatának munkalapját.
    KSZ-munkalapon az ügyfél-árakat a képviselő kezeli — a dolgozói mentés
    nem írja felül őket (preserve_prices)."""
    task = await _own_task_or_404(db, emp, task_id)
    return _worksheet_out(
        await _upsert_worksheet(db, task, body, user, request, preserve_prices=True),
        for_worker=True,
    )


@me_router.post("/{task_id}/status", response_model=TaskOut)
async def my_status(
    task_id: str,
    body: StatusBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
    user: User = Depends(get_current_user),
):
    if body.status not in EMPLOYEE_STATUSES:
        raise HTTPException(status_code=422, detail={"code": "tasks.bad_status"})
    task = await _own_task_or_404(db, emp, task_id)
    task.status = body.status
    if body.comment:
        db.add(TaskComment(task_id=task.id, author_user_id=user.id, text=body.comment.strip()))
    await record_audit(
        db, actor=user, action=f"task.self_{body.status}", entity_type="task",
        entity_id=str(task.id), request=request,
    )
    await db.commit()
    return (await _tasks_out(db, [task]))[0]


@me_router.get("/{task_id}/worksheet/pdf")
async def my_worksheet_pdf(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
    user: User = Depends(get_current_user),
):
    """A dolgozó letöltheti/megoszthatja a saját munkalapja PDF-jét (telefonon).
    KSZ-munkalapnál a BELSŐ példányt kapja (a saját költségeivel) — az ügyfél
    −1-es példányát (a mi árainkkal) a képviselő küldi, a külsős szerviz a mi
    árainkat sosem láthatja."""
    task = await _own_task_or_404(db, emp, task_id)
    pdf, serial = await _build_worksheet_pdf(db, task, "worker")
    await record_audit(
        db, actor=user, action="worksheet.pdf_self", entity_type="worksheet",
        entity_id=serial, request=request,
    )
    await db.commit()
    return _pdf_response(pdf, serial)


@me_router.post("/{task_id}/worksheet/email")
async def my_email_worksheet(
    task_id: str,
    body: WorksheetEmailBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
    user: User = Depends(get_current_user),
):
    """A dolgozó elküldi a saját munkalapja PDF-jét emailben (pl. az ügyfélnek)."""
    task = await _own_task_or_404(db, emp, task_id)
    pdf, serial = await _build_worksheet_pdf(db, task)
    await _email_worksheet_pdf(db, body.to, pdf, serial)
    await record_audit(
        db, actor=user, action="worksheet.email_self", entity_type="worksheet",
        entity_id=serial, detail={"to": body.to}, request=request,
    )
    await db.commit()
    return {"ok": True}


@me_router.post("/{task_id}/worksheet/from-photo", response_model=PhotoFillOut)
async def my_worksheet_from_photo(
    task_id: str,
    body: PhotoFillBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
    user: User = Depends(get_current_user),
):
    """A dolgozó lefotózza a papír munkalapot/jegyzetet → az AI előkitölti a
    munkaleírást, anyagokat, órákat (a dolgozó ellenőrzi és menti)."""
    task = await _own_task_or_404(db, emp, task_id)
    result = await _extract_worksheet_from_photo(db, body.image)
    await record_audit(
        db, actor=user, action="worksheet.photo_fill_self", entity_type="task",
        entity_id=str(task.id), request=request,
    )
    await db.commit()
    return result


# ─── Ügyfél-árajánlat a javítási konstrukciókból (publikus link) ─────────────


class QuoteSendBody(BaseModel):
    to: EmailStr


@router.post("/{task_id}/worksheet/send-quote", response_model=WorksheetOut)
async def send_worksheet_quote(
    task_id: str,
    body: QuoteSendBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("tasks")),
):
    """A felülvizsgált (ügyfél-árakkal ellátott) javítási konstrukciók
    kiküldése árajánlatként: az ügyfél a linken választ és hagy jóvá."""
    import secrets

    from app.core.config import get_settings

    task = await _get_task_or_404(db, task_id)
    ws = await _get_worksheet(db, task.id)
    if ws is None:
        raise HTTPException(status_code=404, detail={"code": "worksheet.not_found"})
    options = [
        w for w in (ws.repair_options or [])
        if w.get("name") and w.get("price_net") is not None
    ]
    if not options:
        raise HTTPException(status_code=422, detail={"code": "worksheet.quote_no_options"})
    if ws.quote_status in ("accepted", "declined"):
        raise HTTPException(status_code=422, detail={"code": "worksheet.quote_already_accepted"})

    if not ws.quote_token:
        ws.quote_token = secrets.token_urlsafe(32)
    ws.quote_status = "sent"
    ws.quote_email = str(body.to)
    ws.quote_sent_at = datetime.now(UTC)
    link = f"{get_settings().frontend_origin.rstrip('/')}/munkalap-ajanlat/{ws.quote_token}"

    smtp = await load_smtp_config(db)
    if smtp is None:
        raise HTTPException(status_code=422, detail={"code": "settings.email_not_configured"})
    lines = [
        "Tisztelt Ügyfelünk!",
        "",
        f"A(z) {ws.serial} munkalaphoz javítási árajánlatot készítettünk.",
        "Az alábbi linken megtekintheti a választható javítási konstrukciókat,",
        "és kiválaszthatja az Önnek megfelelőt:",
        "",
        link,
        "",
        "A kiválasztás után szervizünk azonnal megkezdi a javítást.",
        "",
        "Üdvözlettel,",
        "X-Presso szerviz",
    ]
    ok = await send_email(smtp, str(body.to), f"Javítási árajánlat — {ws.serial}", "\n".join(lines))
    if not ok:
        raise HTTPException(status_code=502, detail={"code": "settings.email_send_failed"})
    await record_audit(
        db, actor=actor, action="worksheet.quote_sent", entity_type="worksheet",
        entity_id=ws.serial, detail={"to": str(body.to), "options": len(options)},
        request=request,
    )
    await db.commit()
    await db.refresh(ws)
    return _worksheet_out(ws)


async def _ws_by_quote_token(db: AsyncSession, token: str) -> Worksheet:
    if not token or len(token) < 16:
        raise HTTPException(status_code=404, detail={"code": "quote.not_found"})
    ws = (
        await db.execute(select(Worksheet).where(Worksheet.quote_token == token))
    ).scalar_one_or_none()
    if ws is None or ws.quote_status == "none":
        raise HTTPException(status_code=404, detail={"code": "quote.not_found"})
    return ws


@quote_public_router.get("/{token}")
async def public_worksheet_quote(token: str, db: AsyncSession = Depends(get_db)):
    """Publikus árajánlat-nézet — KIZÁRÓLAG az ügyfél-árakkal (cost_net soha)."""
    ws = await _ws_by_quote_token(db, token)
    asset_name = None
    if ws.asset_id is not None:
        a = (
            await db.execute(select(Asset).where(Asset.id == ws.asset_id))
        ).scalar_one_or_none()
        if a is not None:
            asset_name = f"{a.name} — {a.manufacturer}" if a.manufacturer else a.name
    row = (
        await db.execute(select(WorksheetSettings).where(WorksheetSettings.id == 1))
    ).scalar_one_or_none()
    from app.services.wfm.worksheet_pdf import DEFAULT_SURVEY_FEE

    survey_fee = (row.survey_fee if row and row.survey_fee is not None else DEFAULT_SURVEY_FEE)
    return {
        "serial": ws.serial,
        "status": ws.quote_status,
        "machine": asset_name,
        "client_name": ws.client_name,
        "company_name": row.company_name if row else None,
        "selected_name": ws.quote_selected_name,
        "accepted_at": ws.quote_accepted_at,
        # Mindig felkínált opció: "nem kérem a javítást" — felmérési díjjal.
        "survey_fee_net": survey_fee,
        "survey_fee_gross": round(survey_fee * 1.27, 0),
        "options": [
            {
                "name": w.get("name"),
                "price_net": w.get("price_net"),
                "price_gross": round(float(w["price_net"]) * 1.27, 0) if w.get("price_net") is not None else None,
            }
            for w in (ws.repair_options or [])
            if w.get("name") and w.get("price_net") is not None
        ],
    }


class QuoteAcceptBody(BaseModel):
    # decline=True: az ügyfél NEM kéri a javítást — a felmérési díjat vállalja.
    option_name: str | None = Field(default=None, max_length=256)
    accepted_by: str = Field(min_length=2, max_length=256)
    decline: bool = False


@quote_public_router.post("/{token}/accept")
async def public_worksheet_quote_accept(
    token: str,
    body: QuoteAcceptBody,
    db: AsyncSession = Depends(get_db),
):
    """Az ügyfél kiválasztja és jóváhagyja az egyik konstrukciót — VAGY a
    javítást nem kéri (decline), és a felmérési díjat vállalja. Elfogadásnál
    a munkalapon már csak a kiválasztott opció szerepel; a szervizes
    értesítést kap (ár nélkül!)."""
    ws = await _ws_by_quote_token(db, token)
    if ws.quote_status in ("accepted", "declined"):
        raise HTTPException(status_code=422, detail={"code": "quote.already_accepted"})
    all_options = list(ws.repair_options or [])

    if body.decline:
        # "Nem kérem a javítást" — a felmérési díj fizetendő.
        from app.services.wfm.worksheet_pdf import DEFAULT_SURVEY_FEE

        row = (
            await db.execute(select(WorksheetSettings).where(WorksheetSettings.id == 1))
        ).scalar_one_or_none()
        survey_fee = row.survey_fee if row and row.survey_fee is not None else DEFAULT_SURVEY_FEE
        selected_label = "Nem kéri a javítást — felmérési díj"
        ws.repair_options = []
        ws.works = list(ws.works or []) + [
            {"name": "Felmérési díj (a javítást az ügyfél nem kérte)",
             "cost_net": None, "price_net": survey_fee}
        ]
        ws.quote_status = "declined"
        ws.quote_selected_name = selected_label
    else:
        if not (body.option_name or "").strip():
            raise HTTPException(status_code=422, detail={"code": "quote.bad_option"})
        chosen = next(
            (
                w for w in all_options
                if (w.get("name") or "").strip() == body.option_name.strip()
                and w.get("price_net") is not None
            ),
            None,
        )
        if chosen is None:
            raise HTTPException(status_code=422, detail={"code": "quote.bad_option"})
        ws.repair_options = [chosen]  # a rendszerben már csak a kiválasztott él
        ws.quote_status = "accepted"
        ws.quote_selected_name = chosen.get("name")
        selected_label = chosen.get("name") or ""

    ws.quote_accepted_by = body.accepted_by.strip()
    ws.quote_accepted_at = datetime.now(UTC)
    task = (
        await db.execute(select(Task).where(Task.id == ws.task_id))
    ).scalar_one_or_none()
    db.add(AuditEvent(
        action="worksheet.quote_declined" if body.decline else "worksheet.quote_accepted",
        entity_type="worksheet",
        entity_id=ws.serial,
        detail={
            "selected": selected_label,
            "accepted_by": ws.quote_accepted_by,
            "all_options": [
                {"name": w.get("name"), "price_net": w.get("price_net")} for w in all_options
            ],
        },
    ))
    await db.commit()

    # Szervizes értesítése (ár NÉLKÜL).
    if task is not None:
        try:
            from app.services.wfm.telegram import send_personal

            emp = (
                await db.execute(select(Employee).where(Employee.id == task.employee_id))
            ).scalar_one_or_none()
            if emp is not None:
                if body.decline:
                    msg = (
                        f"🔴 Az ügyfél NEM kéri a javítást: {ws.serial}.\n"
                        "A gépet ne javítsd — összeszerelés után visszaadandó."
                    )
                else:
                    msg = (
                        f"🟢 Árajánlat elfogadva: {ws.serial} — a munka kezdhető!\n"
                        f"Kiválasztott konstrukció: {selected_label}"
                    )
                await send_personal(db, emp, msg)
        except Exception:
            import logging

            logging.getLogger(__name__).warning("quote accept notify failed", exc_info=True)
        try:
            from app.services.wfm.automation import fire_event

            fire_event("worksheet.quote_accepted", {
                "sorszam": ws.serial,
                "cim": task.title,
                "ugyfel": ws.client_name or "",
                "opcio": selected_label,
                "elfogado": ws.quote_accepted_by or "",
            })
        except Exception:
            pass
    return {"ok": True, "selected": selected_label, "declined": body.decline}


# ─── Gép elhozva a szerelőtől → az ügyfél értesítése (átvehető) ──────────────


class PickupBody(BaseModel):
    to: EmailStr | None = None  # None → a munkalapon tárolt ajánlat-email


@router.post("/{task_id}/worksheet/picked-up", response_model=WorksheetOut)
async def worksheet_picked_up(
    task_id: str,
    body: PickupBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("tasks")),
):
    """Megjelöljük, hogy a kész gépet elhoztuk a külsős szerelőtől, és az
    ügyfélnek e-mail megy: a javítás elkészült, a gép átvehető."""
    task = await _get_task_or_404(db, task_id)
    ws = await _get_worksheet(db, task.id)
    if ws is None:
        raise HTTPException(status_code=404, detail={"code": "worksheet.not_found"})
    if ws.picked_up_at is not None:
        raise HTTPException(status_code=422, detail={"code": "worksheet.already_picked_up"})
    to = (str(body.to) if body.to else None) or ws.quote_email
    if not to:
        raise HTTPException(status_code=422, detail={"code": "worksheet.pickup_no_email"})

    smtp = await load_smtp_config(db)
    if smtp is None:
        raise HTTPException(status_code=422, detail={"code": "settings.email_not_configured"})
    machine = ""
    if ws.asset_id is not None:
        a = (
            await db.execute(select(Asset).where(Asset.id == ws.asset_id))
        ).scalar_one_or_none()
        if a is not None:
            machine = f"{a.name} — {a.manufacturer}" if a.manufacturer else a.name
    row = (
        await db.execute(select(WorksheetSettings).where(WorksheetSettings.id == 1))
    ).scalar_one_or_none()
    company = (row.company_name if row else None) or "X-Presso"
    lines = [
        "Tisztelt Ügyfelünk!",
        "",
        f"Örömmel értesítjük, hogy a(z) {ws.serial} munkalapon szereplő"
        + (f" készülék ({machine})" if machine else " készülék")
        + " javítása elkészült.",
        "",
        "A gép átvehető üzletünkben, nyitvatartási időben.",
        "",
        "Üdvözlettel,",
        company,
    ]
    ok = await send_email(
        smtp, to, f"A javítás elkészült — a gép átvehető ({ws.serial})", "\n".join(lines)
    )
    if not ok:
        raise HTTPException(status_code=502, detail={"code": "settings.email_send_failed"})

    ws.picked_up_at = datetime.now(UTC)
    await record_audit(
        db, actor=actor, action="worksheet.picked_up", entity_type="worksheet",
        entity_id=ws.serial, detail={"to": to}, request=request,
    )
    await db.commit()
    await db.refresh(ws)
    try:
        from app.services.wfm.automation import fire_event

        fire_event("worksheet.picked_up", {
            "sorszam": ws.serial,
            "ugyfel": ws.client_name or "",
            "gep_nev": machine,
        })
    except Exception:
        pass
    return _worksheet_out(ws)


# ─── Átadás az ügyfélnek (kész gép a boltban, fizetés + számla + lezárás) ────


def _handover_items(ws: Worksheet) -> list[dict]:
    """A vevő által fizetendő tételek — KIZÁRÓLAG a mi áraink (price_net)."""
    items: list[dict] = []
    for w in ws.works or []:
        if w.get("price_net") is not None:
            items.append({"name": w.get("name") or "Munkadíj", "amount_net": float(w["price_net"])})
    for w in ws.repair_options or []:
        if w.get("price_net") is not None:
            items.append({"name": w.get("name") or "Javítás", "amount_net": float(w["price_net"])})
    for m in ws.materials or []:
        if m.get("price_net") is not None:
            try:
                qty = float(str(m.get("qty", "1")).replace(",", "."))
            except ValueError:
                qty = 1.0
            items.append({
                "name": m.get("name") or "Anyag",
                "amount_net": float(m["price_net"]) * (qty if qty > 0 else 1.0),
            })
    if (ws.maintenance_fee or 0) > 0 and not ws.fee_discount:
        items.append({"name": "Karbantartási díj", "amount_net": float(ws.maintenance_fee)})
    return items


async def _handover_row(db: AsyncSession, task: Task, ws: Worksheet) -> dict:
    machine = None
    if ws.asset_id is not None:
        a = (
            await db.execute(select(Asset).where(Asset.id == ws.asset_id))
        ).scalar_one_or_none()
        if a is not None:
            machine = f"{a.name} — {a.manufacturer}" if a.manufacturer else a.name
    items = _handover_items(ws)
    total = round(sum(i["amount_net"] for i in items), 2)
    return {
        "task_id": str(task.id),
        "serial": ws.serial,
        "title": task.title,
        "machine": machine,
        "client_name": ws.client_name,
        "client_location": ws.client_location,
        "quote_email": ws.quote_email,
        "quote_status": ws.quote_status,
        "picked_up_at": ws.picked_up_at,
        "handed_over_at": ws.handed_over_at,
        "handover_discount": ws.handover_discount,
        "handover_payment_method": ws.handover_payment_method,
        "handover_document_id": ws.handover_document_id,
        "items": items,
        "total_net": total,
        "total_gross": round(total * 1.27, 0),
    }


@router.get("/handovers/list")
async def list_handovers(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    """Átadásra váró (megjavított, elhozott, de még át nem adott) gépek."""
    rows = (
        await db.execute(
            select(Worksheet, Task)
            .join(Task, Task.id == Worksheet.task_id)
            .where(
                Worksheet.external_service.is_(True),
                Worksheet.picked_up_at.is_not(None),
                Worksheet.handed_over_at.is_(None),
            )
            .order_by(Worksheet.picked_up_at.desc())
            .limit(200)
        )
    ).all()
    return [await _handover_row(db, task, ws) for ws, task in rows]


@router.get("/{task_id}/handover")
async def handover_detail(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    task = await _get_task_or_404(db, task_id)
    ws = await _get_worksheet(db, task.id)
    if ws is None:
        raise HTTPException(status_code=404, detail={"code": "worksheet.not_found"})
    return await _handover_row(db, task, ws)


class HandoverBody(BaseModel):
    payment_method: str = Field(pattern="^(cash|card)$")
    discount: bool = False  # kedvezmény → NEM készül számla


@router.post("/{task_id}/handover")
async def do_handover(
    task_id: str,
    body: HandoverBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("invoicing")),
):
    """Átadás: (opcionális) Billingó-számla készpénz/kártya fizetéssel, a gép
    átadott státuszt kap, a munkalap lezárul (a feladat done lesz)."""
    task = await _get_task_or_404(db, task_id)
    ws = await _get_worksheet(db, task.id)
    if ws is None:
        raise HTTPException(status_code=404, detail={"code": "worksheet.not_found"})
    if ws.handed_over_at is not None:
        raise HTTPException(status_code=422, detail={"code": "handover.already_done"})
    items = _handover_items(ws)
    total = round(sum(i["amount_net"] for i in items), 2)

    document_id = None
    if not body.discount:
        if not items:
            raise HTTPException(status_code=422, detail={"code": "handover.no_items"})
        asset = None
        if ws.asset_id is not None:
            asset = (
                await db.execute(select(Asset).where(Asset.id == ws.asset_id))
            ).scalar_one_or_none()
        partner = None
        if asset is not None and asset.partner_id is not None:
            partner = (
                await db.execute(select(Partner).where(Partner.id == asset.partner_id))
            ).scalar_one_or_none()
        if partner is None:
            raise HTTPException(status_code=422, detail={"code": "handover.no_partner"})
        from app.services.wfm import billingo_service

        try:
            document_id, _mode = await billingo_service.create_handover_invoice(
                db, partner, serial=ws.serial, items=items,
                payment_method=body.payment_method,
            )
        except ValueError:
            raise HTTPException(status_code=422, detail={"code": "settings.billingo_not_configured"})
        except Exception:
            raise HTTPException(status_code=502, detail={"code": "billingo.request_failed"})

    ws.handed_over_at = datetime.now(UTC)
    ws.handover_payment_method = body.payment_method
    ws.handover_discount = body.discount
    ws.handover_total_net = 0.0 if body.discount else total
    ws.handover_document_id = document_id
    task.status = "done"
    # A gép átadott státuszt kap
    if ws.asset_id is not None:
        asset = (
            await db.execute(select(Asset).where(Asset.id == ws.asset_id))
        ).scalar_one_or_none()
        if asset is not None:
            asset.status = "handed_over"
            from app.models import AssetMovement

            db.add(AssetMovement(
                asset_id=asset.id, action="handover",
                actor_user_id=actor.id,
                detail=f"Átadva az ügyfélnek ({ws.serial})"
                + (" — kedvezmény, számla nélkül" if body.discount
                   else f" — {('kártya' if body.payment_method == 'card' else 'készpénz')}, {total:.0f} Ft nettó"),
            ))
    await record_audit(
        db, actor=actor, action="worksheet.handed_over", entity_type="worksheet",
        entity_id=ws.serial,
        detail={"payment_method": body.payment_method, "discount": body.discount,
                "total_net": total, "document_id": document_id},
        request=request,
    )
    await db.commit()
    try:
        from app.services.wfm.automation import fire_event

        fire_event("worksheet.handed_over", {
            "sorszam": ws.serial,
            "ugyfel": ws.client_name or "",
            "osszeg_netto": f"{total:.0f}" if not body.discount else "0",
            "fizetes_mod": "kártya" if body.payment_method == "card" else "készpénz",
            "kedvezmeny": "igen" if body.discount else "nem",
        })
    except Exception:
        pass
    return {"ok": True, "document_id": document_id, "total_net": total,
            "discount": body.discount}
