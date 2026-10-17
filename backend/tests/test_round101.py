"""101. kor: szerzodott kave vedelme, default-kave fallback, mozgas-aktor,
task-szintu szerelo-kiadas, gep-tortenet vegpont."""

from __future__ import annotations

from tests.test_inventory import asset_payload, make_partner


async def test_contracted_coffee_protected(client, manager):
    """6b8271f4: a kihelyezett gep alaptermeke nem torolheto a keszletbol."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await make_partner(client, mgr, name="Védett Kft.")
    coffee = await make_product(client, mgr, price_per_portion=100.0)
    asset = (
        await client.post(
            "/api/assets",
            json=asset_payload(barcode="77700011122", default_product_id=coffee["id"]),
            headers=mgr,
        )
    ).json()
    await client.post(
        f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr,
    )
    # keszlet-sor a kavebol
    await client.post(
        f"/api/partners/{partner['id']}/stock/add",
        json={"product_id": coffee["id"]},
        headers=mgr,
    )
    res = await client.delete(
        f"/api/partners/{partner['id']}/stock/{coffee['id']}", headers=mgr,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "stock.contracted_protected"


async def test_movement_actor_and_machine_history(client, manager):
    """230771e0 + 5404e465: a mozgastortenetben latszik KI csinalta; a
    statisztika gep-tortenet vegpontja mukodik."""
    _, mgr = manager
    partner = await make_partner(client, mgr, name="Történet Kft.")
    asset = (
        await client.post("/api/assets", json=asset_payload(barcode="77700033344"), headers=mgr)
    ).json()
    await client.post(
        f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr,
    )
    await client.post(f"/api/assets/{asset['id']}/return", json={}, headers=mgr)

    detail = (await client.get(f"/api/assets/{asset['id']}", headers=mgr)).json()
    ret = next(m for m in detail["movements"] if m["action"] == "return")
    assert ret["actor_name"]  # ki csinálta — látszik

    hist = (
        await client.get("/api/stats/machine-history?barcode=77700033344", headers=mgr)
    ).json()
    assert hist["barcode"] == "77700033344"
    assert hist["settlements"] == [] and hist["service"] == []


async def test_task_technician_assignment(client, admin):
    """87994b7c: a ceg kiadja a feladatot a szerelojenek; a nev felkerul a
    nevsorra es a munkalapba oroklodik."""
    from datetime import date

    from tests.conftest import make_employee_record, make_user

    _, adm = admin
    cég_user, cég_hdr = await make_user(email="kiado-ceg@example.com", role="szervizes")
    emp = await make_employee_record(
        cég_user, is_contractor=True, is_company=True, company_name="Kiadó Kft.",
    )
    task = (
        await client.post(
            "/api/tasks",
            json={"title": "Jura javítás", "employee_id": str(emp.id),
                  "due_date": date.today().isoformat()},
            headers=adm,
        )
    ).json()

    res = await client.post(
        f"/api/me/tasks/{task['id']}/technician", json={"name": "Lakatos Lajos"},
        headers=cég_hdr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["technician_name"] == "Lakatos Lajos"
    # a nev felkerult a ceg nevsorara
    techs = (await client.get("/api/tasks/my-technicians", headers=cég_hdr)).json()
    assert "Lakatos Lajos" in techs

    # a munkalap orokli a kiadast (ures technician_name eseten)
    res = await client.put(
        f"/api/me/tasks/{task['id']}/worksheet",
        json={"work_description": "Szivattyú csere", "works": [], "repair_options": [],
              "materials": []},
        headers=cég_hdr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["technician_name"] == "Lakatos Lajos"
