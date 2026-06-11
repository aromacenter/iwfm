"""Payroll export endpoint: aggregates schedule + attendance + leave per
employee for a period and streams CSV/XLSX with decrypted identifiers."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_role
from app.core.crypto import decrypt_pii
from app.db import get_db
from app.models import Employee, Shift, TimeEntry, TimeOffRequest, User
from app.services.wfm.compliance import ShiftSpan
from app.services.wfm.payroll_export import (
    PayrollRow,
    build_csv,
    build_xlsx,
    overlap_days,
)

router = APIRouter()


@router.get("/export")
async def export_payroll(
    period_start: date = Query(...),
    period_end: date = Query(...),
    format: str = Query(default="csv", pattern="^(csv|xlsx)$"),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    if period_end < period_start:
        raise HTTPException(status_code=422, detail={"code": "payroll.bad_range"})
    if (period_end - period_start).days > 92:
        raise HTTPException(status_code=422, detail={"code": "payroll.range_too_long"})

    employees = (
        (await db.execute(select(Employee).order_by(Employee.last_name, Employee.first_name)))
        .scalars()
        .all()
    )
    shifts = (
        (
            await db.execute(
                select(Shift).where(
                    Shift.status == "published",
                    Shift.work_date >= period_start,
                    Shift.work_date <= period_end,
                )
            )
        )
        .scalars()
        .all()
    )
    entries = (
        (
            await db.execute(
                select(TimeEntry).where(
                    TimeEntry.clock_in >= datetime.combine(period_start, time.min, tzinfo=UTC),
                    TimeEntry.clock_in <= datetime.combine(period_end, time.max, tzinfo=UTC),
                    TimeEntry.clock_out.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    leaves = (
        (
            await db.execute(
                select(TimeOffRequest).where(
                    TimeOffRequest.status == "approved",
                    TimeOffRequest.start_date <= period_end,
                    TimeOffRequest.end_date >= period_start,
                )
            )
        )
        .scalars()
        .all()
    )

    rows: list[PayrollRow] = []
    for emp in employees:
        emp_shifts = [s for s in shifts if s.employee_id == emp.id]
        scheduled = sum(
            ShiftSpan(
                id=s.id, employee_id=s.employee_id, work_date=s.work_date,
                start_time=s.start_time, end_time=s.end_time, break_minutes=s.break_minutes,
            ).worked_hours
            for s in emp_shifts
        )
        worked = sum(
            max(((e.clock_out - e.clock_in).total_seconds() / 60 - e.break_minutes), 0) / 60
            for e in entries
            if e.employee_id == emp.id
        )
        leave_days = {"annual": 0, "sick": 0, "unpaid": 0, "other": 0}
        for leave in leaves:
            if leave.employee_id == emp.id:
                leave_days[leave.type] += overlap_days(
                    leave.start_date, leave.end_date, period_start, period_end
                )

        rows.append(
            PayrollRow(
                name=f"{emp.last_name} {emp.first_name}",
                tax_id=decrypt_pii(emp.tax_id_encrypted),
                taj=decrypt_pii(emp.taj_encrypted),
                bank_account=decrypt_pii(emp.bank_account_encrypted),
                feor_code=emp.feor_code,
                job_title=emp.job_title,
                weekly_hours=emp.weekly_hours,
                scheduled_hours=scheduled,
                worked_hours=worked,
                annual_leave_days=leave_days["annual"],
                sick_days=leave_days["sick"],
                unpaid_days=leave_days["unpaid"],
                other_days=leave_days["other"],
            )
        )

    await record_audit(
        db, actor=actor, action="payroll.export", entity_type="payroll",
        entity_id=f"{period_start}_{period_end}",
        detail={"format": format, "employees": len(rows)}, request=request,
    )
    await db.commit()

    filename = f"berexport_{period_start}_{period_end}.{format}"
    if format == "xlsx":
        content = build_xlsx(rows, period_start, period_end)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        content = build_csv(rows, period_start, period_end)
        media = "text/csv; charset=utf-8"
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
