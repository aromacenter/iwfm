"""Munkajogi szabálykészlet (Mt. 2012. évi I. tv.) — verziózva, hatály-dátummal.

Elv (spec: docs/berexport-munkaido-nyilvantartas-spec.md, 11. pont):
- a számok NEM égnek be a kalkulációba: minden naphoz `rules_for(day)` adja a
  hatályos készletet (a legkésőbbi `valid_from <= day`);
- új Mt.-módosításnál ÚJ elem kerül a RULESETS-be, a régi sosem változik, így
  a korábbi hónapok újragenerálva is a régi értékkel készülnek;
- a Flotta-pultból lenyomott felülírás (LicenseSettings.labor_rules_override)
  ugyanilyen elemek listája; azonos valid_from-nál a DB-s nyer.

Az értékek a Jogtár hatályos szövegéből (ellenőrizve 2026-11-01):
92. § napi 8 óra · 103. § szünet 6 ó felett 20 p, 9 ó felett +25 p ·
126. § betegszabadság 15 munkanap, 70% · 139. § havi osztó 174 ·
140. § vasárnapi 50%, munkaszüneti napi 100% · 141. § műszak 30% ·
142. § éjszakai 15% (>1 óra) · 143. § rendkívüli 50%, heti pihenőnapon
100% (50%, ha másik pihenőnap jár), munkaszüneti napon 100% ·
144. § készenlét 20%, ügyelet 40%.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from datetime import date
from typing import Any


@dataclass(frozen=True)
class LaborRules:
    valid_from: date
    source: str = ""
    daily_full_hours: int = 8                      # 92. §
    # 103. §: (napi munkaidő PERC felett, +szünet perc) — növekvő sorrendben
    break_steps: tuple[tuple[int, int], ...] = ((6 * 60, 20), (9 * 60, 25))
    break_paid_default: bool = False
    sick_leave_days: int = 15                      # 126. §
    sick_leave_pct: int = 70
    monthly_divisor: int = 174                     # 139. §
    sunday_pct: int = 50                           # 140. § (1)
    holiday_pct: int = 100                         # 140. § (2)
    shift_pct: int = 30                            # 141. §
    night_pct: int = 15                            # 142. §
    night_min_minutes: int = 60
    night_from_hour: int = 22                      # 89. §: 22–06
    night_to_hour: int = 6
    overtime_pct: int = 50                         # 143. § (1)
    overtime_restday_pct: int = 100                # 143. § (2)
    overtime_restday_alt_pct: int = 50
    overtime_holiday_pct: int = 100                # 143. § (3)
    standby_pct: int = 20                          # 144. §
    oncall_pct: int = 40

    def law_break_minutes(self, worked_minutes: int) -> int:
        """A törvényi minimum-szünet egy adott hosszúságú munkanapra."""
        total = 0
        for threshold, minutes in self.break_steps:
            if worked_minutes > threshold:
                total += minutes
        return total

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["valid_from"] = self.valid_from.isoformat()
        d["break_steps"] = [list(s) for s in self.break_steps]
        return d


# A kódban élő készletek — a legrégebbi az Mt. hatálybalépése (2012-07-01),
# a mostani értékekkel, mert a nyilvántartást érintő számok azóta nem
# változtak; az ellenőrzés dátuma a source-ban.
RULESETS: tuple[LaborRules, ...] = (
    LaborRules(
        valid_from=date(2012, 7, 1),
        source="Mt. 2012. évi I. tv. — Jogtár, hatály 2026-10-01, ellenőrizve 2026-11-01",
    ),
)

_FIELD_NAMES = {f.name for f in fields(LaborRules)}


def ruleset_from_dict(raw: dict[str, Any]) -> LaborRules | None:
    """Felülírás-elem (DB/JSON) → LaborRules; hibás elem None (nem borít)."""
    if not isinstance(raw, dict):
        return None
    try:
        vf = raw.get("valid_from")
        valid_from = vf if isinstance(vf, date) else date.fromisoformat(str(vf))
    except (TypeError, ValueError):
        return None
    kwargs: dict[str, Any] = {"valid_from": valid_from}
    for key, value in raw.items():
        if key == "valid_from" or key not in _FIELD_NAMES:
            continue
        if key == "break_steps":
            try:
                kwargs[key] = tuple(
                    (int(step[0]), int(step[1])) for step in value
                )
            except (TypeError, ValueError, IndexError):
                return None
        elif key == "source":
            kwargs[key] = str(value)[:256]
        elif isinstance(value, bool):
            kwargs[key] = value
        else:
            try:
                kwargs[key] = int(value)
            except (TypeError, ValueError):
                return None
    return LaborRules(**kwargs)


def merged_rulesets(overrides: list[dict] | None) -> list[LaborRules]:
    """Kód-készletek + felülírások, valid_from szerint rendezve; azonos
    dátumnál a felülírás nyer."""
    by_date: dict[date, LaborRules] = {r.valid_from: r for r in RULESETS}
    for raw in overrides or []:
        rs = ruleset_from_dict(raw)
        if rs is not None:
            by_date[rs.valid_from] = rs
    return [by_date[k] for k in sorted(by_date)]


def rules_for(day: date, overrides: list[dict] | None = None) -> LaborRules:
    """A napra hatályos készlet: a legkésőbbi valid_from <= day. Ha egyik sem
    (a nap az első hatály előtt van), a legkorábbi készlet."""
    sets = merged_rulesets(overrides)
    current = sets[0]
    for rs in sets:
        if rs.valid_from <= day:
            current = rs
        else:
            break
    return current


def with_source(rs: LaborRules, source: str) -> LaborRules:
    return replace(rs, source=source)
