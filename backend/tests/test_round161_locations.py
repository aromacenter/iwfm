"""161. kör: tárhelyezett raktár — sor/állás/szint/fakk, berakás, áthelyezés,
átrendezés (a készlet a tárhellyel megy), kereső, címkék, egyeztetés kiadáskor."""

from __future__ import annotations

from tests.test_warehouse import _make_product, _make_warehouse


async def _located_site(client, mgr, name="Központ", scheme="letters"):
    site = await _make_warehouse(client, mgr, name, "site")
    res = await client.put(f"/api/warehouses/{site['id']}/layout", json={"layout": "located", "row_scheme": scheme}, headers=mgr)
    assert res.status_code == 200, res.text
    return site


async def test_structure_generate_edit_and_bulk(client, manager):
    _, mgr = manager
    site = await _located_site(client, mgr)
    van = await _make_warehouse(client, mgr, "Autó", "van")
    # autó nem tárhelyezhető
    assert (await client.put(f"/api/warehouses/{van['id']}/layout", json={"layout": "located"}, headers=mgr)).status_code == 422
    # A sor: 3 állás × 2 szint, fakk nélkül = 6 tárhely
    res = await client.post(f"/api/warehouses/{site['id']}/locations/generate", json={"row_code": "a", "bays": 3, "levels": 2, "slots": 0}, headers=mgr)
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["rows"] == [{"row_code": "A", "bays": 3, "levels": 2, "slots": 0, "locations": 6, "used": 0}]
    codes = [x["code"] for x in data["locations"]]
    assert codes == ["A-01-1", "A-01-2", "A-02-1", "A-02-2", "A-03-1", "A-03-2"]
    # B sor fakkokkal: 1 állás × 1 szint × 3 fakk
    res = await client.post(f"/api/warehouses/{site['id']}/locations/generate", json={"row_code": "B", "bays": 1, "levels": 1, "slots": 3}, headers=mgr)
    assert [x["code"] for x in res.json()["locations"] if x["row_code"] == "B"] == ["B-01-1-1", "B-01-1-2", "B-01-1-3"]
    # bővítés: A sor 4. állás — a meglévők megmaradnak
    res = await client.post(f"/api/warehouses/{site['id']}/locations/generate", json={"row_code": "A", "bays": 4, "levels": 2, "slots": 0}, headers=mgr)
    assert next(r for r in res.json()["rows"] if r["row_code"] == "A")["locations"] == 8
    # szám-sémájú raktárban a betű nem jó
    assert (await client.post(f"/api/warehouses/{site['id']}/locations/generate", json={"row_code": "7", "bays": 1, "levels": 1}, headers=mgr)).status_code == 422
    # egy tárhely átrendezése: A-01-1 → A-05-3; foglalt helyre 409
    loc = next(x for x in res.json()["locations"] if x["code"] == "A-01-1")
    res = await client.patch(f"/api/warehouses/{site['id']}/locations/{loc['id']}", json={"bay": 5, "level": 3, "label": "Hűtő mellett"}, headers=mgr)
    assert res.status_code == 200 and res.json()["code"] == "A-05-3" and res.json()["label"] == "Hűtő mellett"
    assert (await client.patch(f"/api/warehouses/{site['id']}/locations/{loc['id']}", json={"bay": 2, "level": 1}, headers=mgr)).status_code == 409
    # tömeges: A → C átnevezés, állások eltolása +1, szintek eltolása
    res = await client.post(f"/api/warehouses/{site['id']}/locations/bulk", json={"op": "rename_row", "row_code": "A", "new_row_code": "C"}, headers=mgr)
    assert res.status_code == 200 and {r["row_code"] for r in res.json()["rows"]} == {"B", "C"}
    res = await client.post(f"/api/warehouses/{site['id']}/locations/bulk", json={"op": "shift_bays", "row_code": "C", "offset": 1}, headers=mgr)
    assert "C-06-3" in [x["code"] for x in res.json()["locations"]] and "C-01-1" not in [x["code"] for x in res.json()["locations"]]
    assert (await client.post(f"/api/warehouses/{site['id']}/locations/bulk", json={"op": "shift_bays", "row_code": "C", "offset": -5}, headers=mgr)).status_code == 422
    # a séma nem váltható, amíg van tárhely; üres sor törölhető
    assert (await client.put(f"/api/warehouses/{site['id']}/layout", json={"layout": "located", "row_scheme": "numbers"}, headers=mgr)).status_code == 422
    res = await client.post(f"/api/warehouses/{site['id']}/locations/bulk", json={"op": "delete_empty", "row_code": "B"}, headers=mgr)
    assert {r["row_code"] for r in res.json()["rows"]} == {"C"}
    # címkelap
    res = await client.get(f"/api/warehouses/{site['id']}/locations/labels.pdf?row=C", headers=mgr)
    assert res.status_code == 200 and res.content.startswith(b"%PDF")


