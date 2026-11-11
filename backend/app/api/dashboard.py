"""Vezérlőpult — rendszerüzenetek és fontos állapotok egy helyen.

Mit mutat (manager+):
* függő távollét-kérelmek (szabadság, táppénz, …) — azonnali döntéssel
* ma távol lévők
* éppen beblokkolt dolgozók
* jövő heti piszkozat-műszakok (még nem közölt beosztás figyelmeztetés)
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

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
    tickets: dict = {}
    service_cost: dict = {}
    for pid, parts, labor in (
        await db.execute(
            select(ServiceTicket.partner_id, ServiceTicket.parts, ServiceTicket.labor_fee)
            .where(
                ServiceTicket.partner_id.is_not(None),
                ServiceTicket.created_at >= since,
            )
        )
    ).all():
        tickets[pid] = tickets.get(pid, 0) + 1
        cost = float(labor or 0) + sum(
            float(p.get("qty", 0)) * float(p.get("unit_price", 0))
            for p in (parts or [])
            if isinstance(p, dict)
        )
        service_cost[pid] = service_cost.get(pid, 0.0) + cost
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
            "service_cost": round(service_cost.get(pid, 0.0)),
            "per_visit": round(gross / cnt) if cnt else 0,
        })
    rows.sort(key=lambda r: -r["gross"])
    return {"top": rows[:15], "bottom": [r for r in reversed(rows[-15:])] if len(rows) > 15 else []}


@router.get("/coverage")
async def coverage_by_city(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("dashboard")),
):
    """Lefedettség településenként: aktív partnerek, kihelyezett gépek és 12
    havi bruttó bevétel — hol sűrű és hol vékony a jelenlét."""
    from app.models import Asset

    since = datetime.now() - timedelta(days=365)
    partners = (
        await db.execute(select(Partner).where(Partner.is_active.is_(True)))
    ).scalars().all()
    city_of = {p.id: (p.address_city or "").strip().title() or "Ismeretlen" for p in partners}

    machines: dict = {}
    for pid, cnt in (
        await db.execute(
            select(Asset.partner_id, func.count())
            .where(Asset.partner_id.is_not(None), Asset.status == "deployed")
            .group_by(Asset.partner_id)
        )
    ).all():
        machines[pid] = int(cnt)
    revenue: dict = {}
    for pid, gross in (
        await db.execute(
            select(Settlement.partner_id, func.sum(Settlement.total_gross))
            .where(Settlement.created_at >= since)
            .group_by(Settlement.partner_id)
        )
    ).all():
        revenue[pid] = float(gross or 0)

    cities: dict[str, dict] = {}
    for p in partners:
        c = cities.setdefault(
            city_of[p.id], {"city": city_of[p.id], "partners": 0, "machines": 0, "gross": 0.0}
        )
        c["partners"] += 1
        c["machines"] += machines.get(p.id, 0)
        c["gross"] += revenue.get(p.id, 0.0)
    rows = sorted(cities.values(), key=lambda r: -r["gross"])
    for r in rows:
        r["gross"] = round(r["gross"])
    return rows[:60]


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
    _threshold = func.coalesce(PartnerStock.min_quantity, Product.low_stock_threshold)
    low_stock_count = (
        await db.execute(
            select(func.count())
            .select_from(PartnerStock)
            .join(Product, Product.id == PartnerStock.product_id)
            .where(
                _threshold.is_not(None),
                Product.is_active.is_(True),
                PartnerStock.quantity <= _threshold,
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


@router.get("/carry-over")
async def carry_over_stats(
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("dashboard")),
):
    """153. kör: kinek mennyi feladata csúszott át automatikusan — a most is
    nyitott átcsúszottak, és az elmúlt N napban átcsúszottak száma (elvégzettekkel
    együtt), személyenként. Számonkéréshez."""
    from app.models import Task

    days = max(1, min(int(days), 365))
    today = date.today()
    since = today - timedelta(days=days)
    rows = (
        await db.execute(
            select(Task, Employee.last_name, Employee.first_name)
            .join(Employee, Employee.id == Task.employee_id)
            .where(Task.carry_over_count > 0)
        )
    ).all()
    per: dict = {}
    for t, ln, fn in rows:
        entry = per.setdefault(str(t.employee_id), {
            "employee_id": str(t.employee_id), "employee_name": f"{ln} {fn}",
            "open_carried": 0, "open_days_total": 0, "carried_recent": 0, "oldest_original": None,
        })
        recent = (t.original_due_date or t.due_date) >= since
        if t.status in ("open", "needs_more_work"):
            entry["open_carried"] += 1
            entry["open_days_total"] += (today - (t.original_due_date or t.due_date)).days
            if entry["oldest_original"] is None or (t.original_due_date or t.due_date) < date.fromisoformat(entry["oldest_original"]):
                entry["oldest_original"] = (t.original_due_date or t.due_date).isoformat()
        if recent:
            entry["carried_recent"] += 1
    out = sorted(per.values(), key=lambda e: (-e["open_carried"], -e["carried_recent"], e["employee_name"]))
    return {"days": days, "employees": out, "open_total": sum(e["open_carried"] for e in out)}


@router.get("/rework-stats")
async def rework_stats(
    days: int = 90,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("dashboard")),
):
    """154. kör (Senzortech 481ab7f4): kinek és melyik csapatnak hány munkája volt az
    elmúlt N napban, és ebből mennyi a visszatérő / garanciális javítás. A csapat
    = a felelős + a segítők együtt (név szerint, ábécérendben)."""
    from app.models import Task, TaskHelper

    days = max(7, min(int(days), 730))
    since = date.today() - timedelta(days=days)
    tasks = (
        await db.execute(
            select(Task, Employee.last_name, Employee.first_name)
            .join(Employee, Employee.id == Task.employee_id)
            .where(Task.due_date >= since)
        )
    ).all()
    helpers = (
        await db.execute(
            select(TaskHelper.task_id, TaskHelper.employee_id, Employee.last_name, Employee.first_name)
            .join(Employee, Employee.id == TaskHelper.employee_id)
        )
    ).all()
    helpers_by_task: dict = {}
    for tid, eid, ln, fn in helpers:
        helpers_by_task.setdefault(tid, []).append((str(eid), f"{ln} {fn}"))

    people: dict = {}
    teams: dict = {}

    def bump(bucket: dict, key: str, name: str, members: list[str], rework: bool) -> None:
        e = bucket.setdefault(key, {"id": key, "name": name, "members": members, "tasks": 0, "rework": 0})
        e["tasks"] += 1
        if rework:
            e["rework"] += 1

    for t, ln, fn in tasks:
        crew = [(str(t.employee_id), f"{ln} {fn}")] + helpers_by_task.get(t.id, [])
        for eid, name in crew:
            bump(people, eid, name, [], bool(t.rework))
        if len(crew) > 1:
            names = sorted(n for _, n in crew)
            bump(teams, "|".join(sorted(e for e, _ in crew)), " + ".join(names), names, bool(t.rework))

    def finish(rows: list) -> list:
        for r in rows:
            r["rework_pct"] = round(100 * r["rework"] / r["tasks"]) if r["tasks"] else 0
        return sorted(rows, key=lambda r: (-r["rework"], -r["rework_pct"], -r["tasks"], r["name"]))

    return {
        "days": days,
        "people": finish(list(people.values())),
        "teams": finish(list(teams.values())),
        "rework_total": sum(1 for t, _, _ in tasks if t.rework),
        "tasks_total": len(tasks),
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


@router.get("/receivables-stats")
async def receivables_stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("dashboard")),
):
    """Kintlévőség-statisztika a vezérlőpultra: ki, mennyivel, mióta tartozik.

    A ki nem egyenlített elszámolásokból (bruttó − fizetett > 0) épül — a
    Billingó fizetés-szinkron (sync-payment) után ez a friss állapot.
    Korosítás a bizonylat kelte szerint: 0–30 / 31–60 / 61–90 / 90+ nap."""
    # UTC-dátum, mert a created_at is UTC — helyi dátummal éjfél körül a
    # másodperces tartozás is "1 naposnak" látszana.
    today = datetime.now(UTC).date()
    rows = (
        await db.execute(
            select(Settlement, Partner.name)
            .join(Partner, Partner.id == Settlement.partner_id)
            .order_by(Settlement.created_at)
        )
    ).all()

    aging = {"b0_30": 0.0, "b31_60": 0.0, "b61_90": 0.0, "b90_plus": 0.0}
    by_partner: dict = {}
    total = 0.0
    overdue_total = 0.0
    for s, pname in rows:
        paid = s.paid_amount if s.paid_amount is not None else s.total_gross
        remaining = round(s.total_gross - paid, 2)
        if remaining <= 0.5:
            continue
        total += remaining
        days = (today - s.created_at.date()).days
        if days <= 30:
            aging["b0_30"] += remaining
        elif days <= 60:
            aging["b31_60"] += remaining
        elif days <= 90:
            aging["b61_90"] += remaining
        else:
            aging["b90_plus"] += remaining
        if s.due_date and s.due_date < today:
            overdue_total += remaining
        entry = by_partner.setdefault(
            s.partner_id,
            {"partner_id": str(s.partner_id), "partner_name": pname,
             "remaining": 0.0, "items": 0, "oldest_at": s.created_at.date(),
             "overdue_items": 0},
        )
        entry["remaining"] += remaining
        entry["items"] += 1
        if s.created_at.date() < entry["oldest_at"]:
            entry["oldest_at"] = s.created_at.date()
        if s.due_date and s.due_date < today:
            entry["overdue_items"] += 1

    debtors = sorted(by_partner.values(), key=lambda x: -x["remaining"])
    for d in debtors:
        d["remaining"] = round(d["remaining"], 2)
        d["oldest_days"] = (today - d["oldest_at"]).days
        d["oldest_at"] = d["oldest_at"].isoformat()

    return {
        "total": round(total, 2),
        "overdue_total": round(overdue_total, 2),
        "aging": {k: round(v, 2) for k, v in aging.items()},
        "debtor_count": len(debtors),
        "top_debtors": debtors[:15],
    }
