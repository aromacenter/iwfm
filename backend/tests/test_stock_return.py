"""Készlet-visszavét partnertől raktárba + tartozás-részletezés + KSZ
ár-megőrzés + kintlévőség-statisztika + alvállalkozó-jelölő."""

from __future__ import annotations


async def _partner(client, mgr, name):
    res = await client.post("/api/partners", json={"name": name}, headers=mgr)
    assert res.status_code in (200, 201), res.text
    return res.json()


async def _warehouse(client, mgr, name="Visszavét Autó", kind="van"):
    res = await client.post(
        "/api/warehouses", json={"name": name, "kind": kind}, headers=mgr
    )
    assert res.status_code in (200, 201), res.text
    return res.json()


async def test_stock_return_partial_and_all(client, manager):
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(client, mgr, "Visszavételes Bolt")
    wh = await _warehouse(client, mgr)
    p1 = await make_product(client, mgr, name="Visszavét Kávé A")
    p2 = await make_product(client, mgr, name="Visszavét Kávé B")
    for p, qty in ((p1, 5.0), (p2, 3.0)):
        await client.post(
            f"/api/partners/{partner['id']}/stock/replenish",
            json={"product_id": p["id"], "quantity": qty}, headers=mgr,
        )

    # részleges visszavét (kávéváltás): 2 kg az A-ból
    res = await client.post(
        f"/api/partners/{partner['id']}/stock/return",
        json={"target_warehouse_id": wh["id"],
              "items": [{"product_id": p1["id"], "quantity": 2.0}]},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["returned"][0]["quantity"] == 2.0

    # túl sok → 422
    res = await client.post(
        f"/api/partners/{partner['id']}/stock/return",
        json={"target_warehouse_id": wh["id"],
              "items": [{"product_id": p1["id"], "quantity": 99.0}]},
        headers=mgr,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "stock.return_too_much"

    # teljes visszavét (szerződés-lezárás): a maradék 3 + 3
    res = await client.post(
        f"/api/partners/{partner['id']}/stock/return",
        json={"target_warehouse_id": wh["id"], "all": True},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    returned = {r["product_name"]: r["quantity"] for r in res.json()["returned"]}
    assert returned["Visszavét Kávé A"] == 3.0
    assert returned["Visszavét Kávé B"] == 3.0

    # a partner készlete kiürült
    stock = (
        await client.get(f"/api/partners/{partner['id']}/stock", headers=mgr)
    ).json()
    assert all(s["quantity"] == 0 for s in stock)

    # a raktárban megjelent (a sor automatikusan létrejött a választékban)
    wh_stock = (
        await client.get(f"/api/warehouses/{wh['id']}/stock", headers=mgr)
    ).json()
    by_name = {s["product_name"]: s["quantity"] for s in wh_stock}
    assert by_name["Visszavét Kávé A"] == 5.0
    assert by_name["Visszavét Kávé B"] == 3.0

    # üres visszavét → 422
    res = await client.post(
        f"/api/partners/{partner['id']}/stock/return",
        json={"target_warehouse_id": wh["id"], "all": True},
        headers=mgr,
    )
    assert res.status_code == 422


async def test_debt_items_in_context(client, manager):
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(client, mgr, "Tartozós Bolt")
    product = await make_product(client, mgr, name="Tartozás Kávé")
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 2.0}, headers=mgr,
    )
    # elszámolás részfizetéssel: 100 adag × 50 = 5000 nettó, fizetve 2000
    res = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash",
              "paid_amount": 2000,
              "lines": [{"product_id": product["id"], "physical_qty": 1.3}]},
        headers=mgr,
    )
    assert res.status_code == 201, res.text

    ctx = (
        await client.get(
            f"/api/partners/{partner['id']}/settlement-context", headers=mgr
        )
    ).json()
    assert ctx["debt"] > 0
    assert len(ctx["debt_items"]) == 1
    item = ctx["debt_items"][0]
    assert item["paid_amount"] == 2000
    assert abs(item["remaining"] - (item["total_gross"] - 2000)) < 1

    # kintlévőség-statisztika a vezérlőpulton
    stats = (
        await client.get("/api/dashboard/receivables-stats", headers=mgr)
    ).json()
    assert stats["total"] > 0
    assert any(d["partner_name"] == "Tartozós Bolt" for d in stats["top_debtors"])
    debtor = next(d for d in stats["top_debtors"] if d["partner_name"] == "Tartozós Bolt")
    assert debtor["oldest_days"] == 0
    assert stats["aging"]["b0_30"] >= debtor["remaining"]


