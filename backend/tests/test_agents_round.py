"""40. kör: nincs-minimum szerződés, felelős képviselő + helyettes,
képviselői költségek (kassza), WhatsApp/Telegram beállítás-API."""

from __future__ import annotations

from tests.conftest import make_employee_record, make_user
from tests.test_consignment import make_product


async def test_no_minimum_contract_suppresses_machine_minimums(client, manager):
    """A "nincs minimum" szerződés a gép-szintű minimumokat is kikapcsolja —
    a partner pontosan a lefőzöttet fizeti."""
    _, mgr = manager
    res = await client.post("/api/partners", json={"name": "Minimum Nélkül Kft."}, headers=mgr)
    partner = res.json()
    product = await make_product(client, mgr, name="NoMin Kávé", price_per_portion=50.0)

    # gép egyedi adag-minimummal
    asset = (
        await client.post(
            "/api/assets",
            json={"barcode": "NOMIN-1", "name": "NoMin Gép",
                  "contract_min_portions": 1000, "contract_below_min_price": 40.0,
                  "default_product_id": product["id"]},
            headers=mgr,
        )
    ).json()
    await client.post(
        f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]},
        headers=mgr,
    )
    # szerződés: nincs minimum
    res = await client.post(
        f"/api/partners/{partner['id']}/contracts",
        json={"valid_from": "2026-01-01", "no_minimum": True},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    assert res.json()["no_minimum"] is True

    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 2.0},
        headers=mgr,
    )
    res = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"], "payment_method": "cash",
            "machines": [{"asset_id": asset["id"], "new_counter": 100}],
            "lines": [{"product_id": product["id"], "physical_qty": 1.3}],
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    detail_id = (
        await client.get(f"/api/settlements?partner_id={partner['id']}", headers=mgr)
    ).json()[0]["id"]
    full = (await client.get(f"/api/settlements/{detail_id}", headers=mgr)).json()
    # nincs minimum-különbözet sor (1000 adag minimum mellett 100 adagnál lenne)
    assert not [ln for ln in full["lines"] if "inimum" in ln["product_name"]], full["lines"]
    assert full["total_net"] == 5000.0  # pontosan a lefőzött 100 adag × 50


async def test_partner_agent_and_substitute_resolution(client, manager):
    """Felelős képviselő a partneren; távolléte alatt a helyettes kapja az
    értesítést (resolve_agent_user)."""
    import uuid as uuid_mod
    from datetime import date, timedelta

    from app import db as app_db
    from app.models import TimeOffRequest
    from app.services.wfm.agents import resolve_agent_user

    _, mgr = manager
    agent_user, agent_hdr = await make_user(email="kepviselo@example.com", role="uzletkoto")
    sub_user, _ = await make_user(email="helyettes@example.com", role="uzletkoto")
    emp = await make_employee_record(agent_user)

    # partner a felelős képviselővel
    res = await client.post(
        "/api/partners",
        json={"name": "Képviselős Bolt", "agent_user_id": str(agent_user.id)},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    assert res.json()["agent_user_id"] == str(agent_user.id)

    # a képviselő kijelöli a helyettesét
    res = await client.put(
        "/api/auth/me/substitute", json={"user_id": str(sub_user.id)}, headers=agent_hdr
    )
    assert res.status_code == 200, res.text
    assert res.json()["substitute_user_id"] == str(sub_user.id)

    # önmaga nem lehet helyettes
    res = await client.put(
        "/api/auth/me/substitute", json={"user_id": str(agent_user.id)}, headers=agent_hdr
    )
    assert res.status_code == 422

    factory = app_db.get_session_factory()
    async with factory() as session:
        # távollét nélkül a képviselő az értesítendő
        resolved = await resolve_agent_user(session, agent_user.id)
        assert resolved.id == agent_user.id
        # jóváhagyott, ma is tartó szabadság → a helyettes
        session.add(TimeOffRequest(
            employee_id=emp.id, type="annual", status="approved",
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + timedelta(days=3),
        ))
        await session.commit()
        resolved = await resolve_agent_user(session, agent_user.id)
        assert resolved.id == sub_user.id


async def test_agent_expenses_and_cash_balance(client, manager, admin):
    """Költség-rögzítés a saját kasszára; a summary kasszája levonja."""
    _, mgr = manager
    _, adm = admin
    product = await make_product(client, mgr, name="Kassza Kávé")
    res = await client.post("/api/partners", json={"name": "Kassza Bolt"}, headers=mgr)
    partner = res.json()
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 1.0},
        headers=mgr,
    )
    res = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash",
              "lines": [{"product_id": product["id"], "physical_qty": 0.3}]},
        headers=mgr,
    )
    assert res.status_code == 201, res.text  # 100 adag × 50 = 5000 nettó / 6350 bruttó

    res = await client.post(
        "/api/settlements/expenses",
        json={"amount_gross": 1500, "note": "tankolás"},
        headers=mgr,
    )
    assert res.status_code == 201, res.text

    summary = (await client.get("/api/settlements/summary", headers=mgr)).json()
    assert summary["expenses_total"] == 1500.0
    assert abs(summary["cash_balance"] - (summary["by_payment"]["cash"]["gross"] - 1500)) < 0.01

    rows = (await client.get("/api/settlements/expenses", headers=mgr)).json()
    assert len(rows) == 1 and rows[0]["note"] == "tankolás"

    # törlés: manager (invoicing joggal) tudja; a lista kiürül
    res = await client.delete(f"/api/settlements/expenses/{rows[0]['id']}", headers=adm)
    assert res.status_code == 200
    assert (await client.get("/api/settlements/expenses", headers=mgr)).json() == []


