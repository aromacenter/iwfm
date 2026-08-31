"""Gép-soros elszámolás (Xpresso-stílus): számláló-különbség, szerviz-adag,
kedvezmény, gép→termék hozzárendelés, tartozás-görgetés, átadott áru."""

from __future__ import annotations


async def _partner(client, mgr, name):
    res = await client.post("/api/partners", json={"name": name}, headers=mgr)
    assert res.status_code in (200, 201), res.text
    return res.json()


async def _machine(client, mgr, partner, barcode, counter=0, **kw):
    res = await client.post(
        "/api/assets",
        json={"barcode": barcode, "name": f"Gép {barcode}", "counter": counter, **kw},
        headers=mgr,
    )
    assert res.status_code in (200, 201), res.text
    asset = res.json()
    dep = await client.post(
        f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr
    )
    assert dep.status_code == 200, dep.text
    return asset


async def test_settlement_context(client, manager):
    """Kontextus: kihelyezett gépek előző számlálóval, tartozás, szerződések."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(client, mgr, "Kontextus Bolt")
    product = await make_product(client, mgr)
    await _machine(client, mgr, partner, "CTX-1", counter=1000,
                   default_product_id=product["id"], rent_fee=2000)
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 5.0},
        headers=mgr,
    )

    ctx = (
        await client.get(f"/api/partners/{partner['id']}/settlement-context", headers=mgr)
    ).json()
    assert len(ctx["machines"]) == 1
    m = ctx["machines"][0]
    assert m["barcode"] == "CTX-1"
    assert m["prev_counter"] == 1000  # még nem volt elszámolás → a gép állása
    assert m["default_product_id"] == product["id"]
    assert m["product_name"] == product["name"]
    assert ctx["debt"] == 0
    assert ctx["active_contracts"] == 1  # rent_fee-s gép
    assert ctx["single_product_id"] == product["id"]


async def test_machine_settlement_flow(client, manager):
    """Számláló-különbség − szerviz-adag, kedvezmény, gép-számláló frissítés."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(client, mgr, "Gépes Bolt")
    product = await make_product(client, mgr, price_per_portion=50.0, grams_per_portion=7)
    asset = await _machine(client, mgr, partner, "GEP-1", counter=1000,
                           default_product_id=product["id"])
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 10.0},
        headers=mgr,
    )

    # 1000 → 1123: 123 lefőzött, 10 szerviz → 113 számlázott adag × 50 Ft
    # kg-ellenőrzés: 123 × 7 g = 0.861 kg várt fogyás → leltár 9.1 kg (nincs hiány)
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
    s = res.json()
    assert len(s["machines"]) == 1
    m = s["machines"][0]
    assert m["prev_counter"] == 1000
    assert m["new_counter"] == 1123
    assert m["portions_billed"] == 113
    assert abs(m["amount_net"] - 113 * 50) < 0.01
    assert abs(s["total_net"] - 113 * 50) < 0.01
    line = s["lines"][0]
    assert line["portions"] == 113

    # a gép számlálója frissült
    detail = (await client.get(f"/api/assets/{asset['id']}", headers=mgr)).json()
    assert detail["counter"] == 1123

    # a következő elszámolás előző számlálója az új állás
    ctx = (
        await client.get(f"/api/partners/{partner['id']}/settlement-context", headers=mgr)
    ).json()
    assert ctx["machines"][0]["prev_counter"] == 1123

    # visszafelé tekert számláló → 422
    bad = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "cash",
            "machines": [{"asset_id": asset["id"], "new_counter": 1100}],
        },
        headers=mgr,
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "settlement.counter_below_prev"


