"""135. kör: gép-jövedelmezőség — számlálós bevétel, karbantartási díj, anyag- és
alkatrész-költség, látogatások, eredmény/árrés; jogosultság."""

from __future__ import annotations

from datetime import date

from tests.test_consignment import make_product
from tests.test_machine_settlement import _machine, _partner
from tests.test_tasks import make_emp


async def test_machine_profitability_rows(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    partner = await _partner(client, mgr, "Jövedelmező Bolt")
    product = await make_product(client, mgr, price_per_portion=50.0, grams_per_portion=7)
    asset = await _machine(client, mgr, partner, "PROFIT-1", counter=1000, default_product_id=product["id"])
    await client.post(f"/api/partners/{partner['id']}/stock/replenish", json={"product_id": product["id"], "quantity": 10.0}, headers=mgr)
    # elszámolás: 1000 → 1200, 200 adag × 50 Ft = 10 000 Ft bevétel
    res = await client.post("/api/settlements", json={
        "partner_id": partner["id"], "payment_method": "cash",
        "lines": [{"product_id": product["id"], "physical_qty": 8.6}],
        "machines": [{"asset_id": asset["id"], "new_counter": 1200}],
    }, headers=mgr)
    assert res.status_code == 201, res.text

    # szerviz-feladat a gépre: munkalap karbantartási díjjal (3000) és anyag-költséggel (2 × 800)
    emp_rec, emp_h = await make_emp(email="profit-szerelo@example.com")
    res = await client.post("/api/tasks", json={
        "title": "Gép szerviz", "employee_id": str(emp_rec.id), "due_date": date.today().isoformat(), "asset_id": asset["id"],
    }, headers=mgr)
    assert res.status_code == 201, res.text
    task = res.json()
    res = await client.put(f"/api/tasks/{task['id']}/worksheet", json={
        "work_description": "Tömítés csere", "maintenance_fee": 3000, "hours_spent": 1.5,
        "materials": [{"name": "Tömítés", "qty": "2", "unit": "db", "cost_net": 800, "price_net": 1500}],
    }, headers=mgr)
    assert res.status_code == 200, res.text

    res = await client.get("/api/stats/machine-profitability?months=12", headers=mgr)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["months"] == 12
    row = next(r for r in body["rows"] if r["barcode"] == "PROFIT-1")
    assert row["revenue_net"] == 10000.0 and row["visits"] == 1
    assert row["maintenance_net"] == 3000.0 and row["service_cost"] == 1600.0 and row["hours"] == 1.5
    assert row["net_result"] == 11400.0  # 10 000 + 3 000 − 1 600
    assert row["margin_pct"] == round(11400 / 13000 * 100, 1)
    assert row["partner_name"] == "Jövedelmező Bolt"
    assert body["totals"]["machines"] >= 1 and body["totals"]["loss_making"] == 0

    # rövid időablak: minden ma történt → ugyanaz; rossz paraméter → 422
    assert (await client.get("/api/stats/machine-profitability?months=0", headers=mgr)).status_code == 422

    # dolgozó nem láthatja
    assert (await client.get("/api/stats/machine-profitability", headers=emp_h)).status_code == 403