async def test_stock_place_relocate_and_reconcile(client, manager):
    _, mgr = manager
    site = await _located_site(client, mgr, scheme="numbers")
    other = await _make_warehouse(client, mgr, "Másik telephely", "site")
    kave = await _make_product(client, mgr, "Kávé")
    res = await client.post(f"/api/warehouses/{site['id']}/locations/generate", json={"row_code": "1", "bays": 2, "levels": 2}, headers=mgr)
    locs = {x["code"]: x["id"] for x in res.json()["locations"]}
    # bevételezés tárhelyre 30, tárhely nélkül 10 → 10 nem tárhelyezett
    assert (await client.post(f"/api/warehouses/{site['id']}/receive", json={"product_id": kave["id"], "quantity": 30, "location_id": locs["1-01-1"]}, headers=mgr)).status_code == 200
    assert (await client.post(f"/api/warehouses/{site['id']}/receive", json={"product_id": kave["id"], "quantity": 10}, headers=mgr)).status_code == 200
    row = (await client.get(f"/api/warehouses/{site['id']}/locations/stock", headers=mgr)).json()[0]
    assert row["total"] == 40 and row["unplaced"] == 10 and row["places"] == [{"location_id": locs["1-01-1"], "code": "1-01-1", "quantity": 30}]
    # berakás: 10-ből 6 az 1-02-2-re, 5 már nem fér
    res = await client.post(f"/api/warehouses/{site['id']}/locations/place", json={"product_id": kave["id"], "location_id": locs["1-02-2"], "quantity": 6}, headers=mgr)
    assert res.status_code == 200 and res.json()["unplaced"] == 4
    res = await client.post(f"/api/warehouses/{site['id']}/locations/place", json={"product_id": kave["id"], "location_id": locs["1-02-2"], "quantity": 5}, headers=mgr)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "warehouse.unplaced_insufficient"
    # áthelyezés tárhelyek közt: 1-01-1 → 1-01-2 12; többet nem lehet, mint ami ott van
    res = await client.post(f"/api/warehouses/{site['id']}/locations/relocate", json={"product_id": kave["id"], "from_location_id": locs["1-01-1"], "to_location_id": locs["1-01-2"], "quantity": 12}, headers=mgr)
    assert res.status_code == 200
    assert {p["code"]: p["quantity"] for p in res.json()["places"]} == {"1-01-1": 18, "1-01-2": 12, "1-02-2": 6}
    assert (await client.post(f"/api/warehouses/{site['id']}/locations/relocate", json={"product_id": kave["id"], "from_location_id": locs["1-01-1"], "to_location_id": locs["1-01-2"], "quantity": 50}, headers=mgr)).status_code == 422
    # tárhely tartalma + kereső
    cont = (await client.get(f"/api/warehouses/{site['id']}/locations/{locs['1-01-2']}/contents", headers=mgr)).json()
    assert cont == [{"product_id": kave["id"], "product_name": "Kávé", "unit": "kg", "quantity": 12}]
    found = (await client.get(f"/api/warehouses/locate?product_id={kave['id']}", headers=mgr)).json()
    assert [(f["code"], f["quantity"]) for f in found] == [("1-01-1", 18), ("1-01-2", 12), ("1-02-2", 6), (None, 4)]
    # átrendezés készlettel: a tárhely id-je marad, a kód változik, a készlet vele megy
    res = await client.patch(f"/api/warehouses/{site['id']}/locations/{locs['1-01-2']}", json={"row_code": "2", "bay": 1, "level": 1}, headers=mgr)
    assert res.status_code == 200 and res.json()["code"] == "2-01-1" and res.json()["quantity"] == 12
    # foglalt tárhely nem törölhető; a raktár nem állítható vissza csak-készletre
    assert (await client.delete(f"/api/warehouses/{site['id']}/locations/{locs['1-01-2']}", headers=mgr)).status_code == 422
    assert (await client.put(f"/api/warehouses/{site['id']}/layout", json={"layout": "simple", "row_scheme": "numbers"}, headers=mgr)).status_code == 422
    # kiadás tárhely nélkül (átadás másik telephelyre, azonnali): 25 megy el → a bontás a kicsiktől fogy
    res = await client.post("/api/warehouses/transfer", json={"from_warehouse_id": site["id"], "to_warehouse_id": other["id"], "product_id": kave["id"], "quantity": 25}, headers=mgr)
    assert res.status_code == 200, res.text
    row = (await client.get(f"/api/warehouses/{site['id']}/locations/stock", headers=mgr)).json()[0]
    assert row["total"] == 15 and row["unplaced"] == 0
    assert {p["code"]: p["quantity"] for p in row["places"]} == {"1-01-1": 15}  # 4 szabad + 6 + 12 ment el, a 18-ból 3
    # mozgásnapló: a tárhely-kód látszik
    movs = (await client.get(f"/api/warehouses/{site['id']}/movements", headers=mgr)).json()
    rel = next(m for m in movs if m["action"] == "relocate")
    assert rel["location"] == "1-01-1" and rel["ref_location"] == "2-01-1"  # a cél közben 2-01-1 lett
    # leltár-korrekció 5-re: a tárhely is 5-re csökken
    assert (await client.post(f"/api/warehouses/{site['id']}/adjust", json={"product_id": kave["id"], "counted_qty": 5}, headers=mgr)).status_code == 200
    row = (await client.get(f"/api/warehouses/{site['id']}/locations/stock", headers=mgr)).json()[0]
    assert row["places"] == [{"location_id": locs["1-01-1"], "code": "1-01-1", "quantity": 5}]


async def test_simple_warehouse_rejects_locations(client, manager):
    _, mgr = manager
    site = await _make_warehouse(client, mgr, "Sima", "site")
    res = await client.get(f"/api/warehouses/{site['id']}/locations", headers=mgr)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "warehouse.not_located"
    w = next(x for x in (await client.get("/api/warehouses", headers=mgr)).json() if x["id"] == site["id"])
    assert w["layout"] == "simple" and w["row_scheme"] == "letters"
