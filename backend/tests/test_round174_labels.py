"""174. kör: tárhely-címkék — állítható méret, papír, mezők, QR/vonalkód,
csoport-felirat, egyesével/tömegesen; mentett címke-minták."""

from __future__ import annotations

import io

from pypdf import PdfReader

from tests.test_round161_locations import _located_site


def _pages(content: bytes) -> PdfReader:
    assert content.startswith(b"%PDF")
    return PdfReader(io.BytesIO(content))


async def _six_locations(client, mgr):
    site = await _located_site(client, mgr, name="Címkés raktár")
    res = await client.post(f"/api/warehouses/{site['id']}/locations/generate", json={"row_code": "A", "bays": 3, "levels": 2}, headers=mgr)
    assert res.status_code == 201
    locs = res.json()["locations"]
    await client.patch(f"/api/warehouses/{site['id']}/locations/{locs[0]['id']}", json={"label": "Hűtő mellett"}, headers=mgr)
    return site, locs


async def test_labels_sizes_papers_and_fields(client, manager):
    _, mgr = manager
    site, locs = await _six_locations(client, mgr)
    base = f"/api/warehouses/{site['id']}/locations/labels"

    # alap: A4, 63,5×33,9 mm → 3×8 = 24/oldal → 6 címke egy oldalon
    res = await client.post(base, json={}, headers=mgr)
    assert res.status_code == 200, res.text
    r = _pages(res.content)
    assert len(r.pages) == 1
    text = r.pages[0].extract_text()
    assert "A-01-1" in text and "A-03-2" in text and "Címkés raktár" in text

    # tekercs: egy címke = egy oldal, 2 példány → 12 oldal, lapméret = címkeméret
    res = await client.post(base, json={"paper": "roll", "label_w": 62, "label_h": 29, "copies": 2, "fields": ["code", "barcode"]}, headers=mgr)
    assert res.status_code == 200, res.text
    r = _pages(res.content)
    assert len(r.pages) == 12
    box = r.pages[0].mediabox
    assert abs(float(box.width) - 62 / 25.4 * 72) < 0.5 and abs(float(box.height) - 29 / 25.4 * 72) < 0.5

    # egy tárhely, csoport-felirattal és megjegyzéssel, A5 fekvő, nagy címke
    one = next(x for x in locs if x["code"] == "A-01-1")
    res = await client.post(base, json={
        "location_ids": [one["id"]], "paper": "A5", "landscape": True, "label_w": 100, "label_h": 50,
        "fields": ["code", "qr", "barcode", "group", "coords", "warehouse", "note"], "group_text": "Szerszámok", "qr_content": "code",
    }, headers=mgr)
    assert res.status_code == 200, res.text
    r = _pages(res.content)
    assert len(r.pages) == 1
    text = r.pages[0].extract_text()
    assert "Szerszámok" in text and "A-01-1" in text and "Hűtő mellett" in text and "A-02-1" not in text

    # csak egy sor (row), nincs ilyen sor → 404
    res = await client.post(base, json={"row": "A", "label_w": 70, "label_h": 37}, headers=mgr)
    assert res.status_code == 200 and len(_pages(res.content).pages) == 1
    assert (await client.post(base, json={"row": "Z"}, headers=mgr)).status_code == 404

    # túl nagy címke a lapra → 422; üres mező-lista → 422; érvénytelen méret → 422
    res = await client.post(base, json={"paper": "A5", "label_w": 200, "label_h": 50}, headers=mgr)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "warehouse.label_too_big"
    res = await client.post(base, json={"fields": []}, headers=mgr)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "warehouse.label_fields"
    assert (await client.post(base, json={"label_w": 5}, headers=mgr)).status_code == 422

    # 12 mm-es P-touch szalag: 60×12, QR + kód — megy, egy címke egy oldal
    res = await client.post(base, json={"paper": "roll", "label_w": 60, "label_h": 12, "fields": ["code", "qr"], "border": False}, headers=mgr)
    assert res.status_code == 200 and len(_pages(res.content).pages) == 6

    # ismeretlen mező-név kiszűrve, a többi marad
    res = await client.post(base, json={"fields": ["code", "whatever"]}, headers=mgr)
    assert res.status_code == 200


async def test_label_templates_crud(client, manager):
    from tests.conftest import make_user

    _, mgr = manager
    _, tech = await make_user(email="uzletkoto-cimke@example.com", role="uzletkoto")
    cfg = {"paper": "roll", "label_w": 50, "label_h": 25, "fields": ["code", "barcode", "group"], "group_text": "Szerszámok"}
    res = await client.post("/api/warehouses/label-templates", json={"name": "Tekercs 50×25", "config": cfg}, headers=mgr)
    assert res.status_code == 200, res.text
    tid = res.json()["id"]
    assert res.json()["config"]["label_w"] == 50 and res.json()["config"]["group_text"] == "Szerszámok"
    # azonos név → felülírás, nem duplikátum
    res = await client.post("/api/warehouses/label-templates", json={"name": "Tekercs 50×25", "config": {**cfg, "label_h": 30}}, headers=mgr)
    assert res.status_code == 200 and res.json()["id"] == tid and res.json()["config"]["label_h"] == 30
    res = await client.get("/api/warehouses/label-templates", headers=mgr)
    assert res.status_code == 200 and [t["name"] for t in res.json()] == ["Tekercs 50×25"]
    # üzletkötő nem menthet és nem törölhet, de láthatja (nyomtatni tud a mintával)
    assert (await client.post("/api/warehouses/label-templates", json={"name": "X", "config": cfg}, headers=tech)).status_code == 403
    assert (await client.delete(f"/api/warehouses/label-templates/{tid}", headers=tech)).status_code == 403
    assert (await client.get("/api/warehouses/label-templates", headers=tech)).status_code == 200
    # érvénytelen beállítás a mintában → 422
    assert (await client.post("/api/warehouses/label-templates", json={"name": "Rossz", "config": {"paper": "B5"}}, headers=mgr)).status_code == 422
    assert (await client.delete(f"/api/warehouses/label-templates/{tid}", headers=mgr)).status_code == 200
    assert (await client.delete(f"/api/warehouses/label-templates/{tid}", headers=mgr)).status_code == 404
    assert (await client.get("/api/warehouses/label-templates", headers=mgr)).json() == []