async def test_machine_discount_and_fallback_product(client, manager):
    """Kedvezmény % a gép-soron; termék-hozzárendelés nélkül az egyetlen
    készlet-termék az alap; több terméknél kötelező a beállítás."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(client, mgr, "Kedvezményes Bolt")
    product = await make_product(client, mgr, price_per_portion=100.0)
    asset = await _machine(client, mgr, partner, "KEDV-1", counter=0)  # nincs default_product
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 5.0},
        headers=mgr,
    )

    # 100 adag, 20% kedvezmény → 100 × 100 × 0.8 = 8 000 Ft (fallback: egyetlen termék)
    res = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "transfer",
            "machines": [{"asset_id": asset["id"], "new_counter": 100,
                          "discount_pct": 20}],
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    s = res.json()
    assert abs(s["machines"][0]["amount_net"] - 8000) < 0.01
    # leltár-sor nélkül is készül termék-tétel a gép adagjaiból
    assert any(ln["portions"] == 100 for ln in s["lines"])

    # a fallback-elszámolás a gépre menti a terméket (megjegyzés)
    detail = (await client.get(f"/api/assets/{asset['id']}", headers=mgr)).json()
    assert detail["default_product_id"] == product["id"]

    # második termék + ÚJ (termék nélküli) gép → a fallback megszűnik: 422
    product2 = await make_product(client, mgr, name="Másik kávé")
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product2["id"], "quantity": 2.0},
        headers=mgr,
    )
    asset2 = await _machine(client, mgr, partner, "KEDV-2", counter=0)
    bad = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "cash",
            "machines": [{"asset_id": asset2["id"], "new_counter": 150}],
        },
        headers=mgr,
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "settlement.machine_no_product"

    # elszámoláskor választott termék → sikeres, és a gép megjegyzi
    ok = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "cash",
            "machines": [{"asset_id": asset2["id"], "new_counter": 150,
                          "product_id": product2["id"]}],
        },
        headers=mgr,
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["machines"][0]["product_name"] == "Másik kávé"
    detail2 = (await client.get(f"/api/assets/{asset2['id']}", headers=mgr)).json()
    assert detail2["default_product_id"] == product2["id"]


async def test_debt_carryover_and_handover(client, manager):
    """Fizetve mező → tartozás görgetés; átadott áru növeli a készletet."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(client, mgr, "Tartozós Bolt")
    product = await make_product(client, mgr, price_per_portion=50.0, vat_percent=27)
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 1.0},
        headers=mgr,
    )

    # 1. elszámolás: 0.7 kg fogyás = 100 adag × 50 = 5 000 nettó / 6 350 bruttó,
    # fizetve csak 4 000 → tartozás 2 350; átadott áru: +3 kg
    res = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "cash",
            "lines": [{"product_id": product["id"], "physical_qty": 0.3}],
            "handovers": [{"product_id": product["id"], "quantity": 3.0,
                           "unit_cost": 4000}],
            "paid_amount": 4000,
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    s1 = res.json()
    assert abs(s1["total_gross"] - 6350) < 1
    assert s1["debt_before"] == 0
    assert s1["paid_amount"] == 4000

    # készlet: 0.3 (leltár) + 3.0 (átadott) = 3.3 kg
    stock = (await client.get(f"/api/partners/{partner['id']}/stock", headers=mgr)).json()
    me = next(x for x in stock if x["product_id"] == product["id"])
    assert abs(me["quantity"] - 3.3) < 0.001

    # a kontextusban a tartozás jelenik meg
    ctx = (
        await client.get(f"/api/partners/{partner['id']}/settlement-context", headers=mgr)
    ).json()
    assert abs(ctx["debt"] - 2350) < 1

    # 2. elszámolás: debt_before hozza a tartozást; túlfizetés törleszt
    res2 = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "cash",
            "lines": [{"product_id": product["id"], "physical_qty": 3.3}],  # 0 fogyás
            "paid_amount": 2350,  # a teljes régi tartozás rendezve
        },
        headers=mgr,
    )
    assert res2.status_code == 201, res2.text
    s2 = res2.json()
    assert abs(s2["debt_before"] - 2350) < 1

    ctx2 = (
        await client.get(f"/api/partners/{partner['id']}/settlement-context", headers=mgr)
    ).json()
    assert abs(ctx2["debt"]) < 1  # rendezve


