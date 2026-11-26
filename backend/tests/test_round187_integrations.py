"""187. kör: kimenő webhook (aláírt JSON, feliratkozás, újrapróba, auto-kikapcsolás,
próba-küldés) + olvasó API-kulcs (X-Api-Key, csak GET, visszavonás)."""

from __future__ import annotations

import json

from tests.conftest import make_user


async def test_webhook_signed_delivery_and_subscription(client, admin, monkeypatch):
    import app.db as app_db
    import app.services.wfm.webhooks as wh

    _, adm = admin
    res = await client.post("/api/integrations/webhooks", json={
        "name": "Webshop", "url": "https://hooks.example.com/x", "events": ["ticket.created"],
    }, headers=adm)
    assert res.status_code == 201, res.text
    created = res.json()
    secret = created["secret"]
    assert secret and len(secret) > 20 and created["secret_hint"].startswith(secret[:4])
    res = await client.post("/api/integrations/webhooks", json={
        "name": "Másik", "url": "https://hooks.example.com/y", "events": ["settlement.paid"],
    }, headers=adm)
    assert res.status_code == 201
    # a lista már nem adja ki a titkot
    rows = (await client.get("/api/integrations/webhooks", headers=adm)).json()
    assert len(rows) == 2 and all(r["secret"] is None for r in rows)
    # ismeretlen esemény → 422
    bad = await client.post("/api/integrations/webhooks", json={"name": "x", "url": "https://a.b/c", "events": ["nincs.ilyen"]}, headers=adm)
    assert bad.status_code == 422

    calls: list[dict] = []

    async def fake_post(url, sec, event, body, delivery_id):
        calls.append({"url": url, "secret": sec, "event": event, "body": body, "delivery": delivery_id})
        return 200, None

    monkeypatch.setattr(wh, "post_once", fake_post)
    monkeypatch.setattr(wh, "RETRY_DELAYS", (0, 0, 0))
    ctx = {"_ticket_id": "t-1", "_partner_id": "p-1", "cim": "Nem fűt", "partner_nev": "Pláza", "link": "https://x/szerviz?ticket=t-1"}
    async with app_db.get_session_factory()() as db:
        assert await wh.deliver_all(db, "ticket.created", ctx, wait=True) == 1
    assert len(calls) == 1 and calls[0]["url"] == "https://hooks.example.com/x" and calls[0]["secret"] == secret
    payload = json.loads(calls[0]["body"])
    assert payload["event"] == "ticket.created" and payload["data"]["cim"] == "Nem fűt" and payload["ids"]["ticket_id"] == "t-1"
    assert payload["link"].endswith("ticket=t-1") and "_ticket_id" not in payload["data"]
    assert wh.verify(secret, calls[0]["body"], wh.sign(secret, calls[0]["body"]))
    assert not wh.verify("rossz", calls[0]["body"], wh.sign(secret, calls[0]["body"]))
    # számlálók
    row = next(r for r in (await client.get("/api/integrations/webhooks", headers=adm)).json() if r["id"] == created["id"])
    assert row["delivered"] == 1 and row["failures"] == 0 and row["last_status"] == 200

    # run_event is kiküldi (a beépített Telegram után)
    seen: list[str] = []

    async def fake_deliver(db, event, c, *, wait=False):
        seen.append(event)
        return 0

    monkeypatch.setattr(wh, "deliver_all", fake_deliver)
    from app.services.wfm.automation import run_event

    async with app_db.get_session_factory()() as db:
        await run_event(db, "ticket.created", dict(ctx))
    assert seen == ["ticket.created"]


