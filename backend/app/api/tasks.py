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
    require_perm,
)
from app.db import get_db
from app.models import (
    Employee,
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
from app.services.wfm.worksheet_pdf import build_worksheet_pdf

router = APIRouter()
me_router = APIRouter()  # /api/me/tasks alá kerül

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
    ai_reason: str | None = None  # csak létrehozáskor, ha az AI jelölte ki


MAX_SIGNATURE_BYTES = 300_000  # ~300KB data URL-enként


class MaterialItem(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    qty: str = Field(default="", max_length=32)
    unit: str = Field(default="db", max_length=16)


class WorksheetBody(BaseModel):
    work_description: str = Field(min_length=1, max_length=8000)
    materials: list[MaterialItem] = Field(default_factory=list, max_length=25)
    hours_spent: float | None = Field(default=None, ge=0, le=1000)
    client_name: str | None = Field(default=None, max_length=256)
    client_location: str | None = Field(default=None, max_length=512)
    employee_signature: str | None = None
    client_signature: str | None = None


class WorksheetOut(BaseModel):
    serial: str
    work_description: str
    materials: list[MaterialItem]
    hours_spent: float | None
    client_name: str | None
    client_location: str | None
    has_employee_signature: bool
    has_client_signature: bool
    updated_at: datetime


def _validate_signature(value: str | None) -> str | None:
    if not value:
        return None
    if not value.startswith("data:image/png;base64,") or len(value) > MAX_SIGNATURE_BYTES:
        raise HTTPException(status_code=422, detail={"code": "worksheet.bad_signature"})
    return value


def _worksheet_out(ws: Worksheet) -> WorksheetOut:
    return WorksheetOut(
        serial=ws.serial,
        work_description=ws.work_description,
        materials=[MaterialItem(**m) for m in (ws.materials or [])],
        hours_spent=ws.hours_spent,
        client_name=ws.client_name,
        client_location=ws.client_location,
        has_employee_signature=ws.employee_signature is not None,
        has_client_signature=ws.client_signature is not None,
        updated_at=ws.updated_at,
    )


async def _next_worksheet_serial(db: AsyncSession) -> str:
    year = datetime.now(UTC).year
    count = (
        await db.execute(
            select(sa_func.count()).select_from(Worksheet).where(
                Worksheet.serial.like(f"ML-{year}-%")
            )
        )
    ).scalar_one()
    return f"ML-{year}-{count + 1:04d}"


async def _get_worksheet(db: AsyncSession, task_id: uuid.UUID) -> Worksheet | None:
    return (
        await db.execute(select(Worksheet).where(Worksheet.task_id == task_id))
    ).scalar_one_or_none()


async def _upsert_worksheet(
    db: AsyncSession, task: Task, body: WorksheetBody, actor: User, request: Request
) -> Worksheet:
    ws = await _get_worksheet(db, task.id)
    created = ws is None
    if ws is None:
        ws = Worksheet(task_id=task.id, serial=await _next_worksheet_serial(db), created_by=actor.id)
        db.add(ws)
    ws.work_description = body.work_description.strip()
    ws.materials = [m.model_dump() for m in body.materials]
    ws.hours_spent = body.hours_spent
    ws.client_name = (body.client_name or "").strip() or None
    ws.client_location = (body.client_location or "").strip() or None
    if body.employee_signature is not None:
        ws.employee_signature = _validate_signature(body.employee_signature)
    if body.client_signature is not None:
        ws.client_signature = _validate_signature(body.client_signature)
    await db.flush()
    await record_audit(
        db, actor=actor, action="worksheet.create" if created else "worksheet.update",
        entity_type="worksheet", entity_id=ws.serial, request=request,
    )
    await db.commit()
    await db.refresh(ws)  # az onupdate-es updated_at lejárt attribútum lenne
    return ws


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
            select(Worksheet.task_id, Worksheet.serial, Worksheet.work_description).where(
                Worksheet.task_id.in_([t.id for t in tasks])
            )
        )
    ).all()
    worksheet_serials = {tid: serial for tid, serial, _ in ws_rows}
    worksheet_done = {tid: bool((desc or "").strip()) for tid, _, desc in ws_rows}
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

    # A feladattal együtt azonnal kiáll az online munkalap (ML-sorszámmal);
    # az elvégzett munkát/anyagokat/aláírást a dolgozó tölti ki később.
    ws = Worksheet(
        task_id=task.id,
        serial=await _next_worksheet_serial(db),
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
    out = (await _tasks_out(db, [task]))[0]
    out.ai_reason = ai_reason
    return out


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
    for key, value in data.items():
        setattr(task, key, value)
    await record_audit(
        db, actor=actor, action="task.update", entity_type="task",
        entity_id=str(task.id), detail={"fields": sorted(data)}, request=request,
    )
    await db.commit()
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


@router.post("/{task_id}/comment", response_model=TaskOut)
async def manager_comment(
    task_id: str,
    body: CommentBody,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("tasks")),
):
    task = await _get_task_or_404(db, task_id)
    db.add(TaskComment(task_id=task.id, author_user_id=actor.id, text=body.text.strip()))
    await db.commit()
    return (await _tasks_out(db, [task]))[0]


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


async def _build_worksheet_pdf(db: AsyncSession, task: Task) -> tuple[bytes, str]:
    """A munkalap PDF-jét állítja elő — (pdf_bytes, sorszám). 404, ha nincs ML."""
    ws = await _get_worksheet(db, task.id)
    if ws is None:
        raise HTTPException(status_code=404, detail={"code": "worksheet.not_found"})

    emp = (
        await db.execute(select(Employee).where(Employee.id == task.employee_id))
    ).scalar_one_or_none()
    comments = (await _comments_map(db, [task.id])).get(task.id, [])

    pdf = build_worksheet_pdf(
        {
            "serial": ws.serial,
            "task_title": task.title,
            "task_description": task.description,
            "due_date": task.due_date.isoformat(),
            "status_label": STATUS_LABELS_HU.get(task.status, task.status),
            "employee_name": f"{emp.last_name} {emp.first_name}" if emp else "—",
            "employee_code": emp.employee_code if emp else None,
            "job_title": emp.job_title if emp else None,
            "work_description": ws.work_description,
            "materials": ws.materials or [],
            "hours_spent": ws.hours_spent,
            "client_name": ws.client_name,
            "client_location": ws.client_location,
            "employee_signature": ws.employee_signature,
            "client_signature": ws.client_signature,
            "comments": [{"author": c.author_name, "text": c.text} for c in comments],
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
        },
        await _worksheet_pdf_settings(db),
    )
    return pdf, ws.serial


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
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("tasks")),
):
    task = await _get_task_or_404(db, task_id)
    pdf, serial = await _build_worksheet_pdf(db, task)
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
    pdf, serial = await _build_worksheet_pdf(db, task)
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
    return _worksheet_out(ws)


@me_router.put("/{task_id}/worksheet", response_model=WorksheetOut)
async def my_upsert_worksheet(
    task_id: str,
    body: WorksheetBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
    user: User = Depends(get_current_user),
):
    """A dolgozó kitölti (vagy frissíti) a saját feladatának munkalapját."""
    task = await _own_task_or_404(db, emp, task_id)
    return _worksheet_out(await _upsert_worksheet(db, task, body, user, request))


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
    """A dolgozó letöltheti/megoszthatja a saját munkalapja PDF-jét (telefonon)."""
    task = await _own_task_or_404(db, emp, task_id)
    pdf, serial = await _build_worksheet_pdf(db, task)
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
