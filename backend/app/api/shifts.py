"""Weekly schedule (Beosztás): CRUD + Mt. compliance preview + publish.

Workflow (Microsoft Shifts-inspired):
1. Manager builds the week in ``draft`` state — the employee does not see it.
2. ``GET /compliance`` previews Mt. violations for the week.
3. ``POST /publish`` publishes the week: errors block, warnings (e.g. the
   168h notice rule, Mt. 97.§(4)) need ``force=true`` and are audited.
4. Editing/deleting a published shift re-checks the 96h modify-notice rule
   (Mt. 97.§(5)) — within 96h it needs ``force=true`` (audited).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_role
from app.db import get_db
from app.models import Employee, Shift, TimeOffRequest, User
from app.services.wfm.compliance import (
    ShiftSpan,
    Violation,
    check_modify_notice,
    check_schedule,
)
from app.services.wfm.email_service import load_smtp_config, send_many

router = APIRouter()


# ─── Schemas ─────────────────────────────────────────────────────────────────


class ShiftBody(BaseModel):
    employee_id: str
    work_date: date
    start_time: time
    end_time: time
    break_minutes: int = Field(default=0, ge=0, le=240)
    role_label: str | None = Field(default=None, max_length=64)
    location: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=512)


class ShiftPatch(BaseModel):
    model_config = {"extra": "forbid"}
    work_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    break_minutes: int | None = Field(default=None, ge=0, le=240)
    role_label: str | None = None
    location: str | None = None
    note: str | None = None
    force: bool = False  # 96h szabály felülbírálása (auditálva)


class ShiftOut(BaseModel):
    id: str
    employee_id: str
    work_date: date
    start_time: time
    end_time: time
    break_minutes: int
    role_label: str | None
    location: str | None
    note: str | None
    status: str
    published_at: datetime | None


class ViolationOut(BaseModel):
    code: str
    severity: str
    message: str
    params: dict = {}
    employee_id: str | None
    shift_ids: list[str]


class WeekOut(BaseModel):
    week_start: date
    shifts: list[ShiftOut]
    time_off: list[dict]
    violations: list[ViolationOut]


class PublishBody(BaseModel):
    week_start: date
    force: bool = False  # warning-ok megerősítése (auditálva)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _shift_out(s: Shift) -> ShiftOut:
    return ShiftOut(
        id=str(s.id),
        employee_id=str(s.employee_id),
        work_date=s.work_date,
        start_time=s.start_time,
        end_time=s.end_time,
        break_minutes=s.break_minutes,
        role_label=s.role_label,
        location=s.location,
        note=s.note,
        status=s.status,
        published_at=s.published_at,
    )


def _violation_out(v: Violation) -> ViolationOut:
    return ViolationOut(
        code=v.code,
        severity=v.severity,
        message=v.message,
        params=v.params,
        employee_id=str(v.employee_id) if v.employee_id else None,
        shift_ids=[str(i) for i in v.shift_ids],
    )


def _span(s: Shift) -> ShiftSpan:
    return ShiftSpan(
        id=s.id,
        employee_id=s.employee_id,
        work_date=s.work_date,
        start_time=s.start_time,
        end_time=s.end_time,
        break_minutes=s.break_minutes,
    )


def _week_bounds(week_start: date) -> tuple[date, date]:
    """Normalize to Monday and return [monday, sunday]."""
    monday = week_start - timedelta(days=week_start.weekday())
    return monday, monday + timedelta(days=6)


async def _week_shifts(
    db: AsyncSession, monday: date, sunday: date, *, context_days: int = 7
) -> list[Shift]:
    """Shifts in the week plus surrounding context (rest-period checks need
    the neighboring days)."""
    rows = (
        await db.execute(
            select(Shift)
            .where(
                Shift.work_date >= monday - timedelta(days=context_days),
                Shift.work_date <= sunday + timedelta(days=context_days),
            )
            .order_by(Shift.work_date, Shift.start_time)
        )
    ).scalars()
    return list(rows)


def _week_violations(
    all_shifts: list[Shift], monday: date, sunday: date, *, now: datetime, publish: bool
) -> list[Violation]:
    """Run the compliance engine per employee; keep violations touching the week."""
    by_emp: dict[uuid.UUID, list[ShiftSpan]] = {}
    for s in all_shifts:
        by_emp.setdefault(s.employee_id, []).append(_span(s))

    week_ids = {s.id for s in all_shifts if monday <= s.work_date <= sunday}
    out: list[Violation] = []
    for spans in by_emp.values():
        for v in check_schedule(spans, now=now, publish=publish):
            if not v.shift_ids or any(i in week_ids for i in v.shift_ids):
                out.append(v)
    return out


async def _approved_time_off(
    db: AsyncSession, monday: date, sunday: date
) -> list[TimeOffRequest]:
    rows = (
        await db.execute(
            select(TimeOffRequest).where(
                TimeOffRequest.status == "approved",
                TimeOffRequest.start_date <= sunday,
                TimeOffRequest.end_date >= monday,
            )
        )
    ).scalars()
    return list(rows)


async def _get_shift_or_404(db: AsyncSession, shift_id: str) -> Shift:
    try:
        sid = uuid.UUID(shift_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "shift.not_found"})
    shift = (await db.execute(select(Shift).where(Shift.id == sid))).scalar_one_or_none()
    if shift is None:
        raise HTTPException(status_code=404, detail={"code": "shift.not_found"})
    return shift


async def _employee_window_shifts(
    db: AsyncSession, employee_id: uuid.UUID, around: date, *, exclude_id: uuid.UUID
) -> list[Shift]:
    """A dolgozó műszakjai az adott dátum körül (±7 nap), a megadott kivételével
    — a pihenőidő/heti szabályok ellenőrzéséhez kell a szomszédos napok kontextusa."""
    rows = (
        await db.execute(
            select(Shift).where(
                Shift.employee_id == employee_id,
                Shift.id != exclude_id,
                Shift.work_date >= around - timedelta(days=7),
                Shift.work_date <= around + timedelta(days=7),
            )
        )
    ).scalars()
    return list(rows)


def _blocking_errors_for(candidate: ShiftSpan, others: list[Shift], *, now: datetime) -> list[Violation]:
    """A jelölt műszak melletti error-szintű Mt.-szabálysértések (P1: publikált
    műszak szerkesztése nem hozhat létre szabálytalan beosztást)."""
    spans = [_span(s) for s in others] + [candidate]
    return [
        v
        for v in check_schedule(spans, now=now)
        if v.severity == "error" and (not v.shift_ids or candidate.id in v.shift_ids)
    ]


# ─── Endpoints ───────────────────────────────────────────────────────────────


@router.get("", response_model=WeekOut)
async def get_week(
    week_start: date = Query(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("manager")),
):
    monday, sunday = _week_bounds(week_start)
    all_shifts = await _week_shifts(db, monday, sunday)
    week_shifts = [s for s in all_shifts if monday <= s.work_date <= sunday]
    violations = _week_violations(
        all_shifts, monday, sunday, now=datetime.now(UTC), publish=False
    )
    time_off = await _approved_time_off(db, monday, sunday)
    return WeekOut(
        week_start=monday,
        shifts=[_shift_out(s) for s in week_shifts],
        time_off=[
            {
                "id": str(t.id),
                "employee_id": str(t.employee_id),
                "type": t.type,
                "start_date": t.start_date.isoformat(),
                "end_date": t.end_date.isoformat(),
            }
            for t in time_off
        ],
        violations=[_violation_out(v) for v in violations],
    )


@router.post("", response_model=ShiftOut, status_code=201)
async def create_shift(
    body: ShiftBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    try:
        emp_id = uuid.UUID(body.employee_id)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "shift.bad_employee"})
    emp = (
        await db.execute(select(Employee).where(Employee.id == emp_id))
    ).scalar_one_or_none()
    if emp is None or emp.status != "active":
        raise HTTPException(status_code=422, detail={"code": "shift.bad_employee"})

    # Jóváhagyott távollétre nem osztható be műszak.
    overlap = (
        await db.execute(
            select(TimeOffRequest).where(
                TimeOffRequest.employee_id == emp_id,
                TimeOffRequest.status == "approved",
                TimeOffRequest.start_date <= body.work_date,
                TimeOffRequest.end_date >= body.work_date,
            )
        )
    ).scalar_one_or_none()
    if overlap is not None:
        raise HTTPException(status_code=422, detail={"code": "shift.on_time_off"})

    shift = Shift(
        employee_id=emp_id,
        work_date=body.work_date,
        start_time=body.start_time,
        end_time=body.end_time,
        break_minutes=body.break_minutes,
        role_label=body.role_label,
        location=body.location,
        note=body.note,
        created_by=actor.id,
    )
    db.add(shift)
    await db.flush()
    await record_audit(
        db, actor=actor, action="shift.create", entity_type="shift",
        entity_id=str(shift.id), request=request,
    )
    await db.commit()
    return _shift_out(shift)


@router.patch("/{shift_id}", response_model=ShiftOut)
async def update_shift(
    shift_id: str,
    body: ShiftPatch,
    request: Request,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    shift = await _get_shift_or_404(db, shift_id)
    now = datetime.now(UTC)

    if shift.status == "published":
        warnings = check_modify_notice(_span(shift), now=now)
        if warnings and not body.force:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "shift.modify_notice",
                    "violations": [
                        {"code": w.code, "severity": w.severity, "message": w.message}
                        for w in warnings
                    ],
                },
            )
        if warnings:
            await record_audit(
                db, actor=actor, action="shift.modify_within_96h", entity_type="shift",
                entity_id=str(shift.id), detail={"rule": "Mt. 97.§ (5)"}, request=request,
            )

    data = body.model_dump(exclude_unset=True, exclude={"force"})

    # P1: publikált műszak szerkesztése nem hozhat létre Mt. error-szintű
    # szabálysértést (napi 12h, heti 48h, pihenőidő, 6 munkanap). A draftot a
    # publikálási kapu ellenőrzi, így ott nem blokkolunk.
    if shift.status == "published" and data:
        new_date = data.get("work_date", shift.work_date)
        candidate = ShiftSpan(
            id=shift.id,
            employee_id=shift.employee_id,
            work_date=new_date,
            start_time=data.get("start_time", shift.start_time),
            end_time=data.get("end_time", shift.end_time),
            break_minutes=data.get("break_minutes", shift.break_minutes),
        )
        others = await _employee_window_shifts(db, shift.employee_id, new_date, exclude_id=shift.id)
        errors = _blocking_errors_for(candidate, others, now=now)
        if errors:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "shift.compliance_blocked",
                    "violations": [_violation_out(v).model_dump() for v in errors],
                },
            )

    for key, value in data.items():
        setattr(shift, key, value)
    await record_audit(
        db, actor=actor, action="shift.update", entity_type="shift",
        entity_id=str(shift.id), detail={"fields": sorted(data)}, request=request,
    )
    await db.commit()

    # Közölt műszak változásáról emailes értesítés (best-effort).
    if shift.status == "published" and data:
        smtp = await load_smtp_config(db)
        if smtp is not None:
            messages = await _schedule_emails(
                db, [shift], subject_prefix="Beosztás módosult"
            )
            if messages:
                background.add_task(send_many, smtp, messages)

    return _shift_out(shift)


@router.delete("/{shift_id}")
async def delete_shift(
    shift_id: str,
    request: Request,
    force: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    shift = await _get_shift_or_404(db, shift_id)
    now = datetime.now(UTC)
    if shift.status == "published":
        warnings = check_modify_notice(_span(shift), now=now)
        if warnings and not force:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "shift.modify_notice",
                    "violations": [
                        {"code": w.code, "severity": w.severity, "message": w.message}
                        for w in warnings
                    ],
                },
            )
        if warnings:
            await record_audit(
                db, actor=actor, action="shift.delete_within_96h", entity_type="shift",
                entity_id=str(shift.id), detail={"rule": "Mt. 97.§ (5)"}, request=request,
            )
    await db.delete(shift)
    await record_audit(
        db, actor=actor, action="shift.delete", entity_type="shift",
        entity_id=str(shift.id), request=request,
    )
    await db.commit()
    return {"ok": True}


@router.get("/compliance", response_model=list[ViolationOut])
async def preview_compliance(
    week_start: date = Query(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("manager")),
):
    """Publish-előnézet: minden szabály, a 168 órás közlési szabállyal együtt."""
    monday, sunday = _week_bounds(week_start)
    all_shifts = await _week_shifts(db, monday, sunday)
    violations = _week_violations(
        all_shifts, monday, sunday, now=datetime.now(UTC), publish=True
    )
    return [_violation_out(v) for v in violations]


async def _schedule_emails(
    db: AsyncSession, shifts_to_send: list[Shift], *, subject_prefix: str
) -> list[tuple[str, str, str]]:
    """(to, subject, body) üzenetek a beosztásról, dolgozónként csoportosítva."""
    by_emp: dict[uuid.UUID, list[Shift]] = {}
    for s in shifts_to_send:
        by_emp.setdefault(s.employee_id, []).append(s)
    rows = (
        await db.execute(
            select(Employee.id, Employee.first_name, User.email)
            .join(User, User.id == Employee.user_id)
            .where(Employee.id.in_(by_emp.keys()))
        )
    ).all()
    messages = []
    for emp_id, first_name, email in rows:
        lines = [
            f"  • {s.work_date.isoformat()}: {s.start_time.strftime('%H:%M')}–{s.end_time.strftime('%H:%M')}"
            + (f" ({s.location})" if s.location else "")
            for s in sorted(by_emp[emp_id], key=lambda x: x.work_date)
        ]
        body_text = (
            f"Szia {first_name}!\n\n{subject_prefix}:\n\n"
            + "\n".join(lines)
            + "\n\nRészletek az alkalmazásban: a beosztásod a telefonodon is megnézheted.\n\nIwfm"
        )
        messages.append((email, f"Iwfm — {subject_prefix}", body_text))
    return messages


@router.post("/publish")
async def publish_week(
    body: PublishBody,
    request: Request,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    monday, sunday = _week_bounds(body.week_start)
    all_shifts = await _week_shifts(db, monday, sunday)
    week_drafts = [
        s for s in all_shifts if monday <= s.work_date <= sunday and s.status == "draft"
    ]
    if not week_drafts:
        raise HTTPException(status_code=422, detail={"code": "publish.nothing_to_publish"})

    now = datetime.now(UTC)
    violations = _week_violations(all_shifts, monday, sunday, now=now, publish=True)
    errors = [v for v in violations if v.severity == "error"]
    warnings = [v for v in violations if v.severity == "warning"]

    if errors:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "publish.blocked",
                "violations": [_violation_out(v).model_dump() for v in errors],
            },
        )
    if warnings and not body.force:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "publish.needs_confirmation",
                "violations": [_violation_out(v).model_dump() for v in warnings],
            },
        )

    for s in week_drafts:
        s.status = "published"
        s.published_at = now

    await record_audit(
        db, actor=actor, action="schedule.publish", entity_type="schedule",
        entity_id=monday.isoformat(),
        detail={
            "shift_count": len(week_drafts),
            "forced_warnings": [v.code for v in warnings] if warnings else [],
        },
        request=request,
    )
    await db.commit()

    # Email értesítés az érintett dolgozóknak (best-effort, háttérben).
    emails_queued = 0
    smtp = await load_smtp_config(db)
    if smtp is not None:
        messages = await _schedule_emails(
            db, week_drafts, subject_prefix=f"Új beosztás ({monday.isoformat()} hét)"
        )
        if messages:
            background.add_task(send_many, smtp, messages)
            emails_queued = len(messages)

    return {
        "ok": True,
        "published": len(week_drafts),
        "warnings": len(warnings),
        "emails_queued": emails_queued,
    }
