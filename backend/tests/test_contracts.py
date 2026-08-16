"""Partner-szerződések: külön entitás érvényességgel + tükör-szinkron."""

from __future__ import annotations

from datetime import date, timedelta


async def _partner(client, mgr, name="Szerződéses Partner"):
    res = await client.post("/api/partners", json={"name": name}, headers=mgr)
    assert res.status_code in (200, 201), res.text
    return res.json()


async def _partner_row(client, mgr, partner_id):
    rows = (await client.get("/api/partners", headers=mgr)).json()
    return next(p for p in rows if p["id"] == partner_id)


async def test_contract_crud_and_status(client, manager):
    _, mgr = manager
    partner = await _partner(client, mgr)

    # aktív szerződés
    res = await client.post(
        f"/api/partners/{partner['id']}/contracts",
        json={"valid_from": "2020-01-01", "min_portions": 500, "below_min_price": 40},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    active = res.json()
    assert active["status"] == "active"

    # jövőbeli szerződés
    future_start = (date.today() + timedelta(days=30)).isoformat()
    res = await client.post(
        f"/api/partners/{partner['id']}/contracts",
        json={"valid_from": future_start, "min_portions": 800, "below_min_price": 45},
        headers=mgr,
    )
    assert res.status_code == 201
    assert res.json()["status"] == "future"

    # hibás dátum-sorrend
    res = await client.post(
        f"/api/partners/{partner['id']}/contracts",
        json={"valid_from": "2026-05-01", "valid_to": "2026-04-01"},
        headers=mgr,
    )
    assert res.status_code == 422

    # lista
    res = await client.get(f"/api/partners/{partner['id']}/contracts", headers=mgr)
    assert res.status_code == 200
    assert len(res.json()) == 2

    # a tükör az AKTÍV szerződést mutatja
    row = await _partner_row(client, mgr, partner["id"])
    assert row["contract_min_portions"] == 500

    # aktív szerződés lejáratása → a tükör kiürül a következő szinkronkor
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    res = await client.patch(
        f"/api/partners/{partner['id']}/contracts/{active['id']}",
        json={"valid_from": "2020-01-01", "valid_to": yesterday,
              "min_portions": 500, "below_min_price": 40},
        headers=mgr,
    )
    assert res.status_code == 200
    assert res.json()["status"] == "expired"
    row = await _partner_row(client, mgr, partner["id"])
    assert row["contract_min_portions"] is None


async def test_contract_drives_settlement_minimum(client, manager):
    """Az elszámolás a MA érvényes szerződés minimumával számol."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(client, mgr, "Minimumos Bolt")
    res = await client.post(
        f"/api/partners/{partner['id']}/contracts",
        json={"valid_from": "2020-01-01", "min_portions": 200, "below_min_price": 30},
        headers=mgr,
    )
    assert res.status_code == 201

    product = await make_product(client, mgr)
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
    assert res.status_code == 201, res.text
    detail = (
        await client.get(f"/api/settlements?partner_id={partner['id']}", headers=mgr)
    ).json()
    # 100 adag fogyás, minimum 200 → 100 különbözet × 30 = 3 000 + kávé 5 000
    assert abs(detail[0]["total_net"] - 8000) < 1, detail[0]


async def test_partner_update_keeps_contract_mirror(client, manager):
    """A partner-adatlap mentése nem nyúl a szerződés-tükörhöz."""
    _, mgr = manager
    partner = await _partner(client, mgr, "Tükrös Bolt")
    await client.post(
        f"/api/partners/{partner['id']}/contracts",
        json={"valid_from": "2020-01-01", "min_kg": 12, "below_min_price_kg": 4500},
        headers=mgr,
    )
    res = await client.patch(
        f"/api/partners/{partner['id']}",
        json={"name": "Tükrös Bolt (átnevezve)"},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["contract_min_kg"] == 12

    # törlés → tükör kiürül
    contracts = (
        await client.get(f"/api/partners/{partner['id']}/contracts", headers=mgr)
    ).json()
    res = await client.delete(
        f"/api/partners/{partner['id']}/contracts/{contracts[0]['id']}", headers=mgr
    )
    assert res.status_code == 200
    row = await _partner_row(client, mgr, partner["id"])
    assert row["contract_min_kg"] is None