async def test_whatsapp_telegram_settings_roundtrip(client, admin):
    """WhatsApp/Telegram beállítások: token írás-only (set-jelző), többi mező
    oda-vissza; teszt-küldés beállítás nélkül 422."""
    _, adm = admin
    res = await client.put(
        "/api/settings/notifications",
        json={
            "daily_enabled": False, "recipients": None, "send_hour": 6,
            "weekly_backup": False, "auto_receipt": False,
            "wa_enabled": True, "wa_phone_id": "12345", "wa_recipients": "+36 30 123 4567",
            "wa_token": "titkos-wa-token",
            "tg_enabled": True, "tg_chat_ids": "-100999", "tg_token": "123:abc",
        },
        headers=adm,
    )
    assert res.status_code == 200, res.text
    got = (await client.get("/api/settings/notifications", headers=adm)).json()
    assert got["wa_enabled"] is True and got["wa_token_set"] is True
    assert got["wa_phone_id"] == "12345"
    assert got["tg_enabled"] is True and got["tg_token_set"] is True
    assert "titkos" not in str(got)  # a token sosem megy vissza

    # a config-betöltő normalizálja a telefonszámot
    from app import db as app_db
    from app.services.wfm.whatsapp import load_whatsapp_config

    factory = app_db.get_session_factory()
    async with factory() as session:
        config = await load_whatsapp_config(session)
        assert config is not None
        assert config["recipients"] == ["36301234567"]

    # token törlése "-"-szal
    res = await client.put(
        "/api/settings/notifications",
        json={"daily_enabled": False, "send_hour": 6, "weekly_backup": False,
              "auto_receipt": False, "wa_enabled": True, "wa_token": "-",
              "tg_enabled": False},
        headers=adm,
    )
    assert res.status_code == 200
    got = (await client.get("/api/settings/notifications", headers=adm)).json()
    assert got["wa_token_set"] is False

    # teszt-küldés hiányos beállítással: 422
    res = await client.post(
        "/api/settings/notifications/whatsapp-test", json={}, headers=adm
    )
    assert res.status_code == 422
    res = await client.post(
        "/api/settings/notifications/telegram-test", json={}, headers=adm
    )
    assert res.status_code == 422