async def test_webhook_failures_retry_and_auto_disable(client, admin, monkeypatch):
    import app.db as app_db
    import app.services.wfm.webhooks as wh
    from app.models import WebhookEndpoint

    _, adm = admin
    w = (await client.post("/api/integrations/webhooks", json={"name": "Hibás", "url": "https://down.example.com/h", "events": []}, headers=adm)).json()
    attempts: list[int] = []

    async def failing(url, sec, event, body, delivery_id):
        attempts.append(1)
        return 500, "boom"

    monkeypatch.setattr(wh, "post_once", failing)
    monkeypatch.setattr(wh, "RETRY_DELAYS", (0, 0, 0))
    async with app_db.get_session_factory()() as db:
        assert await wh.deliver_all(db, "stock.low", {"termek_nev": "Kávé"}, wait=True) == 1
    assert len(attempts) == 3  # három próba
    row = next(r for r in (await client.get("/api/integrations/webhooks", headers=adm)).json() if r["id"] == w["id"])
    assert row["failures"] == 1 and row["last_status"] == 500 and row["active"] is True
    # 19 korábbi hiba + ez → kikapcsol
    import uuid

    async with app_db.get_session_factory()() as db:
        ep = await db.get(WebhookEndpoint, uuid.UUID(w["id"]))
        ep.failures = 19
        await db.commit()
    async with app_db.get_session_factory()() as db:
        await wh.deliver_all(db, "stock.low", {"termek_nev": "Kávé"}, wait=True)
    row = next(r for r in (await client.get("/api/integrations/webhooks", headers=adm)).json() if r["id"] == w["id"])
    assert row["active"] is False and "kikapcsolva" in (row["last_error"] or "")
    # 4xx: nincs újrapróba
    attempts.clear()

    async def client_error(url, sec, event, body, delivery_id):
        attempts.append(1)
        return 404, "nope"

    monkeypatch.setattr(wh, "post_once", client_error)
    res = await client.patch(f"/api/integrations/webhooks/{w['id']}", json={"active": True}, headers=adm)
    assert res.status_code == 200 and res.json()["failures"] == 0
    async with app_db.get_session_factory()() as db:
        await wh.deliver_all(db, "stock.low", {"termek_nev": "Kávé"}, wait=True)
    assert len(attempts) == 1

    # próba-küldés a felületről + titok-csere + törlés
    async def ok_post(url, sec, event, body, delivery_id):
        assert event == "webhook.test"
        return 204, None

    monkeypatch.setattr(wh, "post_once", ok_post)
    res = await client.post(f"/api/integrations/webhooks/{w['id']}/test", headers=adm)
    assert res.status_code == 200 and res.json()["ok"] is True and res.json()["status"] == 204
    rot = (await client.patch(f"/api/integrations/webhooks/{w['id']}", json={"rotate_secret": True}, headers=adm)).json()
    assert rot["secret"] and rot["secret_hint"].startswith(rot["secret"][:4])
    assert (await client.delete(f"/api/integrations/webhooks/{w['id']}", headers=adm)).status_code == 204
    assert all(r["id"] != w["id"] for r in (await client.get("/api/integrations/webhooks", headers=adm)).json())


async def test_api_key_read_only_access(client, admin):
    _, adm = admin
    res = await client.post("/api/integrations/api-keys", json={"name": "webshop-szinkron"}, headers=adm)
    assert res.status_code == 201, res.text
    k = res.json()
    raw = k["key"]
    assert raw and raw.startswith("xa_") and k["prefix"] == raw[:10]
    assert all(x["key"] is None for x in (await client.get("/api/integrations/api-keys", headers=adm)).json())

    hdr = {"X-Api-Key": raw}
    res = await client.get("/api/partners", headers=hdr)
    assert res.status_code == 200, res.text
    assert (await client.get("/api/auth/me", headers=hdr)).status_code == 200
    # írás tilos a kulccsal
    res = await client.post("/api/partners", json={"name": "Kulcsos Kft."}, headers=hdr)
    assert res.status_code == 403 and res.json()["detail"]["code"] == "auth.api_key_read_only"
    assert (await client.get("/api/partners", headers={"X-Api-Key": "xa_rossz"})).status_code == 401
    # utolsó használat rögzül
    rows = (await client.get("/api/integrations/api-keys", headers=adm)).json()
    assert next(x for x in rows if x["id"] == k["id"])["last_used_at"] is not None
    # visszavonás
    res = await client.delete(f"/api/integrations/api-keys/{k['id']}", headers=adm)
    assert res.status_code == 200 and res.json()["revoked_at"] is not None
    assert (await client.get("/api/partners", headers=hdr)).status_code == 401
    # nem admin nem kezelhet kulcsot
    _, mgr = await make_user(email="mgr187@example.com", role="manager")
    assert (await client.post("/api/integrations/api-keys", json={"name": "x"}, headers=mgr)).status_code == 403
    assert (await client.get("/api/integrations/events", headers=adm)).status_code == 200
