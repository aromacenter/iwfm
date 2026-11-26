"""Szerelői teljesítmény-riport (188. kör).

Időszakra, szerelőnként: lezárt szervizjegyek és átlagos átfutás (nyitás →
lezárás, óra), visszatérő hibák aránya (ugyanarra a gépre 30 napon belül új
javítási jegy), elvégzett munkalapok, ráfordított órák, kiszámlázott érték
(munkák + anyagok ügyfél-ára + karbantartási díj), ügyfél-értékelés átlaga.
Az előző, azonos hosszú időszakhoz képest Δ; a csapatátlagtól kilógó értékek
jelölve (flags).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_perm
from app.db import get_db
from app.models import Employee, ServiceTicket, Task, User, Worksheet
from app.services.wfm.license import require_module

router = APIRouter()

REPEAT_WINDOW = timedelta(days=30)


class TechRow(BaseModel):
    employee_id: str
    name: str
    tickets_closed: int
    tickets_closed_prev: int
    avg_resolve_hours: float | None
    avg_resolve_hours_prev: float | None
    repeat_count: int
    repeat_pct: float | None
    worksheets: int
    worksheets_prev: int
    hours: float
    invoiced_net: float
    rating_avg: float | None
    rating_count: int
    flags: list[str]


class TechReport(BaseModel):
    date_from: date | None
    date_to: date | None
    team_avg_resolve_hours: float | None
    team_repeat_pct: float | None
    rows: list[TechRow]


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _bounds(date_from: date | None, date_to: date | None) -> tuple[datetime | None, datetime | None]:
    start = datetime.combine(date_from, time.min, tzinfo=UTC) if date_from else None
    end = datetime.combine(date_to, time.max, tzinfo=UTC) if date_to else None
    return start, end


def _in(dt: datetime | None, start: datetime | None, end: datetime | None) -> bool:
    dt = _aware(dt)
    if dt is None:
        return False
    return (start is None or dt >= start) and (end is None or dt <= end)


def _ws_value(ws: Worksheet) -> float:
    total = float(ws.maintenance_fee or 0) if not getattr(ws, "fee_discount", False) else 0.0
    for w in ws.works or []:
        if isinstance(w, dict):
            total += float(w.get("price_net") or 0)
    for m in ws.materials or []:
        if isinstance(m, dict):
            try:
                qty = float(str(m.get("qty") or "1").replace(",", "."))
            except ValueError:
                qty = 1.0
            total += float(m.get("price_net") or 0) * qty
    return total


async def technician_report(db: AsyncSession, date_from: date | None, date_to: date | None) -> TechReport:
    start, end = _bounds(date_from, date_to)
    prev_start = prev_end = None
    if start is not None and end is not None:
        length = end - start
        prev_end = start - timedelta(seconds=1)
        prev_start = prev_end - length

    employees = (await db.execute(select(Employee))).scalars().all()
    by_user = {e.user_id: e for e in employees if e.user_id is not None}
    by_id = {e.id: e for e in employees}
    stats: dict[uuid.UUID, dict] = {}

    def bucket(emp: Employee) -> dict:
        return stats.setdefault(emp.id, {
            "emp": emp, "closed": 0, "closed_prev": 0, "resolve_h": [], "resolve_h_prev": [], "repeat": 0,
            "ws": 0, "ws_prev": 0, "hours": 0.0, "value": 0.0, "ratings": [],
        })

    # --- szervizjegyek ---
    all_tickets = (await db.execute(select(ServiceTicket).where(ServiceTicket.kind == "repair"))).scalars().all()
    by_asset: dict[uuid.UUID, list[ServiceTicket]] = {}
    for tk in all_tickets:
        if tk.asset_id is not None:
            by_asset.setdefault(tk.asset_id, []).append(tk)
    for tk in (await db.execute(select(ServiceTicket).where(ServiceTicket.status == "done"))).scalars().all():
        emp = by_user.get(tk.assigned_to_user_id) if tk.assigned_to_user_id else None
        if emp is None:
            continue
        b = bucket(emp)
        resolved = _aware(tk.resolved_at)
        created = _aware(tk.created_at)
        hours = (resolved - created).total_seconds() / 3600 if resolved and created else None
        if _in(tk.resolved_at, start, end):
            b["closed"] += 1
            if hours is not None:
                b["resolve_h"].append(hours)
            if tk.asset_id is not None and resolved is not None:
                for other in by_asset.get(tk.asset_id, []):
                    oc = _aware(other.created_at)
                    if other.id != tk.id and oc is not None and resolved < oc <= resolved + REPEAT_WINDOW:
                        b["repeat"] += 1
                        break
        elif prev_start is not None and _in(tk.resolved_at, prev_start, prev_end):
            b["closed_prev"] += 1
            if hours is not None:
                b["resolve_h_prev"].append(hours)

    # --- munkalapok (a feladat lezárása szerint) ---
    rows = (await db.execute(select(Worksheet, Task).join(Task, Task.id == Worksheet.task_id))).all()
    for ws, task in rows:
        emp = by_id.get(task.employee_id)
        if emp is None:
            continue
        b = bucket(emp)
        done_at = task.completed_at or ws.handed_over_at
        if _in(done_at, start, end):
            b["ws"] += 1
            b["hours"] += float(ws.hours_spent or 0)
            b["value"] += _ws_value(ws)
        elif prev_start is not None and _in(done_at, prev_start, prev_end):
            b["ws_prev"] += 1
        if ws.rating is not None and _in(ws.rated_at, start, end):
            b["ratings"].append(int(ws.rating))

    out: list[TechRow] = []
    all_res = [h for b in stats.values() for h in b["resolve_h"]]
    team_avg = round(sum(all_res) / len(all_res), 1) if all_res else None
    total_closed = sum(b["closed"] for b in stats.values())
    total_repeat = sum(b["repeat"] for b in stats.values())
    team_repeat = round(100 * total_repeat / total_closed, 1) if total_closed else None
    for b in stats.values():
        if not (b["closed"] or b["ws"] or b["ratings"] or b["closed_prev"] or b["ws_prev"]):
            continue
        emp: Employee = b["emp"]
        avg = round(sum(b["resolve_h"]) / len(b["resolve_h"]), 1) if b["resolve_h"] else None
        avg_prev = round(sum(b["resolve_h_prev"]) / len(b["resolve_h_prev"]), 1) if b["resolve_h_prev"] else None
        repeat_pct = round(100 * b["repeat"] / b["closed"], 1) if b["closed"] else None
        rating_avg = round(sum(b["ratings"]) / len(b["ratings"]), 2) if b["ratings"] else None
        flags: list[str] = []
        if avg is not None and team_avg and avg > team_avg * 1.5 and b["closed"] >= 3:
            flags.append("slow")
        if repeat_pct is not None and team_repeat is not None and repeat_pct > max(team_repeat * 2, 10) and b["closed"] >= 3:
            flags.append("repeat")
        if rating_avg is not None and rating_avg < 3.5:
            flags.append("rating")
        out.append(TechRow(
            employee_id=str(emp.id), name=f"{emp.last_name} {emp.first_name}".strip(),
            tickets_closed=b["closed"], tickets_closed_prev=b["closed_prev"], avg_resolve_hours=avg, avg_resolve_hours_prev=avg_prev,
            repeat_count=b["repeat"], repeat_pct=repeat_pct, worksheets=b["ws"], worksheets_prev=b["ws_prev"],
            hours=round(b["hours"], 1), invoiced_net=float(round(b["value"])), rating_avg=rating_avg, rating_count=len(b["ratings"]),
            flags=flags,
        ))
    out.sort(key=lambda r: (-(r.tickets_closed + r.worksheets), r.name))
    return TechReport(date_from=date_from, date_to=date_to, team_avg_resolve_hours=team_avg, team_repeat_pct=team_repeat, rows=out)


@router.get("/technicians", response_model=TechReport, dependencies=[Depends(require_module("stats"))])
async def technicians(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("agent_report")),
):
    return await technician_report(db, date_from, date_to)
