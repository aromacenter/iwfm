"""Automatikus be- és kiléptetés a jelenléthez.

- Beléptetés: a dolgozó AZNAPI ELSŐ alkalmazás-belépésekor automatikusan
  nyit egy jelenléti bejegyzést (látszik, mikor kezdte a munkát) — a kiosk
  (/ora) blokkolás változatlanul működik, az automata csak akkor lép, ha
  aznap még nincs bejegyzés.
- Kiléptetés: az 5 perces háttérhurok a beállított munkaidő végén lezárja
  a nyitva felejtett bejegyzéseket. A munkaidő vége (erősorrend): az aznapi
  KÖZZÉTETT műszak vége → a dolgozó elérhetőségének (availability) vége →
  20:00. Ha a feloldott vég a belépés előtt van (esti munka), a bejegyzés
  a belépés után 12 órával záródik.

A nap határa Budapest szerint értendő; a tárolt időpontok UTC-k.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Employee, Shift, TimeEntry

_BUDAPEST = ZoneInfo("Europe/Budapest")
DEFAULT_END = time(20, 0)  # ha se műszak, se elérhetőség nincs aznapra
MAX_OPEN_HOURS = 12  # esti/gazdátlan bejegyzés legkésőbb ennyi óra után záródik

AUTO_IN_NOTE = "Automatikus beléptetés — aznapi első belépés"
AUTO_OUT_NOTE = "Automatikus kiléptetés (munkaidő vége)"


def _aware_utc(dt: datetime) -> datetime:
    """SQLite naiv datetime-ot ad vissza — UTC-ként értelmezzük."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _budapest_day_bounds_utc(now_utc: datetime) -> tuple[datetime, datetime]:
    local = now_utc.astimezone(_BUDAPEST)
    start_local = datetime.combine(local.date(), time.min, tzinfo=_BUDAPEST)
    return start_local.astimezone(UTC), (start_local + timedelta(days=1)).astimezone(UTC)


async def auto_clockin_on_login(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Aznapi első belépéskor jelenlét-nyitás. True, ha most nyitottunk."""
    emp = (
        await db.execute(
            select(Employee).where(
                Employee.user_id == user_id, Employee.status == "active"
            )
        )
    ).scalar_one_or_none()
    if emp is None:
        return False
    now = datetime.now(UTC)
    day_start, day_end = _budapest_day_bounds_utc(now)
    existing = (
        await db.execute(
            select(TimeEntry.id).where(
                TimeEntry.employee_id == emp.id,
                TimeEntry.clock_in >= day_start,
                TimeEntry.clock_in < day_end,
            ).limit(1)
        )
    ).first()
    if existing is not None:
        return False
    db.add(TimeEntry(
        employee_id=emp.id, clock_in=now, source="self", note=AUTO_IN_NOTE,
    ))
    return True


def _resolve_end_utc(
    entry: TimeEntry, emp: Employee | None, shift_end: time | None
) -> datetime:
    clock_in = _aware_utc(entry.clock_in)
    clock_in_local = clock_in.astimezone(_BUDAPEST)
    end_local_time = shift_end
    if end_local_time is None and emp is not None and isinstance(emp.availability, dict):
        interval = emp.availability.get(str(clock_in_local.weekday()))
        if interval and len(interval) == 2:
            try:
                end_local_time = time.fromisoformat(str(interval[1]))
            except ValueError:
                end_local_time = None
    if end_local_time is None:
        end_local_time = DEFAULT_END
    end_utc = datetime.combine(
        clock_in_local.date(), end_local_time, tzinfo=_BUDAPEST
    ).astimezone(UTC)
    if end_utc <= clock_in:
        # esti munka / múltbeli vég: legkésőbb MAX_OPEN_HOURS után zárunk
        end_utc = clock_in + timedelta(hours=MAX_OPEN_HOURS)
    return end_utc


async def auto_clockout(db: AsyncSession) -> int:
    """Nyitva felejtett jelenlétek lezárása a munkaidő végén. Visszaadja,
    hány bejegyzés záródott. A hívó commitol."""
    now = datetime.now(UTC)
    open_entries = (
        await db.execute(select(TimeEntry).where(TimeEntry.clock_out.is_(None)))
    ).scalars().all()
    if not open_entries:
        return 0

    emp_ids = {e.employee_id for e in open_entries}
    employees = {
        emp.id: emp
        for emp in (
            await db.execute(select(Employee).where(Employee.id.in_(emp_ids)))
        ).scalars().all()
    }
    # Az érintett napok közzétett műszakjai (a bejegyzés budapesti napjára)
    dates = {_aware_utc(e.clock_in).astimezone(_BUDAPEST).date() for e in open_entries}
    shifts = (
        await db.execute(
            select(Shift).where(
                Shift.employee_id.in_(emp_ids),
                Shift.status == "published",
                Shift.work_date.in_(dates),
            )
        )
    ).scalars().all()
    shift_end_map = {(s.employee_id, s.work_date): s.end_time for s in shifts}

    closed = 0
    for entry in open_entries:
        local_date = _aware_utc(entry.clock_in).astimezone(_BUDAPEST).date()
        end_utc = _resolve_end_utc(
            entry,
            employees.get(entry.employee_id),
            shift_end_map.get((entry.employee_id, local_date)),
        )
        if now >= end_utc:
            entry.clock_out = end_utc
            entry.note = f"{entry.note} · {AUTO_OUT_NOTE}" if entry.note else AUTO_OUT_NOTE
            closed += 1
    return closed
