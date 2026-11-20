"""Ügynök életjel + parancs-sor + hely (3.5): a ping frissíti a pillanatképet
napló-sor nélkül, visszaadja és törli a sorba tett parancsokat; a helyadat az
ügynökből (lat/lon) és az IP-ből (geo, mockolt lookup) a gép-kártyára kerül."""
from __future__ import annotations

import app.api.saferpay as sp


async def test_ping_commands_and_location(client, admin, monkeypatch):
    _, adm = admin
    m = (await client.post("/api/saferpay/machines", json={"name": "Ping gép", "machine_code": "174241"}, headers=adm)).json()
    auth = {"machine_code": "174241", "key": m["api_key"]}

    async def fake_geo(ip):
        return {"ip": ip, "city": "Szentendre", "country": "HU", "lat": 47.67, "lon": 19.07, "at": "x"}

    monkeypatch.setattr(sp, "_geo_lookup", fake_geo)
    # parancs sorba tétele (rossz parancs 422)
    assert (await client.post(f"/api/saferpay/machines/{m['id']}/commands", json={"commands": ["fly"]}, headers=adm)).status_code == 422
    res = await client.post(f"/api/saferpay/machines/{m['id']}/commands", json={"commands": ["report", "promos", "report"]}, headers=adm)
    assert res.status_code == 200 and res.json()["pending_commands"] == ["report", "promos"]
    mc = next(x for x in (await client.get("/api/saferpay/machines", headers=adm)).json() if x["id"] == m["id"])
    assert mc["pending_commands"] == ["report", "promos"] and mc["online"] is False

    # ping: rossz kulcs 403; jó → parancsok vissza + törölve, online, hely az ügynökből
    assert (await client.post("/api/pay/ping", json={**auth, "key": "x" * 32, "data": {}})).status_code == 403
    res = await client.post("/api/pay/ping", json={**auth, "data": {"agent": "3.5", "uptime_min": 42, "net": "WiFi", "lat": 47.6712, "lon": 19.0714, "loc_src": "network", "loc_acc_m": 1200}}, headers={"X-Forwarded-For": "84.0.0.1, 10.0.0.1"})
    assert res.status_code == 200, res.text
    assert res.json()["commands"] == ["report", "promos"] and res.json()["interval_s"] == 300
    mc = next(x for x in (await client.get("/api/saferpay/machines", headers=adm)).json() if x["id"] == m["id"])
    assert mc["online"] is True and mc["pending_commands"] == [] and mc["ip"] == "84.0.0.1"
    assert mc["geo"]["city"] == "Szentendre" and mc["location"] == {"lat": 47.6712, "lon": 19.0714, "src": "network", "acc_m": 1200}
    assert mc["telemetry"]["net"] == "WiFi" and "ping_at" in mc["telemetry"]
    # a ping NEM ír előzmény-sort
    assert (await client.get(f"/api/saferpay/machines/{m['id']}/telemetry", headers=adm)).json() == []
    # második ping ugyanarról az IP-ről: nincs új geo-lookup (a mock-hívás száma)
    calls = []

    async def counting_geo(ip):
        calls.append(ip)
        return await fake_geo(ip)

    monkeypatch.setattr(sp, "_geo_lookup", counting_geo)
    await client.post("/api/pay/ping", json={**auth, "data": {}}, headers={"X-Forwarded-For": "84.0.0.1"})
    assert calls == []
    await client.post("/api/pay/ping", json={**auth, "data": {}}, headers={"X-Forwarded-For": "84.0.0.2"})
    assert calls == ["84.0.0.2"]
    # privát IP-re nincs lookup; telemetria is visszaadja a parancsokat
    await client.post(f"/api/saferpay/machines/{m['id']}/commands", json={"commands": ["errors"]}, headers=adm)
    res = await client.post("/api/pay/telemetry", json={**auth, "data": {"agent": "3.5"}}, headers={"X-Forwarded-For": "192.168.0.5"})
    assert res.status_code == 200 and res.json()["commands"] == ["errors"] and calls == ["84.0.0.2"]

