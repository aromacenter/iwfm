"""176. kör: szkenner-mód — kód feloldása (tárhely / QR-link / termékkód) és
tárhelyi leltár (többlet szabadból vagy korrekcióval, hiány korrekcióval)."""

from __future__ import annotations

from tests.test_round161_locations import _located_site
from tests.test_warehouse import _make_product


async def test_scan_resolves_location_link_and_product(client, manager):
    _, mgr = manager
    site = await _located_site(client, mgr, name="Szkenner raktár")
    res = await client.post(f"/api/warehouses/{site['id']}/locations/generate", json={"row_code": "A", "bays": 2, "levels": 1}, headers=mgr)
    locs = {x["code"]: x for x in res.json()["locations"]}
    kave = await _make_product(client, mgr, "Kávé 1 kg")
    assert (await client.patch(f"/api/products/{kave['id']}", json={"name": "Kávé 1 kg", "code": "KV-1000"}, headers=mgr)).status_code == 200
    assert (await client.post(f"/api/warehouses/{site['id']}/receive", json={"product_id": kave["id"], "quantity": 10, "location_id": locs["A-01-1"]["id"]}, headers=mgr)).status_code == 200
    base = f"/api/warehouses/{site['id']}/scan"
    # tárhely-kód kisbetűvel
    res = await client.get(base, params={"code": "a-01-1"}, headers=mgr)
    assert res.status_code == 200 and res.json()["kind"] == "location"
    assert res.json()["location"]["code"] == "A-01-1" and res.json()["contents"][0]["quantity"] == 10
    # a címke QR-linkje
    res = await client.get(base, params={"code": f"https://program.x-presso.hu/raktar?wh={site['id']}&loc=A-02-1"}, headers=mgr)
    assert res.status_code == 200 and res.json()["location"]["code"] == "A-02-1" and res.json()["contents"] == []
    # termékkód
    res = await client.get(base, params={"code": "kv-1000"}, headers=mgr)
    assert res.status_code == 200 and res.json()["kind"] == "product" and res.json()["product"]["id"] == kave["id"]
    # ismeretlen
    res = await client.get(base, params={"code": "Z-99-9"}, headers=mgr)
    assert res.status_code == 404 and res.json()["detail"]["code"] == "warehouse.scan_unknown"


async def test_location_count_adjusts_stock(client, manager):
    _, mgr = manager
    site = await _located_site(client, mgr, name="Leltár raktár")
    res = await client.post(f"/api/warehouses/{site['id']}/locations/generate", json={"row_code": "B", "bays": 1, "levels": 2}, headers=mgr)
    locs = {x["code"]: x for x in res.json()["locations"]}
    kave = await _make_product(client, mgr, "Kávé")
    tea = await _make_product(client, mgr, "Tea")
    wh = site["id"]
    # kávé: 10 a B-01-1-en, 5 nem tárhelyezett; tea: semmi
    assert (await client.post(f"/api/warehouses/{wh}/receive", json={"product_id": kave["id"], "quantity": 10, "location_id": locs["B-01-1"]["id"]}, headers=mgr)).status_code == 200
    assert (await client.post(f"/api/warehouses/{wh}/receive", json={"product_id": kave["id"], "quantity": 5}, headers=mgr)).status_code == 200
    # leltár B-01-1: kávé 13 (3 többlet → a szabad 5-ből), tea 4 (nincs sehol → korrekció +4)
    res = await client.post(f"/api/warehouses/{wh}/locations/{locs['B-01-1']['id']}/count", json={"items": [
        {"product_id": kave["id"], "counted": 13}, {"product_id": tea["id"], "counted": 4},
    ]}, headers=mgr)
    assert res.status_code == 200, res.text
    by = {c["product_name"]: c["quantity"] for c in res.json()}
    assert by == {"Kávé": 13, "Tea": 4}
    stock = {s["product_name"]: s["quantity"] for s in (await client.get(f"/api/warehouses/{wh}/stock", headers=mgr)).json()}
    assert stock["Kávé"] == 15 and stock["Tea"] == 4  # a kávé összese nem nőtt, csak a szabad fogyott; a tea korrekcióval jött
    located = {s["product_name"]: s for s in (await client.get(f"/api/warehouses/{wh}/locations/stock", headers=mgr)).json()}
    assert located["Kávé"]["unplaced"] == 2
    # leltár: kávé 20 (7 többlet: 2 szabadból + 5 korrekció), tea 0 (hiány → lekerül és az összes is csökken)
    res = await client.post(f"/api/warehouses/{wh}/locations/{locs['B-01-1']['id']}/count", json={"items": [
        {"product_id": kave["id"], "counted": 20}, {"product_id": tea["id"], "counted": 0},
    ], "note": "esti leltár"}, headers=mgr)
    assert res.status_code == 200, res.text
    assert {c["product_name"]: c["quantity"] for c in res.json()} == {"Kávé": 20}
    stock = {s["product_name"]: s["quantity"] for s in (await client.get(f"/api/warehouses/{wh}/stock", headers=mgr)).json()}
    assert stock["Kávé"] == 20 and stock.get("Tea", 0) == 0
    # a mozgásnaplóban leltár-korrekció a tárhellyel
    movs = (await client.get(f"/api/warehouses/{wh}/movements", headers=mgr)).json()
    movs = movs if isinstance(movs, list) else movs["items"]
    adj = [m for m in movs if m["action"] == "adjust" and m.get("location") == "B-01-1"]
    assert len(adj) >= 3 and any(m["note"] == "esti leltár" for m in adj)
    # inaktív tárhelyre nem megy
    assert (await client.patch(f"/api/warehouses/{wh}/locations/{locs['B-01-2']['id']}", json={"is_active": False}, headers=mgr)).status_code == 200
    assert (await client.post(f"/api/warehouses/{wh}/locations/{locs['B-01-2']['id']}/count", json={"items": [{"product_id": kave["id"], "counted": 1}]}, headers=mgr)).status_code == 422
