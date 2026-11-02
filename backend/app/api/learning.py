"""X-Learning (142. kör): oktatóanyagok (szöveg + YouTube + PDF-csatolmány),
teszt (feleletválasztós, több helyes is), kiosztás egyénileg és szerepkör szerint,
határidő és érvényesség, naplózott próbálkozások (100 % = elvégezve), oklevél PDF,
képzés-napló export. A dolgozó CSAK a saját kartonját látja (/my végpontok);
a vezetői rész a `learning` jogosultsághoz kötött. Külön modulkapcsoló: learning."""

from __future__ import annotations

import base64
import csv
import io
import random
import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_own_employee, record_audit, require_perm
from app.db import get_db
from app.models import (
    Employee,
    LearningAssignment,
    LearningAttachment,
    LearningAttempt,
    LearningMaterial,
    LearningQuestion,
    User,
)

router = APIRouter()

MAX_ATTACHMENT = 20 * 1024 * 1024
ALLOWED_ATTACHMENT = {
    "application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg",
}
REMIND_BEFORE_DAYS = 3


# ─── Pydantic ────────────────────────────────────────────────────────────────


class OptionIn(BaseModel):
    text: str = Field(min_length=1, max_length=512)
    correct: bool = False


class QuestionIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    options: list[OptionIn] = Field(min_length=2, max_length=8)


class MaterialBody(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    category: str | None = Field(default=None, max_length=64)
    body: str | None = Field(default=None, max_length=60000)
    video_url: str | None = Field(default=None, max_length=512)
    validity_months: int | None = Field(default=None, ge=1, le=120)
    random_count: int | None = Field(default=None, ge=1, le=100)
    external_ok: bool = False
    auto_roles: list[str] = Field(default_factory=list, max_length=20)
    default_due_days: int | None = Field(default=None, ge=1, le=365)
    is_active: bool = True


class MaterialPatch(BaseModel):
    model_config = {"extra": "forbid"}
    title: str | None = Field(default=None, min_length=1, max_length=256)
    category: str | None = Field(default=None, max_length=64)
    body: str | None = Field(default=None, max_length=60000)
    video_url: str | None = Field(default=None, max_length=512)
    validity_months: int | None = Field(default=None, ge=1, le=120)
    random_count: int | None = Field(default=None, ge=1, le=100)
    external_ok: bool | None = None
    auto_roles: list[str] | None = None
    default_due_days: int | None = Field(default=None, ge=1, le=365)
    is_active: bool | None = None


class QuestionsBody(BaseModel):
    questions: list[QuestionIn] = Field(default_factory=list, max_length=200)


class AttachmentBody(BaseModel):
    filename: str = Field(min_length=1, max_length=256)
    data_url: str = Field(min_length=10)


class AssignBody(BaseModel):
    employee_ids: list[str] = Field(default_factory=list, max_length=500)
    roles: list[str] = Field(default_factory=list, max_length=20)
    due_date: date | None = None


class AttemptBody(BaseModel):
    answers: dict[str, list[int]] = Field(default_factory=dict)


# ─── Segédek ─────────────────────────────────────────────────────────────────


def _status(a: LearningAssignment, today: date) -> str:
    if a.completed_at is not None:
        if a.valid_until is not None and a.valid_until < today:
            return "expired"
        return "done"
    if a.due_date is not None and a.due_date < today:
        return "overdue"
    return "open"


def _youtube_id(url: str | None) -> str | None:
    """YouTube-link → videó-azonosító (watch?v=, youtu.be/, shorts/, embed/)."""
    if not url:
        return None
    u = url.strip()
    for marker in ("youtu.be/", "/embed/", "/shorts/", "v="):
        if marker in u:
            tail = u.split(marker, 1)[1]
            vid = tail.split("&")[0].split("?")[0].split("/")[0].split("#")[0]
            if 6 <= len(vid) <= 20:
                return vid
    return None


def _material_out(m: LearningMaterial, questions: int = 0, attachments: list | None = None, stats: dict | None = None) -> dict:
    return {
        "id": str(m.id), "title": m.title, "category": m.category, "body": m.body,
        "video_url": m.video_url, "video_id": _youtube_id(m.video_url),
        "validity_months": m.validity_months, "random_count": m.random_count,
        "external_ok": bool(m.external_ok), "auto_roles": list(m.auto_roles or []),
        "default_due_days": m.default_due_days, "is_active": bool(m.is_active),
        "created_by_name": m.created_by_name,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        "question_count": questions,
        "attachments": attachments or [],
        **(stats or {}),
    }


def _att_meta(a: LearningAttachment) -> dict:
    return {"id": str(a.id), "filename": a.filename, "mime": a.mime, "size": a.size}


async def _material_or_404(db: AsyncSession, material_id: str) -> LearningMaterial:
    try:
        mid = uuid.UUID(material_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "learning.not_found"})
    m = (await db.execute(select(LearningMaterial).where(LearningMaterial.id == mid))).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail={"code": "learning.not_found"})
    return m


