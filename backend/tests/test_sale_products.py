"""Csak a kávé bizományos: minden más termék elszámoláskor (átadáskor)
azonnal fizetendő — a sor az elszámolásra kerül, készletbe nem megy."""

from __future__ import annotations

from tests.test_consignment import make_product


async def test_product_consignment_flag_roundtrip(client, manager):
    _, mgr = manager
    coffee = await make_product(client, mgr, name="Bizomány Kávé", is_consignment=True)
    cups = await make_product(
        client, mgr, name="Pohár", unit="db", is_consignment=False,
        price_per_portion=25.0,
    )
    assert coffee["is_consignment"] is True
    assert cups["is_consignment"] is False


async def test_sale_handover_billed_immediately(client, manager):
    _, mgr = manager
    coffee = await make_product(
        client, mgr, name="Eladás Kávé", is_consignment=True, price_per_portion=50.0
    )
    cups = await make_product(
        client, mgr, name="Eladás Pohár", unit="db", is_consignment=False,
        price_per_portion=25.0, vat_percent=27,
    )
    res = await client.post("/api/partners", json={"name": "Eladás Bolt"}, headers=mgr)
    partner = res.json()
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": coffee["id"], "quantity": 2.0},
        headers=mgr,
    )

    # Elszámolás: kávé-leltár + átadás MINDKÉT termékből
    res = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"], "payment_method": "cash",
            "lines": [{"product_id": coffee["id"], "physical_qty": 1.3}],
            "handovers": [
                {"product_id": coffee["id"], "quantity": 3.0},
                {"product_id": cups["id"], "quantity": 100, "price": 20.0},  # átírt ár
            ],
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text

    detail_id = (
        await client.get(f"/api/settlements?partner_id={partner['id']}", headers=mgr)
    ).json()[0]["id"]
    full = (await client.get(f"/api/settlements/{detail_id}", headers=mgr)).json()

    # A pohár azonnal számlázódott: 100 db × 20 Ft = 2000 Ft nettó
    sale = [ln for ln in full["lines"] if "Eladás Pohár" in ln["product_name"]]
    assert len(sale) == 1, full["lines"]
    assert "átadva" in sale[0]["product_name"]
    assert sale[0]["amount_net"] == 2000.0
    # a kávé fogyása is számlázódott (0,7 kg = 100 adag × 50 Ft)
    coffee_line = [
        ln for ln in full["lines"] if ln["product_name"] == "Eladás Kávé"
    ]
    assert len(coffee_line) == 1
    # végösszeg: kávé 5000 + pohár 2000 nettó
    assert full["total_net"] == 7000.0

    # Készlet: a kávé-átadás NÖVELTE (1,3 + 3,0), a pohár NEM került készletbe
    stock = (
        await client.get(f"/api/partners/{partner['id']}/stock", headers=mgr)
    ).json()
    by_name = {s["product_name"]: s for s in stock}
    assert by_name["Eladás Kávé"]["quantity"] == 4.3
    assert "Eladás Pohár" not in by_name

    # Az ár-átírás auditálódott (25 → 20) és a felügyeleti riportban látszik
    from tests.conftest import make_user

    _, adm = await make_user(email="ovadmin@example.com", role="admin")
    body = (await client.get("/api/audit/oversight", headers=adm)).json()
    ovr = [
        o for o in body["overrides"]
        if o["partner"] == "Eladás Bolt" and o["product"] == "Eladás Pohár"
    ]
    assert len(ovr) == 1
    assert ovr[0]["from"] == 25.0 and ovr[0]["to"] == 20.0
    assert ovr[0]["target"] == "átadás"