async def test_ksz_price_preserved_on_worker_save(client, admin, manager):
    """A képviselő által beállított ügyfél-árat a dolgozói mentés nem törli."""
    from datetime import date

    from tests.test_tasks import make_emp

    _, mgr = manager
    emp, emp_headers = await make_emp(email="arvedelem@example.com")
    task = (
        await client.post(
            "/api/tasks",
            json={"title": "Árvédelem teszt", "employee_id": str(emp.id),
                  "due_date": date.today().isoformat(), "external_service": True},
            headers=mgr,
        )
    ).json()

    # dolgozó kitölti költséggel (ár nélkül)
    res = await client.put(
        f"/api/me/tasks/{task['id']}/worksheet",
        json={"work_description": "Csere",
              "materials": [{"name": "Szivattyú", "qty": "1", "unit": "db",
                             "cost_net": 8000}]},
        headers=emp_headers,
    )
    assert res.status_code == 200, res.text

    # képviselő beállítja az ügyfél-árat
    res = await client.put(
        f"/api/tasks/{task['id']}/worksheet",
        json={"work_description": "Csere",
              "materials": [{"name": "Szivattyú", "qty": "1", "unit": "db",
                             "cost_net": 8000, "price_net": 15000}]},
        headers=mgr,
    )
    assert res.status_code == 200
    assert res.json()["materials"][0]["price_net"] == 15000

    # a dolgozó újra ment (ár nélkül) → az ár MEGMARAD, de a dolgozói válasz
    # az ügyfél-árat SOSEM mutatja (csak a saját költségét)
    res = await client.put(
        f"/api/me/tasks/{task['id']}/worksheet",
        json={"work_description": "Csere + tisztítás",
              "materials": [{"name": "Szivattyú", "qty": "1", "unit": "db",
                             "cost_net": 8500}]},
        headers=emp_headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["materials"][0]["price_net"] is None  # rejtve a szervizes elől
    assert res.json()["materials"][0]["cost_net"] == 8500
    ws_mgr = (await client.get(f"/api/tasks/{task['id']}/worksheet", headers=mgr)).json()
    assert ws_mgr["materials"][0]["price_net"] == 15000  # de a rendszerben megvan


async def test_contractor_flag(client, admin):
    """Alvállalkozó: minimál adatokkal vehető fel, oda-vissza váltható."""
    _, adm = admin
    res = await client.post(
        "/api/employees",
        json={"email": "alvallalkozo@example.com", "last_name": "Számlás",
              "first_name": "Sándor", "hire_date": "2026-08-20",
              "is_contractor": True, "company_tax_number": "12345678-1-42",
              "phone": "+36301112233", "address": "Bp, Számla u. 1."},
        headers=adm,
    )
    assert res.status_code == 201, res.text
    emp = res.json()
    assert emp["is_contractor"] is True
    assert emp["company_tax_number"] == "12345678-1-42"

    # átváltás alkalmazottira és vissza
    res = await client.patch(
        f"/api/employees/{emp['id']}", json={"is_contractor": False}, headers=adm
    )
    assert res.status_code == 200
    assert res.json()["is_contractor"] is False
    res = await client.patch(
        f"/api/employees/{emp['id']}", json={"is_contractor": True}, headers=adm
    )
    assert res.json()["is_contractor"] is True
