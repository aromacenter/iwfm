"""Vezérlőpult — rendszerüzenetek és fontos állapotok egy helyen.

Mit mutat (manager+):
* függő távollét-kérelmek (szabadság, táppénz, …) — azonnali döntéssel
* ma távol lévők
* éppen beblokkolt dolgozók
* jövő heti piszkozat-műszakok (még nem közölt beosztás figyelmeztetés)
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db import get_db
from app.models import Employee, Shift, Task, TimeEntry, TimeOffRequest, User

router = APIRouter()


@router.get("")
async def dashboard(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("manager")),
):
    today = date.today()
    next_monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)

    pending = (
        await db.execute(
            select(TimeOffRequest, Employee.last_name, Employee.first_name)
            .join(Employee, Employee.id == TimeOffRequest.employee_id)
            .where(TimeOffRequest.status == "pending")
            .order_by(TimeOffRequest.created_at)
        )
    ).all()

    on_leave = (
        await db.execute(
            select(TimeOffRequest, Employee.last_name, Employee.first_name)
            .join(Employee, Employee.id == TimeOffRequest.employee_id)
            .where(
                TimeOffRequest.status == "approved",
                TimeOffRequest.start_date <= today,
                TimeOffRequest.end_date >= today,
            )
        )
    ).all()

    clocked_in = (
        await db.execute(
            select(TimeEntry, Employee.last_name, Employee.first_name)
            .join(Employee, Employee.id == TimeEntry.employee_id)
            .where(TimeEntry.clock_out.is_(None))
            .order_by(TimeEntry.clock_in)
        )
    ).all()

    draft_count = (
        await db.execute(
            select(func.count())
            .select_from(Shift)
            .where(
                Shift.status == "draft",
                Shift.work_date >= next_monday,
                Shift.work_date <= next_monday + timedelta(days=6),
            )
        )
    ).scalar_one()

    active_employees = (
        await db.execute(
            select(func.count()).select_from(Employee).where(Employee.status == "active")
        )
    ).scalar_one()

    todays_tasks = (
        await db.execute(
            select(Task, Employee.last_name, Employee.first_name)
            .join(Employee, Employee.id == Task.employee_id)
            .where(Task.due_date == today)
            .order_by(Task.status, Task.created_at)
        )
    ).all()

    return {
        "todays_tasks": [
            {
                "id": str(t.id),
                "title": t.title,
                "employee_name": f"{ln} {fn}",
                "status": t.status,
            }
            for t, ln, fn in todays_tasks
        ],
        "pending_time_off": [
            {
                "id": str(t.id),
                "employee_name": f"{ln} {fn}",
                "type": t.type,
                "start_date": t.start_date.isoformat(),
                "end_date": t.end_date.isoformat(),
                "reason": t.reason,
                "created_at": t.created_at.isoformat(),
            }
            for t, ln, fn in pending
        ],
        "on_leave_today": [
            {
                "employee_name": f"{ln} {fn}",
                "type": t.type,
                "until": t.end_date.isoformat(),
            }
            for t, ln, fn in on_leave
        ],
        "clocked_in_now": [
            {
                "employee_name": f"{ln} {fn}",
                "since": e.clock_in.isoformat(),
            }
            for e, ln, fn in clocked_in
        ],
        "next_week": {
            "week_start": next_monday.isoformat(),
            "draft_shifts": draft_count,
        },
        "active_employees": active_employees,
    }
