"""Self-service ownership: an employee only ever sees their own data (IDOR)."""

from datetime import date, timedelta

from tests.conftest import make_employee_record, make_user


async def two_employees(client):
    u1, h1 = await make_user(email="sajat@example.com", role="employee")
    e1 = await make_employee_record(u1, last_name="Első")
    u2, h2 = await make_user(email="masik@example.com", role="employee")
    e2 = await make_employee_record(u2, last_name="Második")
    return (e1, h1), (e2, h2)


async def test_change_password_invalidates_old_sessions(client):
    """M1/M4: jelszóváltás után a régi token érvénytelen, az új jelszó működik."""
    user, headers = await make_user(
        email="jelszo@example.com", role="employee", password="RegiJelszo-123"
    )
    await make_employee_record(user)

    bad = await client.post(
        "/api/me/password",
        json={"current_password": "rossz", "new_password": "UjJelszo-9876"},
        headers=headers,
    )
    assert bad.status_code == 403

    ok = await client.post(
        "/api/me/password",
        json={"current_password": "RegiJelszo-123", "new_password": "UjJelszo-9876"},
        headers=headers,
    )
    assert ok.status_code == 200

    # a régi Bearer token (tv=0) már nem érvényes
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
    # az új jelszóval be lehet lépni
    login = await client.post(
        "/api/auth/login", json={"email": "jelszo@example.com", "password": "UjJelszo-9876"}
    )
    assert login.status_code == 200


async def test_my_schedule_only_published_and_own(client, manager):
    _, mgr = manager
    (e1, h1), (e2, h2) = await two_employees(client)
    day = date.today() + timedelta(days=21)
    monday = day - timedelta(days=day.weekday())

    for emp in (e1, e2):
        await client.post(
            "/api/shifts",
            json={
                "employee_id": str(emp.id),
                "work_date": monday.isoformat(),
                "start_time": "08:00:00",
                "end_time": "16:00:00",
            },
            headers=mgr,
        )

    # publikálás előtt a dolgozó semmit sem lát
    res = await client.get(
        "/api/me/schedule", params={"week_start": monday.isoformat()}, headers=h1
    )
    assert res.json() == []

    await client.post(
        "/api/shifts/publish", json={"week_start": monday.isoformat()}, headers=mgr
    )

    # publikálás után csak a SAJÁT műszakát látja
    res1 = await client.get(
        "/api/me/schedule", params={"week_start": monday.isoformat()}, headers=h1
    )
    assert len(res1.json()) == 1
    assert res1.json()[0]["employee_id"] == str(e1.id)

    res2 = await client.get(
        "/api/me/schedule", params={"week_start": monday.isoformat()}, headers=h2
    )
    assert len(res2.json()) == 1
    assert res2.json()[0]["employee_id"] == str(e2.id)


async def test_my_time_off_isolated(client):
    (e1, h1), (e2, h2) = await two_employees(client)
    start = date.today() + timedelta(days=40)
    await client.post(
        "/api/me/time-off",
        json={
            "type": "annual",
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=2)).isoformat(),
        },
        headers=h1,
    )
    assert len((await client.get("/api/me/time-off", headers=h1)).json()) == 1
    assert (await client.get("/api/me/time-off", headers=h2)).json() == []


async def test_user_without_employee_record_404(client, manager):
    _, mgr = manager  # manager has no Employee record
    res = await client.get("/api/me/schedule", headers=mgr)
    assert res.status_code == 404
