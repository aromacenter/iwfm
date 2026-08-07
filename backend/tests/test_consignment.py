"""Bizomány: termékek, partner külső raktár feltöltés, kg→adag számítás, RBAC."""

from tests.test_inventory import make_partner


async def make_product(client, headers, **kw) -> dict:
    body = {"name": "Házi keverék kávé", "grams_per_portion": 7, "price_per_portion": 50.0}
    body.update(kw)
    res = await client.post("/api/products", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


async def test_product_crud(client, manager):
    _, mgr = manager
    prod = await make_product(client, mgr, price_per_portion=60.0)
    assert prod["grams_per_portion"] == 7
    assert prod["vat_percent"] == 27

    upd = await client.patch(
        f"/api/products/{prod['id']}",
        json={"name": "Prémium kávé", "grams_per_portion": 8, "price_per_portion": 70.0},
        headers=mgr,
    )
    assert upd.status_code == 200
    assert upd.json()["name"] == "Prémium kávé"
    assert upd.json()["grams_per_portion"] == 8

    listed = (await client.get("/api/products", headers=mgr)).json()
    assert len(listed) == 1


async def test_replenish_and_portions(client, manager):
    """1 kg / 7 g/adag = 142 adag (lefelé kerekítve)."""
    _, mgr = manager
    partner = await make_partner(client, mgr)
    product = await make_product(client, mgr)

    res = await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 1.0},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["quantity"] == 1.0
    assert body["portions_available"] == 142  # floor(1000/7)

    # újabb feltöltés összeadódik
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 0.5},
        headers=mgr,
    )
    stock = (await client.get(f"/api/partners/{partner['id']}/stock", headers=mgr)).json()
    assert len(stock) == 1
    assert stock[0]["quantity"] == 1.5
    assert stock[0]["portions_available"] == 214  # floor(1500/7)


async def test_consignment_requires_manager(client, employee_user):
    _, emp_headers, _ = employee_user
    assert (await client.get("/api/products", headers=emp_headers)).status_code == 403
    assert (
        await client.post("/api/products", json={"name": "X"}, headers=emp_headers)
    ).status_code == 403


async def _setup_settlement(client, mgr, *, physical=0.3, qty=1.0):
    """Partner + termék + feltöltés + elszámolás. 1 kg-ból 0.3 kg maradt →
    0.7 kg fogyás = 100 adag (7 g/adag) × 50 Ft = 5000 Ft nettó."""
    partner = await make_partner(client, mgr, name="Kávézó Bt.")
    product = await make_product(client, mgr)
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": qty},
        headers=mgr,
    )
    res = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "cash",
            "lines": [{"product_id": product["id"], "physical_qty": physical}],
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    return partner, product, res.json()


async def test_settlement_flow(client, manager):
    _, mgr = manager
    partner, product, body = await _setup_settlement(client, mgr)

    line = body["lines"][0]
    assert line["previous_qty"] == 1.0
    assert line["physical_qty"] == 0.3
    assert abs(line["consumed_qty"] - 0.7) < 1e-9
    assert abs(line["portions"] - 100.0) < 0.01  # 700 g / 7 g
    assert abs(line["amount_net"] - 5000.0) < 0.01
    assert body["settled_by_name"]  # a bejelentkezett user neve rögzül
    assert body["payment_method"] == "cash"
    assert body["invoiced"] is False

    # a könyv szerinti készlet a fizikai leltárra állt
    stock = (await client.get(f"/api/partners/{partner['id']}/stock", headers=mgr)).json()
    assert stock[0]["quantity"] == 0.3

    # lista + részletek
    listed = (await client.get("/api/settlements", headers=mgr)).json()
    assert len(listed) == 1 and listed[0]["partner_name"] == "Kávézó Bt."
    detail = (await client.get(f"/api/settlements/{body['id']}", headers=mgr)).json()
    assert len(detail["lines"]) == 1


async def test_settlement_bad_payment_rejected(client, manager):
    _, mgr = manager
    partner = await make_partner(client, mgr, name="Rossz Kft.")
    product = await make_product(client, mgr, name="Teszt kávé")
    res = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "bitcoin",
            "lines": [{"product_id": product["id"], "physical_qty": 0}],
        },
        headers=mgr,
    )
    assert res.status_code == 422


async def test_invoice_requires_billingo_config(client, manager):
    """A „Kiszámlázott” gomb Billingó-konfiguráció nélkül 422-t ad, és a
    settlement invoiced=False marad."""
    _, mgr = manager
    _, _, body = await _setup_settlement(client, mgr)

    res = await client.post(f"/api/settlements/{body['id']}/invoice", headers=mgr)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "settings.billingo_not_configured"

    detail = (await client.get(f"/api/settlements/{body['id']}", headers=mgr)).json()
    assert detail["invoiced"] is False


async def test_billingo_settings_roundtrip(client, admin):
    _, headers = admin
    res = await client.put(
        "/api/settings/billingo",
        json={"enabled": True, "api_key": "titkos-kulcs", "block_id": 42, "test_mode": True},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["has_api_key"] is True
    assert body["test_mode"] is True
    assert "api_key" not in body  # a kulcs sosem megy vissza


async def test_agent_summary(client, manager):
    """Üzletkötő-elszámolás: agents lista + fizetési módonkénti összesítés."""
    _, mgr = manager
    await _setup_settlement(client, mgr)

    agents = (await client.get("/api/settlements/agents", headers=mgr)).json()
    assert len(agents) == 1 and agents[0]["count"] == 1

    summary = (
        await client.get(
            "/api/settlements/summary", params={"settled_by": agents[0]["user_id"]}, headers=mgr
        )
    ).json()
    assert summary["count"] == 1
    assert summary["by_payment"]["cash"]["count"] == 1
    assert abs(summary["by_payment"]["cash"]["net"] - 5000.0) < 0.01
    assert summary["by_payment"]["card"]["count"] == 0
    assert abs(summary["total_gross"] - 6350.0) < 0.01  # 5000 * 1.27
