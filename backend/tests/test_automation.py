"""Automatizálások: sablonok, szabályok, motor + több számlálós gépek."""

from __future__ import annotations


async def test_template_crud(client, admin):
    _, adm = admin
    res = await client.post(
        "/api/automation/templates",
        json={"name": "Elszámolás-értesítő", "subject": "Elszámolás — {{partner_nev}}",
              "body": "Kedves {{partner_nev}}! Összeg: {{vegosszeg_brutto}} Ft."},
        headers=adm,
    )
    assert res.status_code == 201, res.text
    tpl = res.json()
    upd = await client.patch(
        f"/api/automation/templates/{tpl['id']}",
        json={"name": "Elszámolás-értesítő v2", "subject": tpl["subject"], "body": tpl["body"]},
        headers=adm,
    )
    assert upd.status_code == 200 and upd.json()["name"] == "Elszámolás-értesítő v2"
    lst = (await client.get("/api/automation/templates", headers=adm)).json()
    assert any(x["id"] == tpl["id"] for x in lst)
    dele = await client.delete(f"/api/automation/templates/{tpl['id']}", headers=adm)
    assert dele.status_code == 200


async def test_rule_validation(client, admin):
    _, adm = admin
    bad = await client.post(
        "/api/automation/rules",
        json={"name": "Rossz", "trigger": "nem.letezik",
              "actions": [{"type": "add_partner_note", "text": "x"}]},
        headers=adm,
    )
    assert bad.status_code == 422

    ok = await client.post(
        "/api/automation/rules",
        json={"name": "Jó szabály", "trigger": "settlement.created",
              "conditions": [{"field": "fizetes_mod", "op": "eq", "value": "cash"}],
              "actions": [{"type": "add_partner_note", "text": "Kp-s elszámolás"}]},
        headers=adm,
    )
    assert ok.status_code == 201, ok.text
    rules = (await client.get("/api/automation/rules", headers=adm)).json()
    assert any(r["name"] == "Jó szabály" for r in rules)


def test_render_and_conditions():
    from app.services.wfm.automation import conditions_met, render

    ctx = {"partner_nev": "Teszt Bolt", "vegosszeg_brutto": 12000, "fizetes_mod": "cash"}
    assert render("Kedves {{partner_nev}}! {{vegosszeg_brutto}} Ft", ctx) == "Kedves Teszt Bolt! 12000 Ft"
    assert render("Nincs ilyen: {{semmi}}.", ctx) == "Nincs ilyen: ."
    assert conditions_met([{"field": "fizetes_mod", "op": "eq", "value": "cash"}], ctx)
    assert conditions_met([{"field": "vegosszeg_brutto", "op": "gte", "value": "10000"}], ctx)
    assert not conditions_met([{"field": "vegosszeg_brutto", "op": "lte", "value": "10000"}], ctx)
    assert conditions_met([{"field": "partner_nev", "op": "contains", "value": "bolt"}], ctx)


async def test_run_event_note_and_email(client, admin, manager, monkeypatch):
    """A motor: feltétel-egyezés → partner-jegyzet + e-mail sablonból."""
    import app.db as app_db
    from app.services.wfm import automation

    _, adm = admin
    _, mgr = manager
    partner = (
        await client.post("/api/partners", json={"name": "Automatás Bolt"}, headers=mgr)
    ).json()

    tpl = (
        await client.post(
            "/api/automation/templates",
            json={"name": "Riasztó", "subject": "Nagy elszámolás — {{partner_nev}}",
                  "body": "Összeg: {{vegosszeg_brutto}} Ft"},
            headers=adm,
        )
    ).json()
    await client.put(
        "/api/settings/notifications",
        json={"daily_enabled": False, "recipients": "vezeto@ceg.hu", "send_hour": 7,
              "weekly_backup": False, "auto_receipt": False},
        headers=adm,
    )
    res = await client.post(
        "/api/automation/rules",
        json={
            "name": "Nagy elszámolás riasztás",
            "trigger": "settlement.created",
            "conditions": [{"field": "vegosszeg_brutto", "op": "gte", "value": "10000"}],
            "actions": [
                {"type": "send_email", "template_id": tpl["id"], "to": "recipients"},
                {"type": "add_partner_note", "text": "Nagy elszámolás: {{vegosszeg_brutto}} Ft"},
            ],
        },
        headers=adm,
    )
    assert res.status_code == 201, res.text

    sent: list[tuple[str, str, str]] = []

    class FakeConfig:
        pass

    async def fake_load(db):
        return FakeConfig()

    async def fake_send(config, to, subject, body, attachments=None):
        sent.append((to, subject, body))

    from app.services.wfm import email_service

    monkeypatch.setattr(email_service, "load_smtp_config", fake_load)
    monkeypatch.setattr(email_service, "send_email", fake_send)

    ctx = {
        "_partner_id": __import__("uuid").UUID(partner["id"]),
        "partner_nev": partner["name"],
        "vegosszeg_brutto": 25000,
        "fizetes_mod": "cash",
    }
    async with app_db.get_session_factory()() as session:
        fired = await automation.run_event(session, "settlement.created", ctx)
    assert fired == 1
    assert sent and sent[0][0] == "vezeto@ceg.hu"
    assert "Nagy elszámolás — Automatás Bolt" == sent[0][1]
    assert "25000" in sent[0][2]

    detail = next(
        p for p in (await client.get("/api/partners", headers=mgr)).json()
        if p["id"] == partner["id"]
    )
    assert "Nagy elszámolás: 25000 Ft" in (detail["notes"] or "")

    # a feltétel NEM teljesül → nem fut
    ctx2 = dict(ctx, vegosszeg_brutto=500)
    async with app_db.get_session_factory()() as session:
        fired2 = await automation.run_event(session, "settlement.created", ctx2)
    assert fired2 == 0