async def _attachments(db: AsyncSession, material_id: uuid.UUID) -> list[LearningAttachment]:
    return list((await db.execute(
        select(LearningAttachment).where(LearningAttachment.material_id == material_id).order_by(LearningAttachment.created_at)
    )).scalars())


async def _questions(db: AsyncSession, material_id: uuid.UUID) -> list[LearningQuestion]:
    return list((await db.execute(
        select(LearningQuestion).where(LearningQuestion.material_id == material_id).order_by(LearningQuestion.sort)
    )).scalars())


async def _employee_role(db: AsyncSession, emp: Employee) -> str | None:
    if emp.user_id is None:
        return None
    return (await db.execute(select(User.role).where(User.id == emp.user_id))).scalar_one_or_none()


def _decode_attachment(data_url: str) -> tuple[bytes, str]:
    try:
        header, payload = data_url.split(",", 1)
        if not header.startswith("data:") or ";base64" not in header:
            raise ValueError
        mime = header[5:].split(";", 1)[0]
        if mime not in ALLOWED_ATTACHMENT:
            raise ValueError
        raw = base64.b64decode(payload, validate=True)
    except Exception:
        raise HTTPException(status_code=422, detail={"code": "learning.bad_attachment"})
    if len(raw) > MAX_ATTACHMENT:
        raise HTTPException(status_code=422, detail={"code": "learning.attachment_too_large"})
    return raw, mime


async def _stats_for(db: AsyncSession, material_ids: list[uuid.UUID], today: date) -> dict:
    rows = (await db.execute(
        select(LearningAssignment).where(LearningAssignment.material_id.in_(material_ids))
    )).scalars().all() if material_ids else []
    out: dict = {mid: {"assigned": 0, "done": 0, "overdue": 0, "expired": 0} for mid in material_ids}
    for a in rows:
        s = out[a.material_id]
        s["assigned"] += 1
        st = _status(a, today)
        if st == "done":
            s["done"] += 1
        elif st == "overdue":
            s["overdue"] += 1
        elif st == "expired":
            s["expired"] += 1
    return out


# ─── Dolgozói nézet: CSAK a saját karton ─────────────────────────────────────


async def _materialize_role_assignments(db: AsyncSession, emp: Employee) -> None:
    """Szerepkör szerinti automatikus kiosztás: a dolgozó szerepköréhez rendelt
    aktív anyagokra sor jön létre, ha még nincs (külsősnek csak external_ok-nál)."""
    role = await _employee_role(db, emp)
    if not role:
        return
    mats = (await db.execute(select(LearningMaterial).where(LearningMaterial.is_active.is_(True)))).scalars().all()
    have = set((await db.execute(
        select(LearningAssignment.material_id).where(LearningAssignment.employee_id == emp.id)
    )).scalars())
    today = date.today()
    changed = False
    for m in mats:
        if role not in (m.auto_roles or []) or m.id in have:
            continue
        if emp.is_contractor and not m.external_ok:
            continue
        due = None
        if m.default_due_days:
            from datetime import timedelta

            due = today + timedelta(days=m.default_due_days)
        db.add(LearningAssignment(material_id=m.id, employee_id=emp.id, due_date=due, source="role", assigned_by_name="szerepkör"))
        changed = True
    if changed:
        await db.commit()


async def _own_assignment(db: AsyncSession, emp: Employee, assignment_id: str) -> tuple[LearningAssignment, LearningMaterial]:
    try:
        aid = uuid.UUID(assignment_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "learning.not_found"})
    row = (await db.execute(
        select(LearningAssignment, LearningMaterial)
        .join(LearningMaterial, LearningMaterial.id == LearningAssignment.material_id)
        .where(LearningAssignment.id == aid, LearningAssignment.employee_id == emp.id)
    )).first()
    if row is None:  # idegen karton: 404, mintha nem létezne
        raise HTTPException(status_code=404, detail={"code": "learning.not_found"})
    return row[0], row[1]


