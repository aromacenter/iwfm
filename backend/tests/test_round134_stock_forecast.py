"""134. kör: készlet-előrejelzés — fogyás-alapú javaslat (napi kiadás, elég-napra,
úton lévő levonása), küszöb nélkül is; rendelés-vázlat a javaslatokból."""

from __future__ import annotations

from tests.test_inventory import make_partner
from tests.test_warehouse import _make_product, _make_warehouse


async def test_forecast_rows_and_draft_po(client, manager):
    _, mgr = manager
    product = await _make_product(client, mgr, name="Előrejelzett Kávé")
    site = await _make_warehouse(client, mgr, name="Központ")
    partner = await make_partner(client, mgr, name="Fogyasztó Kft.")
    res = await client.post(f"/api/warehouses/{site['id']}/receive", json={"product_id": product["id"], "quantity": 40.0}, headers=mgr)
    assert res.status_code == 200, res.text
    # nincs küszöb, nincs kiadás → nincs javaslat
    assert (await client.get("/api/warehouses/reorder-suggestions", headers=mgr)).json() == []

    # 28 kg kiadás a partnernek a raktárból → 14 napos min. ablak → 2 kg/nap, maradt 12 kg → 6 napra elég
    for _ in range(4):
        res = await client.post(f"/api/partners/{partner['id']}/stock/replenish", json={
            "product_id": product["id"], "quantity": 7.0, "source_warehouse_id": site["id"],
        }, headers=mgr)
        assert res.status_code in (200, 201), res.text
    rows = (await client.get("/api/warehouses/reorder-suggestions", headers=mgr)).json()
    assert len(rows) == 1
    r = rows[0]
    assert r["product_name"] == "Előrejelzett Kávé" and r["quantity"] == 12.0
    assert r["daily_consumption"] == 2.0 and r["days_of_cover"] == 6 and r["reasons"] == ["runs_out"]
    assert r["on_order_qty"] == 0 and r["horizon_days"] == 30
    assert r["suggested_qty"] == 48.0  # 2 kg/nap × 30 nap − 12 kg

    # horizont paraméter
    rows = (await client.get("/api/warehouses/reorder-suggestions?horizon=60", headers=mgr)).json()
    assert rows[0]["suggested_qty"] == 108.0

    # rendelés-vázlat a javaslatokból → PO piszkozat a javasolt mennyiséggel
    res = await client.post("/api/warehouses/reorder-suggestions/create-po", json={"horizon": 30}, headers=mgr)
    assert res.status_code == 201, res.text
    pos = res.json()
    assert len(pos) == 1 and pos[0]["status"] == "draft" and pos[0]["warehouse_id"] == site["id"]
    assert pos[0]["lines"][0]["quantity"] == 48.0
    # megrendelve → úton lévőként levonódik, a javaslat 0-ra csökken
    res = await client.post(f"/api/purchase-orders/{pos[0]['id']}/order", headers=mgr)
    assert res.status_code == 200, res.text
    rows = (await client.get("/api/warehouses/reorder-suggestions", headers=mgr)).json()
    assert rows[0]["on_order_qty"] == 48.0 and rows[0]["suggested_qty"] == 0.0
    # nincs mit rendelni → 422
    res = await client.post("/api/warehouses/reorder-suggestions/create-po", json={"horizon": 30}, headers=mgr)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "po.nothing_to_order"
