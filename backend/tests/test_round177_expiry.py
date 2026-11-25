"""177. kör: tételek lejárattal — bevételezés (termék-eltarthatóság vagy
megadott dátum), FIFO-fogyasztás kiadáskor, lejáró-lista, készlet-sor
legkorábbi lejárata, napi jelzés egyszer."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from tests.test_round161_locations import _located_site
from tests.test_warehouse import _make_product


async def test_lots_fifo_and_expiring(client, manager):
    _, mgr = manager
    site = await _located_site(client, mgr, name="Lejárat raktár")
    res = await client.post(f"/api/warehouses/{site['id']}/locations/generate", json={"row_code": "A", "bays": 2, "levels": 1}, headers=mgr)
    locs = {x["code"]: x for x in res.json()["locations"]}
    wh = site["id"]
    tej = await _make_product(client, mgr, "Tej")
    # eltarthatóság 10 nap → bevételezéskor alapértelmezett lejárat
    assert (await client.patch(f"/api/products/{tej['id']}", json={"name": "Tej", "shelf_life_days": 10}, headers=mgr)).status_code == 200
    today = datetime.now(UTC).date()
    # 1. tétel: régi, 3 nap múlva lejár, A-01-1 (megadott dátum)
    r = await client.post(f"/api/warehouses/{wh}/receive", json={"product_id": tej["id"], "quantity": 6, "location_id": locs["A-01-1"]["id"], "expires_at": (today + timedelta(days=3)).isoformat()}, headers=mgr)
    assert r.status_code == 200
    # 2. tétel: eltarthatóságból (10 nap), A-02-1
    r = await client.post(f"/api/warehouses/{wh}/receive", json={"product_id": tej["id"], "quantity": 4, "location_id": locs["A-02-1"]["id"]}, headers=mgr)
    assert r.status_code == 200
    row = next(s for s in (await client.get(f"/api/warehouses/{wh}/stock", headers=mgr)).json() if s["product_name"] == "Tej")
    assert row["next_expiry"] == (today + timedelta(days=3)).isoformat() and row["next_expiry_location"] == "A-01-1"
    lots = (await client.get(f"/api/warehouses/{wh}/lots?product_id={tej['id']}", headers=mgr)).json()
    assert [(x["quantity"], x["expires_at"], x["location_code"], x["status"]) for x in lots] == [
        (6, (today + timedelta(days=3)).isoformat(), "A-01-1", "soon"),
        (4, (today + timedelta(days=10)).isoformat(), "A-02-1", "ok"),
    ]
    # kiadás (leltár-korrekció 10 → 3): FIFO — a korábbi lejáratú 6-ból megy el 7 → az első tétel elfogy, a másodikból 1
    assert (await client.post(f"/api/warehouses/{wh}/adjust", json={"product_id": tej["id"], "counted_qty": 3}, headers=mgr)).status_code == 200
    lots = (await client.get(f"/api/warehouses/{wh}/lots?product_id={tej['id']}", headers=mgr)).json()
    assert [(x["quantity"], x["location_code"]) for x in lots] == [(3, "A-02-1")]
    stock = (await client.get(f"/api/warehouses/{wh}/stock", headers=mgr)).json()
    row = next(s for s in stock if s["product_name"] == "Tej")
    assert row["next_expiry"] == (today + timedelta(days=10)).isoformat() and row["next_expiry_location"] == "A-02-1"
    # lejáró-lista: 30 napon belül igen, 5 napon belül nem
    exp = (await client.get("/api/warehouses/expiring?days=30", headers=mgr)).json()
    assert [(e["product_name"], e["quantity"], e["days_left"], e["warehouse_name"]) for e in exp if e["warehouse_id"] == wh] == [("Tej", 3, 10, "Lejárat raktár")]
    assert [e for e in (await client.get("/api/warehouses/expiring?days=5", headers=mgr)).json() if e["warehouse_id"] == wh] == []
    # lejárat javítása: tegnapra → lejárt
    r = await client.patch(f"/api/warehouses/{wh}/lots/{lots[0]['id']}", json={"expires_at": (today - timedelta(days=1)).isoformat()}, headers=mgr)
    assert r.status_code == 200 and r.json()["status"] == "expired" and r.json()["days_left"] == -1
    exp = [e for e in (await client.get("/api/warehouses/expiring?days=0", headers=mgr)).json() if e["warehouse_id"] == wh]
    assert len(exp) == 1 and exp[0]["status"] == "expired"
    # lejárat nélküli termék: tétel van (FIFO-hoz), de a lejáró-listában nincs
    cukor = await _make_product(client, mgr, "Cukor")
    assert (await client.post(f"/api/warehouses/{wh}/receive", json={"product_id": cukor["id"], "quantity": 2}, headers=mgr)).status_code == 200
    lots = (await client.get(f"/api/warehouses/{wh}/lots?product_id={cukor['id']}", headers=mgr)).json()
    assert len(lots) == 1 and lots[0]["expires_at"] is None and lots[0]["status"] == "ok"


async def test_expiry_notice_once_a_day_and_digest(client, manager, monkeypatch):
    import app.db as app_db
    from app.services.wfm import notifier

    _, mgr = manager
    site = await _located_site(client, mgr, name="Jelző raktár")
    wh = site["id"]
    kave = await _make_product(client, mgr, "Kávé lejáró")
    today = datetime.now(UTC).date()
    assert (await client.post(f"/api/warehouses/{wh}/receive", json={"product_id": kave["id"], "quantity": 5, "expires_at": (today + timedelta(days=2)).isoformat()}, headers=mgr)).status_code == 200
    fired: list = []
    monkeypatch.setattr("app.services.wfm.automation.fire_event", lambda ev, ctx: fired.append((ev, ctx)))
    async with app_db.get_session_factory()() as db:
        n = await notifier.notify_expiring_lots(db, today, force=True)
        assert n >= 1
        assert any(ev == "stock.expiring" and ctx["termek_nev"] == "Kávé lejáró" and ctx["napok"] == 2 for ev, ctx in fired)
        # ugyanazon a napon másodszor nem megy
        assert await notifier.notify_expiring_lots(db, today) == 0
        digest = await notifier.build_daily_digest(db)
    assert "LEJÁRÓ RAKTÁRI TÉTELEK" in digest and "Kávé lejáró" in digest


def _unused(_: date) -> None:  # a date import a típusjelölésekhez
    return None