def _assignment_out(a: LearningAssignment, m: LearningMaterial, today: date, attempts: int = 0, last_score: int | None = None) -> dict:
    return {
        "id": str(a.id), "material_id": str(m.id), "title": m.title, "category": m.category,
        "has_video": bool(_youtube_id(m.video_url)), "has_test": None,
        "due_date": a.due_date.isoformat() if a.due_date else None,
        "status": _status(a, today),
        "completed_at": a.completed_at.isoformat() if a.completed_at else None,
        "valid_until": a.valid_until.isoformat() if a.valid_until else None,
        "attempts": attempts, "last_score": last_score, "source": a.source,
    }


@router.get("/my")
async def my_learning(
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
):
    """A belépett dolgozó saját kartonja: kiosztott anyagok, állapot, határidő."""
    await _materialize_role_assignments(db, emp)
    today = date.today()
    rows = (await db.execute(
        select(LearningAssignment, LearningMaterial)
        .join(LearningMaterial, LearningMaterial.id == LearningAssignment.material_id)
        .where(LearningAssignment.employee_id == emp.id, LearningMaterial.is_active.is_(True))
        .order_by(LearningAssignment.due_date.is_(None), LearningAssignment.due_date, LearningMaterial.title)
    )).all()
    att = {
        aid: (int(cnt), int(best or 0))
        for aid, cnt, best in (await db.execute(
            select(LearningAttempt.assignment_id, sa_func.count(), sa_func.max(LearningAttempt.score_percent))
            .where(LearningAttempt.employee_id == emp.id)
            .group_by(LearningAttempt.assignment_id)
        )).all()
    }
    qcount = {
        mid: int(n) for mid, n in (await db.execute(
            select(LearningQuestion.material_id, sa_func.count()).group_by(LearningQuestion.material_id)
        )).all()
    }
    out = []
    for a, m in rows:
        cnt, best = att.get(a.id, (0, None))
        o = _assignment_out(a, m, today, cnt, best)
        o["has_test"] = qcount.get(m.id, 0) > 0
        out.append(o)
    return {"employee_name": f"{emp.last_name} {emp.first_name}", "items": out}


@router.get("/my/{assignment_id}")
async def my_material(
    assignment_id: str,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
):
    """Egy kiosztott anyag tartalma + a teszt kérdései (helyes jelölés NÉLKÜL;
    véletlen részhalmaz, ha a tananyag úgy van beállítva)."""
    a, m = await _own_assignment(db, emp, assignment_id)
    qs = await _questions(db, m.id)
    if m.random_count and len(qs) > m.random_count:
        qs = random.sample(qs, m.random_count)
    attempts = (await db.execute(
        select(LearningAttempt).where(LearningAttempt.assignment_id == a.id).order_by(LearningAttempt.created_at.desc()).limit(20)
    )).scalars().all()
    today = date.today()
    return {
        "assignment": _assignment_out(a, m, today, len(attempts), max((x.score_percent for x in attempts), default=None)),
        "material": {
            "title": m.title, "category": m.category, "body": m.body, "video_id": _youtube_id(m.video_url),
            "attachments": [_att_meta(x) for x in await _attachments(db, m.id)],
            "validity_months": m.validity_months,
        },
        "questions": [
            {"id": str(q.id), "text": q.text, "options": [o.get("text") for o in (q.options or [])],
             "multi": sum(1 for o in (q.options or []) if o.get("correct")) > 1}
            for q in qs
        ],
        "attempts": [
            {"created_at": x.created_at.isoformat(), "score_percent": x.score_percent, "passed": bool(x.passed)}
            for x in attempts
        ],
    }


@router.get("/my/{assignment_id}/attachments/{attachment_id}")
async def my_attachment(
    assignment_id: str,
    attachment_id: str,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
):
    a, m = await _own_assignment(db, emp, assignment_id)
    try:
        att_id = uuid.UUID(attachment_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "learning.not_found"})
    att = (await db.execute(
        select(LearningAttachment).where(LearningAttachment.id == att_id, LearningAttachment.material_id == m.id)
    )).scalar_one_or_none()
    if att is None:
        raise HTTPException(status_code=404, detail={"code": "learning.not_found"})
    return Response(content=bytes(att.data), media_type=att.mime,
                    headers={"Content-Disposition": f'inline; filename="{att.filename}"'})


