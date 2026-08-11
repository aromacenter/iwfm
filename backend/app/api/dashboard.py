"""Vezérlőpult — rendszerüzenetek és fontos állapotok egy helyen.

Mit mutat (manager+):
* függő távollét-kérelmek (szabadság, táppénz, …) — azonnali döntéssel
* ma távol lévők
* éppen beblokkolt dolgozók
* jövő heti piszkozat-műszakok (még nem közölt beosztás figyelmeztetés)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_perm
from app.db import get_db
from app.models import (
    Employee,
    Partner,
    PartnerStock,
    Product,
    ServiceTicket,
    Settlement,
    Shift,
    Task,
    TimeEntry,
    TimeOffRequest,
    User,
)

router = APIRouter()


@router.get("/partner-performance")
async def partner_performance(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("dashboard")),
):
    """Partner-teljesítmény (12 hónap): bruttó bevétel + elszámolás-darabszám
    partnerenként, mellette gépszám és szervizjegy-darab — top és sereghajtó
    lista (aktív, elszámolós partnerek)."""
    from app.models import Asset

    since = datetime.now() - timedelta(days=365)
    revenue = {
        pid: (float(gross or 0), int(cnt))
        for pid, gross, cnt in (
            await db.execute(
                select(
                    Settlement.partner_id,
                    func.sum(Settlement.total_gross),
                    func.count(),
                )
                .where(Settlement.created_at >= since)
                .group_by(Settlement.partner_id)
            )
        ).all()
    }
    if not revenue:
        return {"top": [], "bottom": []}
    machines = {
        pid: int(cnt)
        for pid, cnt in (
            await db.execute(
                select(Asset.partner_id, func.count())
                .where(Asset.partner_id.is_not(None))
                .group_by(Asset.partner_id)
            )
        ).all()
    }
    tickets = {
        pid: int(cnt)
        for pid, cnt in (
            await db.execute(
                select(ServiceTicket.partner_id, func.count())
                .where(
                    ServiceTicket.partner_id.is_not(None),
                    ServiceTicket.created_at >= since,
                )
                .group_by(ServiceTicket.partner_id)
            )
        ).all()
    }
    partners = {
        p.id: p
        for p in (
            await db.execute(
                select(Partner).where(Partner.id.in_(list(revenue)), Partner.is_active.is_(True))
            )
        ).scalars()
    }

    rows = []
    for pid, (gross, cnt) in revenue.items():
        p = partners.get(pid)
        if p is None:
            continue
        rows.append({
            "partner_id": str(pid),
            "name": p.name,
            "partner_code": p.partner_code,
            "invoicing_company": p.invoicing_company,
            "gross": round(gross),
            "settlements": cnt,
            "machines": machines.get(pid, 0),
            "tickets": tickets.get(pid, 0),
            "per_visit": round(gross / cnt) if cnt else 0,
        })
    rows.sort(key=lambda r: -r["gross"])
    return {"top": rows[:15], "bottom": [r for r in reversed(rows[-15:])] if len(rows) > 15 else []}


@router.get("/consignment-stats")
async def consignment_stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("dashboard")),
):
    """Bizomány-statisztika a vezérlőpulthoz: 12 havi bevétel-trend, top
    partnerek, üzletkötői rangsor, fizetési mód megoszlás, gyors számlálók."""
    today = date.today()
    year_ago = datetime.combine(
        (today.replace(day=1) - timedelta(days=365)).replace(day=1), datetime.min.time()
    )

    settlements = (
        await db.execute(
            select(Settlement, Partner.name)
            .join(Partner, Partner.id == Settlement.partner_id)
            .where(Settlement.created_at >= year_ago)
        )
    ).all()

    # Havi bevétel (utolsó 12 hónap, üres hónapok is)
    months: list[str] = []
    cursor = today.replace(day=1)
    for _i in range(12):
        months.append(cursor.strftime("%Y-%m"))
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    months.reverse()
    monthly = {m: {"gross": 0.0, "count": 0} for m in months}

    top_partners: dict[str, dict] = {}
    agents: dict[str, dict] = {}
    by_payment = {m: {"count": 0, "gross": 0.0} for m in ("cash", "card", "transfer")}
    for s, partner_name in settlements:
        key = s.created_at.strftime("%Y-%m")
        if key in monthly:
            monthly[key]["gross"] += s.total_gross
            monthly[key]["count"] += 1
        bucket = top_partners.setdefault(partner_name, {"name": partner_name, "gross": 0.0, "count": 0})
        bucket["gross"] += s.total_gross
        bucket["count"] += 1
        agent = agents.setdefault(s.settled_by_name, {"name": s.settled_by_name, "gross": 0.0, "count": 0})
        agent["gross"] += s.total_gross
        agent["count"] += 1
        pay = by_payment.get(s.payment_method)
        if pay is not None:
            pay["count"] += 1
            pay["gross"] += s.total_gross

    outstanding_total = (
        await db.execute(
            select(func.coalesce(func.sum(Settlement.total_gross), 0.0)).where(
                Settlement.invoiced.is_(True), Settlement.payment_status == "outstanding"
            )
        )
    ).scalar_one()
    open_tickets = (
        await db.execute(
            select(func.count()).select_from(ServiceTicket).where(
                ServiceTicket.status.in_(("open", "in_progress"))
            )
        )
    ).scalar_one()
    low_stock_count = (
        await db.execute(
            select(func.count())
            .select_from(PartnerStock)
            .join(Product, Product.id == PartnerStock.product_id)
            .where(
                Product.low_stock_threshold.is_not(None),
                Product.is_active.is_(True),
                PartnerStock.quantity <= Product.low_stock_threshold,
            )
        )
    ).scalar_one()

    def _round_all(items: list[dict]) -> list[dict]:
        for item in items:
            item["gross"] = round(item["gross"], 2)
        return items

    return {
        "monthly_revenue": [
            {"month": m, "gross": round(v["gross"], 2), "count": v["count"]}
            for m, v in monthly.items()
        ],
        "top_partners": _round_all(
            sorted(top_partners.values(), key=lambda x: -x["gross"])[:5]
        ),
        "agent_ranking": _round_all(sorted(agents.values(), key=lambda x: -x["gross"])),
        "by_payment": {k: {"count": v["count"], "gross": round(v["gross"], 2)} for k, v in by_payment.items()},
        "outstanding_total": round(float(outstanding_total), 2),
        "open_service_tickets": int(open_tickets),
        "low_stock_count": int(low_stock_count),
    }


@router.get("")
async def dashboard(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("dashboard")),
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
