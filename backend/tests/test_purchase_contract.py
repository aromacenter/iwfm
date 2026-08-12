"""Bevételezési beszerzési ár + partner-szintű szerződési feltételek
(adag/kg minimum, türelmi időszak, feltételes bérleti díj)."""

from __future__ import annotations

from datetime import date, timedelta


async def _partner(client, mgr, name, **contract):
    res = await client.post("/api/partners", json={"name": name, **contract}, headers=mgr)
    assert res.status_code in (200, 201), res.text
    return res.json()


async def _settle(client, mgr, partner, product, physical=0.3):
    """1 kg feltöltés → physical maradék → 0.7 kg fogyás (100 adag × 50 Ft)."""
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 1.0},
        headers=mgr,
    )
    res = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash",
              "lines": [{"product_id": product["id"], "physical_qty": physical}]},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    detail = (await client.get(f"/api/settlements?partner_id={partner['id']}", headers=mgr)).json()
    return detail[0]


async def test_replenish_records_unit_cost(client, manager):
    """Bevételezéskor megadott beszerzési ár a mozgásra kerül, és frissíti a
    termék utolsó beszerzési árát."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(client, mgr, "Beszerzéses Bolt")
    product = await make_product(client, mgr)

    res = await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 5.0, "unit_cost": 4200},
        headers=mgr,
    )
    assert res.status_code == 200, res.text

    prods = (await client.get("/api/products", headers=mgr)).json()
    me = next(p for p in prods if p["id"] == product["id"])
    assert me["purchase_price"] == 4200

    import app.db as app_db
    from sqlalchemy import select

    from app.models import StockMovement

    async with app_db.get_session_factory()() as session:
        moves = (
            (await session.execute(select(StockMovement).where(StockMovement.action == "replenish")))
            .scalars()
            .all()
        )
    assert any(m.unit_cost == 4200 for m in moves)


async def test_product_purchase_price_crud(client, manager):
    _, mgr = manager
    res = await client.post(
        "/api/products",
        json={"name": "Árréses Kávé", "grams_per_portion": 7,
              "price_per_portion": 60, "purchase_price": 5000},
        headers=mgr,
    )
    assert res.status_code in (200, 201), res.text
    assert res.json()["purchase_price"] == 5000


async def test_partner_kg_minimum(client, manager):
    """Kg-alapú partner-minimum: 10 kg/hó, 5000 Ft/kg — 0.7 kg fogyás mellett
    9.3 kg különbözet (első elszámolás = 30 nap)."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(
        client, mgr, "Kg-minimumos Bolt",
        contract_min_kg=10, contract_below_min_price_kg=5000,
    )
    product = await make_product(client, mgr)
    detail = await _settle(client, mgr, partner, product)
    # kávé 100 adag × 50 = 5 000 + különbözet 9.3 kg × 5 000 = 46 500 → 51 500
    assert abs(detail["total_net"] - 51500) < 1, detail


async def test_partner_portion_minimum_overrides_machine(client, manager):
    """A partneren beállított adag-minimum felülírja a gépit."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(
        client, mgr, "Felülírós Bolt",
        contract_min_portions=200, contract_below_min_price=30,
    )
    asset = (
        await client.post(
            "/api/assets",
            json={"barcode": "OVR-1", "name": "Jura X8",
                  "contract_min_portions": 600, "contract_below_min_price": 40},
            headers=mgr,
        )
    ).json()
    await client.post(
        f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr
    )
    product = await make_product(client, mgr)
    detail = await _settle(client, mgr, partner, product)
    # 100 adag fogyás; minimum 200 (NEM 600) → 100 adag × 30 = 3 000 + kávé 5 000
    assert abs(detail["total_net"] - 8000) < 1, detail


async def test_grace_period_skips_minimum(client, manager):
    """Türelmi időszakban (pl. első 3 hónap) nincs minimum-különbözet."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(
        client, mgr, "Türelmi Bolt",
        contract_min_kg=10, contract_below_min_price_kg=5000,
        contract_no_min_until=(date.today() + timedelta(days=90)).isoformat(),
    )
    product = await make_product(client, mgr)
    detail = await _settle(client, mgr, partner, product)
    assert abs(detail["total_net"] - 5000) < 1, detail


