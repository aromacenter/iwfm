"""Digitális szállítólevél: kiszállítás elszámolás nélkül → a következő
elszámolás betölti és kiszámlázza."""

from __future__ import annotations


async def _product(client, mgr, name="Szállított Cukor", unit="db", price=0.0):
    res = await client.post(
        "/api/products",
        json={"name": name, "unit": unit, "grams_per_portion": 1,
              "price_per_portion": price, "category": "Kellékek"},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _partner(client, mgr, name="Kiszállításos Bolt"):
    res = await client.post("/api/partners", json={"name": name}, headers=mgr)
    return res.json()


async def test_delivery_note_lifecycle(client, manager):
    """SZL létrehozás forrás-raktárral → készlet-levonás → elszámolás betölti
    → settled + számlázott sor SZL-hivatkozással."""
    _, mgr = manager
    partner = await _partner(client, mgr)
    product = await _product(client, mgr)

    # forrás-raktár készlettel
    wh = (
        await client.post("/api/warehouses", json={"name": "Autó SZL", "kind": "van"}, headers=mgr)
    ).json()
    await client.post(
        f"/api/warehouses/{wh['id']}/receive",
        json={"product_id": product["id"], "quantity": 30}, headers=mgr,
    )

    res = await client.post(
        "/api/deliveries",
        json={"partner_id": partner["id"], "source_warehouse_id": wh["id"],
              "lines": [{"product_id": product["id"], "quantity": 10, "unit_price": 250}]},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    dn = res.json()
    assert dn["serial"].startswith("SZL-") and dn["status"] == "open"
    assert dn["total_net"] == 2500

    # a raktárból levonódott
    res = await client.get(f"/api/warehouses/{wh['id']}/stock", headers=mgr)
    assert res.json()[0]["quantity"] == 20

    # elszámolás-kontextus mutatja a nyitott SZL-t
    res = await client.get(f"/api/partners/{partner['id']}/settlement-context", headers=mgr)
    assert res.json()["open_deliveries"] == 1
    assert res.json()["open_deliveries_net"] == 2500

    # elszámolás leltár/gép nélkül is mehet — csak az SZL-t számlázza
    res = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash", "lines": []},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    detail = (
        await client.get(f"/api/settlements?partner_id={partner['id']}", headers=mgr)
    ).json()[0]
    assert abs(detail["total_net"] - 2500) < 1
    assert abs(detail["total_gross"] - 3175) < 1  # 27% ÁFA

    # az SZL settled lett, és a sor hivatkozik rá
    res = await client.get(f"/api/deliveries?partner_id={partner['id']}", headers=mgr)
    assert res.json()[0]["status"] == "settled"
    full = (await client.get(f"/api/settlements/{detail['id']}", headers=mgr)).json()
    assert any("SZL-" in ln["product_name"] for ln in full["lines"])

    # üres elszámolás már nem megy (nincs több nyitott SZL)
    res = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash", "lines": []},
        headers=mgr,
    )
    assert res.status_code == 422


async def test_delivery_cancel_restores_stock(client, manager):
    _, mgr = manager
    partner = await _partner(client, mgr, "Visszavonós Bolt")
    product = await _product(client, mgr, "Visszavont Pohár")
    wh = (
        await client.post("/api/warehouses", json={"name": "Autó V", "kind": "van"}, headers=mgr)
    ).json()
    await client.post(
        f"/api/warehouses/{wh['id']}/receive",
        json={"product_id": product["id"], "quantity": 8}, headers=mgr,
    )
    dn = (
        await client.post(
            "/api/deliveries",
            json={"partner_id": partner["id"], "source_warehouse_id": wh["id"],
                  "lines": [{"product_id": product["id"], "quantity": 5, "unit_price": 100}]},
            headers=mgr,
        )
    ).json()
    res = await client.post(f"/api/deliveries/{dn['id']}/cancel", json={}, headers=mgr)
    assert res.status_code == 200 and res.json()["status"] == "cancelled"
    res = await client.get(f"/api/warehouses/{wh['id']}/stock", headers=mgr)
    assert res.json()[0]["quantity"] == 8  # visszakerült
    # visszavont SZL nem számlázódik
    res = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash", "lines": []},
        headers=mgr,
    )
    assert res.status_code == 422