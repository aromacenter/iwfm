"""94. kor: aru-igenyles folyamat, admin-kassza leadas-szabaly,
statisztika-drill, intake-szures szervizesnek."""

from __future__ import annotations


async def test_stock_request_flow(client, manager, admin):
    """7bd7dbd8: igenyles -> jovahagyas -> fuggo atadas -> elfogadas."""
    from tests.conftest import make_user

    _, adm = admin
    _, mgr = manager
    rep_user, rep_hdr = await make_user(email="igenylo@example.com", role="uzletkoto")

    # kozponti telephely + a kepviselo autoja
    site = (await client.post("/api/warehouses", json={"name": "Központ"}, headers=mgr)).json()
    van = (
        await client.post(
            "/api/warehouses",
            json={"name": "Igénylő autója", "kind": "van", "user_id": str(rep_user.id)},
            headers=mgr,
        )
    ).json()
    from tests.test_consignment import make_product

    product = await make_product(client, mgr, price_per_portion=50.0)
    await client.post(
        f"/api/warehouses/{site['id']}/stock/add",
        json={"product_id": product["id"]},
        headers=mgr,
    )
    await client.post(
        f"/api/warehouses/{site['id']}/adjust",
        json={"product_id": product["id"], "counted_qty": 20.0},
        headers=mgr,
    )

    # sajat raktar nezet (ures) + igenyles
    res = await client.get("/api/stock-requests/my-warehouse", headers=rep_hdr)
    assert res.status_code == 200, res.text
    assert res.json()["warehouse_id"] == van["id"]
    res = await client.post(
        "/api/stock-requests",
        json={"product_id": product["id"], "quantity": 5.0, "note": "hétvégére"},
        headers=rep_hdr,
    )
    assert res.status_code == 201, res.text
    req = res.json()
    assert req["status"] == "pending"

    # tul nagy igenyles jovahagyasa: keszlet-vedelem
    big = (
        await client.post(
            "/api/stock-requests",
            json={"product_id": product["id"], "quantity": 999.0},
            headers=rep_hdr,
        )
    ).json()
    res = await client.post(f"/api/stock-requests/{big['id']}/approve", json={}, headers=adm)
    assert res.status_code == 422
    assert "insufficient" in res.text

    # jovahagyas -> az aru AZONNAL a kepviselo raktaraban (e2a7fb7a: o kerte,
    # nem kell meg egyszer elfogadnia), forras csokken
    res = await client.post(f"/api/stock-requests/{req['id']}/approve", json={}, headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "approved"
    site_stock = (await client.get(f"/api/warehouses/{site['id']}/stock", headers=mgr)).json()
    assert site_stock[0]["quantity"] == 15.0

    mywh = (await client.get("/api/stock-requests/my-warehouse", headers=rep_hdr)).json()
    assert mywh["incoming"] == []  # nincs fuggo atadas
    assert mywh["stock"][0]["quantity"] == 5.0


async def test_admin_transfer_is_sink(client, manager, admin):
    """b5a36526: adminhoz leadott (elfogadott) penz nem noveli az admin
    kasszajat; managernel igen."""
    from tests.conftest import make_user

    admin_user, adm = admin
    _, mgr_hdr = manager
    rep_user, rep_hdr = await make_user(email="leado@example.com", role="uzletkoto")

    # kepviselo -> admin leadas
    res = await client.post(
        "/api/stats/cash/transfer",
        json={"to_user_id": str(admin_user.id), "amount": 7000, "note": "leadás"},
        headers=rep_hdr,
    )
    assert res.status_code in (200, 201), res.text
    tid = res.json()["id"]
    res = await client.post(f"/api/stats/cash/transfers/{tid}/decide?accept=true", headers=adm)
    assert res.status_code == 200, res.text

    admin_reg = (await client.get("/api/stats/cash/me", headers=adm)).json()
    assert admin_reg["transfers_in"] == 0  # adminnal a leadas VEGALLOMAS
    rep_reg = (await client.get("/api/stats/cash/me", headers=rep_hdr)).json()
    assert rep_reg["transfers_out"] == 7000


async def test_business_drill(client, manager):
    """84452fba: a dolgozonkenti osszegek mogotti tetelek lekerdezhetok."""
    from tests.test_machine_settlement import _partner
    from tests.test_consignment import make_product

    user, mgr = manager
    partner = await _partner(client, mgr, "Drill Bolt")
    product = await make_product(client, mgr, price_per_portion=100.0)
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 5.0},
        headers=mgr,
    )
    res = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash",
              "lines": [{"product_id": product["id"], "physical_qty": 4.0}]},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    name = res.json()["settled_by_name"]
    rows = (
        await client.get(f"/api/stats/business/drill?kind=revenue&name={name}", headers=mgr)
    ).json()
    assert any(r["partner_name"] == "Drill Bolt" for r in rows)


async def test_intake_list_filtered_for_szervizes(client, manager):
    """eef46eb6: a kulsos szervizes csak a sajat atveteleit latja."""
    from tests.conftest import make_employee_record, make_user

    _, mgr = manager
    sz1_user, sz1 = await make_user(email="szerviz-a@example.com", role="szervizes")
    sz2_user, sz2 = await make_user(email="szerviz-b@example.com", role="szervizes")
    await make_employee_record(sz1_user)
    await make_employee_record(sz2_user)

    res = await client.post(
        "/api/assets",
        json={"barcode": "INTAKE-SAJAT", "name": "Sajat gep", "customer_owned": True},
        headers=mgr,
    )
    asset = res.json()
    res = await client.post(
        "/api/intakes",
        json={"asset_id": asset["id"], "client_name": "Sajat Ugyfel",
              "client_phone": "+36301234567", "client_email": "sajat@ugyfel.hu"},
        headers=sz1,
    )
    assert res.status_code in (200, 201), res.text

    own = (await client.get("/api/intakes", headers=sz1)).json()
    assert any(r["client_name"] == "Sajat Ugyfel" for r in own)
    other = (await client.get("/api/intakes", headers=sz2)).json()
    assert not any(r["client_name"] == "Sajat Ugyfel" for r in other)
    # a vezeto mindent lat
    all_rows = (await client.get("/api/intakes", headers=mgr)).json()
    assert any(r["client_name"] == "Sajat Ugyfel" for r in all_rows)