async def test_conditional_rent_below_minimum(client, manager):
    """Feltételes bérleti díj: minimum alatt a gép bérleti díja jár,
    különbözet nincs."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(
        client, mgr, "Feltételes Bérletis",
        contract_min_portions=600, contract_below_min_price=40,
        contract_rent_if_below_min=True,
    )
    asset = (
        await client.post(
            "/api/assets",
            json={"barcode": "COND-1", "name": "Saeco Royal", "rent_fee": 6000},
            headers=mgr,
        )
    ).json()
    await client.post(
        f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr
    )
    product = await make_product(client, mgr)
    detail = await _settle(client, mgr, partner, product)
    # 100 adag < 600 minimum → bérleti díj 6 000 (30 nap), különbözet NINCS
    assert abs(detail["total_net"] - 11000) < 1, detail


async def test_no_vat_settlement(client, manager):
    """ÁFA/számla nélküli elszámolás: bruttó = nettó, Billingó-számla tiltva."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(client, mgr, "Áfamentes Bolt")
    product = await make_product(client, mgr)
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 1.0},
        headers=mgr,
    )
    res = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash", "no_vat": True,
              "lines": [{"product_id": product["id"], "physical_qty": 0.3}]},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["no_vat"] is True
    assert abs(body["total_net"] - 5000) < 1
    assert body["total_gross"] == body["total_net"]  # nincs ÁFA

    inv = await client.post(f"/api/settlements/{body['id']}/invoice", headers=mgr)
    assert inv.status_code == 409
    assert inv.json()["detail"]["code"] == "settlement.no_vat_no_invoice"


async def test_counter_cross_check(client, manager):
    """Számláló-alapú számlázás + kg-keresztellenőrzés: a számláló szerint
    vártnál kisebb fizikai készlet hiánya kg-áron kerül felszámításra."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(client, mgr, "Számlálós Bolt")
    product = await make_product(client, mgr)  # 7 g/adag, 50 Ft/adag
    # beszerzési ár a kg-hiány árazásához
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 1.0, "unit_cost": 4000},
        headers=mgr,
    )
    # számláló: 50 adag (= 0.35 kg) → várt maradék 0.65 kg; mért 0.3 kg
    # → hiány 0.35 kg × 4000 Ft/kg = 1400; kávé 50 × 50 = 2500 → 3900 nettó
    res = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash",
              "lines": [{"product_id": product["id"], "physical_qty": 0.3,
                         "counter_portions": 50}]},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert abs(body["total_net"] - 3900) < 1, body
    names = [ln["product_name"] for ln in body["lines"]]
    assert any("Készlethiány" in n for n in names), names


async def test_partner_stock_minimum(client, manager):
    """Partnerenkénti minimum készlet: a riasztás a partner-küszöb alapján megy."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(client, mgr, "Küszöbös Bolt")
    product = await make_product(client, mgr)  # nincs globális küszöb
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 3.0},
        headers=mgr,
    )
    # globális küszöb nélkül nincs riasztás
    low = (await client.get("/api/products/low-stock", headers=mgr)).json()
    assert not any(r["partner_id"] == partner["id"] for r in low)

    # partner-küszöb 5 kg → 3 kg készlet riaszt
    res = await client.patch(
        f"/api/partners/{partner['id']}/stock/min",
        json={"product_id": product["id"], "min_quantity": 5},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["min_quantity"] == 5
    low = (await client.get("/api/products/low-stock", headers=mgr)).json()
    mine = [r for r in low if r["partner_id"] == partner["id"]]
    assert mine and mine[0]["threshold"] == 5

    # visszaállítás None-ra → riasztás megszűnik
    await client.patch(
        f"/api/partners/{partner['id']}/stock/min",
        json={"product_id": product["id"], "min_quantity": None},
        headers=mgr,
    )
    low = (await client.get("/api/products/low-stock", headers=mgr)).json()
    assert not any(r["partner_id"] == partner["id"] for r in low)


async def test_conditional_rent_above_minimum(client, manager):
    """Feltételes bérleti díj: minimum FELETT se bérleti díj, se különbözet."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(
        client, mgr, "Minimum Feletti",
        contract_min_portions=50, contract_below_min_price=40,
        contract_rent_if_below_min=True,
    )
    asset = (
        await client.post(
            "/api/assets",
            json={"barcode": "COND-2", "name": "Saeco Royal", "rent_fee": 6000},
            headers=mgr,
        )
    ).json()
    await client.post(
        f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr
    )
    product = await make_product(client, mgr)
    detail = await _settle(client, mgr, partner, product)
    # 100 adag > 50 minimum → csak a kávé: 5 000
    assert abs(detail["total_net"] - 5000) < 1, detail
