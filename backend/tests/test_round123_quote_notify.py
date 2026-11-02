"""123. kör (x-presso jegy b4838bbd): amikor a szervizes kitölti a javítási
konstrukciókat, az admin/manager/képviselő dolgozók személyes Telegramot kapnak
MINDEN gépnél (nem csak saját tárgyi gépnél), a csoport-esemény
(worksheet.quote_ready) is kimegy, ha be van pipálva, és az automatizálás-trigger
is tüzel."""

from __future__ import annotations

from datetime import date

from tests.conftest import make_employee_record, make_user


async def _setup(client, adm, mgr, monkeypatch, tg_events):
    res = await client.put(
        "/api/settings/notifications",
        json={"daily_enabled": False, "send_hour": 6, "weekly_backup": False,
              "auto_receipt": False, "wa_enabled": False,
              "tg_enabled": True, "tg_token": "123:abc", "tg_chat_ids": "-100555",
              "tg_events": tg_events},
        headers=adm,
    )
    assert res.status_code == 200, res.text
    sent: list[tuple[str, str]] = []

    async def fake_send(config, chat_id, text):
        sent.append((chat_id, text))
        return True

    import app.services.wfm.telegram as tg_mod

    monkeypatch.setattr(tg_mod, "send_telegram", fake_send)
    return sent


async def test_quote_ready_notifies_for_customer_machine(client, admin, manager, monkeypatch):
    admin_user, adm = admin
    _, mgr = manager
    sent = await _setup(client, adm, mgr, monkeypatch, ["worksheet.quote_ready"])
    # az admin dolgozó-rekorddal és összekapcsolt Telegrammal
    await make_employee_record(admin_user, last_name="Admin", first_name="Anna", telegram_chat_id="777")
    emp_user, emp_hdr = await make_user(email="r123-szerelo@example.com", role="szervizes")
    emp = await make_employee_record(emp_user, last_name="Szer", first_name="Elő")
    asset = (await client.post("/api/assets", json={
        "barcode": "R123-ELEG", "name": "Elegance Control 2GR", "manufacturer": "Jardas", "customer_owned": True,
    }, headers=mgr)).json()
    task = (await client.post("/api/tasks", json={
        "title": "Eszpresszó hibás", "employee_id": str(emp.id), "due_date": date.today().isoformat(),
        "external_service": True, "asset_id": asset["id"], "client_name": "Kávézó Kft.",
    }, headers=mgr)).json()

    res = await client.put(f"/api/me/tasks/{task['id']}/worksheet", json={
        "work_description": "Bevizsgálva", "client_name": "Kávézó Kft.",
        "repair_options": [{"name": "Szivattyú csere", "cost_net": 12000}, {"name": "Teljes felújítás", "cost_net": 48000}],
    }, headers=emp_hdr)
    assert res.status_code == 200, res.text

    personal = [t for c, t in sent if c == "777"]
    group = [t for c, t in sent if c == "-100555"]
    assert len(personal) == 1 and "Ajánlat elkészült" in personal[0] and "2 konstrukció" in personal[0]
    assert "R123-ELEG" in personal[0] and "/feladatok" in personal[0] and "\\n" not in personal[0]
    assert len(group) == 1 and "Ajánlat elkészült" in group[0] and "Kávézó Kft." in group[0]

    # újramentés már meglévő konstrukciókkal: nem spammel
    res = await client.put(f"/api/me/tasks/{task['id']}/worksheet", json={
        "work_description": "Bevizsgálva, kiegészítve",
        "repair_options": [{"name": "Szivattyú csere", "cost_net": 12500}],
    }, headers=emp_hdr)
    assert res.status_code == 200
    assert len(sent) == 2


async def test_quote_ready_own_machine_and_event_off(client, admin, manager, monkeypatch):
    admin_user, adm = admin
    _, mgr = manager
    sent = await _setup(client, adm, mgr, monkeypatch, [])  # csoport-esemény NINCS bepipálva
    await make_employee_record(admin_user, last_name="Admin", first_name="Anna", telegram_chat_id="777")
    emp_user, emp_hdr = await make_user(email="r123-szerelo2@example.com", role="szervizes")
    emp = await make_employee_record(emp_user, last_name="Szer", first_name="Elő")
    asset = (await client.post("/api/assets", json={
        "barcode": "R123-OWN", "name": "Jura X8", "tangible": True,
    }, headers=mgr)).json()
    task = (await client.post("/api/tasks", json={
        "title": "Saját gép", "employee_id": str(emp.id), "due_date": date.today().isoformat(),
        "asset_id": asset["id"],
    }, headers=mgr)).json()
    res = await client.put(f"/api/me/tasks/{task['id']}/worksheet", json={
        "work_description": "Bevizsgálva", "repair_options": [{"name": "Bojler csere", "cost_net": 30000}],
    }, headers=emp_hdr)
    assert res.status_code == 200, res.text
    # saját gép: belső döntés szövege, személyes üzenet igen, csoport nem (nincs pipálva)
    assert [c for c, _ in sent] == ["777"]
    assert "SAJÁT gép" in sent[0][1] and "belső döntéssel" in sent[0][1]


async def test_quote_ready_event_is_known(client, admin):
    _, adm = admin
    res = await client.put("/api/settings/notifications", json={
        "daily_enabled": False, "send_hour": 6, "weekly_backup": False, "auto_receipt": False,
        "wa_enabled": False, "tg_enabled": True, "tg_token": "123:abc", "tg_chat_ids": "-1",
        "tg_events": ["worksheet.quote_ready"],
    }, headers=adm)
    assert res.status_code == 200, res.text
    from app.services.wfm.automation import TRIGGERS

    assert "worksheet.quote_ready" in TRIGGERS and "opciok" in TRIGGERS["worksheet.quote_ready"]
