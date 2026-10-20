"""Alkatrész-raktár D fázis (1d553d2e): leltárív + leltár könyvelése
(korrekciók csak az eltérő sorokra), szerelői „Számoltam” a saját autón,
CSV export (BOM + pontosvessző)."""
from __future__ import annotations

from tests.test_parts_b import _emp, _setup


async def test_parts_stocktake_and_export(client, admin):
    _, adm = admin
    user, emp_h, _ = await _emp("leltar@example.com")
    _, other_h, _ = await _emp("nincsauto@example.com")
    central, van, part = await _setup(client, adm, user)  # központ A-1: 1 db, autó: 4 db

    # leltárív a központra
    sheet = (await client.get(f"/api/parts/stocktake/sheet?warehouse_id={central['id']}", headers=adm)).json()
    assert sheet == [{"part_id": part["id"], "sku": "BOIL-TERM", "name": "Bojler-termosztát 125°C", "unit": "db", "location": "A-1", "expected": 1.0}]

    # könyvelés: 1 → 3 (korrekció +2), egyező sor nem könyvel; rossz helykód 422
    res = await client.post("/api/parts/stocktake", json={"warehouse_id": central["id"], "note": "őszi", "counts": [{"part_id": part["id"], "location": "A-1", "quantity": 3}]}, headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()["adjusted"] == 1 and res.json()["diffs"][0]["delta"] == 2 and res.json()["diffs"][0]["expected"] == 1
    res = await client.post("/api/parts/stocktake", json={"warehouse_id": central["id"], "counts": [{"part_id": part["id"], "location": "A-1", "quantity": 3}]}, headers=adm)
    assert res.json()["adjusted"] == 0
    res = await client.post("/api/parts/stocktake", json={"warehouse_id": central["id"], "counts": [{"part_id": part["id"], "location": "Q-1", "quantity": 3}]}, headers=adm)
    assert res.status_code == 422
    mv = (await client.get(f"/api/parts/movements?part_id={part['id']}", headers=adm)).json()
    assert mv[0]["kind"] == "adjust" and mv[0]["quantity_delta"] == 2 and mv[0]["note"] == "leltár: őszi"
    # a dolgozó nem leltározhatja a központot (403), autó nélkül a sajátját sem (422)
    assert (await client.post("/api/parts/stocktake", json={"warehouse_id": central["id"], "counts": []}, headers=emp_h)).status_code == 403
    assert (await client.post("/api/parts/my-stocktake", json={"counts": [{"part_id": part["id"], "quantity": 1}]}, headers=other_h)).status_code == 422

    # szerelő számol a saját autóján: 4 → 3
    res = await client.post("/api/parts/my-stocktake", json={"counts": [{"part_id": part["id"], "quantity": 3}]}, headers=emp_h)
    assert res.status_code == 200 and res.json()["adjusted"] == 1 and res.json()["diffs"][0]["delta"] == -1
    mine = (await client.get("/api/parts/my-warehouse", headers=emp_h)).json()
    assert mine["stock"][0]["quantity"] == 3
    mv = (await client.get(f"/api/parts/movements?warehouse_id={van['id']}", headers=adm)).json()
    assert mv[0]["note"] == "leltár (szerelő)" and mv[0]["actor_name"] == "leltar"

    # CSV export: BOM + ";" + tizedes vessző; rossz what 422; dolgozónak 403
    res = await client.get("/api/parts/export.csv?what=stock", headers=adm)
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv")
    body = res.content.decode("utf-8")
    assert body.startswith("﻿Raktár;Helykód;Cikkszám") and "BOIL-TERM" in body and ";3,0;db;" in body
    assert "attachment; filename=\"alkatresz_keszlet_" in res.headers["content-disposition"]
    res = await client.get("/api/parts/export.csv?what=movements", headers=adm)
    assert res.status_code == 200 and "leltár (szerelő)" in res.content.decode("utf-8")
    res = await client.get("/api/parts/export.csv?what=parts", headers=adm)
    assert res.status_code == 200 and "Bojler-termosztát" in res.content.decode("utf-8")
    assert (await client.get("/api/parts/export.csv?what=bad", headers=adm)).status_code == 422
    assert (await client.get("/api/parts/export.csv?what=stock", headers=emp_h)).status_code == 403
