"""136. kör: csapatos munkavégzés — segítők a feladaton: látják (is_helper),
kommentelhetnek, közös munkalap; kívülálló 404; segítő-lista módosítása; átosztásnál
az új felelős kikerül a segítők közül; hibás azonosító 422."""

from __future__ import annotations

from datetime import date

from tests.test_tasks import make_emp


async def test_task_helpers_flow(client, manager):
    _, mgr = manager
    lead, lead_h = await make_emp(email="fo-szerelo@example.com")
    helper, helper_h = await make_emp(email="segito@example.com")
    outsider, outsider_h = await make_emp(email="kivulallo@example.com")
    today = date.today().isoformat()

    # hibás segítő-azonosító → 422
    res = await client.post("/api/tasks", json={
        "title": "Nagy gép telepítés", "employee_id": str(lead.id), "due_date": today, "helper_employee_ids": ["nem-uuid"],
    }, headers=mgr)
    assert res.status_code == 422
    res = await client.post("/api/tasks", json={
        "title": "Nagy gép telepítés", "employee_id": str(lead.id), "due_date": today,
        "helper_employee_ids": [str(helper.id), str(lead.id)],  # a fő felelős kimarad
    }, headers=mgr)
    assert res.status_code == 201, res.text
    task = res.json()
    assert [h["employee_id"] for h in task["helpers"]] == [str(helper.id)]
    assert task["helpers"][0]["name"]

    # a segítő látja a Feladataim nézetben (is_helper), a fő felelős nem segítőként
    mine = (await client.get("/api/me/tasks", headers=helper_h)).json()
    assert [t["id"] for t in mine] == [task["id"]] and mine[0]["is_helper"] is True
    lead_mine = (await client.get("/api/me/tasks", headers=lead_h)).json()
    assert lead_mine[0]["id"] == task["id"] and lead_mine[0]["is_helper"] is False
    assert (await client.get("/api/me/tasks", headers=outsider_h)).json() == []

    # a segítő kommentel + közös munkalapot ment; kívülálló 404
    res = await client.post(f"/api/me/tasks/{task['id']}/comment", json={"text": "Én viszem a létrát"}, headers=helper_h)
    assert res.status_code == 200, res.text
    res = await client.put(f"/api/me/tasks/{task['id']}/worksheet", json={"work_description": "Közös telepítés"}, headers=helper_h)
    assert res.status_code == 200, res.text
    assert (await client.post(f"/api/me/tasks/{task['id']}/comment", json={"text": "?"}, headers=outsider_h)).status_code == 404
    ws = (await client.get("/api/me/tasks", headers=lead_h)).json()[0]
    assert ws["worksheet_completed"] is True

    # segítő-lista módosítása a vezetői oldalról
    res = await client.patch(f"/api/tasks/{task['id']}", json={"helper_employee_ids": [str(outsider.id)]}, headers=mgr)
    assert res.status_code == 200, res.text
    assert [h["employee_id"] for h in res.json()["helpers"]] == [str(outsider.id)]
    assert (await client.get("/api/me/tasks", headers=helper_h)).json() == []
    assert len((await client.get("/api/me/tasks", headers=outsider_h)).json()) == 1

    # átosztás a mostani segítőre → ő lesz a felelős, és kikerül a segítők közül
    res = await client.patch(f"/api/tasks/{task['id']}", json={"employee_id": str(outsider.id)}, headers=mgr)
    assert res.status_code == 200, res.text
    assert res.json()["employee_id"] == str(outsider.id) and res.json()["helpers"] == []
    # hibás azonosító a módosításnál → 422
    assert (await client.patch(f"/api/tasks/{task['id']}", json={"helper_employee_ids": ["x"]}, headers=mgr)).status_code == 422
