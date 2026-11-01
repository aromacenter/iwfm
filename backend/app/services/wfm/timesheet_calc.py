"""Havi munkaidő-nyilvántartás számítása dolgozónként (tiszta függvények).

A kimenet a mintapéldány (olmunkaido.hu) szerkezetét követi — spec:
docs/berexport-munkaido-nyilvantartas-spec.md, 3–4. pont.

Rétegezés (spec 11.3): törvény (labor_rules) → cég (CompanyWorkRules, csak
szigoríthat) → dolgozó (Employee.break_rule / sunday_bonus).

Szabályok röviden:
- BI (beosztott idő)       = beosztás hossza − beosztott szünet
- NYI (nyilvántartott idő) = beosztás szerinti dolgozónál BI; blokkolásosnál a
  nyers blokkolt idő − szünet (kézi vagy szabály szerinti, a NAGYOBB), ha a
  szünet nem fizetett
- fizetett távollét (SZ, BSZ) napján NYI = napi keretóra, a kód a lapon
- KN (nem fizetett) napján NYI = 0, csak a napszámláló nő
- Munkaidő (keret) = Havi órák = munkanapok × napi keretóra
- Pihenőnapok = a hónap napjai − munkanapok (ünnep is pihenőnapnak számít,
  amíg a könyvelő mást nem mond)
- RM 50%  = munkanapon a beosztáson (vagy beosztás nélkül a napi kereten) felüli idő
- RM 100% = pihenőnapon beosztás nélkül dolgozott idő, beosztott pihenőnapon a
  beosztáson felüli rész, munkaszüneti napon minden dolgozott idő (140. § (2))
- VP 50%  = vasárnap dolgozott idő, ha a dolgozónál jár a vasárnapi pótlék
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.services.wfm.holidays import is_non_working_day, is_public_holiday, is_worked_saturday
from app.services.wfm.labor_rules import LaborRules, rules_for

TZ = ZoneInfo("Europe/Budapest")

WEEKDAY_ABBR = ("h", "k", "sze", "cs", "p", "szo", "v")
MONTH_ABBR = ("jan", "feb", "márc", "ápr", "máj", "jún", "júl", "aug", "szept", "okt", "nov", "dec")

# Távollét-típus → lapon megjelenő kód. paid: NYI = napi keret és beleszámít a
# nyilvántartott időbe; hours: az összesítőben van óra-oszlopa (KN-nek nincs).
DEFAULT_ABSENCE_CODES: dict[str, dict] = {
    "annual": {"code": "SZ", "paid": True, "hours": True},
    "sick": {"code": "BSZ", "paid": True, "hours": True},
    "other": {"code": "TVL", "paid": False, "hours": True},
    "unpaid": {"code": "KN", "paid": False, "hours": False},
}
ABSENCE_ORDER = ("SZ", "BSZ", "TVL", "KN")


@dataclass(frozen=True)
class BreakRule:
    """Szünet-szabály: `per_hours` None → a törvényi lépcsők; különben
    minden megkezdett/teljes `per_hours` órányi munkára `minutes` perc."""

    minutes: int = 0
    per_hours: int | None = None
    paid: bool = False

    def deduction(self, worked_minutes: int, law: LaborRules) -> int:
        law_min = law.law_break_minutes(worked_minutes)
        if self.per_hours:
            own = (worked_minutes // (self.per_hours * 60)) * self.minutes
        else:
            own = 0
        # a cég/dolgozó szabálya csak SZIGORÍTHAT: a törvényi minimum alatt nem mehet
        return max(law_min, own)


@dataclass(frozen=True)
class CompanyWorkRules:
    break_rule: BreakRule = BreakRule()
    sunday_bonus_default: bool = False
    group_by_site: bool = True
    absence_codes: dict[str, dict] = field(default_factory=lambda: dict(DEFAULT_ABSENCE_CODES))


@dataclass
class DayRow:
    day: date
    in_employment: bool = True
    workday: bool = False
    rest_day: bool = False
    holiday: bool = False
    sched_start: time | None = None
    sched_end: time | None = None
    sched_minutes: int = 0        # BI
    work_start: time | None = None
    work_end: time | None = None
    work_minutes: int = 0         # NYI
    code: str | None = None       # SZ / BSZ / TVL / KN
    rm100: int = 0
    rm50: int = 0
    vp50: int = 0

    @property
    def label(self) -> str:
        return f"{self.day.day:02d}.{MONTH_ABBR[self.day.month - 1]}"

    @property
    def weekday_abbr(self) -> str:
        return WEEKDAY_ABBR[self.day.weekday()]


@dataclass
class MonthSheet:
    employee_id: str
    name: str
    tax_id: str | None
    hire_date: date
    termination_date: date | None
    site: str | None
    year: int
    month: int
    daily_minutes: int
    rows: list[DayRow]
    workdays: int
    rest_days: int
    frame_minutes: int           # Munkaidő = Havi órák
    recorded_minutes: int        # Nyilvántartott idő
    rm100: int
    rm50: int
    vp50: int
    absences: dict[str, tuple[int, int | None]]   # kód → (nap, perc vagy None)
    rules: LaborRules

    @property
    def period_start(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def period_end(self) -> date:
        return date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])


def fmt_hm(minutes: int | None) -> str:
    """Perc → `Ó:PP` (legalább két számjegyű óra): 0 → 00:00, 10560 → 176:00."""
    if minutes is None:
        return ""
    minutes = max(int(minutes), 0)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def fmt_t(t: time | None) -> str:
    return t.strftime("%H:%M") if t is not None else ""


def _span_minutes(start: time, end: time) -> int:
    s = start.hour * 60 + start.minute
    e = end.hour * 60 + end.minute
    if e <= s:
        e += 24 * 60  # éjfélen átnyúló műszak
    return e - s


def _is_workday(d: date) -> bool:
    if is_worked_saturday(d):
        return True
    return d.weekday() < 5 and not is_non_working_day(d)


def _employee_break_rule(employee, company: CompanyWorkRules) -> BreakRule:
    raw = getattr(employee, "break_rule", None)
    if isinstance(raw, dict) and raw:
        try:
            return BreakRule(
                minutes=int(raw.get("minutes") or 0),
                per_hours=int(raw["per_hours"]) if raw.get("per_hours") else None,
                paid=bool(raw.get("paid", company.break_rule.paid)),
            )
        except (TypeError, ValueError):
            pass
    return company.break_rule


def _sunday_bonus(employee, company: CompanyWorkRules) -> bool:
    own = getattr(employee, "sunday_bonus", None)
    return company.sunday_bonus_default if own is None else bool(own)


def build_month_sheet(
    employee,
    *,
    year: int,
    month: int,
    shifts: list,
    entries: list,
    leaves: list,
    company: CompanyWorkRules | None = None,
    overrides: list[dict] | None = None,
    tax_id: str | None = None,
) -> MonthSheet:
    """Egy dolgozó egy havi lapja. `shifts`: közzétett műszakok (work_date,
    start_time, end_time, break_minutes); `entries`: lezárt blokkolások
    (clock_in, clock_out tz-aware, break_minutes); `leaves`: jóváhagyott
    távollétek (type, start_date, end_date)."""
    company = company or CompanyWorkRules()
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    daily_minutes = int(round(getattr(employee, "weekly_hours", 40) * 60 / 5))
    schedule_based = getattr(employee, "payroll_source", "attendance") == "schedule"
    break_rule = _employee_break_rule(employee, company)
    sunday_bonus = _sunday_bonus(employee, company)
    hire = getattr(employee, "hire_date", None) or first
    term = getattr(employee, "termination_date", None)

    shifts_by_day: dict[date, list] = {}
    for s in shifts:
        if getattr(s, "status", "published") != "published":
            continue
        shifts_by_day.setdefault(s.work_date, []).append(s)
    entries_by_day: dict[date, list] = {}
    for e in entries:
        if e.clock_out is None:
            continue
        local_in = e.clock_in.astimezone(TZ)
        entries_by_day.setdefault(local_in.date(), []).append(e)

    def leave_for(d: date):
        for lv in leaves:
            if lv.start_date <= d <= lv.end_date:
                return lv
        return None

    rows: list[DayRow] = []
    workdays = rest_days = 0
    rm100 = rm50 = vp50 = recorded = 0
    abs_days: dict[str, int] = {}
    abs_hours_flag: dict[str, bool] = {}
    for code_cfg in company.absence_codes.values():
        abs_hours_flag[code_cfg["code"]] = bool(code_cfg.get("hours", True))

    d = first
    while d <= last:
        row = DayRow(day=d)
        rules = rules_for(d, overrides)
        row.in_employment = hire <= d and (term is None or d <= term)
        if not row.in_employment:
            rows.append(row)
            d += timedelta(days=1)
            continue
        row.holiday = is_public_holiday(d)
        row.workday = _is_workday(d)
        row.rest_day = not row.workday
        if row.workday:
            workdays += 1
        else:
            rest_days += 1

        # --- beosztás ---
        day_shifts = sorted(shifts_by_day.get(d, []), key=lambda s: s.start_time)
        if day_shifts:
            row.sched_start = day_shifts[0].start_time
            row.sched_end = day_shifts[-1].end_time
            row.sched_minutes = sum(
                max(_span_minutes(s.start_time, s.end_time) - int(s.break_minutes or 0), 0)
                for s in day_shifts
            )

        # --- távollét ---
        lv = leave_for(d)
        if lv is not None and row.workday:
            cfg = company.absence_codes.get(lv.type, DEFAULT_ABSENCE_CODES.get(lv.type))
            if cfg:
                row.code = cfg["code"]
                abs_days[row.code] = abs_days.get(row.code, 0) + 1
                if cfg.get("paid"):
                    row.work_minutes = daily_minutes
                    recorded += daily_minutes
            rows.append(row)
            d += timedelta(days=1)
            continue

        # --- tényleges munkaidő ---
        if schedule_based:
            row.work_start, row.work_end = row.sched_start, row.sched_end
            row.work_minutes = row.sched_minutes
        else:
            day_entries = sorted(entries_by_day.get(d, []), key=lambda e: e.clock_in)
            if day_entries:
                row.work_start = day_entries[0].clock_in.astimezone(TZ).time().replace(second=0, microsecond=0)
                row.work_end = day_entries[-1].clock_out.astimezone(TZ).time().replace(second=0, microsecond=0)
                raw = sum(
                    int((e.clock_out - e.clock_in).total_seconds() // 60) for e in day_entries
                )
                manual_break = sum(int(e.break_minutes or 0) for e in day_entries)
                if break_rule.paid:
                    deduct = 0
                else:
                    deduct = max(manual_break, break_rule.deduction(raw, rules))
                row.work_minutes = max(raw - deduct, 0)
        recorded += row.work_minutes

        # --- pótlékok (csak tényleges munkára) ---
        if row.work_minutes > 0:
            if row.holiday:
                row.rm100 = row.work_minutes
            elif row.workday:
                base = row.sched_minutes if day_shifts else daily_minutes
                row.rm50 = max(row.work_minutes - base, 0)
            else:  # heti pihenőnap
                row.rm100 = max(row.work_minutes - row.sched_minutes, 0) if day_shifts else row.work_minutes
            if d.weekday() == 6 and sunday_bonus:
                row.vp50 = row.work_minutes
        rm100 += row.rm100
        rm50 += row.rm50
        vp50 += row.vp50
        rows.append(row)
        d += timedelta(days=1)

    absences: dict[str, tuple[int, int | None]] = {}
    for code in ABSENCE_ORDER:
        days = abs_days.get(code, 0)
        has_hours = abs_hours_flag.get(code, True)
        absences[code] = (days, days * daily_minutes if has_hours else None)
    for code, days in abs_days.items():  # egyedi kódok a cég beállításából
        if code not in absences:
            absences[code] = (days, days * daily_minutes if abs_hours_flag.get(code, True) else None)

    return MonthSheet(
        employee_id=str(getattr(employee, "id", "")),
        name=f"{employee.last_name} {employee.first_name}",
        tax_id=tax_id,
        hire_date=hire,
        termination_date=term,
        site=(getattr(employee, "site", None) or None),
        year=year,
        month=month,
        daily_minutes=daily_minutes,
        rows=rows,
        workdays=workdays,
        rest_days=rest_days,
        frame_minutes=workdays * daily_minutes,
        recorded_minutes=recorded,
        rm100=rm100,
        rm50=rm50,
        vp50=vp50,
        absences=absences,
        rules=rules_for(last, overrides),
    )


def company_rules_from_row(row) -> CompanyWorkRules:
    """WorkRulesSettings DB-sor → CompanyWorkRules (sor nélkül: törvényi alap)."""
    if row is None:
        return CompanyWorkRules()
    codes = dict(DEFAULT_ABSENCE_CODES)
    for key, cfg in (row.absence_codes or {}).items():
        if key in codes and isinstance(cfg, dict) and cfg.get("code"):
            codes[key] = {
                "code": str(cfg["code"])[:8],
                "paid": bool(cfg.get("paid", codes[key]["paid"])),
                "hours": bool(cfg.get("hours", codes[key]["hours"])),
            }
    return CompanyWorkRules(
        break_rule=BreakRule(
            minutes=int(row.break_minutes or 0),
            per_hours=int(row.break_per_hours) if row.break_per_hours else None,
            paid=bool(row.break_paid),
        ),
        sunday_bonus_default=bool(row.sunday_bonus_default),
        group_by_site=bool(row.group_by_site if row.group_by_site is not None else True),
        absence_codes=codes,
    )
