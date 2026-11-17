"""160. kör (x-presso 8fba1fe9, 3. újranyitás): céges alapértelmezett munkaidő minden
munkanapra + napi aláírás a kioszkon, a PDF-ben aláírás-oszlopok."""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from datetime import date, time

from sqlalchemy import select

import app.db as app_db
from app.models import TimeEntry, WorkRulesSettings
from tests.test_kiosk import create_employee
from tests.test_round117_timesheet import Emp

PNG = "data:image/png;base64," + base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x01\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
).decode()


@dataclass
class MonthlyEmp(Emp):
    wage_type: str = "monthly"
    fixed_work_start: time | None = None
    fixed_work_end: time | None = None


def test_company_default_hours_fill_every_workday():
    from app.services.wfm.timesheet_calc import CompanyWorkRules, build_month_sheet

    company = CompanyWorkRules(default_work_start=time(8, 0), default_work_end=time(16, 30))
    sheet = build_month_sheet(MonthlyEmp(payroll_source="attendance"), year=2026, month=11, shifts=[], entries=[], leaves=[], company=company)
    work = [r for r in sheet.rows if r.workday]
    assert work and all(r.work_start == time(8, 0) and r.work_end == time(16, 30) and r.work_minutes > 0 for r in work)
    # a karton-idő erősebb a céges alapnál
    own = MonthlyEmp(payroll_source="attendance", fixed_work_start=time(7, 0), fixed_work_end=time(15, 0))
    sheet2 = build_month_sheet(own, year=2026, month=11, shifts=[], entries=[], leaves=[], company=company)
    assert all(r.work_start == time(7, 0) for r in sheet2.rows if r.workday)


async def test_kiosk_signature_flow_and_pdf(client, admin):
    _, adm = admin
    emp = await create_employee(client, adm)
    code = emp["employee_code"]
    # alapból nem kell aláírás — a preview ezt mondja
    pv = (await client.post("/api/kiosk/preview", json={"code": code})).json()
    assert pv["action"] == "in" and pv["signature_required"] is False
    # bekapcsoljuk a beállításban
    cur = (await client.get("/api/settings/work-rules", headers=adm)).json()
    body = {k: cur[k] for k in ("break_minutes", "break_per_hours", "break_paid", "sunday_bonus_default", "group_by_site", "absence_codes", "paycodes")}
    body.update(kiosk_signature=True, default_work_start="08:00:00", default_work_end="16:30:00")
    res = await client.put("/api/settings/work-rules", json=body, headers=adm)
    assert res.status_code == 200, res.text
    out = (await client.get("/api/settings/work-rules", headers=adm)).json()
    assert out["kiosk_signature"] is True and out["default_work_start"] == "08:00" and out["default_work_end"] == "16:30"
    # aláírás nélkül nem megy
    res = await client.post("/api/kiosk/clock", json={"code": code})
    assert res.status_code == 422 and res.json()["detail"]["code"] == "kiosk.signature_required"
    assert (await client.post("/api/kiosk/preview", json={"code": code})).json()["signature_required"] is True
    # aláírással be, majd ki
    r1 = await client.post("/api/kiosk/clock", json={"code": code, "signature": PNG})
    assert r1.status_code == 200 and r1.json()["action"] == "in"
    r2 = await client.post("/api/kiosk/clock", json={"code": code, "signature": PNG})
    assert r2.status_code == 200 and r2.json()["action"] == "out"
    factory = app_db.get_session_factory()
    async with factory() as session:
        e = (await session.execute(select(TimeEntry))).scalars().first()
        assert e.sign_in_data == PNG and e.sign_out_data == PNG
        assert (await session.execute(select(WorkRulesSettings))).scalar_one().kiosk_signature is True
    # a havi PDF a napi sorban hozza az aláírásokat (nem dob hibát, aláírás-oszlop a fejlécben)
    today = date.today()
    assert (await client.patch(f"/api/employees/{emp['id']}", json={"taj": "000 000 000"}, headers=adm)).status_code == 200  # TAJ nélkül nincs a PDF-en
    res = await client.get(f"/api/payroll/timesheet.pdf?year={today.year}&month={today.month}", headers=adm)
    assert res.status_code == 200 and res.content.startswith(b"%PDF")
    from pypdf import PdfReader

    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(res.content)).pages)
    assert "Aláírás" in text
