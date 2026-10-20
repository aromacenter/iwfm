"""Munkaidő-mérés (worktime modul, 1d553d2e C fázis): a szerelő munkalap-
timere (automatikus indulás, szünet, zárás — a szerelő számokat nem lát) és a
vezetői statisztika (írt óra vs. mért aktív idő szerelőnként / munkalaponként).
Flotta-pulton a `worktime` modullal kapcsolható."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_own_employee, require_perm
from app.db import get_db
from app.models import Employee, Task, User, Worksheet, WorksheetTimer
from app.services.wfm import worktime_service as wt
from app.services.wfm.license import require_module
from app.services.wfm.worktime_service import TimerSegment, TimerSummary

router = APIRouter(dependencies=[Depends(require_module("worktime"))])
_TZ = ZoneInfo("Europe/Budapest")


class TimerState(BaseModel):
    """Dolgozói kép — SZÁNDÉKOSAN számok nélkül („sunyi” mód)."""

    state: str  # running | paused | stopped | none
    started_at: datetime | None  # az aktuális munkamenet kezdete
    pause_count: int


async def _own_worksheet(db: AsyncSession, emp: Employee, task_id: str) -> Worksheet:
    try:
        tid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "tasks.not_found"})
    task = (await db.execute(select(Task).where(Task.id == tid))).scalar_one_or_none()
    if task is None or task.employee_id != emp.id:
        raise HTTPException(status_code=404, detail={"code": "tasks.not_found"})
    ws = (await db.execute(select(Worksheet).where(Worksheet.task_id == task.id))).scalar_one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail={"code": "worksheet.not_found"})
    return ws


async def _timer(db: AsyncSession, ws: Worksheet, user: User, create: bool) -> WorksheetTimer | None:
    row = (
        await db.execute(
            select(WorksheetTimer).where(WorksheetTimer.worksheet_id == ws.id, WorksheetTimer.user_id == user.id)
        )
    ).scalar_one_or_none()
    if row is None and create:
        row = WorksheetTimer(worksheet_id=ws.id, user_id=user.id, sessions=[])
        db.add(row)
        await db.flush()
    return row


def _state(row: WorksheetTimer | None) -> TimerState:
    sessions = list(row.sessions or []) if row else []
    st = wt.state_of(sessions)
    started = wt._parse(sessions[-1].get("start")) if sessions and st != "stopped" else None
    pauses = len(sessions[-1].get("pauses") or []) if sessions and st != "stopped" else 0
    return TimerState(state=st, started_at=started, pause_count=pauses)


def _apply(row: WorksheetTimer, sessions: list[dict]) -> None:
    row.sessions = sessions
    summary = wt.summarize(sessions)
    row.active_seconds = summary.active_seconds
    row.pause_seconds = summary.pause_seconds
    row.first_started_at = summary.first_started_at
    row.last_ended_at = summary.last_ended_at
    row.updated_at = datetime.now(UTC)


@router.get("/{task_id}", response_model=TimerState)
async def timer_state(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
    user: User = Depends(get_current_user),
):
    ws = await _own_worksheet(db, emp, task_id)
    return _state(await _timer(db, ws, user, create=False))


@router.post("/{task_id}/{action}", response_model=TimerState)
async def timer_action(
    task_id: str,
    action: str,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
    user: User = Depends(get_current_user),
):
    """start | pause | resume | stop — mind idempotens (a kétszeri start nem
    nyit második munkamenetet, a stop a nyitott szünetet is lezárja)."""
    fn = {"start": wt.start, "pause": wt.pause, "resume": wt.resume, "stop": wt.stop}.get(action)
    if fn is None:
        raise HTTPException(status_code=404, detail={"code": "worktime.bad_action"})
    ws = await _own_worksheet(db, emp, task_id)
    row = await _timer(db, ws, user, create=action == "start")
    if row is None:
        return _state(None)
    _apply(row, fn(list(row.sessions or []), datetime.now(UTC)))
    await db.commit()
    return _state(row)


# ─── Vezetői statisztika ─────────────────────────────────────────────────────


class EmployeeStat(BaseModel):
    employee_id: str | None
    name: str
    worksheets: int
    written_hours: float
    active_seconds: float
    pause_seconds: float
    ratio: float | None  # mért / írt (1.0 = 100 %)


class WorksheetStat(BaseModel):
    worksheet_id: str
    task_id: str
    serial: str
    title: str
    employee_id: str | None
    name: str
    state: str
    started_at: datetime | None
    ended_at: datetime | None
    written_hours: float | None
    active_seconds: float
    pause_seconds: float
    pause_count: int
    segments: list[TimerSegment]


class WorktimeStats(BaseModel):
    date_from: date
    date_to: date
    worksheets: int
    written_hours: float
    active_seconds: float
    pause_seconds: float
    ratio: float | None
    by_employee: list[EmployeeStat]
    rows: list[WorksheetStat]


def _ratio(active_seconds: float, written_hours: float) -> float | None:
    return round(active_seconds / 3600.0 / written_hours, 3) if written_hours > 0 else None


@router.get("/stats/summary", response_model=WorktimeStats)
async def worktime_stats(
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    employee_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("agent_report")),
):
    """Szerelőnként és munkalaponként: munkalapra írt óra vs. mért aktív idő
    (szünetek nélkül) a megadott időszakban (a timer ELSŐ indulása szerint,
    Europe/Budapest nap-határokkal). Admin / manager."""
    if date_to < date_from or (date_to - date_from).days > 400:
        raise HTTPException(status_code=422, detail={"code": "worktime.bad_range"})
    start_utc = datetime.combine(date_from, time.min, tzinfo=_TZ).astimezone(UTC)
    end_utc = (datetime.combine(date_to, time.min, tzinfo=_TZ) + timedelta(days=1)).astimezone(UTC)
    rows = (
        await db.execute(
            select(WorksheetTimer, Worksheet, Task)
            .join(Worksheet, Worksheet.id == WorksheetTimer.worksheet_id)
            .join(Task, Task.id == Worksheet.task_id)
            .where(WorksheetTimer.first_started_at >= start_utc, WorksheetTimer.first_started_at < end_utc)
            .order_by(WorksheetTimer.first_started_at.desc())
            .limit(2000)
        )
    ).all()
    user_ids = {tm.user_id for tm, _, _ in rows}
    emps: dict[uuid.UUID, Employee] = {}
    names: dict[uuid.UUID, str] = {}
    if user_ids:
        for e in (await db.execute(select(Employee).where(Employee.user_id.in_(user_ids)))).scalars().all():
            emps[e.user_id] = e
            names[e.user_id] = f"{e.last_name} {e.first_name}".strip()
        for u in (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all():
            names.setdefault(u.id, u.display_name or u.email or "")
    emp_filter = uuid.UUID(employee_id) if employee_id else None
    now = datetime.now(UTC)
    out_rows: list[WorksheetStat] = []
    agg: dict[uuid.UUID | None, dict] = {}
    for tm, ws, task in rows:
        e = emps.get(tm.user_id)
        eid = e.id if e else None
        if emp_filter is not None and eid != emp_filter:
            continue
        s = wt.summarize(list(tm.sessions or []), now)
        written = float(ws.hours_spent or 0.0)
        a = agg.setdefault(eid, {"name": names.get(tm.user_id, "?"), "worksheets": 0, "written": 0.0, "active": 0.0, "pause": 0.0})
        a["worksheets"] += 1
        a["written"] += written
        a["active"] += s.active_seconds
        a["pause"] += s.pause_seconds
        out_rows.append(WorksheetStat(
            worksheet_id=str(ws.id), task_id=str(task.id), serial=ws.serial, title=task.title,
            employee_id=str(eid) if eid else None, name=names.get(tm.user_id, "?"), state=s.state,
            started_at=s.first_started_at, ended_at=s.last_ended_at, written_hours=ws.hours_spent,
            active_seconds=s.active_seconds, pause_seconds=s.pause_seconds, pause_count=s.pause_count,
            segments=s.segments,
        ))
    by_emp = sorted(
        (
            EmployeeStat(
                employee_id=str(k) if k else None, name=v["name"], worksheets=v["worksheets"],
                written_hours=round(v["written"], 2), active_seconds=v["active"], pause_seconds=v["pause"],
                ratio=_ratio(v["active"], v["written"]),
            )
            for k, v in agg.items()
        ),
        key=lambda x: x.name,
    )
    tot_written = sum(v["written"] for v in agg.values())
    tot_active = sum(v["active"] for v in agg.values())
    return WorktimeStats(
        date_from=date_from, date_to=date_to, worksheets=len(out_rows), written_hours=round(tot_written, 2),
        active_seconds=tot_active, pause_seconds=sum(v["pause"] for v in agg.values()),
        ratio=_ratio(tot_active, tot_written), by_employee=by_emp, rows=out_rows[:500],
    )


async def timer_summary_for_worksheet(db: AsyncSession, ws_id: uuid.UUID) -> TimerSummary | None:
    """A munkalap összesített timere (több szerelő esetén összeadva) — a
    vezetői munkalap-képhez."""
    timers = (
        await db.execute(select(WorksheetTimer).where(WorksheetTimer.worksheet_id == ws_id))
    ).scalars().all()
    if not timers:
        return None
    now = datetime.now(UTC)
    parts = [wt.summarize(list(t.sessions or []), now) for t in timers]
    firsts = [p.first_started_at for p in parts if p.first_started_at]
    lasts = [p.last_ended_at for p in parts if p.last_ended_at]
    states = {p.state for p in parts}
    state = "running" if "running" in states else "paused" if "paused" in states else "stopped"
    return TimerSummary(
        state=state, first_started_at=min(firsts) if firsts else None, last_ended_at=max(lasts) if lasts else None,
        active_seconds=round(sum(p.active_seconds for p in parts), 1),
        pause_seconds=round(sum(p.pause_seconds for p in parts), 1),
        pause_count=sum(p.pause_count for p in parts), session_count=sum(p.session_count for p in parts),
        segments=[seg for p in parts for seg in p.segments],
    )
