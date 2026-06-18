"""Time-off: create, decide, double-decide guard, validation."""

from datetime import date, timedelta

from tests.conftest import make_employee_record, make_user


async def make_emp(email="szabis@example.com"):
    user, _ = await make_user(email=email, role="employee")
    return await make_employee_record(user)


def payload(emp_id: str, **kw) -> dict:
    start = date.today() + timedelta(days=30)
    base = {
        "employee_id": emp_id,
        "type": "annual",
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=4)).isoformat(),
        "reason": "családi program",
    }
    base.update(kw)
    return base


async def test_create_and_approve(client, manager):
    _, headers = manager
    emp = await make_emp()
    res = await client.post("/api/time-off", json=payload(str(emp.id)), headers=headers)
    assert res.status_code == 201
    rid = res.json()["id"]
    assert res.json()["status"] == "pending"

    decided = await client.patch(
        f"/api/time-off/{rid}/decide",
        json={"status": "approved", "note": "rendben"},
        headers=headers,
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "approved"
    assert decided.json()["decided_at"] is not None


async def test_overlapping_timeoff_rejected(client, manager):
    """P2: átfedő (függő/jóváhagyott) távollét-kérelem nem vehető fel."""
    _, headers = manager
    emp = await make_emp()
    first = await client.post("/api/time-off", json=payload(str(emp.id)), headers=headers)
    assert first.status_code == 201

    start = date.today() + timedelta(days=32)  # a 30..34 napos kérelembe lóg
    overlapping = payload(
        str(emp.id),
        start_date=start.isoformat(),
        end_date=(start + timedelta(days=4)).isoformat(),
    )
    res = await client.post("/api/time-off", json=overlapping, headers=headers)
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "timeoff.overlap"


async def test_annual_balance_blocks_approval(client, manager):
    """P2: éves szabadság jóváhagyása nem lépheti túl a keretet (alap 20 munkanap)."""
    _, headers = manager
    emp = await make_emp()
    start = date.today() + timedelta(days=30)
    long_req = payload(
        str(emp.id),
        start_date=start.isoformat(),
        end_date=(start + timedelta(days=40)).isoformat(),  # ~29 munkanap > 20
    )
    rid = (await client.post("/api/time-off", json=long_req, headers=headers)).json()["id"]
    res = await client.patch(
        f"/api/time-off/{rid}/decide", json={"status": "approved"}, headers=headers
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "timeoff.over_balance"


async def test_double_decide_conflict(client, manager):
    _, headers = manager
    emp = await make_emp()
    rid = (
        await client.post("/api/time-off", json=payload(str(emp.id)), headers=headers)
    ).json()["id"]
    await client.patch(f"/api/time-off/{rid}/decide", json={"status": "approved"}, headers=headers)
    res = await client.patch(
        f"/api/time-off/{rid}/decide", json={"status": "rejected"}, headers=headers
    )
    assert res.status_code == 409


async def test_bad_type_and_range_rejected(client, manager):
    _, headers = manager
    emp = await make_emp()
    assert (
        await client.post(
            "/api/time-off", json=payload(str(emp.id), type="vakacio"), headers=headers
        )
    ).status_code == 422
    start = date.today() + timedelta(days=10)
    assert (
        await client.post(
            "/api/time-off",
            json=payload(
                str(emp.id),
                start_date=start.isoformat(),
                end_date=(start - timedelta(days=2)).isoformat(),
            ),
            headers=headers,
        )
    ).status_code == 422


async def test_bad_decision_value_rejected(client, manager):
    _, headers = manager
    emp = await make_emp()
    rid = (
        await client.post("/api/time-off", json=payload(str(emp.id)), headers=headers)
    ).json()["id"]
    res = await client.patch(
        f"/api/time-off/{rid}/decide", json={"status": "maybe"}, headers=headers
    )
    assert res.status_code == 422


async def test_status_filter(client, manager):
    _, headers = manager
    emp = await make_emp()
    await client.post("/api/time-off", json=payload(str(emp.id)), headers=headers)
    res = await client.get("/api/time-off", params={"status": "pending"}, headers=headers)
    assert res.status_code == 200
    assert len(res.json()) == 1
    assert res.json()[0]["employee_name"] is not None


async def test_employee_cannot_use_manager_endpoints(client, employee_user):
    _, emp_headers, emp = employee_user
    assert (await client.get("/api/time-off", headers=emp_headers)).status_code == 403
    assert (
        await client.post("/api/time-off", json=payload(str(emp.id)), headers=emp_headers)
    ).status_code == 403
