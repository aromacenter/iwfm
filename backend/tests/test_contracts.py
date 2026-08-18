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


async def test_contracts_overview_page(client, manager):
    """/api/contracts: minden szerződés + gépek feloldott adagárral + szerződés
    nélküli gépes partnerek."""
    from tests.test_consignment import make_product
    from tests.test_inventory import asset_payload

    _, mgr = manager
    product = await make_product(client, mgr, price_per_portion=50.0)

    # Szerződéses partner géppel + partner-áras felülírással
    p1 = await _partner(client, mgr, "Áttekintős Bolt")
    res = await client.post(
        f"/api/partners/{p1['id']}/contracts",
        json={"valid_from": "2020-01-01", "min_portions": 300, "below_min_price": 35},
        headers=mgr,
    )
    assert res.status_code == 201
    asset = (
        await client.post(
            "/api/assets",
            json=asset_payload(barcode="OVW-1", name="Áttekintő gép",
                               default_product_id=product["id"]),
            headers=mgr,
        )
    ).json()
    await client.post(
        f"/api/assets/{asset['id']}/deploy", json={"partner_id": p1["id"]}, headers=mgr
    )
    res = await client.put(
        f"/api/partners/{p1['id']}/prices/{product['id']}",
        json={"price_per_portion": 42.0},
        headers=mgr,
    )
    assert res.status_code == 200, res.text

    # Gépes partner szerződés NÉLKÜL
    p2 = await _partner(client, mgr, "Szerződéstelen Bolt")
    orphan = (
        await client.post(
            "/api/assets", json=asset_payload(barcode="OVW-2", name="Kóbor gép"),
            headers=mgr,
        )
    ).json()
    await client.post(
        f"/api/assets/{orphan['id']}/deploy", json={"partner_id": p2["id"]}, headers=mgr
    )

    res = await client.get("/api/contracts", headers=mgr)
    assert res.status_code == 200, res.text
    data = res.json()

    row = next(c for c in data["contracts"] if c["partner_name"] == "Áttekintős Bolt")
    assert row["status"] == "active"
    assert row["min_portions"] == 300
    machines = row["machines"]
    assert len(machines) == 1
    assert machines[0]["barcode"] == "OVW-1"
    assert machines[0]["product_name"] == product["name"]
    # a partner-ár felülírás érvényesül, nem a termék alapára
    assert machines[0]["price_per_portion"] == 42.0

    orphan_row = next(
        r for r in data["no_contract"] if r["partner_name"] == "Szerződéstelen Bolt"
    )
    assert orphan_row["machines"][0]["barcode"] == "OVW-2"
    # a szerződéses partner nem szerepel a szerződés nélküliek közt
    assert all(r["partner_name"] != "Áttekintős Bolt" for r in data["no_contract"])