@router.post("/my/{assignment_id}/attempt")
async def my_attempt(
    assignment_id: str,
    body: AttemptBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
    user: User = Depends(get_current_user),
):
    """Teszt beadása: minden kérdésre a jelölt opciók; 100 % = elvégezve
    (érvényesség a beállított hónapokig). Minden próbálkozás naplózva."""
    a, m = await _own_assignment(db, emp, assignment_id)
    qs = {str(q.id): q for q in await _questions(db, m.id)}
    if not qs:
        raise HTTPException(status_code=422, detail={"code": "learning.no_test"})
    answered = {k: v for k, v in body.answers.items() if k in qs}
    expected = m.random_count if (m.random_count and len(qs) > m.random_count) else len(qs)
    if len(answered) < expected:
        raise HTTPException(status_code=422, detail={"code": "learning.incomplete", "expected": expected, "got": len(answered)})
    correct = 0
    for qid, picked in answered.items():
        q = qs[qid]
        right = {i for i, o in enumerate(q.options or []) if o.get("correct")}
        if set(picked) == right:
            correct += 1
    score = int(round(correct / len(answered) * 100))
    passed = score == 100
    attempt = LearningAttempt(assignment_id=a.id, employee_id=emp.id, score_percent=score, passed=passed, answers=answered)
    db.add(attempt)
    if passed:
        now = datetime.now(UTC)
        a.completed_at = now
        if m.validity_months:
            mth = now.month - 1 + m.validity_months
            y, mo = now.year + mth // 12, mth % 12 + 1
            import calendar

            a.valid_until = date(y, mo, min(now.day, calendar.monthrange(y, mo)[1]))
        else:
            a.valid_until = None
    await record_audit(
        db, actor=user, action="learning.attempt", entity_type="learning_assignment", entity_id=str(a.id),
        detail={"score": score, "passed": passed, "material": m.title}, request=request,
    )
    await db.commit()
    return {
        "score_percent": score, "passed": passed, "correct": correct, "total": len(answered),
        "status": _status(a, date.today()), "valid_until": a.valid_until.isoformat() if a.valid_until else None,
    }


@router.get("/my/{assignment_id}/certificate.pdf")
async def my_certificate(
    assignment_id: str,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
):
    a, m = await _own_assignment(db, emp, assignment_id)
    if a.completed_at is None:
        raise HTTPException(status_code=422, detail={"code": "learning.not_completed"})
    from app.services.wfm.learning_pdf import build_certificate_pdf

    pdf, serial = await build_certificate_pdf(db, a, m, emp)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="oklevel_{serial}.pdf"'})


# ─── Vezetői rész (learning jogosultság) ─────────────────────────────────────


@router.get("/materials")
async def list_materials(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("learning")),
):
    mats = (await db.execute(select(LearningMaterial).order_by(LearningMaterial.updated_at.desc()))).scalars().all()
    today = date.today()
    stats = await _stats_for(db, [m.id for m in mats], today)
    qcount = {
        mid: int(n) for mid, n in (await db.execute(
            select(LearningQuestion.material_id, sa_func.count()).group_by(LearningQuestion.material_id)
        )).all()
    }
    acount: dict = {}
    for mid, meta in (await db.execute(
        select(LearningAttachment.material_id, LearningAttachment.id, LearningAttachment.filename, LearningAttachment.mime, LearningAttachment.size)
    )).all():
        acount.setdefault(mid, []).append({"id": str(meta), "filename": None})
    # a csatolmány-metákat egyszerűbb külön lekérni
    atts: dict = {}
    for att in (await db.execute(select(LearningAttachment))).scalars().all():
        atts.setdefault(att.material_id, []).append(_att_meta(att))
    return [_material_out(m, qcount.get(m.id, 0), atts.get(m.id, []), stats.get(m.id)) for m in mats]


