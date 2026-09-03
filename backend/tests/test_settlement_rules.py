"""Elszámolás-szabályok: kedvezmény=készpénz+kávé-ÁFA, nem-kávé leltár nélkül,
szerződött kávé kontextus, cikkszám."""

from __future__ import annotations

from tests.test_consignment import make_product


async def test_no_vat_cash_only_and_coffee_scope(client, manager):
    """Kedvezmény: csak készpénz; a kávé ÁFA-mentes, a nem kávé bruttón marad."""
    _, mgr = manager
    partner = (
        await client.post("/api/partners", json={"name": "Kedvezményes Bolt"}, headers=mgr)
    ).json()
    coffee = await make_product(client, mgr, price_per_portion=100.0, grams_per_portion=7)
    cream = await make_product(
        client, mgr, name="Tejszín", price_per_portion=226.0,
        unit="db", is_consignment=False,
    )
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": coffee["id"], "quantity": 1.0},
        headers=mgr,
    )

    # kedvezmény + átutalás → 422
    denied = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "transfer",
              "no_vat": True,
              "lines": [{"product_id": coffee["id"], "physical_qty": 0.3}]},
        headers=mgr,
    )
    assert denied.status_code == 422
    assert denied.json()["detail"]["code"] == "settlement.no_vat_cash_only"

    # kedvezmény + készpénz: kávé ÁFA 0, az átadott tejszín 27%-kal bruttósodik
    res = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash",
              "no_vat": True,
              "lines": [{"product_id": coffee["id"], "physical_qty": 0.3}],
              "handovers": [{"product_id": cream["id"], "quantity": 2}]},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    lines = res.json()["lines"]
    coffee_line = next(x for x in lines if "átadva" not in x["product_name"])
    cream_line = next(x for x in lines if "átadva" in x["product_name"])
    assert coffee_line["vat_percent"] == 0
    assert cream_line["vat_percent"] == 27
    assert abs(cream_line["amount_gross"] - 2 * 226 * 1.27) < 0.5


async def test_handover_only_settlement(client, manager):
    """Csak átadott áruval (leltár és gép nélkül) is rögzíthető elszámolás."""
    _, mgr = manager
    partner = (
        await client.post("/api/partners", json={"name": "Átadós Bolt"}, headers=mgr)
    ).json()
    cream = await make_product(
        client, mgr, name="Zott mini", price_per_portion=226.0,
        unit="db", is_consignment=False,
    )
    res = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash",
              "lines": [],
              "handovers": [{"product_id": cream["id"], "quantity": 3}]},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    assert abs(res.json()["total_net"] - 3 * 226) < 0.01


async def test_open_delivery_items_in_context(client, manager):
    """A settlement-context tételesen listázza az előző elszámolás óta
    szállítólevélen átadott árut."""
    _, mgr = manager
    partner = (
        await client.post("/api/partners", json={"name": "SZL Bolt"}, headers=mgr)
    ).json()
    cream = await make_product(
        client, mgr, name="Habspray", price_per_portion=500.0,
        unit="db", is_consignment=False,
    )
    dn = await client.post(
        "/api/deliveries",
        json={"partner_id": partner["id"],
              "lines": [{"product_id": cream["id"], "quantity": 4, "unit_price": 500.0}]},
        headers=mgr,
    )
    assert dn.status_code == 201, dn.text
    ctx = (
        await client.get(f"/api/partners/{partner['id']}/settlement-context", headers=mgr)
    ).json()
    assert ctx["open_deliveries"] == 1
    items = ctx["open_delivery_items"]
    assert len(items) == 1
    assert items[0]["product_name"] == "Habspray"
    assert items[0]["amount_net"] == 2000.0
    assert items[0]["serial"].startswith("SZL-")


async def test_product_code_roundtrip(client, manager):
    """Cikkszám: menthető és visszajön a termék-API-ból."""
    _, mgr = manager
    p = await make_product(client, mgr, name="Kódos kávé", code="103")
    assert p["code"] == "103"
    listed = (await client.get("/api/products", headers=mgr)).json()
    assert any(x["code"] == "103" for x in listed)


async def test_backfill_codes_from_notes(client, manager):
    """Az importban a notes-ba kerult 'Xpresso kod' 3 jegyu szamok a code
    mezobe emelhetok — a mar kodolt termekek erintetlenek."""
    _, mgr = manager
    p1 = await make_product(client, mgr, name="Jegyzetes kave", notes="Xpresso kód: 105")
    p2 = await make_product(client, mgr, name="Csak szam", notes="342")
    p3 = await make_product(client, mgr, name="Kodos", code="777", notes="Xpresso kód: 999")
    p4 = await make_product(client, mgr, name="Semmi", notes="valami szoveg")

    res = await client.post("/api/products/backfill-codes", headers=mgr)
    assert res.status_code == 200, res.text
    assert res.json()["updated"] == 2

    listed = {x["name"]: x["code"] for x in (await client.get("/api/products", headers=mgr)).json()}
    assert listed["Jegyzetes kave"] == "105"
    assert listed["Csak szam"] == "342"
    assert listed["Kodos"] == "777"  # meglevo kod nem valtozik
    assert listed["Semmi"] is None
