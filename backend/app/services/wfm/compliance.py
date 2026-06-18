"""Mt. (2012. évi I. törvény) munkaidő-megfelelőségi motor.

Pure functions over shift data — no DB access — so every rule is unit-testable
in isolation. A ``Violation`` with severity ``error`` blocks publishing; a
``warning`` requires the manager's explicit confirmation (force flag, audited).

Implemented rules:

* MAX_DAILY        Mt. 99.§   — beosztás szerinti napi munkaidő ≤ 12 óra
* MAX_WEEKLY       Mt. 99.§   — beosztás szerinti heti munkaidő ≤ 48 óra
* DAILY_REST       Mt. 104.§  — két műszak között ≥ 11 óra napi pihenőidő
* WEEKLY_REST      Mt. 105-106.§ — hetente ≥ 48 óra egybefüggő pihenőidő
* SIX_DAYS         Mt. 105.§  — hat egybefüggő munkanap után pihenőnap jár
* PUBLISH_NOTICE   Mt. 97.§(4) — a beosztást ≥ 168 órával előre kell közölni
* MODIFY_NOTICE    Mt. 97.§(5) — közölt beosztás ≥ 96 órával előre módosítható
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Iterable
from uuid import UUID
from zoneinfo import ZoneInfo

# A beosztás-idők helyi (magyar) idő szerint értendők. A műszak abszolút
# instansát ebben a zónában számoljuk, így a közlési határidő (Mt. 97.§) és a
# DST-átmeneten átnyúló műszakok óraszáma is helyes.
TZ = ZoneInfo("Europe/Budapest")

HOURS_MAX_DAILY = 12
HOURS_MAX_WEEKLY = 48
HOURS_DAILY_REST = 11
HOURS_WEEKLY_REST = 48
MAX_CONSECUTIVE_WORKDAYS = 6
HOURS_PUBLISH_NOTICE = 168
HOURS_MODIFY_NOTICE = 96


@dataclass
class ShiftSpan:
    """Lightweight, ORM-independent view of one shift."""

    id: UUID | None
    employee_id: UUID
    work_date: date
    start_time: time
    end_time: time
    break_minutes: int = 0

    @property
    def start(self) -> datetime:
        return datetime.combine(self.work_date, self.start_time, tzinfo=TZ)

    @property
    def end(self) -> datetime:
        end_day = self.work_date
        if self.end_time <= self.start_time:  # átnyúlik éjfélen
            end_day = self.work_date + timedelta(days=1)
        return datetime.combine(end_day, self.end_time, tzinfo=TZ)

    @property
    def worked_hours(self) -> float:
        """Beosztás szerinti munkaidő — a munkaközi szünet nem része (Mt. 86.§)."""
        return max(
            ((self.end - self.start).total_seconds() / 3600) - self.break_minutes / 60,
            0.0,
        )


@dataclass
class Violation:
    code: str
    severity: str  # 'error' | 'warning'
    message: str  # magyar tartalék-szöveg; a kliens a code+params alapján fordít
    employee_id: UUID | None = None
    shift_ids: list[UUID] = field(default_factory=list)
    params: dict = field(default_factory=dict)  # i18n interpolációhoz


def _sorted(shifts: Iterable[ShiftSpan]) -> list[ShiftSpan]:
    return sorted(shifts, key=lambda s: s.start)


def check_max_daily(shifts: Iterable[ShiftSpan]) -> list[Violation]:
    """Mt. 99.§ — a beosztás szerinti napi munkaidő legfeljebb 12 óra."""
    out = []
    for s in shifts:
        if s.worked_hours > HOURS_MAX_DAILY:
            out.append(
                Violation(
                    code="MAX_DAILY",
                    severity="error",
                    message=(
                        f"{s.work_date.isoformat()}: a napi munkaidő "
                        f"{s.worked_hours:.1f} óra — a megengedett legfeljebb "
                        f"{HOURS_MAX_DAILY} óra (Mt. 99.§)."
                    ),
                    employee_id=s.employee_id,
                    shift_ids=[s.id] if s.id else [],
                    params={"date": s.work_date.isoformat(), "hours": f"{s.worked_hours:.1f}"},
                )
            )
    return out


def check_max_weekly(shifts: Iterable[ShiftSpan]) -> list[Violation]:
    """Mt. 99.§ — a beosztás szerinti heti munkaidő legfeljebb 48 óra (ISO hét)."""
    weekly: dict[tuple[int, int], list[ShiftSpan]] = {}
    for s in shifts:
        iso = s.work_date.isocalendar()
        weekly.setdefault((iso.year, iso.week), []).append(s)
    out = []
    for (year, week), members in sorted(weekly.items()):
        total = sum(m.worked_hours for m in members)
        if total > HOURS_MAX_WEEKLY:
            out.append(
                Violation(
                    code="MAX_WEEKLY",
                    severity="error",
                    message=(
                        f"{year}. év {week}. hét: a heti munkaidő {total:.1f} óra — "
                        f"a megengedett legfeljebb {HOURS_MAX_WEEKLY} óra (Mt. 99.§)."
                    ),
                    employee_id=members[0].employee_id,
                    shift_ids=[m.id for m in members if m.id],
                    params={"year": year, "week": week, "hours": f"{total:.1f}"},
                )
            )
    return out


def check_daily_rest(shifts: Iterable[ShiftSpan]) -> list[Violation]:
    """Mt. 104.§ — a napi munka befejezése és a következő munkanapi kezdés
    között legalább 11 óra egybefüggő pihenőidő jár."""
    ordered = _sorted(shifts)
    out = []
    for prev, nxt in zip(ordered, ordered[1:]):
        rest = (nxt.start - prev.end).total_seconds() / 3600
        if rest < 0:
            out.append(
                Violation(
                    code="OVERLAP",
                    severity="error",
                    message=(
                        f"Ütköző műszakok: {prev.work_date.isoformat()} és "
                        f"{nxt.work_date.isoformat()} átfedésben vannak."
                    ),
                    employee_id=prev.employee_id,
                    shift_ids=[i for i in (prev.id, nxt.id) if i],
                    params={"from": prev.work_date.isoformat(), "to": nxt.work_date.isoformat()},
                )
            )
        elif rest < HOURS_DAILY_REST:
            out.append(
                Violation(
                    code="DAILY_REST",
                    severity="error",
                    message=(
                        f"{prev.work_date.isoformat()} → {nxt.work_date.isoformat()}: "
                        f"csak {rest:.1f} óra pihenőidő a műszakok között — legalább "
                        f"{HOURS_DAILY_REST} óra kötelező (Mt. 104.§)."
                    ),
                    employee_id=prev.employee_id,
                    shift_ids=[i for i in (prev.id, nxt.id) if i],
                    params={
                        "from": prev.work_date.isoformat(),
                        "to": nxt.work_date.isoformat(),
                        "hours": f"{rest:.1f}",
                    },
                )
            )
    return out


def check_weekly_rest(shifts: Iterable[ShiftSpan]) -> list[Violation]:
    """Mt. 105–106.§ — hetente legalább 48 óra egybefüggő pihenőidő jár.

    Hétről hétre megvizsgáljuk, létezik-e olyan ≥48 órás műszakmentes rés,
    amely az adott ISO hetet érinti. A hét széleinél a szomszédos hetek
    műszakjait is figyelembe vesszük.
    """
    ordered = _sorted(shifts)
    if not ordered:
        return []

    # Foglalt intervallumok + "őrszem" rések a legelső előtt / legutolsó után.
    busy = [(s.start, s.end) for s in ordered]
    horizon_start = busy[0][0] - timedelta(days=14)
    horizon_end = busy[-1][1] + timedelta(days=14)
    gaps: list[tuple[datetime, datetime]] = []
    cursor = horizon_start
    for b_start, b_end in busy:
        if b_start > cursor:
            gaps.append((cursor, b_start))
        cursor = max(cursor, b_end)
    gaps.append((cursor, horizon_end))

    weeks = {s.work_date.isocalendar()[:2] for s in ordered}
    out = []
    for year, week in sorted(weeks):
        week_start = datetime.combine(
            date.fromisocalendar(year, week, 1), time.min, tzinfo=TZ
        )
        week_end = week_start + timedelta(days=7)
        ok = any(
            (g_end - g_start).total_seconds() / 3600 >= HOURS_WEEKLY_REST
            and g_end > week_start
            and g_start < week_end
            for g_start, g_end in gaps
        )
        if not ok:
            members = [s for s in ordered if s.work_date.isocalendar()[:2] == (year, week)]
            out.append(
                Violation(
                    code="WEEKLY_REST",
                    severity="error",
                    message=(
                        f"{year}. év {week}. hét: nincs legalább "
                        f"{HOURS_WEEKLY_REST} óra egybefüggő heti pihenőidő "
                        f"(Mt. 105–106.§)."
                    ),
                    employee_id=members[0].employee_id,
                    shift_ids=[m.id for m in members if m.id],
                    params={"year": year, "week": week},
                )
            )
    return out


def check_six_consecutive_days(shifts: Iterable[ShiftSpan]) -> list[Violation]:
    """Mt. 105.§ — hat egybefüggő munkanapot követően pihenőnapot kell beosztani."""
    days = sorted({s.work_date for s in shifts})
    if not days:
        return []
    by_day = {}
    for s in shifts:
        by_day.setdefault(s.work_date, []).append(s)

    out = []
    run = [days[0]]
    for d in days[1:] + [None]:  # None lezárja az utolsó sorozatot
        if d is not None and (d - run[-1]).days == 1:
            run.append(d)
            continue
        if len(run) > MAX_CONSECUTIVE_WORKDAYS:
            ids = [s.id for day in run for s in by_day[day] if s.id]
            emp = by_day[run[0]][0].employee_id
            out.append(
                Violation(
                    code="SIX_DAYS",
                    severity="error",
                    message=(
                        f"{run[0].isoformat()} – {run[-1].isoformat()}: "
                        f"{len(run)} egybefüggő munkanap — hat nap után "
                        f"pihenőnap kötelező (Mt. 105.§)."
                    ),
                    employee_id=emp,
                    shift_ids=ids,
                    params={
                        "from": run[0].isoformat(),
                        "to": run[-1].isoformat(),
                        "count": len(run),
                    },
                )
            )
        if d is not None:
            run = [d]
    return out


def check_publish_notice(
    shifts: Iterable[ShiftSpan], *, now: datetime
) -> list[Violation]:
    """Mt. 97.§(4) — a munkaidő-beosztást legalább 168 órával (7 nappal) a
    beosztás szerinti napi munkaidő kezdete előtt, írásban kell közölni.

    Figyelmeztetés (nem hiba): a törvény a munkavállaló kérésére, illetve
    előre nem látható körülmény esetén enged eltérést — a vezető megerősítéssel
    (force) közzéteheti, és ez auditálásra kerül.
    """
    out = []
    for s in shifts:
        notice = (s.start - now).total_seconds() / 3600
        if notice < HOURS_PUBLISH_NOTICE:
            out.append(
                Violation(
                    code="PUBLISH_NOTICE",
                    severity="warning",
                    message=(
                        f"{s.work_date.isoformat()}: a közlés csak {max(notice, 0):.0f} "
                        f"órával a műszak kezdete előtt történne — a törvény szerint "
                        f"legalább {HOURS_PUBLISH_NOTICE} óra (7 nap) szükséges "
                        f"(Mt. 97.§ (4))."
                    ),
                    employee_id=s.employee_id,
                    shift_ids=[s.id] if s.id else [],
                    params={"date": s.work_date.isoformat(), "hours": f"{max(notice, 0):.0f}"},
                )
            )
    return out


def check_modify_notice(shift: ShiftSpan, *, now: datetime) -> list[Violation]:
    """Mt. 97.§(5) — közölt (publikált) beosztás módosítása legalább 96 órával
    a napi munkaidő kezdete előtt lehetséges (kivéve a munkavállaló kérése)."""
    notice = (shift.start - now).total_seconds() / 3600
    if notice < HOURS_MODIFY_NOTICE:
        return [
            Violation(
                code="MODIFY_NOTICE",
                severity="warning",
                message=(
                    f"A(z) {shift.work_date.isoformat()} napi közölt műszak már csak "
                    f"{max(notice, 0):.0f} órával a kezdés előtt módosulna — a törvény "
                    f"szerint legalább {HOURS_MODIFY_NOTICE} óra szükséges, kivéve a "
                    f"munkavállaló írásbeli kérését (Mt. 97.§ (5))."
                ),
                employee_id=shift.employee_id,
                shift_ids=[shift.id] if shift.id else [],
                params={"date": shift.work_date.isoformat(), "hours": f"{max(notice, 0):.0f}"},
            )
        ]
    return []


def check_schedule(
    shifts: Iterable[ShiftSpan], *, now: datetime, publish: bool = False
) -> list[Violation]:
    """Run every rule for one employee's shift set.

    ``publish=True`` adds the 168h notice check (only meaningful when the
    shifts are about to be communicated to the employee).
    """
    shift_list = list(shifts)
    violations = [
        *check_max_daily(shift_list),
        *check_max_weekly(shift_list),
        *check_daily_rest(shift_list),
        *check_weekly_rest(shift_list),
        *check_six_consecutive_days(shift_list),
    ]
    if publish:
        violations.extend(check_publish_notice(shift_list, now=now))
    return violations