async def test_boot_greeting_is_kept(client, admin, monkeypatch):
    """166. kör: bekapcsolás utáni első ping (event=boot) → boot_at + köszönés a pillanatképben."""
    from app.api import saferpay as sp

    async def fake_geo(ip):
        return None

    monkeypatch.setattr(sp, "_geo_lookup", fake_geo)
    _, adm = admin
    m = (await client.post("/api/saferpay/machines", json={"machine_code": "boot-1", "name": "Teszt"}, headers=adm)).json()
    auth = {"machine_code": "boot-1", "key": m["api_key"]}
    res = await client.post("/api/pay/ping", json={**auth, "data": {"agent": "3.11", "event": "boot", "greeting": "Jó reggelt, megvagyok!"}})
    assert res.status_code == 200, res.text
    mc = next(x for x in (await client.get("/api/saferpay/machines", headers=adm)).json() if x["id"] == m["id"])
    assert mc["telemetry"]["greeting"] == "Jó reggelt, megvagyok!" and mc["telemetry"]["boot_at"]
    # a sima ping nem írja felül a bekapcsolás idejét
    boot_at = mc["telemetry"]["boot_at"]
    await client.post("/api/pay/ping", json={**auth, "data": {"agent": "3.11"}})
    mc = next(x for x in (await client.get("/api/saferpay/machines", headers=adm)).json() if x["id"] == m["id"])
    assert mc["telemetry"]["boot_at"] == boot_at

async def test_pin_command_and_status(client, admin, monkeypatch):
    """167. kör: szervizkód-parancs (pin:1234 / pin:) — a naplóban maszkolva; a ping hozza a pin_set állapotot."""
    from app.api import saferpay as sp

    async def fake_geo(ip):
        return None

    monkeypatch.setattr(sp, "_geo_lookup", fake_geo)
    _, adm = admin
    m = (await client.post("/api/saferpay/machines", json={"machine_code": "pin-1", "name": "Teszt"}, headers=adm)).json()
    mid = m["id"]
    assert (await client.post(f"/api/saferpay/machines/{mid}/commands", json={"commands": ["pin:12"]}, headers=adm)).status_code == 422
    assert (await client.post(f"/api/saferpay/machines/{mid}/commands", json={"commands": ["pin:abcd"]}, headers=adm)).status_code == 422
    assert (await client.post(f"/api/saferpay/machines/{mid}/commands", json={"commands": ["pin:1234"]}, headers=adm)).status_code == 200
    assert (await client.post(f"/api/saferpay/machines/{mid}/commands", json={"commands": ["pin:5678", "report"]}, headers=adm)).status_code == 200
    mc = next(x for x in (await client.get("/api/saferpay/machines", headers=adm)).json() if x["id"] == mid)
    assert mc["pending_commands"] == ["pin:5678", "report"]  # az előző kód-parancsot felülírja
    audit = (await client.get("/api/audit?action=vending.commands", headers=adm)).json()
    rows = audit if isinstance(audit, list) else audit["items"]
    assert all("5678" not in str(r["detail"]) and "1234" not in str(r["detail"]) for r in rows)
    auth = {"machine_code": "pin-1", "key": m["api_key"]}
    res = await client.post("/api/pay/ping", json={**auth, "data": {"agent": "3.12", "pin_set": True, "pin_fail": 2}})
    assert res.json()["commands"] == ["pin:5678", "report"]
    mc = next(x for x in (await client.get("/api/saferpay/machines", headers=adm)).json() if x["id"] == mid)
    assert mc["telemetry"]["pin_set"] is True and mc["telemetry"]["pin_fail"] == 2 and mc["pending_commands"] == []
    assert (await client.post(f"/api/saferpay/machines/{mid}/commands", json={"commands": ["pin:"]}, headers=adm)).status_code == 200
