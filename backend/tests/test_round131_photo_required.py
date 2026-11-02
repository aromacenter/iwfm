"""131. kör: fotó-kötelezettség a feladat lezárásához — beállítás (none/service/all +
minimum), photos_needed a feladat-listában, 422 lezáráskor fotó nélkül."""

from __future__ import annotations

from datetime import date

from tests.test_inventory import asset_payload
from tests.test_support import PNG_DATA_URL
from tests.test_tasks import make_emp

WS_BASE = {
    "company_name": "Teszt Kft.", "company_address": None, "footer_text": None,
    "customer_footer_text": None, "intake_footer_text": None, "survey_fee": None,
    "accent_color": "#1e40af", "show_materials": True, "show_hours": True,
    "show_client_signature": True, "show_comments": True,
}


async def _set_policy(client, adm, policy: str, minimum: int = 1):
    res = await client.put("/api/settings/worksheet", json={**WS_BASE, "photo_policy": policy, "photo_min": minimum}, headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()["photo_policy"] == policy and res.json()["photo_min"] == minimum


async def test_photo_policy_blocks_close_until_photos(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    emp_rec, emp_h = await make_emp(email="fotos@example.com")
    today = date.today().isoformat()

    # gépes feladat (szerviz) + sima feladat
    asset = (await client.post("/api/assets", json=asset_payload(barcode="FOTO-GEP-1"), headers=mgr)).json()
    res = await client.post("/api/tasks", json={
        "title": "Gép karbantartás", "employee_id": str(emp_rec.id), "due_date": today, "asset_id": asset["id"],
    }, headers=mgr)
    assert res.status_code in (200, 201), res.text
    machine_task = res.json()
    res = await client.post("/api/tasks", json={"title": "Raktár rendezés", "employee_id": str(emp_rec.id), "due_date": today}, headers=mgr)
    assert res.status_code in (200, 201), res.text
    plain_task = res.json()

    # alapból nincs kötelezettség
    got = (await client.get("/api/settings/worksheet", headers=adm)).json()
    assert got["photo_policy"] == "none" and got["photo_min"] == 1
    mine = (await client.get("/api/me/tasks", headers=emp_h)).json()
    assert all(t["photos_needed"] == 0 for t in mine)

    # service: csak a gépes feladat kér fotót (2 db)
    await _set_policy(client, adm, "service", 2)
    mine = {t["id"]: t for t in (await client.get("/api/me/tasks", headers=emp_h)).json()}
    assert mine[machine_task["id"]]["photos_needed"] == 2
    assert mine[plain_task["id"]]["photos_needed"] == 0
    res = await client.post(f"/api/me/tasks/{machine_task['id']}/status", json={"status": "done"}, headers=emp_h)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "worksheet.photo_required"
    assert res.json()["detail"]["missing"] == 2
    # a sima feladat zárható
    res = await client.post(f"/api/me/tasks/{plain_task['id']}/status", json={"status": "done"}, headers=emp_h)
    assert res.status_code == 200, res.text

    # egy fotó még kevés, kettővel zárható
    res = await client.post(f"/api/me/tasks/{machine_task['id']}/worksheet/photos", json={"photos": [PNG_DATA_URL]}, headers=emp_h)
    assert res.status_code == 200, res.text
    assert res.json()["photos_needed"] == 1
    res = await client.post(f"/api/me/tasks/{machine_task['id']}/status", json={"status": "done"}, headers=emp_h)
    assert res.status_code == 422
    res = await client.post(f"/api/me/tasks/{machine_task['id']}/worksheet/photos", json={"photos": [PNG_DATA_URL]}, headers=emp_h)
    assert res.json()["photos_needed"] == 0
    res = await client.post(f"/api/me/tasks/{machine_task['id']}/status", json={"status": "done"}, headers=emp_h)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "done"

    # all: a sima feladat is kér fotót; "nem kész" visszaváltás fotó nélkül is mehet
    await _set_policy(client, adm, "all", 1)
    res = await client.post("/api/tasks", json={"title": "Másik sima", "employee_id": str(emp_rec.id), "due_date": today}, headers=mgr)
    other = res.json()
    res = await client.post(f"/api/me/tasks/{other['id']}/status", json={"status": "done"}, headers=emp_h)
    assert res.status_code == 422
    res = await client.post(f"/api/me/tasks/{other['id']}/status", json={"status": "needs_more_work"}, headers=emp_h)
    assert res.status_code == 200

    # érvénytelen beállítás → 422
    res = await client.put("/api/settings/worksheet", json={**WS_BASE, "photo_policy": "sometimes"}, headers=adm)
    assert res.status_code == 422
    res = await client.put("/api/settings/worksheet", json={**WS_BASE, "photo_policy": "all", "photo_min": 9}, headers=adm)
    assert res.status_code == 422
