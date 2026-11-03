"""GDPR érintetti jogok: anonimizálás (17. cikk) és saját adat-export (15./20. cikk)."""

import uuid

from sqlalchemy import select

import app.db as app_db
from app.core.security import mint_token
from app.models import AuditEvent, Employee, User
from tests.conftest import (
    gen_bank_account,
    gen_tax_id,
    gen_taj,
    make_employee_record,
    make_user,
)


def employee_payload(**overrides) -> dict:
    payload = {
        "email": "gdpr.dolgozo@example.com",
        "last_name": "Kovács",
        "first_name": "Anna",
        "birth_name": "Kovács Anna",
        "mother_name": "Szabó Mária",
        "birth_place": "Budapest",
        "birth_date": "1992-04-13",
        "phone": "+36301234567",
        "address": "1011 Budapest, Fő u. 1.",
        "residence_address": "1011 Budapest, Fő u. 2.",
        "hire_date": "2026-01-05",
        "job_title": "raktáros",
        "notes": "allergia: pollen",
        "tax_id": gen_tax_id(3),
        "taj": gen_taj(3),
        "bank_account": gen_bank_account(3),
        "wage_amount": "450000 HUF/hó",
    }
    payload.update(overrides)
    return payload


async def _create(client, headers, **overrides) -> dict:
    res = await client.post("/api/employees", json=employee_payload(**overrides), headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


# ─── Anonimizálás ────────────────────────────────────────────────────────────


async def test_anonymize_requires_admin(client, admin, manager):
    _, admin_headers = admin
    _, mgr_headers = manager
    created = await _create(client, admin_headers)
    res = await client.post(
        f"/api/employees/{created['id']}/anonymize",
        json={"confirm": "Kovács"},
        headers=mgr_headers,
    )
    assert res.status_code == 403


async def test_anonymize_confirm_mismatch(client, admin):
    _, headers = admin
    created = await _create(client, headers)
    res = await client.post(
        f"/api/employees/{created['id']}/anonymize",
        json={"confirm": "Valaki Más"},
        headers=headers,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "employee.confirm_mismatch"

    # semmi nem változott
    detail = (await client.get(f"/api/employees/{created['id']}", headers=headers)).json()
    assert detail["last_name"] == "Kovács"
    assert detail["status"] == "active"


async def test_anonymize_clears_pii_and_disables_login(client, admin):
    _, headers = admin
    created = await _create(client, headers)
    emp_id = created["id"]
    password = created["generated_password"]
    creds = {"email": "gdpr.dolgozo@example.com", "password": password}
    assert (await client.post("/api/auth/login", json=creds)).status_code == 200

    res = await client.post(
        f"/api/employees/{emp_id}/anonymize", json={"confirm": "kovács"}, headers=headers
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["last_name"] == "Törölt"
    assert body["first_name"] == "dolgozó"
    assert body["status"] == "inactive"
    assert body["employee_code"] == created["employee_code"]  # törzsszám megmarad
    assert body["hire_date"] == "2026-01-05"
    for key in (
        "birth_name", "mother_name", "birth_place", "birth_date", "phone", "address",
        "residence_address", "notes", "tax_id_masked", "taj_masked", "bank_account_masked",
    ):
        assert body[key] in (None, ""), key
    assert body["has_wage"] is False
    assert body["telegram_linked"] is False
    assert body["email"] == f"deleted-{emp_id}@anonymized.invalid"
    assert body["username"] is None

    # a lista is a törölt nevet mutatja
    listed = (await client.get("/api/employees", headers=headers)).json()
    names = {(e["last_name"], e["first_name"]) for e in listed}
    assert ("Törölt", "dolgozó") in names
    assert ("Kovács", "Anna") not in names

    # a visszafejtés sem ad vissza semmit
    reveal = (await client.get(f"/api/employees/{emp_id}/reveal", headers=headers)).json()
    assert reveal == {"tax_id": None, "taj": None, "bank_account": None, "wage_amount": None}

    # a login-fiók nem léphet be (régi email + jelszó)
    assert (await client.post("/api/auth/login", json=creds)).status_code == 401

    factory = app_db.get_session_factory()
    async with factory() as session:
        emp = (await session.execute(select(Employee).where(Employee.id == uuid.UUID(emp_id)))).scalar_one()
        assert emp.tax_id_encrypted is None and emp.taj_encrypted is None
        assert emp.bank_account_encrypted is None and emp.wage_encrypted is None
        user = (await session.execute(select(User).where(User.id == emp.user_id))).scalar_one()
        assert user.is_active is False
        assert user.token_version == 1
        assert user.display_name == "Törölt dolgozó"
        actions = (await session.execute(select(AuditEvent.action))).scalars().all()
    assert "employee.anonymize" in actions

    # idempotens: második hívás (törzsszámmal megerősítve) is 200
    again = await client.post(
        f"/api/employees/{emp_id}/anonymize",
        json={"confirm": created["employee_code"]},
        headers=headers,
    )
    assert again.status_code == 200
    assert again.json()["last_name"] == "Törölt"


async def test_anonymize_self_refused(client, admin):
    admin_user, headers = admin
    own = await make_employee_record(admin_user, last_name="Admin", first_name="Aladár")
    res = await client.post(
        f"/api/employees/{own.id}/anonymize", json={"confirm": "Admin"}, headers=headers
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "employee.anonymize_self"
    # az admin továbbra is be van jelentkezve
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200


# ─── Saját adat-export ───────────────────────────────────────────────────────


async def test_data_export_returns_own_decrypted_data(client, admin):
    _, admin_headers = admin
    tax_id, taj, bank = gen_tax_id(3), gen_taj(3), gen_bank_account(3)
    created = await _create(client, admin_headers)
    emp_headers = {
        "Authorization": f"Bearer {mint_token(user_id=uuid.UUID(created['user_id']), role='employee')}"
    }

    res = await client.get("/api/me/data-export", headers=emp_headers)
    assert res.status_code == 200, res.text
    assert res.headers["content-disposition"] == 'attachment; filename="sajat-adatok.json"'
    body = res.json()
    assert body["exported_at"]
    emp = body["employee"]
    assert emp["id"] == created["id"]
    assert emp["last_name"] == "Kovács" and emp["first_name"] == "Anna"
    assert emp["mother_name"] == "Szabó Mária"
    assert emp["birth_date"] == "1992-04-13"
    assert emp["tax_id"] == tax_id
    assert emp["taj"] == taj
    assert emp["bank_account"] == bank
    assert emp["wage_amount"] == "450000 HUF/hó"
    assert body["user"]["email"] == "gdpr.dolgozo@example.com"
    assert body["user"]["role"] == "employee"
    assert "password_hash" not in body["user"]
    assert "password_hash" not in res.text
    counts = body["counts"]
    for key in ("time_entries", "time_off_requests", "shifts", "tasks_assigned", "audit_events_as_actor"):
        assert isinstance(counts[key], int), key

    factory = app_db.get_session_factory()
    async with factory() as session:
        actions = (await session.execute(select(AuditEvent.action))).scalars().all()
    assert "me.data_export" in actions


async def test_data_export_requires_employee_record(client, manager):
    _, mgr = manager  # nincs Employee rekordja
    assert (await client.get("/api/me/data-export", headers=mgr)).status_code == 404


async def test_employee_cannot_read_others_via_employees_api(client, admin):
    _, admin_headers = admin
    other = await _create(client, admin_headers)
    user, emp_headers = await make_user(email="masik.dolgozo@example.com", role="employee")
    await make_employee_record(user)

    assert (await client.get(f"/api/employees/{other['id']}", headers=emp_headers)).status_code == 403
    assert (
        await client.get(f"/api/employees/{other['id']}/reveal", headers=emp_headers)
    ).status_code == 403
    assert (
        await client.post(
            f"/api/employees/{other['id']}/anonymize", json={"confirm": "Kovács"}, headers=emp_headers
        )
    ).status_code == 403
    # a saját export viszont megy, és csak a saját rekordot adja
    mine = await client.get("/api/me/data-export", headers=emp_headers)
    assert mine.status_code == 200
    assert mine.json()["employee"]["last_name"] == "Teszt"