async def test_telegram_event_selection(client, admin, monkeypatch):
    """Beépített Telegram-értesítés: csak a bepipált eseményről megy üzenet,
    a sablonba a kontextus behelyettesítődik."""
    _, adm = admin
    res = await client.put(
        "/api/settings/notifications",
        json={"daily_enabled": False, "send_hour": 6, "weekly_backup": False,
              "auto_receipt": False, "wa_enabled": False,
              "tg_enabled": True, "tg_token": "123:abc", "tg_chat_ids": "-100555",
              "tg_events": ["order.created"]},
        headers=adm,
    )
    assert res.status_code == 200, res.text
    got = (await client.get("/api/settings/notifications", headers=adm)).json()
    assert got["tg_events"] == ["order.created"]

    # ismeretlen esemény-kulcs: 422
    res = await client.put(
        "/api/settings/notifications",
        json={"daily_enabled": False, "send_hour": 6, "weekly_backup": False,
              "auto_receipt": False, "tg_enabled": True,
              "tg_events": ["nem.letezik"]},
        headers=adm,
    )
    assert res.status_code == 422

    sent: list[tuple[str, str]] = []

    async def fake_send(config, chat_id, text):
        sent.append((chat_id, text))
        return True

    import app.services.wfm.telegram as tg_mod

    monkeypatch.setattr(tg_mod, "send_telegram", fake_send)

    from app import db as app_db
    from app.services.wfm.automation import run_event

    factory = app_db.get_session_factory()
    async with factory() as session:
        # bepipált esemény → megy az üzenet a behelyettesített sablonnal
        await run_event(session, "order.created", {
            "rendeles_szam": "R-2026-0042", "partner_nev": "Teszt Bolt",
            "tetel_lista": "2× Házi keverék",
        })
        # nem pipált esemény → nem megy semmi
        await run_event(session, "partner.created", {"partner_nev": "Másik"})

    assert len(sent) == 1, sent
    assert sent[0][0] == "-100555"
    assert "R-2026-0042" in sent[0][1] and "Teszt Bolt" in sent[0][1]


async def test_telegram_personal_link_and_task_notify(client, admin, manager, monkeypatch):
    """Dolgozói összekapcsolás törzsszámmal (process_updates) + személyes
    üzenet a kiosztott feladatról; admin leválaszthat."""
    _, adm = admin
    _, mgr = manager

    # Telegram bekapcsolva
    res = await client.put(
        "/api/settings/notifications",
        json={"daily_enabled": False, "send_hour": 6, "weekly_backup": False,
              "auto_receipt": False, "tg_enabled": True, "tg_token": "123:abc",
              "tg_chat_ids": "-100555"},
        headers=adm,
    )
    assert res.status_code == 200, res.text

    # dolgozó a rendszerben (törzsszámmal)
    res = await client.post(
        "/api/employees",
        json={"last_name": "Bot", "first_name": "Béla", "email": "botbela@example.com",
              "hire_date": "2024-01-01", "weekly_hours": 40},
        headers=adm,
    )
    assert res.status_code == 201, res.text
    emp = res.json()
    code = emp["employee_code"]
    assert code and len(code) == 6

    sent: list[tuple[str, str]] = []

    async def fake_send(config, chat_id, text):
        sent.append((str(chat_id), text))
        return True

    import app.services.wfm.telegram as tg_mod

    monkeypatch.setattr(tg_mod, "send_telegram", fake_send)

    # bejövő üzenetek szimulálva: rossz kód, jó kód (privát), csoport-üzenet
    async def fake_fetch(config, offset):
        return [
            {"update_id": 10, "message": {"chat": {"id": 777, "type": "private"}, "text": "000000"}},
            {"update_id": 11, "message": {"chat": {"id": 777, "type": "private"}, "text": f"/start {code}"}},
            {"update_id": 12, "message": {"chat": {"id": -100555, "type": "supergroup"}, "text": code}},
        ]

    monkeypatch.setattr(tg_mod, "fetch_updates", fake_fetch)

    from app import db as app_db

    factory = app_db.get_session_factory()
    async with factory() as session:
        linked = await tg_mod.process_updates(session)
    assert linked == 1
    # visszaigazolás + hibaüzenet ment ki a privát chatre
    assert any("Összekapcsolva" in t for _c, t in sent)
    assert any("Ismeretlen" in t for _c, t in sent)

    # a listában látszik az összekapcsolás
    rows = (await client.get("/api/employees", headers=adm)).json()
    me = next(e for e in rows if e["id"] == emp["id"])
    assert me["telegram_linked"] is True

    # feladat-kiosztás → személyes üzenet a 777-es chatre
    sent.clear()
    res = await client.post(
        "/api/tasks",
        json={"title": "Gép tisztítás", "employee_id": emp["id"], "due_date": "2026-08-30"},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    personal = [x for x in sent if x[0] == "777"]
    assert len(personal) == 1, sent
    assert "Gép tisztítás" in personal[0][1]

    # admin leválaszt → jelző eltűnik, több üzenet nem megy
    res = await client.post(f"/api/employees/{emp['id']}/telegram-unlink", json={}, headers=adm)
    assert res.status_code == 200
    rows = (await client.get("/api/employees", headers=adm)).json()
    me = next(e for e in rows if e["id"] == emp["id"])
    assert me["telegram_linked"] is False
