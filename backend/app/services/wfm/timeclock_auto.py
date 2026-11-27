"""Nyitva felejtett blokkolás — emlékeztető (192. kör).

A blokkolás TÉNYSZERŰ: csak valódi be-/kiblokkolás (kioszk, telefon, vagy a
vezető kézi, naplózott rögzítése) hoz létre vagy zár bejegyzést. A korábbi
automatikus beléptetés (aznapi első app-belépés) és automatikus kiléptetés
(kitalált időpont a munkaidő végén) megszűnt — a user döntése, 2026-11-27.

Ami maradt: az 5 perces háttérhurok a munkaidő vége után (erősorrend: aznapi
KÖZZÉTETT műszak vége → a dolgozó elérhetőségének vége → 20:00; esti munka
esetén legkésőbb 12 óra után) EGYSZER emlékezteti a dolgozót Telegramon, hogy
blokkoljon ki, és eseményt küld a vezetőknek (timeclock.left_open). A
bejegyzés nyitva marad, amíg a dolgozó ki nem blokkol vagy a vezető a
Jelenlét oldalon (naplózva) le nem zárja.

A nap határa Budapest szerint értendő; a tárolt időpontok UTC-k.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Employee, Shift, TimeEntry

logger = logging.getLogger(__name__)

_BUDAPEST = ZoneInfo("Europe/Budapest")
DEFAULT_END = time(20, 0)  # ha se műszak, se elérhetőség nincs aznapra
MAX_OPEN_HOURS = 12  # esti/gazdátlan bejegyzésnél ennyi óra után megy az emlékeztető

REMIND_NOTE = "Nyitva felejtett blokkolás — emlékeztető kiküldve"


def _aware_utc(dt: datetime) -> datetime:
    """SQLite naiv datetime-ot ad vissza — UTC-ként értelmezzük."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def resolve_end_utc(entry: TimeEntry, emp: Employee | None, shift_end: time | None) -> datetime:
    """A bejegyzés napjára várt munkaidő-vég (UTC)."""
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
    end_utc = datetime.combine(clock_in_local.date(), end_local_time, tzinfo=_BUDAPEST).astimezone(UTC)
    if end_utc <= clock_in:
        end_utc = clock_in + timedelta(hours=MAX_OPEN_HOURS)
    return end_utc


async def remind_open_entries(db: AsyncSession, *, now: datetime | None = None) -> int:
    """A munkaidő vége után még nyitott bejegyzésekről EGYSZER emlékeztető.
    Visszaadja, hány bejegyzéshez ment ki most. A hívó commitol."""
    now = now or datetime.now(UTC)
    open_entries = (
        await db.execute(select(TimeEntry).where(TimeEntry.clock_out.is_(None)))
    ).scalars().all()
    pending = [e for e in open_entries if REMIND_NOTE not in (e.note or "")]
    if not pending:
        return 0

    emp_ids = {e.employee_id for e in pending}
    employees = {
        emp.id: emp
        for emp in (await db.execute(select(Employee).where(Employee.id.in_(emp_ids)))).scalars().all()
    }
    dates = {_aware_utc(e.clock_in).astimezone(_BUDAPEST).date() for e in pending}
    shifts = (
        await db.execute(
            select(Shift).where(
                Shift.employee_id.in_(emp_ids), Shift.status == "published", Shift.work_date.in_(dates),
            )
        )
    ).scalars().all()
    shift_end_map = {(s.employee_id, s.work_date): s.end_time for s in shifts}

    from app.services.wfm.automation import fire_event
    from app.services.wfm.links import frontend_url
    from app.services.wfm.telegram import notify_event, send_personal

    sent = 0
    for entry in pending:
        emp = employees.get(entry.employee_id)
        local_date = _aware_utc(entry.clock_in).astimezone(_BUDAPEST).date()
        end_utc = resolve_end_utc(entry, emp, shift_end_map.get((entry.employee_id, local_date)))
        if now < end_utc:
            continue
        stamp = now.astimezone(_BUDAPEST).strftime("%Y-%m-%d %H:%M")
        entry.note = f"{entry.note} · {REMIND_NOTE} {stamp}" if entry.note else f"{REMIND_NOTE} {stamp}"
        sent += 1
        if emp is None:
            continue
        since = _aware_utc(entry.clock_in).astimezone(_BUDAPEST).strftime("%Y-%m-%d %H:%M")
        name = f"{emp.last_name} {emp.first_name}"
        try:
            await send_personal(
                db, emp,
                f"⏰ Elfelejtettél kiblokkolni? {since} óta nyitva a blokkolásod. "
                "Blokkolj ki a telefonodon vagy a terminálon — ha már végeztél, szólj a vezetődnek, "
                "hogy a Jelenlét oldalon rögzítse a tényleges távozási időt.\n🔗 "
                + frontend_url("/beosztasom"),
            )
            ctx = {
                "dolgozo": name, "mikortol": since, "munkaido_vege": end_utc.astimezone(_BUDAPEST).strftime("%H:%M"),
                "_entry_id": str(entry.id),
            }
            fire_event("timeclock.left_open", ctx)
            await notify_event(db, "timeclock.left_open", ctx)
        except Exception:  # noqa: BLE001 - best-effort értesítés
            logger.warning("open-entry reminder failed", exc_info=True)
    return sent
