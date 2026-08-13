"""Employee self-service (a dolgozó saját nézete — telefonon).

Strict ownership: every query is filtered to the employee record linked to
the logged-in user. No cross-employee access is possible from here (IDOR-safe
by construction).

* GET  /me/schedule    — saját PUBLIKÁLT műszakok (a draft nem látszik!)
* GET  /me/time-off    — saját kérelmek
* POST /me/time-off    — új kérelem
* GET  /me/clock       — nyitott bejegyzés állapota (CSAK olvasás)
* GET  /me/profile     — név + törzsszám

Be-/kiblokkolás telefonról szándékosan NEM lehetséges (visszaélés-veszély):
a munkaidő rögzítése kizárólag a helyszíni blokkoló-terminálon történik
(/api/kiosk/clock, törzsszámmal).
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import set_session_cookie
from app.api.deps import get_current_user, get_own_employee, record_audit
from app.api.shifts import ShiftOut, _shift_out, _week_bounds
from app.api.timeclock import to_out as entry_out
from app.api.timeoff import TimeOffOut, ensure_no_overlap, to_out as timeoff_out, validate_range
from app.core.security import hash_password, mint_token, verify_password
from app.db import get_db
from app.models import Employee, Shift, TimeEntry, TimeOffRequest, User

router = APIRouter()


class MyTimeOffCreate(BaseModel):
    type: str = "annual"
    start_date: date
    end_date: date
    reason: str | None = Field(default=None, max_length=512)


@router.get("/profile")
async def my_profile(emp: Employee = Depends(get_own_employee)):
    """Saját alapadatok az önkiszolgáló fejléchez (név + törzsszám)."""
    return {
        "name": f"{emp.last_name} {emp.first_name}",
        "employee_code": emp.employee_code,
        "job_title": emp.job_title,
    }


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


@router.post("/password")
async def change_password(
    body: PasswordChange,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Saját jelszó módosítása (bármely szerepkör). A token_version növelésével
    minden korábbi munkamenet érvénytelenné válik; az aktuális kérés friss
    sütit kap, hogy bejelentkezve maradjon."""
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status_code=403, detail={"code": "auth.bad_password"})
    if body.new_password == body.current_password:
        raise HTTPException(status_code=422, detail={"code": "auth.same_password"})
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    await record_audit(
        db, actor=user, action="auth.password_change", entity_type="user",
        entity_id=str(user.id), request=request,
    )
    await db.commit()
    set_session_cookie(
        response, mint_token(user_id=user.id, role=user.role, token_version=user.token_version)
    )
    return {"ok": True}


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
    await ensure_no_overlap(db, emp.id, body.start_date, body.end_date)
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


# A korábbi POST /me/clock-in és /me/clock-out végpontok szándékosan
# megszűntek: a blokkolás kizárólag a helyszíni terminálon (kiosk) lehetséges,
# mert a telefonos blokkolás visszaélésre adhat lehetőséget.