@router.post("/materials", status_code=201)
async def create_material(
    body: MaterialBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("learning")),
):
    m = LearningMaterial(
        title=body.title.strip(), category=(body.category or "").strip() or None, body=body.body,
        video_url=(body.video_url or "").strip() or None, validity_months=body.validity_months,
        random_count=body.random_count, external_ok=body.external_ok, auto_roles=list(body.auto_roles) or None,
        default_due_days=body.default_due_days, is_active=body.is_active, created_by_name=actor.display_name,
    )
    db.add(m)
    await db.flush()
    await record_audit(db, actor=actor, action="learning.material_create", entity_type="learning_material",
                       entity_id=str(m.id), detail={"title": m.title}, request=request)
    await db.commit()
    return _material_out(m)


@router.patch("/materials/{material_id}")
async def update_material(
    material_id: str,
    body: MaterialPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("learning")),
):
    m = await _material_or_404(db, material_id)
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        if k == "auto_roles":
            v = list(v or []) or None
        elif isinstance(v, str) and k in ("title", "category", "video_url"):
            v = v.strip() or None
        setattr(m, k, v)
    await record_audit(db, actor=actor, action="learning.material_update", entity_type="learning_material",
                       entity_id=str(m.id), detail={"fields": sorted(data)}, request=request)
    await db.commit()
    qs = await _questions(db, m.id)
    return _material_out(m, len(qs), [_att_meta(x) for x in await _attachments(db, m.id)])


@router.delete("/materials/{material_id}", status_code=204)
async def delete_material(
    material_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("delete")),
):
    m = await _material_or_404(db, material_id)
    await record_audit(db, actor=actor, action="learning.material_delete", entity_type="learning_material",
                       entity_id=str(m.id), detail={"title": m.title}, request=request)
    await db.delete(m)
    await db.commit()
    return None


@router.get("/materials/{material_id}/questions")
async def get_questions(
    material_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("learning")),
):
    m = await _material_or_404(db, material_id)
    return [{"id": str(q.id), "text": q.text, "options": q.options or []} for q in await _questions(db, m.id)]


@router.put("/materials/{material_id}/questions")
async def put_questions(
    material_id: str,
    body: QuestionsBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("learning")),
):
    """A kérdésbank cseréje (minden kérdésnél legalább egy helyes opció)."""
    m = await _material_or_404(db, material_id)
    for q in body.questions:
        if not any(o.correct for o in q.options):
            raise HTTPException(status_code=422, detail={"code": "learning.no_correct_option"})
    for old in await _questions(db, m.id):
        await db.delete(old)
    await db.flush()
    for i, q in enumerate(body.questions):
        db.add(LearningQuestion(material_id=m.id, text=q.text.strip(), options=[o.model_dump() for o in q.options], sort=i))
    await record_audit(db, actor=actor, action="learning.questions", entity_type="learning_material",
                       entity_id=str(m.id), detail={"count": len(body.questions)}, request=request)
    await db.commit()
    return {"count": len(body.questions)}


@router.post("/materials/{material_id}/attachments", status_code=201)
async def add_attachment(
    material_id: str,
    body: AttachmentBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("learning")),
):
    m = await _material_or_404(db, material_id)
    raw, mime = _decode_attachment(body.data_url)
    att = LearningAttachment(material_id=m.id, filename=body.filename.strip()[:256], mime=mime, data=raw, size=len(raw))
    db.add(att)
    await db.flush()
    await record_audit(db, actor=actor, action="learning.attachment", entity_type="learning_material",
                       entity_id=str(m.id), detail={"filename": att.filename, "size": att.size}, request=request)
    await db.commit()
    return _att_meta(att)


@router.get("/materials/{material_id}/attachments/{attachment_id}")
async def get_attachment(
    material_id: str,
    attachment_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("learning")),
):
    m = await _material_or_404(db, material_id)
    try:
        att_id = uuid.UUID(attachment_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "learning.not_found"})
    att = (await db.execute(
        select(LearningAttachment).where(LearningAttachment.id == att_id, LearningAttachment.material_id == m.id)
    )).scalar_one_or_none()
    if att is None:
        raise HTTPException(status_code=404, detail={"code": "learning.not_found"})
    return Response(content=bytes(att.data), media_type=att.mime,
                    headers={"Content-Disposition": f'inline; filename="{att.filename}"'})


