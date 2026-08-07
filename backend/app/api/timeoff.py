"""Szabadság / távollét kérelmek: lista, felvétel, döntés.

Managers see/decide everything; employees create and see their own requests
through the self-service router (me.py) which reuses the same helpers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.db import get_db
from app.models import Employee, TimeOffRequest, User
from app.services.wfm.email_service import load_smtp_config, send_many

router = APIRouter()

VALID_TYPES = ("annual", "sick", "unpaid", "other")


class TimeOffCreate(BaseModel):
    employee_id: str
    type: str = "annual"
    start_date: date
    end_date: date
    reason: str | None = Field(default=None, max_length=512)


class DecideBody(BaseModel):
    status: str  # approved | rejected
    note: str | None = Field(default=None, max_length=512)


class TimeOffOut(BaseModel):
    id: str
    employee_id: str
    employee_name: str | None = None
    type: str
    start_date: date
    end_date: date
    reason: str | None
    status: str
    decided_at: datetime | None
    decision_note: str | None
    created_at: datetime


def to_out(t: TimeOffRequest, employee_name: str | None = None) -> TimeOffOut:
    return TimeOffOut(
        id=str(t.id),
        employee_id=str(t.employee_id),
        employee_name=employee_name,
        type=t.type,
        start_date=t.start_date,
        end_date=t.end_date,
        reason=t.reason,
        status=t.status,
        decided_at=t.decided_at,
        decision_note=t.decision_note,
        created_at=t.created_at,
    )


def validate_range(type_: str, start: date, end: date) -> None:
    if type_ not in VALID_TYPES:
        raise HTTPException(status_code=422, detail={"code": "timeoff.bad_type"})
    if end < start:
        raise HTTPException(status_code=422, detail={"code": "timeoff.bad_range"})


async def ensure_no_overlap(
    db: AsyncSession, employee_id: uuid.UUID, start: date, end: date, *, exclude_id: uuid.UUID | None = None
) -> None:
    """P2: ugyanarra az időszakra ne legyen másik függő/jóváhagyott kérelem."""
    query = select(TimeOffRequest.id).where(
        TimeOffRequest.employee_id == employee_id,
        TimeOffRequest.status.in_(("pending", "approved")),
        TimeOffRequest.start_date <= end,
        TimeOffRequest.end_date >= start,
    )
    if exclude_id is not None:
        query = query.where(TimeOffRequest.id != exclude_id)
    if (await db.execute(query)).first() is not None:
        raise HTTPException(status_code=409, detail={"code": "timeoff.overlap"})


def working_days(start: date, end: date, *, year: int | None = None) -> int:
    """Munkanapok (hétfő–péntek) száma a tartományban (záró nap beleértve).
    Munkaszüneti napokat nem von le — ez dokumentált egyszerűsítés."""
    days = 0
    cursor = start
    while cursor <= end:
        if (year is None or cursor.year == year) and cursor.weekday() < 5:
            days += 1
        cursor += timedelta(days=1)
    return days


async def _annual_used_working_days(
    db: AsyncSession, employee_id: uuid.UUID, year: int, *, exclude_id: uuid.UUID
) -> int:
    """A dolgozó adott évben már JÓVÁHAGYOTT éves szabadságának munkanapjai."""
    rows = (
        await db.execute(
            select(TimeOffRequest).where(
                TimeOffRequest.employee_id == employee_id,
                TimeOffRequest.type == "annual",
                TimeOffRequest.status == "approved",
                TimeOffRequest.id != exclude_id,
            )
        )
    ).scalars()
    return sum(working_days(r.start_date, r.end_date, year=year) for r in rows)


@router.get("", response_model=list[TimeOffOut])
async def list_time_off(
    status: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("timeoff")),
):
    query = (
        select(TimeOffRequest, Employee.last_name, Employee.first_name)
        .join(Employee, Employee.id == TimeOffRequest.employee_id)
        .order_by(TimeOffRequest.created_at.desc())
    )
    if status:
        query = query.where(TimeOffRequest.status == status)
    rows = (await db.execute(query)).all()
    return [to_out(t, f"{ln} {fn}") for t, ln, fn in rows]


@router.post("", response_model=TimeOffOut, status_code=201)
async def create_time_off(
    body: TimeOffCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("timeoff")),
):
    validate_range(body.type, body.start_date, body.end_date)
    try:
        emp_id = uuid.UUID(body.employee_id)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "timeoff.bad_employee"})
    emp = (
        await db.execute(select(Employee).where(Employee.id == emp_id))
    ).scalar_one_or_none()
    if emp is None:
        raise HTTPException(status_code=422, detail={"code": "timeoff.bad_employee"})
    await ensure_no_overlap(db, emp_id, body.start_date, body.end_date)

    req = TimeOffRequest(
        employee_id=emp_id,
        type=body.type,
        start_date=body.start_date,
        end_date=body.end_date,
        reason=body.reason,
    )
    db.add(req)
    await db.flush()
    await record_audit(
        db, actor=actor, action="timeoff.create", entity_type="timeoff",
        entity_id=str(req.id), request=request,
    )
    await db.commit()
    return to_out(req, f"{emp.last_name} {emp.first_name}")


@router.patch("/{request_id}/decide", response_model=TimeOffOut)
async def decide_time_off(
    request_id: str,
    body: DecideBody,
    request: Request,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("timeoff")),
):
    if body.status not in ("approved", "rejected"):
        raise HTTPException(status_code=422, detail={"code": "timeoff.bad_decision"})
    try:
        rid = uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "timeoff.not_found"})
    req = (
        await db.execute(select(TimeOffRequest).where(TimeOffRequest.id == rid))
    ).scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail={"code": "timeoff.not_found"})
    if req.status != "pending":
        raise HTTPException(status_code=409, detail={"code": "timeoff.already_decided"})

    # P2: éves szabadság jóváhagyásakor a keret (annual_leave_days, munkanapban)
    # nem léphető túl. A munkaszüneti napok levonása nincs implementálva.
    if body.status == "approved" and req.type == "annual":
        emp = (
            await db.execute(select(Employee).where(Employee.id == req.employee_id))
        ).scalar_one_or_none()
        if emp is not None:
            year = req.start_date.year
            used = await _annual_used_working_days(db, req.employee_id, year, exclude_id=req.id)
            requested = working_days(req.start_date, req.end_date, year=year)
            if used + requested > emp.annual_leave_days:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "timeoff.over_balance",
                        "entitlement": emp.annual_leave_days,
                        "used": used,
                        "requested": requested,
                    },
                )

    req.status = body.status
    req.decided_by = actor.id
    req.decided_at = datetime.now(UTC)
    req.decision_note = body.note
    await record_audit(
        db, actor=actor, action=f"timeoff.{body.status}", entity_type="timeoff",
        entity_id=str(req.id), request=request,
    )
    await db.commit()

    # Email a dolgozónak a döntésről (best-effort).
    smtp = await load_smtp_config(db)
    if smtp is not None:
        row = (
            await db.execute(
                select(Employee.first_name, User.email)
                .join(User, User.id == Employee.user_id)
                .where(Employee.id == req.employee_id)
            )
        ).first()
        if row is not None:
            first_name, email = row
            decision_hu = "JÓVÁHAGYVA ✓" if body.status == "approved" else "elutasítva"
            text = (
                f"Szia {first_name}!\n\n"
                f"A távollét-kérelmed ({req.start_date} → {req.end_date}) {decision_hu}."
                + (f"\nMegjegyzés: {body.note}" if body.note else "")
                + "\n\nIwfm"
            )
            background.add_task(
                send_many, smtp, [(email, "Iwfm — távollét-kérelem elbírálva", text)]
            )

    return to_out(req)
