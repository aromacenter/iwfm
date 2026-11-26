"""186. kör: ügyfél-értékelés a munkalap után — token a két aláírásnál, publikus
értékelés egyszer, esemény + gyenge értékelésnél privát Telegram a vezetőknek,
e-mail link, összesítés dolgozónként."""

from __future__ import annotations

from tests.conftest import make_employee_record, make_user
from tests.test_maintenance_fee import PNG_SIG


async def _setup(client, mgr, hint: str):
    res = await client.post("/api/partners", json={"name": f"Értékelős {hint} Kft.", "contact_email": f"ert-{hint.lower()}@example.com"}, headers=mgr)
    assert res.status_code == 201, res.text
    partner = res.json()
    res = await client.post("/api/assets", json={"barcode": f"RATE-{hint}", "name": "Értékelős Gép", "serial_number": f"SN-RATE-{hint}"}, headers=mgr)
    assert res.status_code == 201, res.text
    asset = res.json()
    await client.post(f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr)
    return partner, asset


async def _task_with_signed_worksheet(client, adm, mgr, *, emp_email: str, serial_hint: str):
    res = await client.post("/api/employees", json={
        "last_name": "Értékelt", "first_name": serial_hint, "email": emp_email,
        "hire_date": "2024-01-01", "weekly_hours": 40,
    }, headers=adm)
    assert res.status_code == 201, res.text
    emp = res.json()
    partner, asset = await _setup(client, mgr, serial_hint)
    res = await client.post("/api/tasks", json={
        "title": f"Javítás {serial_hint}", "employee_id": emp["id"], "due_date": "2026-08-30", "asset_id": asset["id"],
    }, headers=mgr)
    assert res.status_code == 201, res.text
    task_id = res.json()["id"]
    res = await client.put(f"/api/tasks/{task_id}/worksheet", json={
        "work_description": "Kész", "materials": [], "employee_signature": PNG_SIG,
        "client_signature": PNG_SIG, "client_signer_name": "Vevő Vilmos", "client_name": "Vevő Kft.",
    }, headers=mgr)
    assert res.status_code == 200, res.text
    return task_id, res.json(), emp


async def test_signed_worksheet_gets_token_and_public_rating_once(client, admin, manager, monkeypatch):
    import app.services.wfm.telegram as tg_mod

    _, adm = admin
    _, mgr = manager
    task_id, ws, _ = await _task_with_signed_worksheet(client, adm, mgr, emp_email="ert1@example.com", serial_hint="A")
    token = ws["rating_token"]
    assert token and len(token) >= 24 and ws["rating"] is None

    notified: list[tuple[str, dict]] = []
    personal: list[str] = []

    async def fake_notify(db, event, ctx):
        notified.append((event, ctx))
        return True

    async def fake_personal(db, emp, text):
        personal.append(text)
        return True

    monkeypatch.setattr(tg_mod, "notify_event", fake_notify)
    monkeypatch.setattr(tg_mod, "send_personal", fake_personal)

    # publikus info — belépés nélkül
    res = await client.get(f"/api/public/rating/{token}")
    assert res.status_code == 200, res.text
    assert res.json()["serial"] == ws["serial"] and res.json()["rated"] is False and res.json()["technician"] == "Értékelt A"
    assert (await client.get("/api/public/rating/nincs-ilyen")).status_code == 404

    res = await client.post(f"/api/public/rating/{token}", json={"rating": 5, "comment": "Gyors volt, köszi!", "source": "device"})
    assert res.status_code == 200, res.text
    assert notified and notified[0][0] == "worksheet.rated" and notified[0][1]["ertekeles"] == 5 and notified[0][1]["dolgozo"] == "Értékelt A"
    assert personal == []  # jó értékelés: nincs riasztás
    # másodszor már nem
    assert (await client.post(f"/api/public/rating/{token}", json={"rating": 1})).status_code == 409
    assert (await client.get(f"/api/public/rating/{token}")).json()["rated"] is True
    got = (await client.get(f"/api/tasks/{task_id}/worksheet", headers=mgr)).json()
    assert got["rating"] == 5 and got["rating_comment"] == "Gyors volt, köszi!"
    assert (await client.post(f"/api/public/rating/{token}", json={"rating": 9})).status_code in (409, 422)


async def test_low_rating_pings_managers_and_summary(client, admin, manager, monkeypatch):
    import app.services.wfm.telegram as tg_mod

    _, adm = admin
    _, mgr = manager
    # a vezető dolgozói kartonnal (hogy legyen kinek privátot küldeni)
    boss_user, _ = await make_user(email="boss186@example.com", role="manager")
    await make_employee_record(boss_user)
    task_id, ws, _ = await _task_with_signed_worksheet(client, adm, mgr, emp_email="ert2@example.com", serial_hint="B")
    task2, ws2, _ = await _task_with_signed_worksheet(client, adm, mgr, emp_email="ert3@example.com", serial_hint="C")

    personal: list[str] = []

    async def fake_personal(db, emp, text):
        personal.append(text)
        return True

    async def fake_notify(db, event, ctx):
        return True

    monkeypatch.setattr(tg_mod, "send_personal", fake_personal)
    monkeypatch.setattr(tg_mod, "notify_event", fake_notify)
    res = await client.post(f"/api/public/rating/{ws['rating_token']}", json={"rating": 1, "comment": "Két napot késett."})
    assert res.status_code == 200, res.text
    assert personal and "Gyenge ügyfél-értékelés" in personal[0] and "Két napot késett." in personal[0] and f"/feladatok?task={task_id}" in personal[0]
    res = await client.post(f"/api/public/rating/{ws2['rating_token']}", json={"rating": 4})
    assert res.status_code == 200

    summ = (await client.get("/api/ratings/summary", headers=mgr)).json()
    assert summ["count"] == 2 and summ["avg"] == 2.5 and summ["low"] == 1 and summ["dist"]["1"] == 1 and summ["dist"]["4"] == 1
    names = {r["name"]: r for r in summ["by_employee"]}
    assert names["Értékelt B"]["avg"] == 1 and names["Értékelt B"]["low"] == 1 and names["Értékelt C"]["avg"] == 4
    assert any(r["comment"] == "Két napot késett." for r in summ["recent"])
    # napló
    log = (await client.get("/api/audit?action=worksheet.rated", headers=adm)).json()
    rows = log if isinstance(log, list) else log.get("items", [])
    assert any(e["action"] == "worksheet.rated" for e in rows)


async def test_rating_link_in_customer_email(client, admin, manager):
    from app.api.tasks import _rating_lines
    from app.models import Worksheet

    ws = Worksheet(task_id=None, serial="ML-TEST", work_description="x")  # csak a helper viselkedése
    lines = _rating_lines(ws)
    assert ws.rating_token and lines and "/ertekeles/" + ws.rating_token in lines[0]
    ws.rating = 5
    assert _rating_lines(ws) == []
