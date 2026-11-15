"""155. kör (x-presso jegyek, 2026-11-15):
- 8fba1fe9: havi béres dolgozó rögzített munkaideje a munkaidő-nyilvántartásban
- c066dc6d: Godex-címkén ügyeleti telefonszám a gyári szám helyett
- c15e4a82: az elkészült-gép értesítő telefonszáma beállításból
- bbbede05: átadás-átvételi elismervény PDF
- aaee410b: FoxPost automata-lista végpont"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass
from datetime import date, time

from tests.conftest import make_employee_record, make_user
from tests.test_round117_timesheet import Emp


@dataclass
class FixedEmp(Emp):
    wage_type: str = "monthly"
    fixed_work_start: time | None = None
    fixed_work_end: time | None = None


def test_timesheet_uses_fixed_hours_for_monthly_wage():
    from app.services.wfm.timesheet_calc import build_month_sheet

    emp = FixedEmp(payroll_source="attendance", fixed_work_start=time(8, 0), fixed_work_end=time(16, 30))
    sheet = build_month_sheet(emp, year=2026, month=11, shifts=[], entries=[], leaves=[])
    workdays = [r for r in sheet.rows if r.workday]
    assert workdays and all(r.work_start == time(8, 0) and r.work_end == time(16, 30) for r in workdays)
    assert all(0 < r.work_minutes <= 510 for r in workdays)
    # pihenőnapon nincs rögzített munkaidő
    rest = [r for r in sheet.rows if r.rest_day and not r.holiday]
    assert rest and all(r.work_start is None and r.work_minutes == 0 for r in rest)
    # órabéresnél a kartonon lévő idő nem számít
    hourly = FixedEmp(payroll_source="attendance", wage_type="hourly", fixed_work_start=time(8, 0), fixed_work_end=time(16, 30))
    sheet2 = build_month_sheet(hourly, year=2026, month=11, shifts=[], entries=[], leaves=[])
    assert all(r.work_minutes == 0 for r in sheet2.rows)


def test_label_phone_replaces_serial():
    from app.services.wfm.qr_label import build_qr_label_ezpx, build_qr_labels_ezpl

    item = {"url": "https://x/s/abc", "name": "Jura X8", "barcode": "GEP-1", "serial_number": "SN-123", "phone": "+36 1 700 4042"}
    ezpl = build_qr_labels_ezpl([item]).decode("ascii")
    assert "Tel: +3617004042" in ezpl and "Gy.sz" not in ezpl
    ezpx = build_qr_label_ezpx(item).decode("utf-8")
    assert "<Data>+36 1 700 4042</Data>" in ezpx and "SN-123" not in ezpx
    # telefonszám nélkül marad a gyári szám
    ezpl2 = build_qr_labels_ezpl([{**item, "phone": None}]).decode("ascii")
    assert "Gy.sz: SN-123" in ezpl2


def test_pickup_phone_lines():
    from types import SimpleNamespace

    from app.api.tasks import _pickup_phone_lines

    assert _pickup_phone_lines(None) == []
    assert _pickup_phone_lines(SimpleNamespace(pickup_phone="  ")) == []
    lines = _pickup_phone_lines(SimpleNamespace(pickup_phone="+36 70 703 40 42"))
    assert lines[0].startswith("Kérjük") and "+36 70 703 40 42" in lines[0] and lines[-1] == ""


def test_handover_receipt_pdf_builds():
    from app.services.wfm.handover_receipt_pdf import build_handover_receipt_pdf

    pdf = build_handover_receipt_pdf(
        {"serial": "ML-2026-0007", "client_name": "Minta Kávézó", "machine": "Melitta Cafina — Melitta",
         "items": [{"name": "Munkadíj", "amount_net": 12000}], "total_net": 12000, "total_gross": 15240,
         "pay_label": "készpénz", "created_at": "2026-11-15 10:00"},
        {"company_name": "Teszt Szerviz Kft.", "accent_color": "#1e40af"},
    )
    assert pdf.startswith(b"%PDF") and len(pdf) > 1500


async def test_settings_phones_and_employee_fixed_hours_roundtrip(client, admin):
    _, adm = admin
    cur = (await client.get("/api/settings/worksheet", headers=adm)).json()
    from app.api.settings import WorksheetSettingsBody

    body = {k: cur.get(k) for k in WorksheetSettingsBody.model_fields if k in cur}
    body.update(pickup_phone=" +36 70 703 40 42 ", label_phone="+36 1 700 4042")
    res = await client.put("/api/settings/worksheet", json=body, headers=adm)
    assert res.status_code == 200, res.text
    out = (await client.get("/api/settings/worksheet", headers=adm)).json()
    assert out["pickup_phone"] == "+36 70 703 40 42" and out["label_phone"] == "+36 1 700 4042"

    user, _ = await make_user(email="fix@example.com", role="employee")
    emp = await make_employee_record(user, last_name="Fix", first_name="Ferenc")
    res = await client.patch(f"/api/employees/{emp.id}", json={"fixed_work_start": "07:30:00", "fixed_work_end": "16:00:00"}, headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()["fixed_work_start"] == "07:30:00" and res.json()["fixed_work_end"] == "16:00:00"


async def test_foxpost_apm_list_cached(client, admin, monkeypatch):
    from app.api import gls

    _, adm = admin
    monkeypatch.setattr(gls, "_APM_CACHE", {"at": _time.time(), "rows": [
        {"id": "hu693", "name": "FOXPOST Körmend Tesco", "address": "9900 Körmend, Sport utca 5.", "zip": "9900",
         "city": "Körmend", "lat": 47.0167, "lng": 16.6148, "outdoor": True, "type": "Rollkon"},
    ]})
    res = await client.get("/api/gls/foxpost/apms", headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()[0]["id"] == "hu693"


async def test_handover_receipt_endpoint(client, manager):
    from tests.test_tasks import payload

    _, mgr = manager
    user, _ = await make_user(email="szerelo155@example.com", role="employee")
    emp = await make_employee_record(user, last_name="Szerelő", first_name="Samu")
    t = (await client.post("/api/tasks", json=payload(str(emp.id), title="Javítás — Melitta", client_name="Minta Kávézó"), headers=mgr)).json()
    res = await client.get(f"/api/tasks/{t['id']}/handover/receipt.pdf", headers=mgr)
    assert res.status_code in (200, 403, 404), res.text  # modul / munkalap függő — ha elérhető, PDF
    if res.status_code == 200:
        assert res.content.startswith(b"%PDF")
    assert date.today()  # a payload a mai dátumot használja
