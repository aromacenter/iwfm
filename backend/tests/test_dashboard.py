"""Vezérlőpult: függő kérelmek, ma távol lévők, bent lévők, piszkozat-figyelő."""

from datetime import date, timedelta

from tests.conftest import make_employee_record, make_user


async def test_dashboard_aggregates(client, manager):
    _, headers = manager
    user, _ = await make_user(email="dash.dolgozo@example.com", role="employee")
    emp = await make_employee_record(user)

    # függő kérelem
    start = date.today() + timedelta(days=30)
    await client.post(
        "/api/time-off",
        json={
            "employee_id": str(emp.id),
            "type": "sick",
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=2)).isoformat(),
        },
        headers=headers,
    )
    # ma távol lévő (jóváhagyott, ma is tartó)
    rid = (
        await client.post(
            "/api/time-off",
            json={
                "employee_id": str(emp.id),
                "type": "annual",
                "start_date": (date.today() - timedelta(days=1)).isoformat(),
                "end_date": (date.today() + timedelta(days=1)).isoformat(),
            },
            headers=headers,
        )
    ).json()["id"]
    await client.patch(
        f"/api/time-off/{rid}/decide", json={"status": "approved"}, headers=headers
    )
    # jövő heti piszkozat műszak
    next_monday = date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)
    await client.post(
        "/api/shifts",
        json={
            "employee_id": str(emp.id),
            "work_date": (next_monday + timedelta(days=3)).isoformat(),
            "start_time": "08:00:00",
            "end_time": "16:00:00",
        },
        headers=headers,
    )

    res = await client.get("/api/dashboard", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data["pending_time_off"]) == 1
    assert data["pending_time_off"][0]["type"] == "sick"
    assert data["pending_time_off"][0]["employee_name"].startswith("Teszt")
    assert len(data["on_leave_today"]) == 1
    assert data["next_week"]["draft_shifts"] == 1
    assert data["active_employees"] == 1


async def test_dashboard_requires_manager(client, employee_user):
    _, emp_headers, _ = employee_user
    assert (await client.get("/api/dashboard", headers=emp_headers)).status_code == 403
