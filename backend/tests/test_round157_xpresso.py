"""157. kör (x-presso jegyek, 2026-11-16):
- 6be54dff: az ottfelejtett timer őre az újraindított munkamenetet is lezárja
- 1b978d56: elismervény példányszáma a tulajdonos szerint
- ef1cea75: fizetési útmutató minden számla megjegyzésének elejére"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

import app.db as app_db
from app.models import WorksheetTimer
from tests.conftest import make_employee_record, make_user
from tests.test_consignment import make_product
from tests.test_inventory import make_partner
from tests.test_license import _arm_operator
from tests.test_round116 import _set_modules


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


async def test_stale_guard_closes_restarted_session(client, admin, monkeypatch):
    from app.api.worktime import close_stale_timers

    _, adm = admin
    _arm_operator(monkeypatch)
    await _set_modules(client, ["worktime", "parts"])
    user, emp_h = await make_user(email="timer157@example.com", role="employee")
    emp = await make_employee_record(user, last_name="Kiss", first_name="Ottó")
    task = (await client.post("/api/tasks", json={"title": "KSZ javítás", "employee_id": str(emp.id), "due_date": date.today().isoformat()}, headers=adm)).json()
    assert (await client.post(f"/api/worktime/{task['id']}/start", headers=emp_h)).json()["state"] == "running"
    now = datetime.now(UTC)
    # egy rövid, lezárt menet 5 napja, majd egy 3 napja ÚJRAINDÍTOTT, nyitva felejtett menet
    s1 = now - timedelta(days=5)
    s2 = now - timedelta(days=3)
    factory = app_db.get_session_factory()
    async with factory() as session:
        tm = (await session.execute(select(WorksheetTimer).where(WorksheetTimer.user_id == user.id))).scalars().first()
        tm.sessions = [
            {"start": _iso(s1), "end": _iso(s1 + timedelta(minutes=5)), "mode": "work", "pauses": [], "modes": []},
            {"start": _iso(s2), "end": None, "mode": "work", "pauses": [], "modes": []},
        ]
        tm.first_started_at = s1
        tm.last_ended_at = s1 + timedelta(minutes=5)  # az előző menet vége — a régi őr ezen csúszott el
        await session.commit()
    assert (await client.get(f"/api/worktime/{task['id']}", headers=emp_h)).json()["state"] == "running"
    async with factory() as session:
        assert await close_stale_timers(session, now) == 1
        assert await close_stale_timers(session, now) == 0
    assert (await client.get(f"/api/worktime/{task['id']}", headers=emp_h)).json()["state"] == "stopped"
    d0 = (date.today() - timedelta(days=6)).isoformat()
    stats = (await client.get(f"/api/worktime/stats/summary?from={d0}&to={date.today().isoformat()}&employee_id={emp.id}", headers=adm)).json()
    row = next(r for r in stats["rows"] if r["task_id"] == task["id"])
    assert row["state"] == "stopped" and row["auto_closed"] is True and abs(row["active_seconds"] - (12 * 3600 + 300)) < 2


async def test_intake_copies_follow_ownership(client, manager):
    _, mgr = manager
    u, _ = await make_user(email="r157-intake@example.com", role="employee")
    await make_employee_record(u, last_name="Átvevő", first_name="Anna")
    # saját, de még NEM tárgyi (eladásra váró, nyers) gép → 1 példány
    own_raw = (await client.post("/api/assets", json={"barcode": "R157-RAW", "name": "Nyers gép", "tangible": False, "customer_owned": False}, headers=mgr)).json()
    res = await client.post("/api/intakes", json={"asset_id": own_raw["id"], "faults": "ellenőrzés", "counter": 1000}, headers=mgr)  # saját gép: elérhetőség nélkül is
    assert res.status_code == 201, res.text
    res = await client.post(f"/api/intakes/{res.json()['id']}/print", json={}, headers=mgr)
    assert res.status_code == 200 and res.json()["copies"] == 1
    # ügyfél gépe → 2 példány
    cust = (await client.post("/api/assets", json={"barcode": "R157-CUST", "name": "Ügyfél gépe", "customer_owned": True}, headers=mgr)).json()
    res = await client.post("/api/intakes", json={"asset_id": cust["id"], "counter": 1000, "client_name": "Vevő Vera", "client_phone": "+3630999", "client_email": "vera@example.com", "faults": "folyik"}, headers=mgr)
    assert res.status_code == 201, res.text
    res = await client.post(f"/api/intakes/{res.json()['id']}/print", json={}, headers=mgr)
    assert res.json()["copies"] == 2


async def test_invoice_payment_note_default_override_disable(client, admin):
    from app.api.settings import WorksheetSettingsBody
    from app.services.wfm.invoice_note import DEFAULT_PAYMENT_NOTE, payment_note, with_note

    _, adm = admin
    assert with_note("", "x") == "x" and with_note("N", None) == "N" and with_note("N", "x") == "N\nx"
    factory = app_db.get_session_factory()
    async with factory() as session:
        assert await payment_note(session) == DEFAULT_PAYMENT_NOTE
    cur = (await client.get("/api/settings/worksheet", headers=adm)).json()
    assert cur["invoice_payment_note_default"] == DEFAULT_PAYMENT_NOTE and cur["invoice_payment_note"] is None
    body = {k: cur.get(k) for k in WorksheetSettingsBody.model_fields if k in cur}
    body["invoice_payment_note"] = "Közlemény: a számla sorszáma."
    assert (await client.put("/api/settings/worksheet", json=body, headers=adm)).status_code == 200
    async with factory() as session:
        assert await payment_note(session) == "Közlemény: a számla sorszáma."
    body["invoice_payment_note"] = "-"
    assert (await client.put("/api/settings/worksheet", json=body, headers=adm)).status_code == 200
    async with factory() as session:
        assert await payment_note(session) == ""


async def test_settlement_invoice_comment_starts_with_note(client, manager):
    from app.models import Partner, Settlement
    from app.services.wfm.billingo_service import INVOICE_COMMENT, settlement_invoice_comment
    from app.services.wfm.invoice_note import DEFAULT_PAYMENT_NOTE

    _, mgr = manager
    partner = await make_partner(client, mgr)
    product = await make_product(client, mgr)
    assert (await client.post(f"/api/partners/{partner['id']}/stock/replenish", json={"product_id": product["id"], "quantity": 2.0}, headers=mgr)).status_code == 200
    res = await client.post("/api/settlements", json={"partner_id": partner["id"], "payment_method": "transfer", "lines": [{"product_id": product["id"], "physical_qty": 1.0}]}, headers=mgr)
    assert res.status_code == 201, res.text
    factory = app_db.get_session_factory()
    async with factory() as session:
        s = (await session.execute(select(Settlement))).scalars().first()
        p = (await session.execute(select(Partner).where(Partner.id == s.partner_id))).scalar_one()
        text = await settlement_invoice_comment(session, s, p)
    assert text.startswith(DEFAULT_PAYMENT_NOTE) and INVOICE_COMMENT in text
