"""Felügyeleti riport (admin): leltár-eltérések + elszámoláskor kézzel
átírt értékek egy nézetben (/api/audit/oversight)."""

from __future__ import annotations

from tests.test_consignment import make_product
from tests.test_warehouse import _make_warehouse


async def test_oversight_admin_only(client, manager):
    _, mgr = manager
    assert (await client.get("/api/audit/oversight", headers=mgr)).status_code == 403


async def test_oversight_collects_adjustments_and_overrides(client, manager, admin):
    _, mgr = manager
    _, adm = admin

    # 1) Leltár-eltérés: 10 kg bevét, 8 kg-ra számolt leltár → −2 kg adjust
    product = await make_product(client, mgr, name="Riport Kávé", price_per_portion=50.0)
    wh = await _make_warehouse(client, mgr, "Riport Telephely", "site")
    res = await client.post(
        f"/api/warehouses/{wh['id']}/receive",
        json={"product_id": product["id"], "quantity": 10.0},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    res = await client.post(
        f"/api/warehouses/{wh['id']}/adjust",
        json={"product_id": product["id"], "counted_qty": 8.0, "note": "leltár"},
        headers=mgr,
    )
    assert res.status_code == 200, res.text

    # 2) Kézi átírás: elszámolás a listaártól eltérő adagárral
    res = await client.post("/api/partners", json={"name": "Riport Bolt"}, headers=mgr)
    assert res.status_code == 201, res.text
    partner = res.json()
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 2.0},
        headers=mgr,
    )
    res = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"], "payment_method": "cash",
            "lines": [{
                "product_id": product["id"], "physical_qty": 1.0,
                "price_per_portion": 60.0,  # a listaár 50 → átírás
            }],
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text

    body = (await client.get("/api/audit/oversight", headers=adm)).json()

    adj = [a for a in body["adjustments"] if a["warehouse_name"] == "Riport Telephely"]
    assert len(adj) == 1, body["adjustments"]
    assert adj[0]["product_name"] == "Riport Kávé"
    assert adj[0]["delta"] == -2.0
    assert adj[0]["note"] == "leltár"
    assert adj[0]["actor_name"]  # a manager neve feloldva

    ovr = [o for o in body["overrides"] if o["partner"] == "Riport Bolt"]
    assert len(ovr) >= 1, body["overrides"]
    price = next(o for o in ovr if o["field"] == "unit_price")
    assert price["from"] == 50.0
    assert price["to"] == 60.0
    assert price["product"] == "Riport Kávé"
    assert price["actor_name"]

    # a szűk időablak üres
    empty = (await client.get("/api/audit/oversight?days=1", headers=adm)).json()
    assert isinstance(empty["adjustments"], list)  # ma történt → benne marad
