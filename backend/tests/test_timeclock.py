"""Time clock: manual entries, corrections, self clock-in/out, ownership."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

import app.db as app_db
from app.models import AuditEvent
from tests.conftest import make_employee_record, make_user


async def make_emp(email="oras@example.com"):
    user, _ = await make_user(email=email, role="employee")
    return await make_employee_record(user)


def entry_payload(emp_id: str, **kw) -> dict:
    day = datetime.now(UTC).replace(hour=8, minute=0, second=0, microsecond=0)
    base = {
        "employee_id": emp_id,
        "clock_in": day.isoformat(),
        "clock_out": (day + timedelta(hours=8, minutes=30)).isoformat(),
        "break_minutes": 30,
    }
    base.update(kw)
    return base


async def test_manual_entry_and_worked_minutes(client, manager):
    _, headers = manager
    emp = await make_emp()
    res = await client.post(
        "/api/time-entries", json=entry_payload(str(emp.id)), headers=headers
    )
    assert res.status_code == 201, res.text
    # 8.5h jelenlét - 30 perc szünet = 480 perc
    assert res.json()["worked_minutes"] == 480
    assert res.json()["source"] == "manual"


async def test_bad_range_rejected(client, manager):
    _, headers = manager
    emp = await make_emp()
    day = datetime.now(UTC)
    res = await client.post(
        "/api/time-entries",
        json=entry_payload(
            str(emp.id),
            clock_in=day.isoformat(),
            clock_out=(day - timedelta(hours=1)).isoformat(),
        ),
        headers=headers,
    )
    assert res.status_code == 422


async def test_list_entries_in_period(client, manager):
    _, headers = manager
    emp = await make_emp()
    await client.post("/api/time-entries", json=entry_payload(str(emp.id)), headers=headers)
    res = await client.get(
        "/api/time-entries",
        params={
            "date_from": (date.today() - timedelta(days=1)).isoformat(),
            "date_to": (date.today() + timedelta(days=1)).isoformat(),
        },
        headers=headers,
    )
    assert res.status_code == 200
    assert len(res.json()) == 1
    assert res.json()[0]["employee_name"] is not None


async def test_edit_entry_audited(client, manager):
    _, headers = manager
    emp = await make_emp()
    created = await client.post(
        "/api/time-entries", json=entry_payload(str(emp.id)), headers=headers
    )
    entry_id = created.json()["id"]
    res = await client.patch(
        f"/api/time-entries/{entry_id}", json={"break_minutes": 60}, headers=headers
    )
    assert res.status_code == 200
    assert res.json()["worked_minutes"] == 450

    factory = app_db.get_session_factory()
    async with factory() as session:
        actions = (await session.execute(select(AuditEvent.action))).scalars().all()
    assert "timeclock.manual_edit" in actions


async def test_mobile_clocking_removed_kiosk_only(client, employee_user):
    """Telefonról nem lehet blokkolni (csalás-védelem) — csak a kiosk terminál.
    A GET /me/clock státusz-olvasás megmaradt."""
    _, headers, emp = employee_user

    status0 = await client.get("/api/me/clock", headers=headers)
    assert status0.status_code == 200
    assert status0.json()["open"] is None

    # a régi végpontok megszűntek
    assert (await client.post("/api/me/clock-in", json={}, headers=headers)).status_code in (404, 405)
    assert (await client.post("/api/me/clock-out", json={}, headers=headers)).status_code in (404, 405)


async def test_employee_cannot_touch_manager_timeclock(client, employee_user):
    _, headers, emp = employee_user
    assert (
        await client.get(
            "/api/time-entries",
            params={"date_from": "2026-01-01", "date_to": "2026-01-31"},
            headers=headers,
        )
    ).status_code == 403
    assert (
        await client.post(
            "/api/time-entries", json=entry_payload(str(emp.id)), headers=headers
        )
    ).status_code == 403
