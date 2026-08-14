"""Raktárlánc: raktárak, mozgások, beszerzési rendelések, partner-feltöltés
forrás-raktárból."""

from __future__ import annotations


async def _make_product(client, headers, name="Raktár Kávé", price=50.0):
    res = await client.post(
        "/api/products",
        json={"name": name, "grams_per_portion": 7, "price_per_portion": price},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _make_warehouse(client, headers, name="Telephely", kind="site"):
    res = await client.post(
        "/api/warehouses", json={"name": name, "kind": kind}, headers=headers
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_warehouse_receive_transfer_adjust(client, manager):
    """Bevét → áthelyezés autóra → elégtelen készlet → leltár-korrekció."""
    _, mgr = manager
    product = await _make_product(client, mgr)
    site = await _make_warehouse(client, mgr, "Telephely", "site")
    van = await _make_warehouse(client, mgr, "Uzletkötő autó", "van")

    # bevételezés 20 kg, 4200 Ft/kg
    res = await client.post(
        f"/api/warehouses/{site['id']}/receive",
        json={"product_id": product["id"], "quantity": 20.0, "unit_cost": 4200},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["quantity"] == 20.0

    # a beszerzési ár a terméken frissült
    res = await client.get("/api/products", headers=mgr)
    prod = next(p for p in res.json() if p["id"] == product["id"])
    assert prod["purchase_price"] == 4200

    # áthelyezés 8 kg az autóra
    res = await client.post(
        "/api/warehouses/transfer",
        json={
            "from_warehouse_id": site["id"], "to_warehouse_id": van["id"],
            "product_id": product["id"], "quantity": 8.0,
        },
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["from_quantity"] == 12.0
    assert res.json()["to_quantity"] == 8.0

    # elégtelen készlet: 100 kg nem mozgatható
    res = await client.post(
        "/api/warehouses/transfer",
        json={
            "from_warehouse_id": site["id"], "to_warehouse_id": van["id"],
            "product_id": product["id"], "quantity": 100.0,
        },
        headers=mgr,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "warehouse.insufficient_stock"

    # azonos raktárba nem lehet áthelyezni
    res = await client.post(
        "/api/warehouses/transfer",
        json={
            "from_warehouse_id": site["id"], "to_warehouse_id": site["id"],
            "product_id": product["id"], "quantity": 1.0,
        },
        headers=mgr,
    )
    assert res.status_code == 422

    # leltár: valójában csak 11.5 kg van a telephelyen + küszöb 5 kg
    res = await client.post(
        f"/api/warehouses/{site['id']}/adjust",
        json={"product_id": product["id"], "counted_qty": 11.5, "min_quantity": 5.0},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["quantity"] == 11.5

    # készletlista
    res = await client.get(f"/api/warehouses/{site['id']}/stock", headers=mgr)
    rows = res.json()
    assert len(rows) == 1 and rows[0]["quantity"] == 11.5 and rows[0]["min_quantity"] == 5.0

    # raktárlista összesítőkkel
    res = await client.get("/api/warehouses", headers=mgr)
    names = {w["name"]: w for w in res.json()}
    assert names["Telephely"]["total_quantity"] == 11.5
    assert names["Uzletkötő autó"]["total_quantity"] == 8.0


async def test_stock_mirrors_product_catalog(client, manager):
    """A raktár készletnézete mindig a termék-katalógust tükrözi: új termék
    azonnal megjelenik 0 készlettel (folyamatos szinkron)."""
    _, mgr = manager
    site = await _make_warehouse(client, mgr)
    await _make_product(client, mgr, "Kávé A")
    res = await client.get(f"/api/warehouses/{site['id']}/stock", headers=mgr)
    assert [r["product_name"] for r in res.json()] == ["Kávé A"]
    assert res.json()[0]["quantity"] == 0.0

    # utólag felvett termék is automatikusan bekerül
    await _make_product(client, mgr, "Kávé B")
    res = await client.get(f"/api/warehouses/{site['id']}/stock", headers=mgr)
    assert [r["product_name"] for r in res.json()] == ["Kávé A", "Kávé B"]


async def test_reorder_suggestions(client, manager):
    """Küszöb alatti készletre javaslat készül (küszöb kétszereséig)."""
    _, mgr = manager
    product = await _make_product(client, mgr)
    site = await _make_warehouse(client, mgr)
    await client.post(
        f"/api/warehouses/{site['id']}/receive",
        json={"product_id": product["id"], "quantity": 3.0},
        headers=mgr,
    )
    await client.post(
        f"/api/warehouses/{site['id']}/adjust",
        json={"product_id": product["id"], "counted_qty": 3.0, "min_quantity": 10.0},
        headers=mgr,
    )
    res = await client.get("/api/warehouses/reorder-suggestions", headers=mgr)
    assert res.status_code == 200, res.text
    rows = res.json()
    assert len(rows) == 1
    assert rows[0]["product_name"] == product["name"]
    assert rows[0]["suggested_qty"] == 17.0  # 2×10 − 3


async def test_purchase_order_flow(client, manager):
    """PO: piszkozat → megrendelve → beérkeztetés → raktárkészlet + státusz."""
    _, mgr = manager
    product = await _make_product(client, mgr)
    site = await _make_warehouse(client, mgr)
    res = await client.post(
        "/api/partners",
        json={"name": "Kávé Nagyker Kft.", "partner_type": "supplier"},
        headers=mgr,
    )
    supplier = res.json()

    res = await client.post(
        "/api/purchase-orders",
        json={
            "supplier_partner_id": supplier["id"], "warehouse_id": site["id"],
            "expected_at": "2026-08-20",
            "lines": [{"product_id": product["id"], "quantity": 25.0, "unit_cost": 4100}],
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    po = res.json()
    assert po["order_no"].startswith("PO-") and po["status"] == "draft"
    assert po["supplier_name"] == "Kávé Nagyker Kft."

    res = await client.post(f"/api/purchase-orders/{po['id']}/order", json={}, headers=mgr)
    assert res.status_code == 200 and res.json()["status"] == "ordered"

    # teljes beérkeztetés
    res = await client.post(f"/api/purchase-orders/{po['id']}/receive", json={}, headers=mgr)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "received"
    assert data["lines"][0]["received_qty"] == 25.0

    # a raktárba került + beszerzési ár frissült
    res = await client.get(f"/api/warehouses/{site['id']}/stock", headers=mgr)
    assert res.json()[0]["quantity"] == 25.0
    res = await client.get("/api/products", headers=mgr)
    prod = next(p for p in res.json() if p["id"] == product["id"])
    assert prod["purchase_price"] == 4100

    # beérkezett rendelés már nem mondható le
    res = await client.post(f"/api/purchase-orders/{po['id']}/cancel", json={}, headers=mgr)
    assert res.status_code == 422


async def test_replenish_from_warehouse(client, manager):
    """Partner-feltöltés forrás-raktárból: a raktárkészlet csökken; elégtelen
    készletnél 422 és a partner-készlet sem változik."""
    _, mgr = manager
    product = await _make_product(client, mgr)
    van = await _make_warehouse(client, mgr, "Autó", "van")
    await client.post(
        f"/api/warehouses/{van['id']}/receive",
        json={"product_id": product["id"], "quantity": 10.0},
        headers=mgr,
    )
    res = await client.post("/api/partners", json={"name": "Feltöltős Bolt"}, headers=mgr)
    partner = res.json()

    res = await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={
            "product_id": product["id"], "quantity": 4.0,
            "source_warehouse_id": van["id"],
        },
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["quantity"] == 4.0  # partner-készlet

    res = await client.get(f"/api/warehouses/{van['id']}/stock", headers=mgr)
    assert res.json()[0]["quantity"] == 6.0  # 10 − 4

    # elégtelen raktárkészlet → 422, semmi nem változik
    res = await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={
            "product_id": product["id"], "quantity": 50.0,
            "source_warehouse_id": van["id"],
        },
        headers=mgr,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "warehouse.insufficient_stock"
    res = await client.get(f"/api/partners/{partner['id']}/stock", headers=mgr)
    assert res.json()[0]["quantity"] == 4.0
    res = await client.get(f"/api/warehouses/{van['id']}/stock", headers=mgr)
    assert res.json()[0]["quantity"] == 6.0

    # forrás-raktár nélkül a régi viselkedés változatlan
    res = await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 2.0},
        headers=mgr,
    )
    assert res.status_code == 200
    assert res.json()["quantity"] == 6.0