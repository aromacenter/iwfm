"""108. kör — hibajegyek (2026-10-23): raktár-láthatóság + csak vezető kezelhet
(2b3aba3d), alkatrész automatikus cikkszámmal (dcdae1bc), bizonylat fejléce a
partner számlázó cége szerint + 50/80 mm-es nyugta (8b48f283, 11e969d6)."""
from __future__ import annotations

from tests.conftest import make_user
from tests.test_parts import _enable_modules


async def test_warehouse_visibility_and_manager_only(client, admin, manager):
    _, adm = admin
    mgr_user, mgr = manager
    uk_user, uk = await make_user(email="r108-uk@example.com", role="uzletkoto")
    uk2_user, uk2 = await make_user(email="r108-uk2@example.com", role="uzletkoto")

    # képviselő NEM hozhat létre raktárat
    res = await client.post("/api/warehouses", json={"name": "Csempész raktár"}, headers=uk)
    assert res.status_code == 403 and res.json()["detail"]["code"] == "warehouse.manage_forbidden"

    site = (await client.post("/api/warehouses", json={"name": "Telephely"}, headers=mgr)).json()
    van = (await client.post("/api/warehouses", json={"name": "UK autó", "kind": "van", "user_id": str(uk_user.id)}, headers=mgr)).json()
    van2 = (await client.post("/api/warehouses", json={"name": "UK2 autó", "kind": "van", "user_id": str(uk2_user.id)}, headers=mgr)).json()

    # vezető mindent lát; a képviselő csak a sajátját
    assert {w["id"] for w in (await client.get("/api/warehouses", headers=adm)).json()} >= {site["id"], van["id"], van2["id"]}
    assert {w["id"] for w in (await client.get("/api/warehouses", headers=uk)).json()} == {van["id"]}
    res = await client.get(f"/api/warehouses/{site['id']}/stock", headers=uk)
    assert res.status_code == 403 and res.json()["detail"]["code"] == "warehouse.forbidden"
    assert (await client.get(f"/api/warehouses/{van['id']}/stock", headers=uk)).status_code == 200

    # az admin engedélyezi a telephelyet a képviselőnek → látja, és a másik autót nem
    res = await client.patch(
        f"/api/warehouses/{site['id']}",
        json={"name": "Telephely", "kind": "site", "allowed_user_ids": [str(uk_user.id)]}, headers=adm,
    )
    assert res.status_code == 200, res.text
    assert res.json()["allowed_user_ids"] == [str(uk_user.id)] and res.json()["allowed_user_names"]
    ids = {w["id"] for w in (await client.get("/api/warehouses", headers=uk)).json()}
    assert ids == {van["id"], site["id"]}
    assert (await client.get(f"/api/warehouses/{site['id']}/stock", headers=uk)).status_code == 200
    assert (await client.get(f"/api/warehouses/{van2['id']}/stock", headers=uk)).status_code == 403
    # módosítani / törölni a képviselő akkor sem tud
    res = await client.patch(f"/api/warehouses/{site['id']}", json={"name": "Enyém"}, headers=uk)
    assert res.status_code == 403
    assert (await client.delete(f"/api/warehouses/{van['id']}", headers=uk)).status_code == 403


async def test_part_auto_sku(client, admin):
    _, adm = admin
    await _enable_modules(["warehouse", "parts"])
    a = (await client.post("/api/parts", json={"name": "Tömítés 32 mm"}, headers=adm))
    assert a.status_code == 201, a.text
    b = (await client.post("/api/parts", json={"name": "Tömítés 35 mm", "unit": "db"}, headers=adm)).json()
    assert a.json()["sku"] == "AUTO-0001" and b["sku"] == "AUTO-0002"
    # megadott cikkszám továbbra is megy, és a módosítás üres cikkszámmal a régit tartja
    c = (await client.post("/api/parts", json={"sku": "x-1", "name": "Kézi"}, headers=adm)).json()
    assert c["sku"] == "X-1"
    res = await client.patch(f"/api/parts/{c['id']}", json={"name": "Kézi 2", "min_stock": 0, "unit": "db"}, headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()["sku"] == "X-1" and res.json()["name"] == "Kézi 2"


async def test_settlement_receipt_formats_and_company_header(client, manager, monkeypatch):
    _, mgr = manager
    partner = (await client.post("/api/partners", json={"name": "Nyugtás Kft.", "invoicing_company": "pc"}, headers=mgr)).json()
    product = (await client.post("/api/products", json={"name": "Kávé R108", "price_per_portion": 50, "unit_size": 1, "is_consignment": True}, headers=mgr)).json()
    await client.post(f"/api/partners/{partner['id']}/stock/replenish", json={"product_id": product["id"], "quantity": 1.0}, headers=mgr)
    res = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash", "lines": [{"product_id": product["id"], "physical_qty": 0.5}]},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    sid = res.json()["id"]

    # a fejléc cége a partner számlázó cége szerint (pc → Premium Caffe), a cím csak xp-nél
    captured: dict = {}
    from app.services.wfm import settlement_pdf as mod

    real_a4 = mod.build_settlement_pdf

    def spy(data, settings=None, **kw):
        captured.update(settings or {})
        return real_a4(data, settings)

    monkeypatch.setattr(mod, "build_settlement_pdf", spy)  # a consignment függvény-szinten importál
    res = await client.get(f"/api/settlements/{sid}/pdf", headers=mgr)
    assert res.status_code == 200 and res.content[:4] == b"%PDF"
    assert captured.get("company_name") == "Premium Caffe Kft."
    assert captured.get("company_address") is None

    # 50 és 80 mm-es nyugta: valódi PDF, más fájlnév; rossz formátum 422
    for fmt in ("50", "80"):
        res = await client.get(f"/api/settlements/{sid}/pdf?format={fmt}", headers=mgr)
        assert res.status_code == 200, res.text
        assert res.content[:4] == b"%PDF" and f"-{fmt}mm.pdf" in res.headers["content-disposition"]
    assert (await client.get(f"/api/settlements/{sid}/pdf?format=a3", headers=mgr)).status_code == 422