async def test_previous_settlement_visible(client, manager):
    """A listában minden elszámolásnál látszik az előző elszámolás időpontja."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(client, mgr, "Időszakos Bolt")
    product = await make_product(client, mgr)
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 2.0},
        headers=mgr,
    )
    first = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash",
              "lines": [{"product_id": product["id"], "physical_qty": 1.5}]},
        headers=mgr,
    )
    assert first.status_code == 201
    second = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash",
              "lines": [{"product_id": product["id"], "physical_qty": 1.0}]},
        headers=mgr,
    )
    assert second.status_code == 201

    lst = (
        await client.get(f"/api/settlements?partner_id={partner['id']}", headers=mgr)
    ).json()
    assert len(lst) == 2
    # legújabb elöl: annak az előzője az első elszámolás időpontja
    assert lst[0]["previous_at"] == lst[1]["created_at"]
    assert lst[1]["previous_at"] is None

    # egyedi lekérésen is
    detail = (await client.get(f"/api/settlements/{lst[0]['id']}", headers=mgr)).json()
    assert detail["previous_at"] == lst[1]["created_at"]


async def test_delete_stock_row(client, manager):
    """Készlet-sor törlése: a mennyiség kivezetésre kerül (remove mozgás)."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(client, mgr, "Törlős Bolt")
    product = await make_product(client, mgr, name="Törlendő kávé")
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 2.5},
        headers=mgr,
    )
    res = await client.delete(
        f"/api/partners/{partner['id']}/stock/{product['id']}", headers=mgr
    )
    assert res.status_code == 200, res.text
    stock = (await client.get(f"/api/partners/{partner['id']}/stock", headers=mgr)).json()
    assert not any(x["product_id"] == product["id"] for x in stock)
    # másodszor már 404
    again = await client.delete(
        f"/api/partners/{partner['id']}/stock/{product['id']}", headers=mgr
    )
    assert again.status_code == 404

    import app.db as app_db
    from sqlalchemy import select

    from app.models import StockMovement

    async with app_db.get_session_factory()() as session:
        moves = (
            await session.execute(
                select(StockMovement).where(StockMovement.action == "remove")
            )
        ).scalars().all()
    assert any(m.quantity_delta == -2.5 for m in moves)


async def test_settlement_requires_lines_or_machines(client, manager):
    _, mgr = manager
    partner = await _partner(client, mgr, "Üres Bolt")
    res = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash"},
        headers=mgr,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "settlement.empty"


