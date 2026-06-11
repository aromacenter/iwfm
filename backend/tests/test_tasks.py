"""Feladatok: manager CRUD, dolgozói státusz/komment, ownership, dashboard."""

from datetime import date, timedelta

from tests.conftest import make_employee_record, make_user


async def make_emp(email="feladatos@example.com"):
    user, headers = await make_user(email=email, role="employee")
    emp = await make_employee_record(user)
    return emp, headers


def payload(emp_id: str, **kw) -> dict:
    base = {
        "title": "Raktár rendezés",
        "description": "A C polcsor átrendezése.",
        "employee_id": emp_id,
        "due_date": date.today().isoformat(),
    }
    base.update(kw)
    return base


async def test_create_and_list(client, manager):
    _, mgr = manager
    emp, _ = await make_emp()
    res = await client.post("/api/tasks", json=payload(str(emp.id)), headers=mgr)
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "open"
    assert res.json()["employee_name"].startswith("Teszt")

    listed = await client.get("/api/tasks", headers=mgr)
    assert len(listed.json()) == 1


async def test_required_skill_attached(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    emp, _ = await make_emp()
    skill = (
        await client.post("/api/settings/skills", json={"name": "villanyszerelő"}, headers=adm)
    ).json()
    res = await client.post(
        "/api/tasks", json=payload(str(emp.id), required_skill_id=skill["id"]), headers=mgr
    )
    assert res.json()["required_skill"]["name"] == "villanyszerelő"


async def test_employee_sees_own_and_updates_status(client, manager):
    _, mgr = manager
    emp, emp_headers = await make_emp()
    task = (await client.post("/api/tasks", json=payload(str(emp.id)), headers=mgr)).json()

    mine = await client.get("/api/me/tasks", headers=emp_headers)
    assert len(mine.json()) == 1

    done = await client.post(
        f"/api/me/tasks/{task['id']}/status",
        json={"status": "done", "comment": "Kész, a polcok átrendezve."},
        headers=emp_headers,
    )
    assert done.status_code == 200
    assert done.json()["status"] == "done"
    assert done.json()["comments"][-1]["text"].startswith("Kész")


async def test_employee_needs_more_work_status(client, manager):
    _, mgr = manager
    emp, emp_headers = await make_emp()
    task = (await client.post("/api/tasks", json=payload(str(emp.id)), headers=mgr)).json()
    res = await client.post(
        f"/api/me/tasks/{task['id']}/status",
        json={"status": "needs_more_work"},
        headers=emp_headers,
    )
    assert res.json()["status"] == "needs_more_work"

    # open-t nem állíthat a dolgozó
    bad = await client.post(
        f"/api/me/tasks/{task['id']}/status", json={"status": "open"}, headers=emp_headers
    )
    assert bad.status_code == 422


async def test_employee_cannot_touch_others_task(client, manager):
    _, mgr = manager
    emp1, _ = await make_emp(email="elso@example.com")
    _, other_headers = await make_emp(email="masodik@example.com")
    task = (await client.post("/api/tasks", json=payload(str(emp1.id)), headers=mgr)).json()

    assert (
        await client.post(
            f"/api/me/tasks/{task['id']}/comment", json={"text": "hack"}, headers=other_headers
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/api/me/tasks/{task['id']}/status", json={"status": "done"}, headers=other_headers
        )
    ).status_code == 404
    assert (await client.get("/api/me/tasks", headers=other_headers)).json() == []


async def test_employee_comment_flow(client, manager):
    _, mgr = manager
    emp, emp_headers = await make_emp()
    task = (await client.post("/api/tasks", json=payload(str(emp.id)), headers=mgr)).json()
    res = await client.post(
        f"/api/me/tasks/{task['id']}/comment",
        json={"text": "Hiányzik hozzá a létra."},
        headers=emp_headers,
    )
    assert res.status_code == 200
    assert len(res.json()["comments"]) == 1


async def test_manager_reopen_and_delete(client, manager):
    _, mgr = manager
    emp, emp_headers = await make_emp()
    task = (await client.post("/api/tasks", json=payload(str(emp.id)), headers=mgr)).json()
    await client.post(
        f"/api/me/tasks/{task['id']}/status", json={"status": "done"}, headers=emp_headers
    )
    reopened = await client.patch(
        f"/api/tasks/{task['id']}", json={"status": "open"}, headers=mgr
    )
    assert reopened.json()["status"] == "open"
    assert (await client.delete(f"/api/tasks/{task['id']}", headers=mgr)).status_code == 200
    assert (await client.get("/api/tasks", headers=mgr)).json() == []


async def test_dashboard_shows_todays_tasks(client, manager):
    _, mgr = manager
    emp, _ = await make_emp()
    await client.post("/api/tasks", json=payload(str(emp.id)), headers=mgr)
    await client.post(
        "/api/tasks",
        json=payload(
            str(emp.id), title="Holnapi", due_date=(date.today() + timedelta(days=1)).isoformat()
        ),
        headers=mgr,
    )
    dash = (await client.get("/api/dashboard", headers=mgr)).json()
    assert len(dash["todays_tasks"]) == 1
    assert dash["todays_tasks"][0]["title"] == "Raktár rendezés"


async def test_employee_cannot_use_manager_endpoints(client, employee_user):
    _, emp_headers, emp = employee_user
    assert (await client.get("/api/tasks", headers=emp_headers)).status_code == 403
    assert (
        await client.post("/api/tasks", json=payload(str(emp.id)), headers=emp_headers)
    ).status_code == 403
