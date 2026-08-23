"""Karbantartási díj: gépből előtöltés, munkalapon átírás/kedvezmény, a két
aláírás utáni auto-számlázás feltételei; szervizjegy-kiosztás Telegramja."""

from __future__ import annotations

from tests.conftest import make_employee_record, make_user

PNG_SIG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="


async def _setup(client, mgr, fee=5000.0):
    res = await client.post("/api/partners", json={
        "name": "Karbantartós Kft.", "contact_email": "karb@example.com",
    }, headers=mgr)
    partner = res.json()
    res = await client.post(
        "/api/assets",
        json={"barcode": "MAINT-1", "name": "Karbantartós Gép",
              "maintenance_fee": fee},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    asset = res.json()
    assert asset["maintenance_fee"] == fee
    await client.post(
        f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]},
        headers=mgr,
    )
    return partner, asset


async def test_worksheet_fee_prefill_and_flow(client, admin, manager, monkeypatch):
    _, adm = admin
    _, mgr = manager
    partner, asset = await _setup(client, mgr)

    # dolgozó a feladathoz
    res = await client.post(
        "/api/employees",
        json={"last_name": "Karb", "first_name": "Antal", "email": "karbantal@example.com",
              "hire_date": "2024-01-01", "weekly_hours": 40},
        headers=adm,
    )
    emp = res.json()

    # KSZ-feladat a géppel → a munkalap díja előtöltődik
    res = await client.post(
        "/api/tasks",
        json={"title": "Éves karbantartás", "employee_id": emp["id"],
              "due_date": "2026-08-30", "external_service": True,
              "asset_id": asset["id"]},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    task_id = res.json()["id"]
    ws = (await client.get(f"/api/tasks/{task_id}/worksheet", headers=mgr)).json()
    assert ws["maintenance_fee"] == 5000.0
    assert ws["fee_discount"] is False
    assert ws["invoiced"] is False

    # a számlázó hívást elfogjuk: Billingó nélkül is tesztelhető
    calls: list[dict] = []

    async def fake_invoice(db, partner_obj, *, serial, asset_label, amount_net, vat_percent=27):
        from datetime import date
        calls.append({"partner": partner_obj.name, "serial": serial, "amount": amount_net})
        return "DOC-1", "invoice", date(2026, 9, 1)

    import app.services.wfm.billingo_service as bsvc

    monkeypatch.setattr(bsvc, "create_maintenance_invoice", fake_invoice)

    # 1) csak dolgozói aláírás → még nincs számla
    res = await client.put(
        f"/api/tasks/{task_id}/worksheet",
        json={"work_description": "Karbantartás kész", "materials": [],
              "maintenance_fee": 6000.0,  # átírta a végző
              "employee_signature": PNG_SIG},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["maintenance_fee"] == 6000.0
    assert not calls

    # 2) ügyfél-aláírás is → auto-számla + jelölés
    res = await client.put(
        f"/api/tasks/{task_id}/worksheet",
        json={"work_description": "Karbantartás kész", "materials": [],
              "client_signature": PNG_SIG},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert len(calls) == 1, calls
    assert calls[0]["amount"] == 6000.0
    assert calls[0]["partner"] == "Karbantartós Kft."
    assert res.json()["invoiced"] is True

    # 3) újramentés nem számláz duplán
    await client.put(
        f"/api/tasks/{task_id}/worksheet",
        json={"work_description": "Karbantartás kész", "materials": []},
        headers=mgr,
    )
    assert len(calls) == 1


async def test_fee_discount_skips_invoice(client, admin, manager, monkeypatch):
    _, adm = admin
    _, mgr = manager
    partner, asset = await _setup(client, mgr)
    res = await client.post(
        "/api/employees",
        json={"last_name": "Kedv", "first_name": "Ezmény", "email": "kedvezmeny@example.com",
              "hire_date": "2024-01-01", "weekly_hours": 40},
        headers=adm,
    )
    emp = res.json()
    res = await client.post(
        "/api/tasks",
        json={"title": "Kedvezményes karbantartás", "employee_id": emp["id"],
              "due_date": "2026-08-30", "external_service": True,
              "asset_id": asset["id"]},
        headers=mgr,
    )
    task_id = res.json()["id"]

    calls: list = []

    async def fake_invoice(*a, **k):
        calls.append(1)
        from datetime import date
        return "DOC-X", "invoice", date(2026, 9, 1)

    import app.services.wfm.billingo_service as bsvc

    monkeypatch.setattr(bsvc, "create_maintenance_invoice", fake_invoice)

    res = await client.put(
        f"/api/tasks/{task_id}/worksheet",
        json={"work_description": "Kész", "materials": [], "fee_discount": True,
              "employee_signature": PNG_SIG, "client_signature": PNG_SIG},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["fee_discount"] is True
    assert not calls  # kedvezmény → nincs auto-számla


async def test_ticket_assign_personal_telegram(client, admin, manager, monkeypatch):
    """Szervizjegy-kiosztáskor a szervizes privát Telegramot kap, és a jegy
    lekérdezhető a saját szűrővel (feladataim-blokk forrása)."""
    _, adm = admin
    _, mgr = manager

    # Telegram be + összekapcsolt szervizes
    await client.put(
        "/api/settings/notifications",
        json={"daily_enabled": False, "send_hour": 6, "weekly_backup": False,
              "auto_receipt": False, "tg_enabled": True, "tg_token": "123:abc",
              "tg_chat_ids": "-100555"},
        headers=adm,
    )
    srv_user, srv_hdr = await make_user(email="szervizes@example.com", role="szervizes")
    emp = await make_employee_record(srv_user)

    from app import db as app_db

    factory = app_db.get_session_factory()
    async with factory() as session:
        db_emp = await session.get(type(emp), emp.id)
        db_emp.telegram_chat_id = "888"
        await session.commit()

    sent: list[tuple[str, str]] = []

    async def fake_send(config, chat_id, text):
        sent.append((str(chat_id), text))
        return True

    import app.services.wfm.telegram as tg_mod

    monkeypatch.setattr(tg_mod, "send_telegram", fake_send)

    res = await client.post(
        "/api/service",
        json={"title": "Bojler javítás", "kind": "repair", "priority": "high",
              "assigned_to_user_id": str(srv_user.id)},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    personal = [x for x in sent if x[0] == "888"]
    assert len(personal) == 1, sent
    assert "Bojler javítás" in personal[0][1] and "SÜRGŐS" in personal[0][1]

    # a szervizes lekérdezheti a saját jegyeit (feladataim-blokk)
    rows = (
        await client.get(f"/api/service?assigned_to={srv_user.id}", headers=srv_hdr)
    ).json()
    assert len(rows) == 1 and rows[0]["title"] == "Bojler javítás"
