"""Alkatrész-raktár (parts modul, A fázis): raktár helykódokkal, cikktörzs,
vételezés / áthelyezés / korrekció, készlet és mozgásnapló, modulkapcsoló."""
from __future__ import annotations


async def _enable_modules(modules):
    import app.db as app_db
    from app.models import LicenseSettings
    from app.services.wfm import license as license_service

    factory = app_db.get_session_factory()
    async with factory() as session:
        await session.merge(LicenseSettings(id=1, plan="xl", enabled_modules=modules))
        await session.commit()
    license_service.invalidate_cache()


async def test_parts_flow(client, admin):
    _, adm = admin
    # raktár oszlopokkal/sorokkal; rossz oszlopnév 422
    res = await client.post("/api/parts/warehouses", json={"name": "Főraktár", "columns": ["a", "b", "zz"], "rows": 40}, headers=adm)
    assert res.status_code == 201, res.text
    wh = res.json()
    assert wh["columns"] == ["A", "B", "ZZ"] and wh["rows"] == 40
    res = await client.post("/api/parts/warehouses", json={"name": "Rossz", "columns": ["A-1"]}, headers=adm)
    assert res.status_code == 422
    van = (await client.post("/api/parts/warehouses", json={"name": "Szerelő autó", "kind": "vehicle", "columns": [], "rows": 1}, headers=adm)).json()

    # cikktörzs; duplikált SKU 409
    res = await client.post("/api/parts", json={"sku": "szel-01", "name": "Szelep 3/8", "unit": "db", "min_stock": 2, "price_net": 4500}, headers=adm)
    assert res.status_code == 201, res.text
    part = res.json()
    assert part["sku"] == "SZEL-01" and part["low"] is True
    assert (await client.post("/api/parts", json={"sku": "SZEL-01", "name": "dupla"}, headers=adm)).status_code == 409

    # vételezés rossz helykódra 422, jóra OK; cost_net frissül
    res = await client.post("/api/parts/receipt", json={"part_id": part["id"], "warehouse_id": wh["id"], "location": "C-3", "quantity": 5}, headers=adm)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "parts.bad_location"
    res = await client.post("/api/parts/receipt", json={"part_id": part["id"], "warehouse_id": wh["id"], "location": "b-12", "quantity": 5, "unit_cost": 3100, "document_no": "SZL-2026-17"}, headers=adm)
    assert res.status_code == 201, res.text
    assert res.json()["location"] == "B-12" and res.json()["quantity"] == 5
    lst = (await client.get("/api/parts", headers=adm)).json()
    row = next(p for p in lst if p["id"] == part["id"])
    assert row["total_qty"] == 5 and row["low"] is False and row["cost_net"] == 3100

    # áthelyezés a szerelő autójára: túl sok 409, 2 db OK; készlet mindkét helyen
    res = await client.post("/api/parts/transfer", json={"part_id": part["id"], "from_warehouse_id": wh["id"], "from_location": "B-12", "to_warehouse_id": van["id"], "quantity": 9}, headers=adm)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "parts.insufficient_stock"
    res = await client.post("/api/parts/transfer", json={"part_id": part["id"], "from_warehouse_id": wh["id"], "from_location": "B-12", "to_warehouse_id": van["id"], "quantity": 2}, headers=adm)
    assert res.status_code == 201 and res.json()["to_quantity"] == 2
    stock = (await client.get("/api/parts/stock", headers=adm)).json()
    by = {(s["warehouse_id"], s["location"]): s["quantity"] for s in stock}
    assert by[(wh["id"], "B-12")] == 3 and by[(van["id"], "")] == 2

    # a használt helykód oszlopát nem lehet kivenni (409), bővíteni lehet
    res = await client.patch(f"/api/parts/warehouses/{wh['id']}", json={"name": "Főraktár", "columns": ["A"], "rows": 40}, headers=adm)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "parts.location_in_use"
    res = await client.patch(f"/api/parts/warehouses/{wh['id']}", json={"name": "Főraktár", "columns": ["A", "B", "ZZ", "C"], "rows": 60}, headers=adm)
    assert res.status_code == 200 and res.json()["rows"] == 60 and res.json()["part_count"] == 1

    # leltári korrekció 3 → 2.5 (adjust −0.5)
    res = await client.post("/api/parts/adjust", json={"part_id": part["id"], "warehouse_id": wh["id"], "location": "B-12", "quantity": 2.5, "note": "leltár"}, headers=adm)
    assert res.status_code == 200 and res.json()["quantity"] == 2.5

    # mozgásnapló: receipt, 2× transfer, adjust
    mv = (await client.get(f"/api/parts/movements?part_id={part['id']}", headers=adm)).json()
    assert [m["kind"] for m in mv] == ["adjust", "transfer", "transfer", "receipt"]
    assert mv[-1]["document_no"] == "SZL-2026-17" and mv[-1]["unit_cost"] == 3100
    t_out = next(m for m in mv if m["kind"] == "transfer" and m["quantity_delta"] < 0)
    assert t_out["ref_warehouse_name"] == "Szerelő autó"

    # nem üres raktár nem törölhető
    res = await client.delete(f"/api/parts/warehouses/{wh['id']}", headers=adm)
    assert res.status_code == 409


async def test_parts_module_switch(client, admin):
    _, adm = admin
    await _enable_modules(["warehouse"])  # parts nincs bekapcsolva
    res = await client.get("/api/parts", headers=adm)
    assert res.status_code == 403 and res.json()["detail"]["code"] == "license.module_disabled"
    await _enable_modules(["warehouse", "parts"])
    assert (await client.get("/api/parts", headers=adm)).status_code == 200
