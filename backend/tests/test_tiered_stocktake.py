"""Sávos adagárazás (tiered modul) és leltár modul (stocktake) tesztjei."""

from __future__ import annotations

from tests.test_machine_settlement import _machine, _partner


async def test_tiered_price_applied(client, manager):
    """A szerződés sávjai szerint árazódik a gép adagszáma."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(client, mgr, "Sávos Bolt")
    product = await make_product(client, mgr, price_per_portion=50.0, grams_per_portion=7)
    asset = await _machine(client, mgr, partner, "SAV-1", counter=1000,
                           default_product_id=product["id"])
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 10.0},
        headers=mgr,
    )
    res = await client.post(
        f"/api/partners/{partner['id']}/contracts",
        json={
            "valid_from": "2026-01-01",
            "price_tiers": [
                {"qty_from": 0, "qty_to": 100, "price": 60},
                {"qty_from": 101, "qty_to": None, "price": 40},
            ],
        },
        headers=mgr,
    )
    assert res.status_code in (200, 201), res.text
    assert res.json()["price_tiers"][1]["price"] == 40

    # 1000 → 1123: 123 lefőzött, 10 szerviz → 113 adag → 2. sáv (40 Ft)
    res = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "cash",
            "lines": [{"product_id": product["id"], "physical_qty": 9.1}],
            "machines": [{"asset_id": asset["id"], "new_counter": 1123,
                          "service_portions": 10}],
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    m = res.json()["machines"][0]
    assert m["portions_billed"] == 113
    assert m["price_per_portion"] == 40
    assert m["amount_net"] == 113 * 40


async def test_tiered_validation(client, manager):
    """Átfedő vagy hibás sáv 422-t ad."""
    _, mgr = manager
    partner = await _partner(client, mgr, "Sávos Hibás Bolt")
    res = await client.post(
        f"/api/partners/{partner['id']}/contracts",
        json={
            "valid_from": "2026-01-01",
            "price_tiers": [
                {"qty_from": 0, "qty_to": 100, "price": 60},
                {"qty_from": 50, "qty_to": None, "price": 40},  # átfed
            ],
        },
        headers=mgr,
    )
    assert res.status_code == 422, res.text


async def test_stocktake_flow(client, manager):
    """Leltár: nyitás pillanatképpel, számolás, zárás adjust-mozgással."""
    from tests.test_consignment import make_product

    _, mgr = manager
    product = await make_product(client, mgr, price_per_portion=50.0)
    wh = (
        await client.post("/api/warehouses", json={"name": "Leltár Telephely"}, headers=mgr)
    ).json()
    # termék a raktárba + 8 kg bevét
    res = await client.post(
        f"/api/warehouses/{wh['id']}/stock/add",
        json={"product_id": product["id"]},
        headers=mgr,
    )
    assert res.status_code in (200, 201), res.text
    res = await client.post(
        f"/api/warehouses/{wh['id']}/adjust",
        json={"product_id": product["id"], "counted_qty": 8.0},
        headers=mgr,
    )
    assert res.status_code == 200, res.text

    # leltár nyitása — pillanatkép a 8 kg-ról
    res = await client.post(
        "/api/stocktakes", json={"warehouse_id": wh["id"], "note": "Évzáró"}, headers=mgr
    )
    assert res.status_code == 201, res.text
    st = res.json()
    assert st["status"] == "open"
    assert st["line_count"] == 1
    line = st["lines"][0]
    assert line["system_qty"] == 8.0

    # második nyitás ugyanarra a raktárra: 422
    res = await client.post(
        "/api/stocktakes", json={"warehouse_id": wh["id"]}, headers=mgr
    )
    assert res.status_code == 422

    # számolt érték mentése: 6.5 kg (1.5 kg hiány)
    res = await client.put(
        f"/api/stocktakes/{st['id']}/lines",
        json={"lines": [{"id": line["id"], "counted_qty": 6.5}]},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["counted_count"] == 1

    # zárás → a készlet 6.5-re áll, adjust-mozgás keletkezik
    res = await client.post(f"/api/stocktakes/{st['id']}/close", headers=mgr)
    assert res.status_code == 200, res.text
    closed = res.json()
    assert closed["status"] == "closed"
    assert closed["diff_count"] == 1

    stock = (
        await client.get(f"/api/warehouses/{wh['id']}/stock", headers=mgr)
    ).json()
    assert stock[0]["quantity"] == 6.5
    movs = (
        await client.get(f"/api/warehouses/{wh['id']}/movements", headers=mgr)
    ).json()
    assert any(m["action"] == "adjust" and "Leltár" in (m["note"] or "") for m in movs)

    # zárt leltárra nem lehet menteni
    res = await client.put(
        f"/api/stocktakes/{st['id']}/lines",
        json={"lines": [{"id": line["id"], "counted_qty": 7.0}]},
        headers=mgr,
    )
    assert res.status_code == 422
