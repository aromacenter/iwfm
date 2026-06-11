"""Jelenlét (time clock): manager nézet + kézi bejegyzés/javítás.

Employee self clock-in/out lives in me.py; this router is the manager side:
listing entries for a period and creating/correcting entries manually (every
manual edit is audited — payroll integrity).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_role
from app.db import get_db
from app.models import Employee, TimeEntry, User

router = APIRouter()


class EntryCreate(BaseModel):
    employee_id: str
    clock_in: datetime
    clock_out: datetime | None = None
    break_minutes: int = Field(default=0, ge=0, le=240)
    note: str | None = Field(default=None, max_length=512)


class EntryPatch(BaseModel):
    model_config = {"extra": "forbid"}
    clock_in: datetime | None = None
    clock_out: datetime | None = None
    break_minutes: int | None = Field(default=None, ge=0, le=240)
    note: str | None = None


class EntryOut(BaseModel):
    id: str
    employee_id: str
    employee_name: str | None = None
    clock_in: datetime
    clock_out: datetime | None
    break_minutes: int
    worked_minutes: int | None
    source: str
    note: str | None


def worked_minutes(entry: TimeEntry) -> int | None:
    if entry.clock_out is None:
        return None
    # SQLite naive / PostgreSQL aware értékek keveredhetnek — egységesítünk.
    clock_in = _aware(entry.clock_in)
    clock_out = _aware(entry.clock_out)
    delta = (clock_out - clock_in).total_seconds() / 60
    return max(int(delta) - entry.break_minutes, 0)


def to_out(entry: TimeEntry, employee_name: str | None = None) -> EntryOut:
    return EntryOut(
        id=str(entry.id),
        employee_id=str(entry.employee_id),
        employee_name=employee_name,
        clock_in=entry.clock_in,
        clock_out=entry.clock_out,
        break_minutes=entry.break_minutes,
        worked_minutes=worked_minutes(entry),
        source=entry.source,
        note=entry.note,
    )


def _aware(value: datetime | None) -> datetime | None:
    """Coerce naive client datetimes to UTC (PostgreSQL timestamptz needs tz)."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _validate_times(clock_in: datetime, clock_out: datetime | None) -> None:
    if clock_out is not None and clock_out <= clock_in:
        raise HTTPException(status_code=422, detail={"code": "timeclock.bad_range"})


@router.get("", response_model=list[EntryOut])
async def list_entries(
    date_from: date = Query(...),
    date_to: date = Query(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("manager")),
):
    rows = (
        await db.execute(
            select(TimeEntry, Employee.last_name, Employee.first_name)
            .join(Employee, Employee.id == TimeEntry.employee_id)
            .where(
                TimeEntry.clock_in >= datetime.combine(date_from, time.min, tzinfo=UTC),
                TimeEntry.clock_in <= datetime.combine(date_to, time.max, tzinfo=UTC),
            )
            .order_by(TimeEntry.clock_in.desc())
        )
    ).all()
    return [to_out(e, f"{ln} {fn}") for e, ln, fn in rows]


@router.post("", response_model=EntryOut, status_code=201)
async def create_entry(
    body: EntryCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    body.clock_in = _aware(body.clock_in)
    body.clock_out = _aware(body.clock_out)
    _validate_times(body.clock_in, body.clock_out)
    try:
        emp_id = uuid.UUID(body.employee_id)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "timeclock.bad_employee"})
    emp = (
        await db.execute(select(Employee).where(Employee.id == emp_id))
    ).scalar_one_or_none()
    if emp is None:
        raise HTTPException(status_code=422, detail={"code": "timeclock.bad_employee"})

    entry = TimeEntry(
        employee_id=emp_id,
        clock_in=body.clock_in,
        clock_out=body.clock_out,
        break_minutes=body.break_minutes,
        note=body.note,
        source="manual",
        created_by=actor.id,
    )
    db.add(entry)
    await db.flush()
    await record_audit(
        db, actor=actor, action="timeclock.manual_create", entity_type="time_entry",
        entity_id=str(entry.id), request=request,
    )
    await db.commit()
    return to_out(entry, f"{emp.last_name} {emp.first_name}")


@router.patch("/{entry_id}", response_model=EntryOut)
async def update_entry(
    entry_id: str,
    body: EntryPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    try:
        eid = uuid.UUID(entry_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "timeclock.not_found"})
    entry = (
        await db.execute(select(TimeEntry).where(TimeEntry.id == eid))
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail={"code": "timeclock.not_found"})

    data = body.model_dump(exclude_unset=True)
    for key in ("clock_in", "clock_out"):
        if key in data:
            data[key] = _aware(data[key])
    new_in = data.get("clock_in", entry.clock_in)
    new_out = data.get("clock_out", entry.clock_out)
    _validate_times(_aware(new_in), _aware(new_out))

    for key, value in data.items():
        setattr(entry, key, value)
    entry.edited_by = actor.id
    await record_audit(
        db, actor=actor, action="timeclock.manual_edit", entity_type="time_entry",
        entity_id=str(entry.id), detail={"fields": sorted(data)}, request=request,
    )
    await db.commit()
    return to_out(entry)
