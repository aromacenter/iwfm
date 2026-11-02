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
# Könyvelői egyeztetés (2026-11-02): SZ szabadság, BSZ betegszabadság (a táppénzt
# nem kell külön jelölni, azt a bérprogram számolja), TVL igazolt távollét,
# FN fizetés nélküli. A KN (kiküldetési nap) nem távollét — kódként a cég
# felveheti a Beállításokban, ha használja.
DEFAULT_ABSENCE_CODES: dict[str, dict] = {
    "annual": {"code": "SZ", "paid": True, "hours": True},
    "sick": {"code": "BSZ", "paid": True, "hours": True},
    "other": {"code": "TVL", "paid": False, "hours": True},
    "unpaid": {"code": "FN", "paid": False, "hours": False},
}
ABSENCE_ORDER = ("SZ", "BSZ", "TVL", "FN")


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
    # Könyvelő: a vasárnapi 50% minden normál munkarendű dolgozónak jár, munkakörtől
    # függetlenül — kivétel a munkaidőkeretes, pihenőnapra is beosztható dolgozó
    # (nála az Employee.sunday_bonus = False kapcsolja ki).
    sunday_bonus_default: bool = True
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
    code: str | None = None       # SZ / BSZ / TVL / FN
    rm100: int = 0                # rendkívüli munka heti pihenőnapon / munkaszüneti napon (143. §)
    rm50: int = 0                 # rendkívüli munka munkanapon (143. §)
    vp50: int = 0                 # vasárnapi pótlék (140. § (1))
    up100: int = 0                # munkaszüneti napi pótlék (140. § (2)) — ünnepnapi munka
    ep15: int = 0                 # éjszakai pótlék (142. §): 22–06 óra, ha > 1 óra és nem műszakpótlékos
    mp30: int = 0                 # műszakpótlék (141. §): 18–06 óra, műszakpótlékra jogosultnál

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
    holidays: int                # munkaszüneti napok a hónapban (külön sor, nem pihenőnap)
    frame_minutes: int           # Munkaidő = Havi órák
    recorded_minutes: int        # Nyilvántartott idő
    rm100: int
    rm50: int
    vp50: int
    up100: int
    ep15: int
    mp30: int
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


def _overlap_minutes(start: datetime, end: datetime, from_h: int, to_h: int) -> int:
    """Hány perc esik a [start, end) munkaidőből a from_h–to_h (éjfélen átnyúló)
    sávba — pl. 22–06 (éjszakai) vagy 18–06 (műszak)."""
    if end <= start:
        return 0
    total = 0
    # az ablakot naponként vizsgáljuk a kezdőnap előtti naptól a befejezés napjáig
    day = (start - timedelta(days=1)).date()
    while day <= end.date():
        w_start = datetime.combine(day, time(from_h, 0), tzinfo=start.tzinfo)
        w_end = datetime.combine(day + timedelta(days=1), time(to_h, 0), tzinfo=start.tzinfo)
        lo, hi = max(start, w_start), min(end, w_end)
        if hi > lo:
            total += int((hi - lo).total_seconds() // 60)
        day += timedelta(days=1)
    return total


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
    workdays = rest_days = holidays = 0
    rm100 = rm50 = vp50 = up100 = ep15 = mp30 = recorded = 0
    shift_bonus = bool(getattr(employee, "shift_bonus", False))
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
        elif row.holiday:
            holidays += 1   # munkaszüneti nap: külön számláló (könyvelő: nem sima pihenőnap)
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
        spans: list[tuple[datetime, datetime]] = []  # az éjszakai / műszak sávhoz
        if schedule_based:
            row.work_start, row.work_end = row.sched_start, row.sched_end
            row.work_minutes = row.sched_minutes
            for sh in day_shifts:
                st = datetime.combine(d, sh.start_time, tzinfo=TZ)
                en = datetime.combine(d, sh.end_time, tzinfo=TZ)
                if en <= st:
                    en += timedelta(days=1)
                spans.append((st, en))
        else:
            day_entries = sorted(entries_by_day.get(d, []), key=lambda e: e.clock_in)
            if day_entries:
                row.work_start = day_entries[0].clock_in.astimezone(TZ).time().replace(second=0, microsecond=0)
                row.work_end = day_entries[-1].clock_out.astimezone(TZ).time().replace(second=0, microsecond=0)
                spans = [(e.clock_in.astimezone(TZ), e.clock_out.astimezone(TZ)) for e in day_entries]
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
                # munkaszüneti nap: 100% ünnepnapi pótlék minden dolgozott órára (140. § (2)),
                # a beosztáson felüli / beosztás nélküli rész rendkívüli is (143. § (3))
                row.up100 = row.work_minutes
                row.rm100 = max(row.work_minutes - row.sched_minutes, 0) if day_shifts else row.work_minutes
            elif row.workday:
                base = row.sched_minutes if day_shifts else daily_minutes
                row.rm50 = max(row.work_minutes - base, 0)
            else:  # heti pihenőnap
                row.rm100 = max(row.work_minutes - row.sched_minutes, 0) if day_shifts else row.work_minutes
            # vasárnapi pótlék a munkaszüneti napra eső vasárnapon is jár (könyvelő: mindkettő)
            if d.weekday() == 6 and sunday_bonus:
                row.vp50 = row.work_minutes
            # éjszakai / műszakpótlék a tényleges sávok alapján
            if spans:
                night = sum(_overlap_minutes(st, en, rules.night_from_hour, rules.night_to_hour) for st, en in spans)
                if shift_bonus:
                    row.mp30 = sum(_overlap_minutes(st, en, 18, 6) for st, en in spans)
                elif night > rules.night_min_minutes:
                    row.ep15 = night
        rm100 += row.rm100
        rm50 += row.rm50
        vp50 += row.vp50
        up100 += row.up100
        ep15 += row.ep15
        mp30 += row.mp30
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
        holidays=holidays,
        frame_minutes=workdays * daily_minutes,
        recorded_minutes=recorded,
        rm100=rm100,
        rm50=rm50,
        vp50=vp50,
        up100=up100,
        ep15=ep15,
        mp30=mp30,
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
        sunday_bonus_default=bool(row.sunday_bonus_default) if row.sunday_bonus_default is not None else True,
        group_by_site=bool(row.group_by_site if row.group_by_site is not None else True),
        absence_codes=codes,
    )
