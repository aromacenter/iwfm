"""117. kör: havi munkaidő-nyilvántartás (a mintapéldány szerkezete), szabálymotor
(labor_rules verziók + Flotta-felülírás), céges/dolgozói szünet- és pótlék-réteg.

Mintaértékek (spec 7. pont): 2026. június = 22 munkanap, 8 pihenőnap, 176:00;
2 nap szabadság = SZ 2 / 16:00; 08:00–16:20 + 20 p szünet → BI 08:00."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select

import app.db as app_db
from app.models import AuditEvent
from app.services.wfm.labor_rules import RULESETS, rules_for, ruleset_from_dict
from app.services.wfm.timesheet_calc import (
    BreakRule,
    CompanyWorkRules,
    build_month_sheet,
    fmt_hm,
)
from tests.conftest import gen_bank_account, gen_taj, gen_tax_id
from tests.test_license import OP, _arm_operator

TZ_OFFSET = timedelta(hours=2)  # 2026 június: CEST = UTC+2


# ── egyszerű helyettesítők a tiszta kalkulációhoz ─────────────────────────────

@dataclass
class Emp:
    id: str = "e1"
    last_name: str = "Minta"
    first_name: str = "Mátyás"
    hire_date: date = date(2018, 5, 17)
    termination_date: date | None = None
    weekly_hours: int = 40
    payroll_source: str = "schedule"
    sunday_bonus: bool | None = None
    break_rule: dict | None = None
    site: str | None = None


@dataclass
class Sh:
    work_date: date
    start_time: time
    end_time: time
    break_minutes: int = 0
    status: str = "published"


@dataclass
class En:
    clock_in: datetime
    clock_out: datetime | None
    break_minutes: int = 0


@dataclass
class Lv:
    type: str
    start_date: date
    end_date: date


def _june_shifts(start=time(8, 0), end=time(16, 20), brk=20):
    out = []
    d = date(2026, 6, 1)
    while d.month == 6:
        if d.weekday() < 5:
            out.append(Sh(d, start, end, brk))
        d += timedelta(days=1)
    return out


def _local(d: date, hh: int, mm: int) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=UTC) - TZ_OFFSET


# ── kalkuláció ────────────────────────────────────────────────────────────────

def test_sample_month_frame_and_hours():
    sheet = build_month_sheet(Emp(), year=2026, month=6, shifts=_june_shifts(), entries=[], leaves=[])
    assert sheet.workdays == 22
    assert sheet.rest_days == 8
    assert fmt_hm(sheet.frame_minutes) == "176:00"
    assert fmt_hm(sheet.recorded_minutes) == "176:00"
    assert len(sheet.rows) == 30
    first = sheet.rows[0]
    assert first.label == "01.jún" and first.weekday_abbr == "h"
    assert fmt_hm(first.sched_minutes) == "08:00" and fmt_hm(first.work_minutes) == "08:00"
    assert first.sched_start == time(8, 0) and first.sched_end == time(16, 20)
    sat = sheet.rows[5]
    assert sat.weekday_abbr == "szo" and sat.rest_day and sat.work_minutes == 0 and sat.sched_start is None
    assert sheet.rm100 == sheet.rm50 == sheet.vp50 == 0
    assert sheet.absences["SZ"] == (0, 0) and sheet.absences["FN"] == (0, None)
    assert sheet.holidays == 0 and sheet.up100 == sheet.ep15 == sheet.mp30 == 0


def test_two_leave_days_sample():
    leaves = [Lv("annual", date(2026, 6, 5), date(2026, 6, 8))]  # p + h (a hétvége nem számít)
    sheet = build_month_sheet(Emp(), year=2026, month=6, shifts=_june_shifts(time(8, 0), time(16, 0), 0), entries=[], leaves=leaves)
    assert sheet.absences["SZ"] == (2, 960)
    assert fmt_hm(sheet.recorded_minutes) == "176:00"  # a fizetett távollét beleszámít
    row = sheet.rows[4]
    assert row.code == "SZ" and row.work_start is None and fmt_hm(row.work_minutes) == "08:00"
    assert row.sched_start == time(8, 0)  # a beosztás-oszlopok maradnak
    assert sheet.rows[5].code is None  # szombaton nincs kód


def test_unpaid_leave_has_days_but_no_hours():
    leaves = [Lv("unpaid", date(2026, 6, 10), date(2026, 6, 10))]
    sheet = build_month_sheet(Emp(), year=2026, month=6, shifts=_june_shifts(), entries=[], leaves=leaves)
    assert sheet.absences["FN"] == (1, None)
    assert fmt_hm(sheet.recorded_minutes) == "168:00"
    assert sheet.rows[9].code == "FN" and sheet.rows[9].work_minutes == 0


def test_attendance_based_break_rule_company_and_manual_max():
    emp = Emp(payroll_source="attendance")
    d = date(2026, 6, 2)
    entries = [En(_local(d, 7, 52), _local(d, 16, 31), break_minutes=20)]
    company = CompanyWorkRules(break_rule=BreakRule(minutes=30, per_hours=4, paid=False))
    sheet = build_month_sheet(emp, year=2026, month=6, shifts=[], entries=entries, leaves=[], company=company)
    row = sheet.rows[1]
    assert row.work_start == time(7, 52) and row.work_end == time(16, 31)
    assert fmt_hm(row.work_minutes) == "07:39"  # 8:39 − 1:00 (max(kézi 20, szabály 60))
    # fizetett szünet: nincs levonás
    paid = CompanyWorkRules(break_rule=BreakRule(minutes=30, per_hours=4, paid=True))
    sheet2 = build_month_sheet(emp, year=2026, month=6, shifts=[], entries=entries, leaves=[], company=paid)
    assert fmt_hm(sheet2.rows[1].work_minutes) == "08:39"
    # törvényi alap (nincs céges szabály): 8:39 > 6 ó → 20 perc
    sheet3 = build_month_sheet(emp, year=2026, month=6, shifts=[], entries=[En(_local(d, 7, 52), _local(d, 16, 31))], leaves=[])
    assert fmt_hm(sheet3.rows[1].work_minutes) == "08:19"


def test_employee_break_rule_overrides_company():
    emp = Emp(payroll_source="attendance", break_rule={"minutes": 45, "per_hours": 4, "paid": False})
    d = date(2026, 6, 3)
    sheet = build_month_sheet(
        emp, year=2026, month=6, shifts=[], entries=[En(_local(d, 8, 0), _local(d, 16, 0))], leaves=[],
        company=CompanyWorkRules(break_rule=BreakRule(30, 4, False)),
    )
    assert fmt_hm(sheet.rows[2].work_minutes) == "06:30"  # 8:00 − 2×45


def test_supplements_rm50_rm100_vp50():
    emp = Emp(payroll_source="attendance", sunday_bonus=True)
    mon, sat, sun = date(2026, 6, 1), date(2026, 6, 6), date(2026, 6, 7)
    shifts = [Sh(mon, time(8, 0), time(16, 20), 20)]
    entries = [
        En(_local(mon, 8, 0), _local(mon, 18, 10), 20),   # 9:50 → NYI 9:30 (szabály: 20+25=45 > kézi 20) → RM50 = 9:30 − 8:00
        En(_local(sat, 9, 0), _local(sat, 13, 0)),        # pihenőnap, beosztás nélkül → RM100 4:00
        En(_local(sun, 9, 0), _local(sun, 12, 0)),        # vasárnap → RM100 3:00 + VP 3:00
    ]
    sheet = build_month_sheet(emp, year=2026, month=6, shifts=shifts, entries=entries, leaves=[])
    assert fmt_hm(sheet.rows[0].work_minutes) == "09:25"
    assert fmt_hm(sheet.rows[0].rm50) == "01:25"
    assert fmt_hm(sheet.rows[5].rm100) == "04:00"
    assert fmt_hm(sheet.rows[6].rm100) == "03:00" and fmt_hm(sheet.rows[6].vp50) == "03:00"
    assert fmt_hm(sheet.rm100) == "07:00" and fmt_hm(sheet.vp50) == "03:00"
    # céges alap (könyvelő): a vasárnapi pótlék mindenkinek jár → None is jár
    sheet2 = build_month_sheet(Emp(payroll_source="attendance"), year=2026, month=6, shifts=shifts, entries=entries, leaves=[])
    assert fmt_hm(sheet2.vp50) == "03:00"
    # munkaidőkeretes, pihenőnapra beosztható dolgozó: kikapcsolva
    sheet3 = build_month_sheet(Emp(payroll_source="attendance", sunday_bonus=False), year=2026, month=6, shifts=shifts, entries=entries, leaves=[])
    assert sheet3.vp50 == 0


def test_holiday_work_is_rm100_and_rest_day():
    # 2026. augusztus 20. (csütörtök) munkaszüneti nap, aug. 21. áthelyezett pihenőnap
    emp = Emp(payroll_source="attendance")
    hol = date(2026, 8, 20)
    sheet = build_month_sheet(
        emp, year=2026, month=8, shifts=[Sh(hol, time(8, 0), time(12, 0))],
        entries=[En(_local(hol, 8, 0), _local(hol, 12, 0))], leaves=[],
    )
    row = sheet.rows[19]
    # beosztott munka munkaszüneti napon: ÜP 100% minden órára, rendkívüli (RM 100%) nincs
    assert row.holiday and row.rest_day and fmt_hm(row.up100) == "04:00" and row.rm100 == 0
    assert sheet.workdays == 20 and sheet.holidays == 1 and sheet.rest_days == 10  # hétvégék + áthelyezett pihenőnap
    # beosztás nélküli ünnepnapi munka: ÜP 100% ÉS rendkívüli 100% is
    sheet2 = build_month_sheet(emp, year=2026, month=8, shifts=[], entries=[En(_local(hol, 8, 0), _local(hol, 12, 0))], leaves=[])
    assert fmt_hm(sheet2.rows[19].up100) == "04:00" and fmt_hm(sheet2.rows[19].rm100) == "04:00"


def test_mid_month_hire_and_termination():
    emp = Emp(hire_date=date(2026, 6, 15))
    sheet = build_month_sheet(emp, year=2026, month=6, shifts=_june_shifts(), entries=[], leaves=[])
    assert sheet.workdays == 12 and fmt_hm(sheet.frame_minutes) == "96:00"
    assert not sheet.rows[0].in_employment and sheet.rows[14].in_employment
    assert fmt_hm(sheet.recorded_minutes) == "96:00"  # a jogviszony előtti műszakok nem számítanak
    emp2 = Emp(termination_date=date(2026, 6, 10))
    sheet2 = build_month_sheet(emp2, year=2026, month=6, shifts=_june_shifts(), entries=[], leaves=[])
    assert sheet2.workdays == 8 and sheet2.rest_days == 2


def test_part_time_daily_frame():
    sheet = build_month_sheet(Emp(weekly_hours=30), year=2026, month=6, shifts=[], entries=[], leaves=[])
    assert sheet.daily_minutes == 360 and fmt_hm(sheet.frame_minutes) == "132:00"


def test_fmt_hm():
    assert fmt_hm(0) == "00:00" and fmt_hm(10560) == "176:00" and fmt_hm(459) == "07:39" and fmt_hm(None) == ""


# ── szabálymotor ──────────────────────────────────────────────────────────────

def test_rules_for_versions_and_override():
    base = rules_for(date(2026, 6, 15))
    assert base.sunday_pct == 50 and base.holiday_pct == 100 and base.sick_leave_days == 15
    assert base.law_break_minutes(8 * 60) == 20 and base.law_break_minutes(9 * 60 + 30) == 45
    assert base.law_break_minutes(6 * 60) == 0
    override = [{"valid_from": "2027-01-01", "sunday_pct": 60, "source": "teszt"}]
    assert rules_for(date(2026, 12, 31), override).sunday_pct == 50
    assert rules_for(date(2027, 1, 1), override).sunday_pct == 60
    assert rules_for(date(2027, 3, 1), override).holiday_pct == 100  # a nem adott mezők az alapértékek
    # a hatály előtti nap a legkorábbi készletet kapja
    assert rules_for(date(2000, 1, 1)).valid_from == RULESETS[0].valid_from
    # azonos dátumú felülírás nyer a kód felett
    same = [{"valid_from": RULESETS[0].valid_from.isoformat(), "overtime_pct": 55}]
    assert rules_for(date(2026, 6, 1), same).overtime_pct == 55


def test_ruleset_from_dict_rejects_garbage():
    assert ruleset_from_dict({"valid_from": "nem-dátum"}) is None
    assert ruleset_from_dict({"valid_from": "2027-01-01", "break_steps": "x"}) is None
    assert ruleset_from_dict("x") is None
    ok = ruleset_from_dict({"valid_from": "2027-01-01", "break_steps": [[360, 25]], "ismeretlen": 1})
    assert ok is not None and ok.break_steps == ((360, 25),) and ok.law_break_minutes(400) == 25


# ── API ───────────────────────────────────────────────────────────────────────

async def _seed_employee(client, admin_headers, mgr_headers, *, seed: int, name: str, site: str | None, with_taj=True, **extra):
    body = {
        "email": f"ts{seed}@example.com", "last_name": name, "first_name": "Teszt",
        "hire_date": "2026-01-01", "tax_id": gen_tax_id(seed), "bank_account": gen_bank_account(seed),
        "payroll_source": "schedule", "site": site, **extra,
    }
    if with_taj:
        body["taj"] = gen_taj(seed)
    res = await client.post("/api/employees", json=body, headers=admin_headers)
    assert res.status_code == 201, res.text
    emp_id = res.json()["id"]
    d = date(2026, 6, 1)
    while d.month == 6:
        if d.weekday() < 5:
            await client.post("/api/shifts", json={
                "employee_id": emp_id, "work_date": d.isoformat(),
                "start_time": "08:00:00", "end_time": "16:20:00", "break_minutes": 20,
            }, headers=mgr_headers)
        d += timedelta(days=1)
    for ws in (date(2026, 6, 1), date(2026, 6, 8), date(2026, 6, 15), date(2026, 6, 22), date(2026, 6, 29)):
        await client.post("/api/shifts/publish", json={"week_start": ws.isoformat()}, headers=mgr_headers)
    return emp_id


async def test_timesheet_pdf_endpoint(client, admin, manager):
    from pypdf import PdfReader

    _, adm = admin
    _, mgr = manager
    e1 = await _seed_employee(client, adm, mgr, seed=11, name="Alfa", site="Budapest")
    await _seed_employee(client, adm, mgr, seed=12, name="Béta", site="Győr")
    await _seed_employee(client, adm, mgr, seed=13, name="Külsős", site=None, with_taj=False)

    rid = (await client.post("/api/time-off", json={
        "employee_id": e1, "type": "annual", "start_date": "2026-06-05", "end_date": "2026-06-08",
    }, headers=mgr)).json()["id"]
    await client.patch(f"/api/time-off/{rid}/decide", json={"status": "approved"}, headers=mgr)

    res = await client.get("/api/payroll/timesheet.pdf?year=2026&month=6", headers=adm)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/pdf")
    assert res.content[:4] == b"%PDF"
    reader = PdfReader(io.BytesIO(res.content))
    assert len(reader.pages) == 2  # a TAJ nélküli külsős kimarad
    text = reader.pages[0].extract_text()
    assert "Munkaidő nyilvántartás" in text and "2026-06-01 - 2026-06-30" in text
    assert "Alfa Teszt" in text and "Telephely: Budapest" in text
    assert "176:00" in text and "Munkanapok" in text and "22" in text
    assert "SZ" in text and "16:00" in text  # 2 nap szabadság
    assert "Munkavállaló aláírása" in text and "1. oldal" in text
    assert gen_tax_id(11) in text  # adóazonosító csak a PDF-ben
    assert "Mt. szabálykészlet" in text

    # audit
    factory = app_db.get_session_factory()
    async with factory() as session:
        ev = (await session.execute(
            select(AuditEvent).where(AuditEvent.action == "payroll.timesheet")
        )).scalars().all()
    assert len(ev) == 1 and ev[0].detail["employees"] == 2

    # egy dolgozó / egy telephely
    one = await client.get(f"/api/payroll/timesheet.pdf?year=2026&month=6&employee_id={e1}", headers=adm)
    assert len(PdfReader(io.BytesIO(one.content)).pages) == 1
    gyor = await client.get("/api/payroll/timesheet.pdf?year=2026&month=6&site=Győr", headers=adm)
    assert "Béta Teszt" in PdfReader(io.BytesIO(gyor.content)).pages[0].extract_text()

    # telephelyenként külön fájl (ZIP)
    zres = await client.get("/api/payroll/timesheet.pdf?year=2026&month=6&split=per_site", headers=adm)
    assert zres.status_code == 200 and zres.headers["content-type"] == "application/zip"
    names = sorted(zipfile.ZipFile(io.BytesIO(zres.content)).namelist())
    assert len(names) == 2 and all(n.endswith(".pdf") for n in names)

    # jogosultság: dolgozó nem tölthet le
    _, emp_headers = await __import__("tests.conftest", fromlist=["make_user"]).make_user(
        email="sima@example.com", role="employee"
    )
    assert (await client.get("/api/payroll/timesheet.pdf?year=2026&month=6", headers=emp_headers)).status_code == 403


async def test_timesheet_requires_payroll_module(client, admin, monkeypatch):
    _, adm = admin
    _arm_operator(monkeypatch)
    res = await client.put("/api/operator/license", json={
        "plan": "xl", "valid_until": None, "enabled_modules": ["employees", "attendance"],
    }, headers=OP)
    assert res.status_code == 200
    res = await client.get("/api/payroll/timesheet.pdf?year=2026&month=6", headers=adm)
    assert res.status_code == 403 and res.json()["detail"]["code"] == "license.module_disabled"


async def test_work_rules_settings_and_below_law(client, admin):
    _, adm = admin
    res = await client.get("/api/settings/work-rules", headers=adm)
    assert res.status_code == 200
    body = res.json()
    assert body["law"]["sunday_pct"] == 50 and body["law"]["break_steps"] == [[360, 20], [540, 25]]
    assert body["law_override_count"] == 0 and body["absence_codes"]["unpaid"]["code"] == "FN"
    assert body["sunday_bonus_default"] is True  # könyvelő: alapból jár

    # 4 óránként 30 perc: szigorúbb a törvénynél → OK
    res = await client.put("/api/settings/work-rules", json={
        "break_minutes": 30, "break_per_hours": 4, "break_paid": False,
        "sunday_bonus_default": True, "group_by_site": False,
        "absence_codes": {"other": {"code": "TP", "paid": False, "hours": True}},
    }, headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()["break_per_hours"] == 4 and res.json()["sunday_bonus_default"] is True
    assert res.json()["absence_codes"]["other"]["code"] == "TP"
    assert res.json()["absence_codes"]["annual"]["code"] == "SZ"  # a többi az alap

    # 6 óránként 10 perc: a törvényi 20 alatt → 422
    res = await client.put("/api/settings/work-rules", json={
        "break_minutes": 10, "break_per_hours": 6, "break_paid": False,
    }, headers=adm)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "work_rules.below_law"


async def test_operator_labor_rules_push(client, admin, monkeypatch):
    _, adm = admin
    _arm_operator(monkeypatch)
    res = await client.put("/api/operator/labor-rules", json={"items": [
        {"valid_from": "2027-01-01", "sunday_pct": 60, "source": "Mt. mód. 2027"},
        {"valid_from": "rossz"},
    ]}, headers=OP)
    assert res.status_code == 200 and res.json()["stored"] == 1
    body = (await client.get("/api/settings/work-rules", headers=adm)).json()
    assert body["law_override_count"] == 1 and "2027-01-01" in body["law_versions"]
    assert body["law"]["sunday_pct"] == 50  # ma még a régi

    factory = app_db.get_session_factory()
    async with factory() as session:
        ev = (await session.execute(
            select(AuditEvent).where(AuditEvent.action == "labor_rules.pushed")
        )).scalars().all()
    assert len(ev) == 1 and ev[0].detail["valid_from"] == ["2027-01-01"]

    # üres lista = felülírás törlése
    res = await client.put("/api/operator/labor-rules", json={"items": []}, headers=OP)
    assert res.json()["stored"] == 0
    assert (await client.get("/api/settings/work-rules", headers=adm)).json()["law_override_count"] == 0


async def test_employee_new_fields_roundtrip(client, admin):
    _, adm = admin
    res = await client.post("/api/employees", json={
        "email": "mezok@example.com", "last_name": "Mező", "first_name": "Márta",
        "hire_date": "2026-01-01", "site": "Pécs", "sunday_bonus": True,
        "bonus_in_base_salary": True, "break_rule": {"minutes": 30, "per_hours": 4, "paid": False},
    }, headers=adm)
    assert res.status_code == 201, res.text
    out = res.json()
    assert out["site"] == "Pécs" and out["sunday_bonus"] is True and out["bonus_in_base_salary"] is True
    assert out["break_rule"] == {"minutes": 30, "per_hours": 4, "paid": False}
    assert out["shift_bonus"] is False
    emp_id = out["id"]
    res = await client.patch(f"/api/employees/{emp_id}", json={"sunday_bonus": None, "break_rule": {}, "shift_bonus": True}, headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()["sunday_bonus"] is None and res.json()["break_rule"] is None and res.json()["shift_bonus"] is True
    res = await client.patch(f"/api/employees/{emp_id}", json={"break_rule": {"minutes": 999, "per_hours": 4}}, headers=adm)
    assert res.status_code == 422


def test_easter_sunday_gets_both_bonuses():
    # 2026-04-05 húsvétvasárnap: munkaszüneti nap vasárnapon → ÜP 100% ÉS VP 50% is jár (könyvelő)
    emp = Emp(payroll_source="attendance")
    d = date(2026, 4, 5)
    sheet = build_month_sheet(emp, year=2026, month=4, shifts=[], entries=[En(_local(d, 9, 0), _local(d, 13, 0))], leaves=[])
    row = sheet.rows[4]
    assert row.holiday and fmt_hm(row.up100) == "04:00" and fmt_hm(row.vp50) == "04:00" and fmt_hm(row.rm100) == "04:00"
    assert sheet.holidays >= 2  # húsvétvasárnap + húsvéthétfő (+ nagypéntek)


def test_night_and_shift_bonus_columns():
    # 20:00–02:00 blokkolás: 4 óra esik 22–06 közé → ÉP 15% (>1 óra); műszakpótlékosnál MP 30% a 18–06 sáv
    emp = Emp(payroll_source="attendance")
    d = date(2026, 6, 2)
    entries = [En(_local(d, 20, 0), _local(d + timedelta(days=1), 2, 0))]
    sheet = build_month_sheet(emp, year=2026, month=6, shifts=[], entries=entries, leaves=[])
    row = sheet.rows[1]
    assert fmt_hm(row.ep15) == "04:00" and row.mp30 == 0 and fmt_hm(sheet.ep15) == "04:00"
    # rövid éjszakai rész (< 1 óra): nincs ÉP
    short = build_month_sheet(emp, year=2026, month=6, shifts=[], entries=[En(_local(d, 16, 0), _local(d, 22, 50))], leaves=[])
    assert short.rows[1].ep15 == 0
    # műszakpótlékos: MP a 18–06 sávra, ÉP nem
    emp_shift = Emp(payroll_source="attendance")
    emp_shift.shift_bonus = True  # type: ignore[attr-defined]
    sh = build_month_sheet(emp_shift, year=2026, month=6, shifts=[], entries=entries, leaves=[])
    assert fmt_hm(sh.rows[1].mp30) == "06:00" and sh.rows[1].ep15 == 0
    # beosztás-alapú dolgozónál a beosztás sávja számít
    sched = Emp(payroll_source="schedule")
    sh2 = build_month_sheet(sched, year=2026, month=6, shifts=[Sh(d, time(22, 0), time(6, 0))], entries=[], leaves=[])
    assert fmt_hm(sh2.rows[1].ep15) == "08:00" and fmt_hm(sh2.rows[1].work_minutes) == "08:00"


def test_day_rows_fall_back_to_the_other_source(monkeypatch):
    """154. kör (x-presso 8fba1fe9): a napi sor akkor is kitöltődik, ha a
    bérszámfejtési forrásban nincs adat — beosztás-alapú dolgozónál műszak
    híján a blokkolás, blokkolás-alapúnál blokkolás híján a beosztás."""
    monkeypatch.setattr("app.services.wfm.timesheet_calc.TZ", UTC)
    shifts = [Sh(date(2026, 6, 2), time(8, 0), time(16, 20), 20)]  # csak keddre van műszak
    entries = [En(datetime(2026, 6, 1, 7, 52, tzinfo=UTC), datetime(2026, 6, 1, 16, 31, tzinfo=UTC))]  # csak hétfőre blokkolás

    # beosztás-alapú: kedd a műszakból, hétfő a blokkolásból (eddig üres maradt)
    sheet = build_month_sheet(Emp(payroll_source="schedule"), year=2026, month=6, shifts=shifts, entries=entries, leaves=[])
    mon = next(r for r in sheet.rows if r.day == date(2026, 6, 1))
    tue = next(r for r in sheet.rows if r.day == date(2026, 6, 2))
    assert (tue.work_start, tue.work_end, tue.work_minutes) == (time(8, 0), time(16, 20), 480)
    assert mon.work_start == time(7, 52) and mon.work_end == time(16, 31) and mon.work_minutes > 0

    # blokkolás-alapú: hétfő a blokkolásból, kedd a beosztásból (eddig üres maradt)
    sheet = build_month_sheet(Emp(payroll_source="attendance"), year=2026, month=6, shifts=shifts, entries=entries, leaves=[])
    mon = next(r for r in sheet.rows if r.day == date(2026, 6, 1))
    tue = next(r for r in sheet.rows if r.day == date(2026, 6, 2))
    assert mon.work_start == time(7, 52) and mon.work_minutes > 0
    assert (tue.work_start, tue.work_end, tue.work_minutes) == (time(8, 0), time(16, 20), 480)
    # amelyik napra semmi sincs, az üres marad
    wed = next(r for r in sheet.rows if r.day == date(2026, 6, 3))
    assert wed.work_start is None and wed.work_minutes == 0