@router.delete("/materials/{material_id}/attachments/{attachment_id}", status_code=204)
async def delete_attachment(
    material_id: str,
    attachment_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("learning")),
):
    m = await _material_or_404(db, material_id)
    try:
        att_id = uuid.UUID(attachment_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "learning.not_found"})
    att = (await db.execute(
        select(LearningAttachment).where(LearningAttachment.id == att_id, LearningAttachment.material_id == m.id)
    )).scalar_one_or_none()
    if att is None:
        raise HTTPException(status_code=404, detail={"code": "learning.not_found"})
    await db.delete(att)
    await db.commit()
    return None


@router.post("/materials/{material_id}/assign")
async def assign_material(
    material_id: str,
    body: AssignBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("learning")),
):
    """Kiosztás egyénileg és/vagy szerepkör szerint (a szerepkör MOSTANI
    dolgozóira sor készül; a későbbi belépőkhöz az auto_roles mező).
    Külsős (alvállalkozó) csak external_ok anyagot kaphat."""
    m = await _material_or_404(db, material_id)
    targets: dict[uuid.UUID, Employee] = {}
    for raw in body.employee_ids:
        try:
            eid = uuid.UUID(raw)
        except ValueError:
            raise HTTPException(status_code=422, detail={"code": "learning.bad_employee"})
        e = (await db.execute(select(Employee).where(Employee.id == eid))).scalar_one_or_none()
        if e is None:
            raise HTTPException(status_code=422, detail={"code": "learning.bad_employee"})
        targets[e.id] = e
    if body.roles:
        rows = (await db.execute(
            select(Employee).join(User, User.id == Employee.user_id).where(User.role.in_(body.roles), Employee.status == "active")
        )).scalars().all()
        for e in rows:
            targets.setdefault(e.id, e)
    if not targets:
        raise HTTPException(status_code=422, detail={"code": "learning.nobody"})
    external = [e for e in targets.values() if e.is_contractor]
    if external and not m.external_ok:
        raise HTTPException(status_code=422, detail={"code": "learning.external_not_allowed",
                                                     "employees": [f"{e.last_name} {e.first_name}" for e in external]})
    have = set((await db.execute(
        select(LearningAssignment.employee_id).where(LearningAssignment.material_id == m.id)
    )).scalars())
    created = 0
    for eid in targets:
        if eid in have:
            continue
        db.add(LearningAssignment(material_id=m.id, employee_id=eid, due_date=body.due_date,
                                  source="manual" if str(eid) in body.employee_ids else "role",
                                  assigned_by_name=actor.display_name))
        created += 1
    if body.roles:
        m.auto_roles = sorted(set(list(m.auto_roles or []) + list(body.roles)))
    await record_audit(db, actor=actor, action="learning.assign", entity_type="learning_material",
                       entity_id=str(m.id), detail={"created": created, "roles": body.roles}, request=request)
    await db.commit()
    return {"created": created, "already": len(targets) - created}


@router.delete("/materials/{material_id}/assignments/{assignment_id}", status_code=204)
async def unassign(
    material_id: str,
    assignment_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("learning")),
):
    m = await _material_or_404(db, material_id)
    try:
        aid = uuid.UUID(assignment_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "learning.not_found"})
    a = (await db.execute(
        select(LearningAssignment).where(LearningAssignment.id == aid, LearningAssignment.material_id == m.id)
    )).scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail={"code": "learning.not_found"})
    await db.delete(a)
    await db.commit()
    return None


@router.get("/materials/{material_id}/results")
async def material_results(
    material_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("learning")),
):
    """Ki hol tart: dolgozónként állapot, próbálkozások, utolsó/legjobb eredmény."""
    m = await _material_or_404(db, material_id)
    today = date.today()
    rows = (await db.execute(
        select(LearningAssignment, Employee)
        .join(Employee, Employee.id == LearningAssignment.employee_id)
        .where(LearningAssignment.material_id == m.id)
        .order_by(Employee.last_name, Employee.first_name)
    )).all()
    att = {
        aid: (int(cnt), int(best or 0), last)
        for aid, cnt, best, last in (await db.execute(
            select(LearningAttempt.assignment_id, sa_func.count(), sa_func.max(LearningAttempt.score_percent), sa_func.max(LearningAttempt.created_at))
            .where(LearningAttempt.assignment_id.in_([a.id for a, _ in rows]))
            .group_by(LearningAttempt.assignment_id)
        )).all()
    } if rows else {}
    out = []
    for a, e in rows:
        cnt, best, last = att.get(a.id, (0, None, None))
        out.append({
            "assignment_id": str(a.id), "employee_id": str(e.id), "employee_name": f"{e.last_name} {e.first_name}",
            "is_contractor": bool(e.is_contractor), "status": _status(a, today),
            "due_date": a.due_date.isoformat() if a.due_date else None,
            "completed_at": a.completed_at.isoformat() if a.completed_at else None,
            "valid_until": a.valid_until.isoformat() if a.valid_until else None,
            "attempts": cnt, "best_score": best, "last_attempt_at": last.isoformat() if last else None,
            "source": a.source,
        })
    return out


