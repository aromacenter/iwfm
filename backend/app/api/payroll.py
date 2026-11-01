"""Payroll export endpoint: aggregates schedule + attendance + leave per
employee for a period and streams CSV/XLSX with decrypted identifiers."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.core.crypto import decrypt_pii
from app.db import get_db
from app.models import Employee, Shift, TimeEntry, TimeOffRequest, User, WorkRulesSettings
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
    actor: User = Depends(require_perm("payroll")),
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
    skipped_no_taj = 0
    for emp in employees:
        # c6af18d8: a külsős szervizesek és a külsős szervizcégek emberei nem
        # bérszámfejtettek nálunk — ismertetőjelük, hogy nincs TAJ-számuk rögzítve.
        taj = decrypt_pii(emp.taj_encrypted)
        if not (taj or "").strip():
            skipped_no_taj += 1
            continue
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
                taj=taj,
                bank_account=decrypt_pii(emp.bank_account_encrypted),
                feor_code=emp.feor_code,
                job_title=emp.job_title,
                weekly_hours=emp.weekly_hours,
                scheduled_hours=scheduled,
                worked_hours=worked,
                payroll_source=emp.payroll_source or "attendance",
                annual_leave_days=leave_days["annual"],
                sick_days=leave_days["sick"],
                unpaid_days=leave_days["unpaid"],
                other_days=leave_days["other"],
            )
        )

    await record_audit(
        db, actor=actor, action="payroll.export", entity_type="payroll",
        entity_id=f"{period_start}_{period_end}",
        detail={"format": format, "employees": len(rows), "skipped_no_taj": skipped_no_taj},
        request=request,
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


# ── Havi munkaidő-nyilvántartás (117. kör) ───────────────────────────────────
# Dolgozónként egy oldal a mintapéldány szerkezetében — spec:
# docs/berexport-munkaido-nyilvantartas-spec.md


@router.get("/timesheet.pdf")
async def timesheet_pdf(
    year: int = Query(..., ge=2012, le=2100),
    month: int = Query(..., ge=1, le=12),
    employee_id: str | None = Query(default=None),
    site: str | None = Query(default=None, max_length=128),
    split: str = Query(default="none", pattern="^(none|per_site)$"),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("payroll")),
):
    import calendar as _cal
    import io as _io
    import zipfile

    from app.models import WorksheetSettings
    from app.services.wfm import license as license_service
    from app.services.wfm.holidays import load_overrides
    from app.services.wfm.timesheet_calc import build_month_sheet, company_rules_from_row
    from app.services.wfm.timesheet_pdf import build_timesheet_pdf

    first = date(year, month, 1)
    last = date(year, month, _cal.monthrange(year, month)[1])
    await load_overrides(db)

    rules_row = (
        await db.execute(select(WorkRulesSettings).where(WorkRulesSettings.id == 1))
    ).scalar_one_or_none()
    company_rules = company_rules_from_row(rules_row)
    lic = await license_service.get_license_row(db)
    overrides = (lic.labor_rules_override if lic is not None else None) or []
    ws = (
        await db.execute(select(WorksheetSettings).where(WorksheetSettings.id == 1))
    ).scalar_one_or_none()
    company = (ws.company_name if ws and ws.company_name else None) or (
        lic.customer_name if lic and lic.customer_name else None
    ) or "X-admin"

    q = select(Employee).where(
        Employee.is_contractor.is_(False),
        Employee.hire_date <= last,
    ).order_by(Employee.last_name, Employee.first_name)
    employees = [
        e for e in (await db.execute(q)).scalars().all()
        if (e.termination_date is None or e.termination_date >= first)
        and (employee_id is None or str(e.id) == employee_id)
        and (site is None or (e.site or "") == site)
    ]
    # külsősök (TAJ nélkül) kimaradnak — ugyanaz a szűrő, mint a CSV-nél (spec 6.9)
    employees = [e for e in employees if (decrypt_pii(e.taj_encrypted) or "").strip()]
    emp_ids = {e.id for e in employees}

    shifts = (await db.execute(select(Shift).where(
        Shift.status == "published", Shift.work_date >= first, Shift.work_date <= last,
    ))).scalars().all()
    lo = datetime.combine(first, time.min, tzinfo=UTC) - timedelta(days=1)
    hi = datetime.combine(last, time.max, tzinfo=UTC) + timedelta(days=1)
    entries = (await db.execute(select(TimeEntry).where(
        TimeEntry.clock_in >= lo, TimeEntry.clock_in <= hi, TimeEntry.clock_out.is_not(None),
    ))).scalars().all()
    leaves = (await db.execute(select(TimeOffRequest).where(
        TimeOffRequest.status == "approved",
        TimeOffRequest.start_date <= last, TimeOffRequest.end_date >= first,
    ))).scalars().all()

    sheets = []
    for emp in employees:
        sheets.append(build_month_sheet(
            emp, year=year, month=month,
            shifts=[x for x in shifts if x.employee_id == emp.id],
            entries=[x for x in entries if x.employee_id == emp.id],
            leaves=[x for x in leaves if x.employee_id == emp.id],
            company=company_rules, overrides=overrides,
            tax_id=decrypt_pii(emp.tax_id_encrypted),
        ))
    if company_rules.group_by_site:
        sheets.sort(key=lambda sh: ((sh.site or "").lower(), sh.name.lower()))
    else:
        sheets.sort(key=lambda sh: sh.name.lower())

    host = (request.headers.get("x-forwarded-host") or request.url.hostname or "") if request else ""
    source_line = f"https://{host} - X-admin" if host else "X-admin"
    period = f"{year}-{month:02d}"

    await record_audit(
        db, actor=actor, action="payroll.timesheet", entity_type="payroll",
        entity_id=period,
        detail={"employees": len(sheets), "split": split, "site": site,
                "rules_valid_from": sheets[0].rules.valid_from.isoformat() if sheets else None},
        request=request,
    )
    await db.commit()

    if split == "per_site":
        buf = _io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            groups: dict[str, list] = {}
            for sh in sheets:
                groups.setdefault(sh.site or "kozpont", []).append(sh)
            for site_name, group in groups.items():
                safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in site_name)[:40] or "telephely"
                zf.writestr(
                    f"munkaido_nyilvantartas_{period}_{safe}.pdf",
                    build_timesheet_pdf(group, company=company, source_line=source_line),
                )
        return Response(
            content=buf.getvalue(), media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="munkaido_nyilvantartas_{period}.zip"'},
        )
    content = build_timesheet_pdf(sheets, company=company, source_line=source_line)
    return Response(
        content=content, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="munkaido_nyilvantartas_{period}.pdf"'},
    )
