"""Scheduling: CRUD, compliance preview, publish gating, 96h modify rule."""

from datetime import UTC, date, datetime, timedelta

from tests.conftest import make_employee_record, make_user


def next_monday(weeks_ahead: int = 2) -> date:
    today = date.today()
    monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    return monday + timedelta(weeks=weeks_ahead - 1)


def shift_payload(emp_id: str, day: date, start="08:00:00", end="16:00:00", **kw) -> dict:
    return {
        "employee_id": emp_id,
        "work_date": day.isoformat(),
        "start_time": start,
        "end_time": end,
        **kw,
    }


async def make_emp(client, email="munkas@example.com"):
    user, headers, = await make_user(email=email, role="employee")
    emp = await make_employee_record(user)
    return emp


async def test_create_and_get_week(client, manager):
    _, headers = manager
    emp = await make_emp(client)
    monday = next_monday()

    res = await client.post(
        "/api/shifts", json=shift_payload(str(emp.id), monday), headers=headers
    )
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "draft"

    week = await client.get(
        "/api/shifts", params={"week_start": monday.isoformat()}, headers=headers
    )
    assert week.status_code == 200
    assert len(week.json()["shifts"]) == 1


async def test_employee_cannot_manage_shifts(client, employee_user):
    _, emp_headers, emp = employee_user
    res = await client.post(
        "/api/shifts", json=shift_payload(str(emp.id), next_monday()), headers=emp_headers
    )
    assert res.status_code == 403


async def test_shift_on_approved_leave_rejected(client, manager):
    _, headers = manager
    emp = await make_emp(client)
    monday = next_monday()

    created = await client.post(
        "/api/time-off",
        json={
            "employee_id": str(emp.id),
            "type": "annual",
            "start_date": monday.isoformat(),
            "end_date": (monday + timedelta(days=2)).isoformat(),
        },
        headers=headers,
    )
    rid = created.json()["id"]
    await client.patch(
        f"/api/time-off/{rid}/decide", json={"status": "approved"}, headers=headers
    )

    res = await client.post(
        "/api/shifts", json=shift_payload(str(emp.id), monday), headers=headers
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "shift.on_time_off"


async def test_publish_blocked_on_error_violation(client, manager):
    _, headers = manager
    emp = await make_emp(client)
    monday = next_monday()

    # 13 órás műszak → MAX_DAILY error
    await client.post(
        "/api/shifts",
        json=shift_payload(str(emp.id), monday, start="06:00:00", end="19:00:00"),
        headers=headers,
    )
    res = await client.post(
        "/api/shifts/publish", json={"week_start": monday.isoformat()}, headers=headers
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "publish.blocked"


async def test_publish_clean_week_succeeds(client, manager):
    _, headers = manager
    emp = await make_emp(client)
    monday = next_monday(weeks_ahead=3)  # bőven 168 órán túl

    for i in range(5):
        await client.post(
            "/api/shifts",
            json=shift_payload(str(emp.id), monday + timedelta(days=i)),
            headers=headers,
        )
    res = await client.post(
        "/api/shifts/publish", json={"week_start": monday.isoformat()}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["published"] == 5

    week = await client.get(
        "/api/shifts", params={"week_start": monday.isoformat()}, headers=headers
    )
    assert all(s["status"] == "published" for s in week.json()["shifts"])


async def test_publish_within_168h_needs_force(client, manager):
    _, headers = manager
    emp = await make_emp(client)
    tomorrow = date.today() + timedelta(days=1)

    await client.post(
        "/api/shifts", json=shift_payload(str(emp.id), tomorrow), headers=headers
    )
    res = await client.post(
        "/api/shifts/publish", json={"week_start": tomorrow.isoformat()}, headers=headers
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "publish.needs_confirmation"
    codes = [v["code"] for v in res.json()["detail"]["violations"]]
    assert "PUBLISH_NOTICE" in codes

    forced = await client.post(
        "/api/shifts/publish",
        json={"week_start": tomorrow.isoformat(), "force": True},
        headers=headers,
    )
    assert forced.status_code == 200


async def test_modify_published_within_96h_needs_force(client, manager):
    _, headers = manager
    emp = await make_emp(client)
    in_two_days = date.today() + timedelta(days=2)

    created = await client.post(
        "/api/shifts", json=shift_payload(str(emp.id), in_two_days), headers=headers
    )
    shift_id = created.json()["id"]
    await client.post(
        "/api/shifts/publish",
        json={"week_start": in_two_days.isoformat(), "force": True},
        headers=headers,
    )

    res = await client.patch(
        f"/api/shifts/{shift_id}", json={"start_time": "10:00:00"}, headers=headers
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "shift.modify_notice"

    forced = await client.patch(
        f"/api/shifts/{shift_id}",
        json={"start_time": "10:00:00", "force": True},
        headers=headers,
    )
    assert forced.status_code == 200
    assert forced.json()["start_time"] == "10:00:00"


async def test_compliance_preview_lists_violations(client, manager):
    _, headers = manager
    emp = await make_emp(client)
    monday = next_monday()

    await client.post(
        "/api/shifts",
        json=shift_payload(str(emp.id), monday, start="06:00:00", end="20:00:00"),
        headers=headers,
    )
    res = await client.get(
        "/api/shifts/compliance", params={"week_start": monday.isoformat()}, headers=headers
    )
    assert res.status_code == 200
    codes = [v["code"] for v in res.json()]
    assert "MAX_DAILY" in codes


async def test_delete_draft_shift(client, manager):
    _, headers = manager
    emp = await make_emp(client)
    monday = next_monday()
    created = await client.post(
        "/api/shifts", json=shift_payload(str(emp.id), monday), headers=headers
    )
    res = await client.delete(f"/api/shifts/{created.json()['id']}", headers=headers)
    assert res.status_code == 200
