"""Veszélyzóna: teljes törlés entitásonként (csak admin, TORLES megerősítéssel)."""

from __future__ import annotations


async def _seed(client, mgr) -> dict:
    """Partner + termék + készlet + elszámolás + kihelyezett gép."""
    from tests.test_consignment import make_product
    from tests.test_service import make_asset

    partner = (
        await client.post("/api/partners", json={"name": "Wipe Teszt"}, headers=mgr)
    ).json()
    product = await make_product(client, mgr)
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 1.0},
        headers=mgr,
    )
    settlement = (
        await client.post(
            "/api/settlements",
            json={
                "partner_id": partner["id"],
                "payment_method": "cash",
                "lines": [{"product_id": product["id"], "physical_qty": 0.5}],
            },
            headers=mgr,
        )
    ).json()
    asset = await make_asset(client, mgr, barcode="WIPE-1")
    await client.post(
        f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr
    )
    return {"partner": partner, "product": product, "settlement": settlement, "asset": asset}


async def test_wipe_requires_admin_and_confirm(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    # nem admin → 403
    res = await client.post(
        "/api/admin/wipe", json={"entity": "assets", "confirm": "TORLES"}, headers=mgr
    )
    assert res.status_code == 403
    # rossz megerősítő szó → 422
    res = await client.post(
        "/api/admin/wipe", json={"entity": "assets", "confirm": "torles?"}, headers=adm
    )
    assert res.status_code == 422
    # ismeretlen entitás → 422
    res = await client.post(
        "/api/admin/wipe", json={"entity": "users", "confirm": "TORLES"}, headers=adm
    )
    assert res.status_code == 422


async def test_wipe_assets_products_partners(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    await _seed(client, mgr)

    counts = (await client.get("/api/admin/wipe/counts", headers=adm)).json()
    assert counts["assets"] == 1 and counts["products"] == 1
    assert counts["partners"] == 1 and counts["settlements"] == 1

    # gépek
    res = await client.post(
        "/api/admin/wipe", json={"entity": "assets", "confirm": "TORLES"}, headers=adm
    )
    assert res.status_code == 200 and res.json()["deleted"] == 1
    assert (await client.get("/api/assets", headers=mgr)).json() == []

    # termékek (készlet + ármozgás is takarítva)
    res = await client.post(
        "/api/admin/wipe", json={"entity": "products", "confirm": "TORLES"}, headers=adm
    )
    assert res.status_code == 200 and res.json()["deleted"] == 1
    assert (await client.get("/api/products", headers=mgr)).json() == []

    # partnerek → elszámolás-előzmény is törlődik
    res = await client.post(
        "/api/admin/wipe", json={"entity": "partners", "confirm": "TORLES"}, headers=adm
    )
    assert res.status_code == 200 and res.json()["deleted"] == 1
    assert (await client.get("/api/partners", headers=mgr)).json() == []
    assert (await client.get("/api/settlements", headers=mgr)).json() == []

    counts = (await client.get("/api/admin/wipe/counts", headers=adm)).json()
    assert counts == {"assets": 0, "products": 0, "partners": 0, "settlements": 0}


async def test_wipe_partners_returns_deployed_assets_to_stock(client, admin, manager):
    """Partner-törléskor a kihelyezett gép raktárira áll, nem vész el."""
    _, adm = admin
    _, mgr = manager
    seeded = await _seed(client, mgr)

    res = await client.post(
        "/api/admin/wipe", json={"entity": "partners", "confirm": "TORLES"}, headers=adm
    )
    assert res.status_code == 200
    detail = (
        await client.get(f"/api/assets/{seeded['asset']['id']}", headers=mgr)
    ).json()
    assert detail["status"] == "in_stock"
    assert detail["partner_id"] is None