@router.get("/overview")
async def overview(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("learning")),
):
    today = date.today()
    rows = (await db.execute(select(LearningAssignment))).scalars().all()
    counts = {"assigned": len(rows), "done": 0, "open": 0, "overdue": 0, "expired": 0}
    for a in rows:
        counts[_status(a, today)] += 1
    mats = int((await db.execute(select(sa_func.count()).select_from(LearningMaterial).where(LearningMaterial.is_active.is_(True)))).scalar_one() or 0)
    return {"materials": mats, **counts}


@router.get("/export.csv")
async def export_csv(
    year: int = Query(..., ge=2012, le=2100),
    month: int = Query(..., ge=1, le=12),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("payroll")),
):
    """Képzés-napló a bérexport mellé: a hónapban elvégzett képzések
    dolgozónként (anyag, dátum, érvényesség), pontosvesszős CSV, UTF-8 BOM."""
    import calendar as _cal

    first = datetime(year, month, 1, tzinfo=UTC)
    last = datetime(year, month, _cal.monthrange(year, month)[1], 23, 59, 59, tzinfo=UTC)
    rows = (await db.execute(
        select(LearningAssignment, LearningMaterial, Employee)
        .join(LearningMaterial, LearningMaterial.id == LearningAssignment.material_id)
        .join(Employee, Employee.id == LearningAssignment.employee_id)
        .where(LearningAssignment.completed_at >= first, LearningAssignment.completed_at <= last)
        .order_by(Employee.last_name, Employee.first_name, LearningAssignment.completed_at)
    )).all()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(("nev", "tananyag", "kategoria", "elvegezve", "ervenyes", "kiosztas"))
    for a, m, e in rows:
        w.writerow((f"{e.last_name} {e.first_name}", m.title, m.category or "", a.completed_at.date().isoformat(),
                    a.valid_until.isoformat() if a.valid_until else "", a.source))
    return Response(content=("﻿" + buf.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="kepzes_naplo_{year}-{month:02d}.csv"'})


# ─── Emlékeztetők (a notifier hívja) ─────────────────────────────────────────


async def run_learning_reminders(db: AsyncSession, today: date | None = None) -> int:
    """Telegram a dolgozónak: határidő 3 napon belül vagy lejárt / érvényesség
    lejárt — naponta egyszer soronként (reminded_on)."""
    from datetime import timedelta

    from app.services.wfm.telegram import send_personal

    today = today or date.today()
    horizon = today + timedelta(days=REMIND_BEFORE_DAYS)
    rows = (await db.execute(
        select(LearningAssignment, LearningMaterial, Employee)
        .join(LearningMaterial, LearningMaterial.id == LearningAssignment.material_id)
        .join(Employee, Employee.id == LearningAssignment.employee_id)
        .where(LearningMaterial.is_active.is_(True))
    )).all()
    sent = 0
    for a, m, e in rows:
        st = _status(a, today)
        due_soon = st == "open" and a.due_date is not None and a.due_date <= horizon
        if not (due_soon or st in ("overdue", "expired")):
            continue
        if a.reminded_on == today:
            continue
        if st == "overdue":
            text = f"📚 Lejárt határidejű tananyag: {m.title} (határidő: {a.due_date:%Y.%m.%d}). Kérlek, végezd el és töltsd ki a tesztet."
        elif st == "expired":
            text = f"📚 Lejárt az érvényessége: {m.title} — újra el kell végezni a tesztet."
        else:
            text = f"📚 Tananyag határidő közeleg: {m.title} — {a.due_date:%Y.%m.%d}."
        if await send_personal(db, e, text):
            sent += 1
        a.reminded_on = today
    await db.commit()
    return sent
