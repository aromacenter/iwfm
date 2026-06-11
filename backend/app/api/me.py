"""Employee self-service (a dolgozó saját nézete — telefonon).

Strict ownership: every query is filtered to the employee record linked to
the logged-in user. No cross-employee access is possible from here (IDOR-safe
by construction).

* GET  /me/schedule    — saját PUBLIKÁLT műszakok (a draft nem látszik!)
* GET  /me/time-off    — saját kérelmek
* POST /me/time-off    — új kérelem
* POST /me/clock-in    — bejelentkezés (egy nyitott bejegyzés lehet)
* POST /me/clock-out   — kijelentkezés
* GET  /me/clock       — nyitott bejegyzés állapota
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_own_employee, record_audit
from app.api.shifts import ShiftOut, _shift_out, _week_bounds
from app.api.timeclock import EntryOut, to_out as entry_out
from app.api.timeoff import TimeOffOut, to_out as timeoff_out, validate_range
from app.db import get_db
from app.models import Employee, Shift, TimeEntry, TimeOffRequest, User

router = APIRouter()


class MyTimeOffCreate(BaseModel):
    type: str = "annual"
    start_date: date
    end_date: date
    reason: str | None = Field(default=None, max_length=512)


class ClockBody(BaseModel):
    note: str | None = Field(default=None, max_length=512)


@router.get("/profile")
async def my_profile(emp: Employee = Depends(get_own_employee)):
    """Saját alapadatok az önkiszolgáló fejléchez (név + törzsszám)."""
    return {
        "name": f"{emp.last_name} {emp.first_name}",
        "employee_code": emp.employee_code,
        "job_title": emp.job_title,
    }


@router.get("/schedule", response_model=list[ShiftOut])
async def my_schedule(
    week_start: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
):
    """Saját publikált műszakok — alapból a mai naptól 4 hétre előre."""
    if week_start is not None:
        monday, _ = _week_bounds(week_start)
        date_from, date_to = monday, monday + timedelta(days=6)
    else:
        date_from = date.today()
        date_to = date_from + timedelta(days=28)
    rows = (
        await db.execute(
            select(Shift)
            .where(
                Shift.employee_id == emp.id,
                Shift.status == "published",  # draft sosem látszik
                Shift.work_date >= date_from,
                Shift.work_date <= date_to,
            )
            .order_by(Shift.work_date, Shift.start_time)
        )
    ).scalars()
    return [_shift_out(s) for s in rows]


@router.get("/time-off", response_model=list[TimeOffOut])
async def my_time_off(
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
):
    rows = (
        await db.execute(
            select(TimeOffRequest)
            .where(TimeOffRequest.employee_id == emp.id)
            .order_by(TimeOffRequest.created_at.desc())
        )
    ).scalars()
    return [timeoff_out(t) for t in rows]


@router.post("/time-off", response_model=TimeOffOut, status_code=201)
async def request_time_off(
    body: MyTimeOffCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
    user: User = Depends(get_current_user),
):
    validate_range(body.type, body.start_date, body.end_date)
    req = TimeOffRequest(
        employee_id=emp.id,
        type=body.type,
        start_date=body.start_date,
        end_date=body.end_date,
        reason=body.reason,
    )
    db.add(req)
    await db.flush()
    await record_audit(
        db, actor=user, action="timeoff.self_request", entity_type="timeoff",
        entity_id=str(req.id), request=request,
    )
    await db.commit()
    return timeoff_out(req)


async def _open_entry(db: AsyncSession, emp: Employee) -> TimeEntry | None:
    return (
        await db.execute(
            select(TimeEntry)
            .where(TimeEntry.employee_id == emp.id, TimeEntry.clock_out.is_(None))
            .order_by(TimeEntry.clock_in.desc())
        )
    ).scalars().first()


@router.get("/clock")
async def clock_status(
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
):
    entry = await _open_entry(db, emp)
    return {"open": entry_out(entry).model_dump(mode="json") if entry else None}


@router.post("/clock-in", response_model=EntryOut, status_code=201)
async def clock_in(
    body: ClockBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
    user: User = Depends(get_current_user),
):
    if await _open_entry(db, emp) is not None:
        raise HTTPException(status_code=409, detail={"code": "timeclock.already_open"})
    entry = TimeEntry(
        employee_id=emp.id,
        clock_in=datetime.now(UTC),
        note=body.note,
        source="self",
        created_by=user.id,
    )
    db.add(entry)
    await db.flush()
    await record_audit(
        db, actor=user, action="timeclock.clock_in", entity_type="time_entry",
        entity_id=str(entry.id), request=request,
    )
    await db.commit()
    return entry_out(entry)


@router.post("/clock-out", response_model=EntryOut)
async def clock_out(
    body: ClockBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
    user: User = Depends(get_current_user),
):
    entry = await _open_entry(db, emp)
    if entry is None:
        raise HTTPException(status_code=409, detail={"code": "timeclock.not_open"})
    entry.clock_out = datetime.now(UTC)
    if body.note:
        entry.note = (entry.note + " | " if entry.note else "") + body.note
    await record_audit(
        db, actor=user, action="timeclock.clock_out", entity_type="time_entry",
        entity_id=str(entry.id), request=request,
    )
    await db.commit()
    return entry_out(entry)
