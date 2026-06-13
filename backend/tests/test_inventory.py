"""Készlet: partnerek, eszközök, vonalkód, kihelyezés/visszavétel, mozgástörténet."""


async def make_partner(client, headers, name="Minta Kft.") -> dict:
    res = await client.post(
        "/api/partners",
        json={"name": name, "contact_name": "Kis Pál", "contact_email": "p@example.com"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


def asset_payload(**kw) -> dict:
    base = {"barcode": "ESZ-000123", "name": "Eszpresszó gép", "category": "kávégép",
            "model": "X200", "serial_number": "SN-9988"}
    base.update(kw)
    return base


async def test_create_asset_unique_barcode(client, manager):
    _, mgr = manager
    res = await client.post("/api/assets", json=asset_payload(), headers=mgr)
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "in_stock"
    assert res.json()["barcode"] == "ESZ-000123"
    # ugyanaz a vonalkód ütközik
    dup = await client.post("/api/assets", json=asset_payload(name="Másik"), headers=mgr)
    assert dup.status_code == 409
    assert dup.json()["detail"]["code"] == "asset.barcode_taken"


async def test_generate_barcode_increments(client, manager):
    _, mgr = manager
    g1 = (await client.get("/api/assets/generate-barcode", headers=mgr)).json()["barcode"]
    assert g1 == "ESZ-000001"
    await client.post("/api/assets", json=asset_payload(barcode=g1), headers=mgr)
    g2 = (await client.get("/api/assets/generate-barcode", headers=mgr)).json()["barcode"]
    assert g2 == "ESZ-000002"


async def test_barcode_lookup(client, manager):
    _, mgr = manager
    await client.post("/api/assets", json=asset_payload(barcode="SCAN-1"), headers=mgr)
    found = await client.get("/api/assets/by-barcode/SCAN-1", headers=mgr)
    assert found.status_code == 200
    assert found.json()["name"] == "Eszpresszó gép"
    miss = await client.get("/api/assets/by-barcode/NOPE", headers=mgr)
    assert miss.status_code == 404
    assert miss.json()["detail"]["code"] == "asset.barcode_not_found"


async def test_deploy_and_return_with_history(client, manager):
    _, mgr = manager
    partner = await make_partner(client, mgr)
    asset = (await client.post("/api/assets", json=asset_payload(), headers=mgr)).json()

    dep = await client.post(
        f"/api/assets/{asset['id']}/deploy",
        json={"partner_id": partner["id"], "note": "telephelyre"},
        headers=mgr,
    )
    assert dep.status_code == 200
    assert dep.json()["status"] == "deployed"
    assert dep.json()["partner_name"] == "Minta Kft."
    assert dep.json()["deployed_at"] is not None

    ret = await client.post(f"/api/assets/{asset['id']}/return", json={}, headers=mgr)
    assert ret.status_code == 200
    assert ret.json()["status"] == "in_stock"
    assert ret.json()["partner_id"] is None

    detail = (await client.get(f"/api/assets/{asset['id']}", headers=mgr)).json()
    actions = [m["action"] for m in detail["movements"]]
    assert "created" in actions and "deploy" in actions and "return" in actions
    # a kihelyezés mozgás megőrzi a partnert
    deploy_move = next(m for m in detail["movements"] if m["action"] == "deploy")
    assert deploy_move["partner_name"] == "Minta Kft."


async def test_cannot_return_if_not_deployed(client, manager):
    _, mgr = manager
    asset = (await client.post("/api/assets", json=asset_payload(), headers=mgr)).json()
    res = await client.post(f"/api/assets/{asset['id']}/return", json={}, headers=mgr)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "asset.not_deployed"


async def test_partner_asset_count_and_filter(client, manager):
    _, mgr = manager
    partner = await make_partner(client, mgr)
    a1 = (await client.post("/api/assets", json=asset_payload(barcode="A1"), headers=mgr)).json()
    await client.post("/api/assets", json=asset_payload(barcode="A2"), headers=mgr)
    await client.post(
        f"/api/assets/{a1['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr
    )

    partners = (await client.get("/api/partners", headers=mgr)).json()
    assert partners[0]["asset_count"] == 1

    deployed = (await client.get("/api/assets?status=deployed", headers=mgr)).json()
    assert len(deployed) == 1 and deployed[0]["barcode"] == "A1"
    by_partner = (await client.get(f"/api/assets?partner_id={partner['id']}", headers=mgr)).json()
    assert len(by_partner) == 1


async def test_search_assets(client, manager):
    _, mgr = manager
    await client.post("/api/assets", json=asset_payload(barcode="X1", serial_number="ABC-777"), headers=mgr)
    await client.post("/api/assets", json=asset_payload(barcode="X2", name="Fúrógép"), headers=mgr)
    by_serial = (await client.get("/api/assets?q=777", headers=mgr)).json()
    assert len(by_serial) == 1
    by_name = (await client.get("/api/assets?q=fúró", headers=mgr)).json()
    assert len(by_name) == 1


async def test_status_change_clears_deployment(client, manager):
    _, mgr = manager
    partner = await make_partner(client, mgr)
    asset = (await client.post("/api/assets", json=asset_payload(), headers=mgr)).json()
    await client.post(
        f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr
    )
    # szervizbe állítás → kihelyezés megszűnik
    res = await client.patch(
        f"/api/assets/{asset['id']}", json={"status": "maintenance"}, headers=mgr
    )
    assert res.status_code == 200
    assert res.json()["status"] == "maintenance"
    assert res.json()["partner_id"] is None
    # 'deployed' nem állítható PATCH-csel
    bad = await client.patch(
        f"/api/assets/{asset['id']}", json={"status": "deployed"}, headers=mgr
    )
    assert bad.status_code == 422


async def test_employee_cannot_access_inventory(client, employee_user):
    _, emp_headers, _ = employee_user
    assert (await client.get("/api/assets", headers=emp_headers)).status_code == 403
    assert (await client.get("/api/partners", headers=emp_headers)).status_code == 403
    assert (
        await client.post("/api/assets", json=asset_payload(), headers=emp_headers)
    ).status_code == 403


async def test_partner_crud(client, manager):
    _, mgr = manager
    partner = await make_partner(client, mgr)
    upd = await client.patch(
        f"/api/partners/{partner['id']}",
        json={"name": "Átnevezett Kft.", "is_active": False},
        headers=mgr,
    )
    assert upd.status_code == 200
    assert upd.json()["name"] == "Átnevezett Kft."
    assert upd.json()["is_active"] is False


async def test_partner_full_fields(client, manager):
    _, mgr = manager
    body = {
        "name": "Teljes Kft.",
        "partner_type": "supplier",
        "tax_number": "12345678-2-42",
        "eu_tax_number": "HU12345678",
        "reg_number": "01-09-123456",
        "website": "https://teljes.hu",
        "billing_address": "1051 Budapest, Fő utca 1.",
        "bank_account": "11773016-12345678-00000000",
        "payment_terms_days": 30,
    }
    res = await client.post("/api/partners", json=body, headers=mgr)
    assert res.status_code == 201, res.text
    out = res.json()
    for key, value in body.items():
        assert out[key] == value
    # default partner_type
    plain = await make_partner(client, mgr, name="Alap Kft.")
    assert plain["partner_type"] == "customer"


async def test_partner_bad_type(client, manager):
    _, mgr = manager
    res = await client.post(
        "/api/partners", json={"name": "X", "partner_type": "nope"}, headers=mgr
    )
    assert res.status_code == 422