def test_parse_tpl_json():
    from app.api.automation import _parse_tpl_json

    raw = '```json\n{"name": "Emlékeztető", "subject": "Fizetés — {{partner_nev}}", "body": "Kedves {{partner_nev}}!"}\n```'
    parsed = _parse_tpl_json(raw)
    assert parsed == {
        "name": "Emlékeztető",
        "subject": "Fizetés — {{partner_nev}}",
        "body": "Kedves {{partner_nev}}!",
    }
    # név nélkül a tárgy lesz a név; tárgy vagy szöveg nélkül None
    assert _parse_tpl_json('{"subject": "T", "body": "B"}')["name"] == "T"
    assert _parse_tpl_json('{"name": "X", "body": "B"}') is None
    assert _parse_tpl_json("nem json") is None


async def test_template_ai_generate(client, admin, monkeypatch):
    """AI-vázlat: a leírásból név/tárgy/szöveg jön vissza (nem ment)."""
    from app.services.wfm import ai_service

    _, adm = admin

    prompts: list[str] = []

    async def fake_generate(db, prompt, **kw):
        prompts.append(prompt)
        return ('{"name": "Fizetési emlékeztető", "subject": "Lejárt elszámolás — '
                '{{partner_nev}}", "body": "Kedves {{partner_nev}}!\\n\\nÖsszeg: '
                '{{vegosszeg_brutto}} Ft."}')

    monkeypatch.setattr(ai_service, "generate", fake_generate)

    res = await client.post(
        "/api/automation/templates/generate",
        json={"description": "fizetési emlékeztető lejárt elszámolásról",
              "trigger": "settlement.created"},
        headers=adm,
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["name"] == "Fizetési emlékeztető"
    assert "{{partner_nev}}" in data["subject"]
    # a trigger változói bekerültek a promptba
    assert "{{vegosszeg_brutto}}" in prompts[0]
    # a sablon nem került mentésre
    lst = (await client.get("/api/automation/templates", headers=adm)).json()
    assert not any(x["name"] == "Fizetési emlékeztető" for x in lst)

    # ismeretlen trigger → 422 validáció
    bad = await client.post(
        "/api/automation/templates/generate",
        json={"description": "valami", "trigger": "nem.letezik"},
        headers=adm,
    )
    assert bad.status_code == 422


async def test_template_ai_not_configured(client, admin):
    """AI-kulcs nélkül 422 settings.ai_not_configured."""
    _, adm = admin
    res = await client.post(
        "/api/automation/templates/generate",
        json={"description": "üdvözlő levél új partnernek"},
        headers=adm,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "settings.ai_not_configured"


async def test_multi_counter_asset(client, manager):
    """Több számlálós gép: darabszám + állások; a counter mindig az összeg."""
    _, mgr = manager
    res = await client.post(
        "/api/assets",
        json={"barcode": "MULTI-1", "name": "Necta Colibri C6",
              "counter_count": 2, "counters": [1200, 800]},
        headers=mgr,
    )
    assert res.status_code in (200, 201), res.text
    a = res.json()
    assert a["counter_count"] == 2
    assert a["counters"] == [1200, 800]
    assert a["counter"] == 2000

    upd = await client.patch(
        f"/api/assets/{a['id']}", json={"counters": [1500, 900]}, headers=mgr
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["counter"] == 2400


async def test_qr_counter_multi(client, manager):
    """QR-oldali számláló-bejelentés több számlálóval + kg-készlettel."""
    from tests.test_support import _asset_with_token

    _, mgr = manager
    asset, token = await _asset_with_token(client, mgr)
    await client.patch(
        f"/api/assets/{asset['id']}", json={"counter_count": 2}, headers=mgr
    )
    res = await client.post(
        f"/api/support/{token}/counter",
        json={"counter": 0, "counters": [700, 300], "stock_kg": 4.5,
              "reporter_name": "Teszt Elek"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["new_counter"] == 1000
    detail = (await client.get(f"/api/assets/{asset['id']}", headers=mgr)).json()
    assert detail["counter"] == 1000
    assert detail["counters"] == [700, 300]