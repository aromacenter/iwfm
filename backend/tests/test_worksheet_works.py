"""Tételes munkadíjak a munkalapon: soronkénti munka + ár, KSZ-en belső
költség vs képviselői ügyfél-ár (megőrzés), gép-adatok a saját feladatokban,
és a külsős szervizes szűkített jogosultsága."""

from __future__ import annotations

from tests.conftest import make_employee_record, make_user


async def _ksz_setup(client, adm, mgr):
    res = await client.post("/api/partners", json={"name": "Works Kft."}, headers=mgr)
    partner = res.json()
    res = await client.post(
        "/api/assets",
        json={"barcode": "WORKS-1", "name": "Works Gép", "serial_number": "SN-77",
              "maintenance_fee": 4000.0},
        headers=mgr,
    )
    asset = res.json()
    await client.post(
        f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]},
        headers=mgr,
    )
    user, emp_headers = await make_user(email="worksguy@example.com", role="szervizes")
    emp = await make_employee_record(user, last_name="Works", first_name="Béla")
    res = await client.post(
        "/api/tasks",
        json={"title": "Javítás tételesen", "employee_id": str(emp.id),
              "due_date": "2026-09-01", "external_service": True,
              "asset_id": asset["id"]},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    return partner, asset, emp_headers, res.json()["id"]


async def test_works_roundtrip_price_preserve_and_asset_info(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    partner, asset, emp_headers, task_id = await _ksz_setup(client, adm, mgr)

    # A saját feladatban látszanak a gép adatai
    mine = (await client.get("/api/me/tasks", headers=emp_headers)).json()
    a = mine[0]["asset"]
    assert a["name"] == "Works Gép"
    assert a["serial_number"] == "SN-77"
    assert a["barcode"] == "WORKS-1"
    assert a["partner_name"] == "Works Kft."

    # Szervizes menti a tételes munkákat a BELSŐ díjaival (cost_net)
    res = await client.put(
        f"/api/me/tasks/{task_id}/worksheet",
        json={
            "work_description": "",
            "works": [
                {"name": "Szivattyú csere", "cost_net": 8000},
                {"name": "Vízkőtelenítés", "cost_net": 3000},
            ],
            "materials": [],
        },
        headers=emp_headers,
    )
    assert res.status_code == 200, res.text
    ws = res.json()
    assert [w["name"] for w in ws["works"]] == ["Szivattyú csere", "Vízkőtelenítés"]
    assert ws["works"][0]["cost_net"] == 8000
    assert ws["works"][0]["price_net"] is None

    # Képviselő beállítja a MI árainkat (price_net)
    res = await client.put(
        f"/api/tasks/{task_id}/worksheet",
        json={
            "work_description": "",
            "works": [
                {"name": "Szivattyú csere", "cost_net": 8000, "price_net": 14000},
                {"name": "Vízkőtelenítés", "cost_net": 3000, "price_net": 6000},
            ],
            "materials": [],
        },
        headers=mgr,
    )
    assert res.status_code == 200, res.text

    # A szervizes újra ment ár nélkül → az ügyfél-árak NEM veszhetnek el
    res = await client.put(
        f"/api/me/tasks/{task_id}/worksheet",
        json={
            "work_description": "kész",
            "works": [
                {"name": "Szivattyú csere", "cost_net": 8500},
                {"name": "Vízkőtelenítés", "cost_net": 3000},
            ],
            "materials": [],
        },
        headers=emp_headers,
    )
    assert res.status_code == 200, res.text
    ws = res.json()
    by_name = {w["name"]: w for w in ws["works"]}
    assert by_name["Szivattyú csere"]["price_net"] == 14000
    assert by_name["Szivattyú csere"]["cost_net"] == 8500
    assert by_name["Vízkőtelenítés"]["price_net"] == 6000

    # Üres munkalap (se leírás, se tétel) → 422
    res = await client.put(
        f"/api/me/tasks/{task_id}/worksheet",
        json={"work_description": "", "works": [], "materials": []},
        headers=emp_headers,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "worksheet.empty"

    # Mindkét PDF-változat elkészül (belső + ügyfél -1)
    res = await client.get(f"/api/tasks/{task_id}/worksheet/pdf", headers=mgr)
    assert res.status_code == 200 and res.content[:4] == b"%PDF"
    res = await client.get(
        f"/api/tasks/{task_id}/worksheet/pdf?variant=customer", headers=mgr
    )
    assert res.status_code == 200 and res.content[:4] == b"%PDF"


async def test_szervizes_scoped_permissions(client, admin, manager):
    _, mgr = manager
    await client.post("/api/partners", json={"name": "Perm Kft."}, headers=mgr)
    res = await client.post(
        "/api/assets", json={"barcode": "PERM-1", "name": "Perm Gép"}, headers=mgr
    )
    assert res.status_code == 201

    _, sz_headers = await make_user(email="kulsosszerviz@example.com", role="szervizes")
    # gép-lista elérhető (a szerviz gép-választójához) machines jog nélkül is
    res = await client.get("/api/assets", headers=sz_headers)
    assert res.status_code == 200
    # de a gép-létrehozás (machines funkció) tiltott
    res = await client.post(
        "/api/assets", json={"barcode": "PERM-2", "name": "Tiltott"}, headers=sz_headers
    )
    assert res.status_code == 403
    # beosztás-funkciók sem járnak az alapértelmezett mátrix szerint
    me = (await client.get("/api/auth/me", headers=sz_headers)).json()
    assert "service" in me["permissions"]
    assert "my_tasks" in me["permissions"]
    assert "my_schedule" not in me["permissions"]
    assert "machines" not in me["permissions"]
