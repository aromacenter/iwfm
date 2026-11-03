"""Employee self-service (a dolgozó saját nézete — telefonon).

Strict ownership: every query is filtered to the employee record linked to
the logged-in user. No cross-employee access is possible from here (IDOR-safe
by construction).

* GET  /me/schedule    — saját PUBLIKÁLT műszakok (a draft nem látszik!)
* GET  /me/time-off    — saját kérelmek
* POST /me/time-off    — új kérelem
* GET  /me/clock       — nyitott bejegyzés állapota (CSAK olvasás)
* GET  /me/profile     — név + törzsszám
* GET  /me/data-export — saját adatok JSON-ban (GDPR 15./20. cikk), naplózva

Be-/kiblokkolás telefonról szándékosan NEM lehetséges (visszaélés-veszély):
a munkaidő rögzítése kizárólag a helyszíni blokkoló-terminálon történik
(/api/kiosk/clock, törzsszámmal).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import set_session_cookie
from app.api.deps import get_current_user, get_own_employee, record_audit
from app.api.shifts import ShiftOut, _shift_out, _week_bounds
from app.api.timeclock import to_out as entry_out
from app.api.timeoff import TimeOffOut, ensure_no_overlap, to_out as timeoff_out, validate_range
from app.core.crypto import decrypt_pii
from app.core.security import hash_password, mint_token, verify_password
from app.db import get_db
from app.models import AuditEvent, Employee, Shift, Task, TimeEntry, TimeOffRequest, User

router = APIRouter()


class MyTimeOffCreate(BaseModel):
    type: str = "annual"
    start_date: date
    end_date: date
    reason: str | None = Field(default=None, max_length=512)
    at_time: str | None = Field(default=None, max_length=5)  # 138. kör
    minutes: int | None = Field(default=None, ge=1, le=720)


@router.get("/profile")
async def my_profile(emp: Employee = Depends(get_own_employee)):
    """Saját alapadatok az önkiszolgáló fejléchez (név + törzsszám)."""
    return {
        "name": f"{emp.last_name} {emp.first_name}",
        "employee_code": emp.employee_code,
        "job_title": emp.job_title,
    }


@router.get("/data-export")
async def my_data_export(
    request: Request,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
    user: User = Depends(get_current_user),
):
    """GDPR 15./20. cikk (hozzáférés / adathordozhatóság): a dolgozó SAJÁT
    törzsadatai géppel olvasható JSON-ban — az érzékeny azonosítók
    visszafejtve (a saját adata), jelszó-hash nélkül. Naplózva."""
    employee = {
        "id": str(emp.id),
        "employee_code": emp.employee_code,
        "status": emp.status,
        "last_name": emp.last_name,
        "first_name": emp.first_name,
        "birth_name": emp.birth_name,
        "mother_name": emp.mother_name,
        "birth_place": emp.birth_place,
        "birth_date": emp.birth_date,
        "citizenship": emp.citizenship,
        "phone": emp.phone,
        "address": emp.address,
        "residence_address": emp.residence_address,
        "tax_id": decrypt_pii(emp.tax_id_encrypted),
        "taj": decrypt_pii(emp.taj_encrypted),
        "bank_account": decrypt_pii(emp.bank_account_encrypted),
        "wage_amount": decrypt_pii(emp.wage_encrypted),
        "hire_date": emp.hire_date,
        "termination_date": emp.termination_date,
        "job_title": emp.job_title,
        "feor_code": emp.feor_code,
        "employment_type": emp.employment_type,
        "contract_type": emp.contract_type,
        "weekly_hours": emp.weekly_hours,
        "wage_type": emp.wage_type,
        "annual_leave_days": emp.annual_leave_days,
        "payroll_source": emp.payroll_source,
        "site": emp.site,
        "sunday_bonus": emp.sunday_bonus,
        "shift_bonus": emp.shift_bonus,
        "bonus_in_base_salary": emp.bonus_in_base_salary,
        "break_rule": emp.break_rule,
        "availability": emp.availability,
        "is_contractor": emp.is_contractor,
        "is_company": emp.is_company,
        "company_name": emp.company_name,
        "company_technicians": emp.company_technicians,
        "company_tax_number": emp.company_tax_number,
        "telegram_linked": emp.telegram_chat_id is not None,
        "notes": emp.notes,
        "created_at": emp.created_at,
        "updated_at": emp.updated_at,
    }
    user_out = {
        "id": str(user.id),
        "email": user.email,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at,
    }

    async def _count(model, *where) -> int:
        return int(
            (await db.execute(select(func.count()).select_from(model).where(*where))).scalar_one()
        )

    counts = {
        "time_entries": await _count(TimeEntry, TimeEntry.employee_id == emp.id),
        "time_off_requests": await _count(TimeOffRequest, TimeOffRequest.employee_id == emp.id),
        "shifts": await _count(Shift, Shift.employee_id == emp.id),
        "tasks_assigned": await _count(Task, Task.employee_id == emp.id),
        "audit_events_as_actor": await _count(AuditEvent, AuditEvent.actor_user_id == user.id),
    }

    await record_audit(
        db, actor=user, action="me.data_export", entity_type="employee",
        entity_id=str(emp.id), request=request,
    )
    await db.commit()

    payload = jsonable_encoder(
        {
            "exported_at": datetime.now(UTC),
            "employee": employee,
            "user": user_out,
            "counts": counts,
        }
    )
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": 'attachment; filename="sajat-adatok.json"'},
    )


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
    from app.api.timeoff import PARTIAL_TYPES, validate_partial

    validate_range(body.type, body.start_date, body.end_date)
    at_time = validate_partial(body.type, body.start_date, body.end_date, body.at_time)
    await ensure_no_overlap(db, emp.id, body.start_date, body.end_date, type_=body.type)
    req = TimeOffRequest(
        employee_id=emp.id,
        type=body.type,
        start_date=body.start_date,
        end_date=body.end_date,
        reason=body.reason,
        at_time=at_time,
        minutes=body.minutes if body.type in PARTIAL_TYPES else None,
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