async def test_asset_default_product_crud(client, manager):
    """A gép „ebből főz" mezője menthető és törölhető."""
    from tests.test_consignment import make_product

    _, mgr = manager
    product = await make_product(client, mgr, name="Gépes kávé")
    res = await client.post(
        "/api/assets",
        json={"barcode": "DP-1", "name": "Necta Kikko",
              "default_product_id": product["id"]},
        headers=mgr,
    )
    assert res.status_code in (200, 201), res.text
    a = res.json()
    assert a["default_product_id"] == product["id"]

    upd = await client.patch(
        f"/api/assets/{a['id']}", json={"default_product_id": None}, headers=mgr
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["default_product_id"] is None


async def test_counter_prices_weighted_settlement(client, manager):
    """Szerzodeses adagar szamlalonkent: az elszamolas a szamlalonkenti
    kulonbsegekkel sulyozott atlagarat hasznalja (None elem = termekar)."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(client, mgr, "Sulyozott Bolt")
    product = await make_product(client, mgr, price_per_portion=100.0, grams_per_portion=7)
    asset = await _machine(
        client, mgr, partner, "GEP-CPS", counter=0,
        counter_count=3, counters=[0, 0, 0],
        default_product_id=product["id"],
        counter_prices=[200.0, 300.0, None],
    )
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 10.0},
        headers=mgr,
    )

    # allasok: 10 + 20 + 30 = 60 adag; ertek: 10*200 + 20*300 + 30*100(termekar)
    # = 2000 + 6000 + 3000 = 11000 -> atlag 183.33 Ft/adag
    res = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "cash",
            "lines": [{"product_id": product["id"], "physical_qty": 9.5}],
            "machines": [{"asset_id": asset["id"], "new_counters": [10, 20, 30]}],
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    m = res.json()["machines"][0]
    assert m["portions_billed"] == 60
    assert abs(m["amount_net"] - 11000) < 0.01
    assert abs(m["price_per_portion"] - 11000 / 60) < 0.01


async def test_counter_prices_roundtrip_and_swap(client, manager):
    """Adagarak mentese a gepre + gepcsere: egyezo kiosztasnal oroklodik,
    elteronel a kezzel megadott arak ervenyesek."""
    _, mgr = manager
    partner = (
        await client.post("/api/partners", json={"name": "Csere Aruhaz"}, headers=mgr)
    ).json()
    old = (
        await client.post(
            "/api/assets",
            json={"barcode": "CPS-REGI", "name": "Necta", "counter_count": 2,
                  "counters": [5, 5], "rent_fee": 4000},
            headers=mgr,
        )
    ).json()
    await client.post(
        f"/api/assets/{old['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr
    )
    # arak rogzitese utolag (a szerzodes-ablak PATCH-e)
    upd = await client.patch(
        f"/api/assets/{old['id']}", json={"counter_prices": [150.0, 250.0]}, headers=mgr
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["counter_prices"] == [150.0, 250.0]

    # 1) egyezo kiosztasu cseregep: orokles
    same = (
        await client.post(
            "/api/assets",
            json={"barcode": "CPS-UJ1", "name": "Necta", "counter_count": 2},
            headers=mgr,
        )
    ).json()
    res = await client.post(
        f"/api/assets/{old['id']}/swap",
        json={"replacement_asset_id": same["id"]},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["counter_prices"] == [150.0, 250.0]
    assert res.json()["rent_fee"] == 4000

    # 2) eltero kiosztasu cseregep: kezi arak a csere-folyamatbol
    diff = (
        await client.post(
            "/api/assets",
            json={"barcode": "CPS-UJ2", "name": "Jura", "counter_count": 3},
            headers=mgr,
        )
    ).json()
    res2 = await client.post(
        f"/api/assets/{same['id']}/swap",
        json={"replacement_asset_id": diff["id"],
              "counter_prices": [100.0, 200.0, 300.0]},
        headers=mgr,
    )
    assert res2.status_code == 200, res2.text
    assert res2.json()["counter_prices"] == [100.0, 200.0, 300.0]

    # 3) eltero kiosztas kezi arak nelkul: nem oroklodik vakon
    third = (
        await client.post(
            "/api/assets",
            json={"barcode": "CPS-UJ3", "name": "Melitta", "counter_count": 1},
            headers=mgr,
        )
    ).json()
    res3 = await client.post(
        f"/api/assets/{diff['id']}/swap",
        json={"replacement_asset_id": third["id"]},
        headers=mgr,
    )
    assert res3.status_code == 200, res3.text
    assert res3.json()["counter_prices"] is None


async def test_counters_detail_snapshot(client, manager):
    """A mentett elszamolas szamlalonkenti bontast tarol — mintha kulon
    gepek lennenek: allasonkent elozo/uj/adag/ar/osszeg."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(client, mgr, "Bontas Bolt")
    product = await make_product(client, mgr, price_per_portion=100.0, grams_per_portion=7)
    asset = await _machine(
        client, mgr, partner, "GEP-DET", counter=30, counter_count=2,
        counters=[10, 20], default_product_id=product["id"],
        counter_prices=[200.0, None],
    )
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 5.0},
        headers=mgr,
    )
    res = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "cash",
            "lines": [{"product_id": product["id"], "physical_qty": 4.5}],
            "machines": [{"asset_id": asset["id"], "new_counters": [15, 40]}],
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    m = res.json()["machines"][0]
    detail = m["counters_detail"]
    assert detail is not None and len(detail) == 2
    # 1. szamlalo: 10 -> 15 = 5 adag x 200 Ft; 2.: 20 -> 40 = 20 adag x 100 Ft
    assert detail[0] == {"prev": 10, "new": 15, "portions": 5, "price": 200.0, "amount": 1000.0}
    assert detail[1] == {"prev": 20, "new": 40, "portions": 20, "price": 100.0, "amount": 2000.0}
    assert abs(m["amount_net"] - 3000) < 0.01


async def test_totalizer_counter_excluded(client, manager):
    """0 Ft-os szerzodeses aru szamlalo = OSSZESITO (kontroll): a fogyasba
    nem szamit bele, a bontasban kontroll-sorkent jelenik meg."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(client, mgr, "Kontroll Bolt")
    product = await make_product(client, mgr, price_per_portion=100.0, grams_per_portion=7)
    asset = await _machine(
        client, mgr, partner, "GEP-CTRL", counter=0,
        counter_count=3, counters=[0, 0, 0],
        default_product_id=product["id"],
        counter_prices=[0.0, 120.0, 120.0],  # 1. = osszesito
    )
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 5.0},
        headers=mgr,
    )
    # osszesito: 64; fogyasztok: 60 + 4 = 64 → lefozott 64 (NEM 128)
    res = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "cash",
            "lines": [{"product_id": product["id"], "physical_qty": 4.5}],
            "machines": [{"asset_id": asset["id"], "new_counters": [64, 60, 4]}],
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    m = res.json()["machines"][0]
    assert m["portions_billed"] == 64  # az osszesito nem duplaz
    assert abs(m["amount_net"] - 64 * 120) < 0.01
    detail = m["counters_detail"]
    assert detail[0]["control"] is True and detail[0]["control_ok"] is True
    assert detail[0]["portions"] == 64 and detail[0]["amount"] == 0.0
    assert detail[1]["price"] == 120.0
