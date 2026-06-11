"""Kiosk (blokkoló-terminál): törzsszámos be-/kiblokkolás, throttle, kódok."""

from sqlalchemy import select

import app.db as app_db
from app.models import Employee
from tests.conftest import gen_bank_account, gen_tax_id, gen_taj


async def create_employee(client, admin_headers, email="kiosk.dolgozo@example.com"):
    res = await client.post(
        "/api/employees",
        json={
            "email": email,
            "last_name": "Blokk",
            "first_name": "Olga",
            "hire_date": "2026-01-01",
            "tax_id": gen_tax_id(11),
            "taj": gen_taj(11),
            "bank_account": gen_bank_account(11),
        },
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_employee_gets_six_digit_code(client, admin):
    _, headers = admin
    emp = await create_employee(client, headers)
    assert emp["employee_code"] is not None
    assert len(emp["employee_code"]) == 6
    assert emp["employee_code"].isdigit()


async def test_codes_are_unique(client, admin):
    _, headers = admin
    e1 = await create_employee(client, headers, email="a@example.com")
    e2 = await create_employee(client, headers, email="b@example.com")
    assert e1["employee_code"] != e2["employee_code"]


async def test_kiosk_clock_in_then_out(client, admin):
    _, headers = admin
    emp = await create_employee(client, headers)
    code = emp["employee_code"]

    # beblokkolás — nincs auth!
    r1 = await client.post("/api/kiosk/clock", json={"code": code})
    assert r1.status_code == 200, r1.text
    assert r1.json()["action"] == "in"
    assert "Blokk" in r1.json()["employee_name"]

    # kiblokkolás (toggle)
    r2 = await client.post("/api/kiosk/clock", json={"code": code})
    assert r2.status_code == 200
    assert r2.json()["action"] == "out"
    assert r2.json()["worked_minutes"] is not None


async def test_kiosk_unknown_code_404(client, admin):
    _, headers = admin
    await create_employee(client, headers)
    res = await client.post("/api/kiosk/clock", json={"code": "000001"})
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "kiosk.unknown_code"


async def test_kiosk_bad_format_422(client):
    assert (await client.post("/api/kiosk/clock", json={"code": "12345"})).status_code == 422
    assert (await client.post("/api/kiosk/clock", json={"code": "abcdef"})).status_code == 422


async def test_kiosk_throttled_after_10_failures(client, admin):
    _, headers = admin
    await create_employee(client, headers)
    for _ in range(10):
        await client.post("/api/kiosk/clock", json={"code": "000002"})
    res = await client.post("/api/kiosk/clock", json={"code": "000002"})
    assert res.status_code == 429


async def test_inactive_employee_code_rejected(client, admin):
    _, headers = admin
    emp = await create_employee(client, headers)
    await client.patch(f"/api/employees/{emp['id']}", json={"status": "inactive"}, headers=headers)
    res = await client.post("/api/kiosk/clock", json={"code": emp["employee_code"]})
    assert res.status_code == 404


async def test_backfill_assigns_codes(client):
    """Régi (kód nélküli) dolgozók induláskor kapnak törzsszámot."""
    from tests.conftest import make_employee_record, make_user

    user, _ = await make_user(email="regi@example.com", role="employee")
    emp = await make_employee_record(user)  # employee_code = NULL

    from app.services.wfm.codes import backfill_employee_codes

    factory = app_db.get_session_factory()
    async with factory() as session:
        filled = await backfill_employee_codes(session)
    assert filled == 1
    async with factory() as session:
        refreshed = (
            await session.execute(select(Employee).where(Employee.id == emp.id))
        ).scalar_one()
        assert refreshed.employee_code and len(refreshed.employee_code) == 6
