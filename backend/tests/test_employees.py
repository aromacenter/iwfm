"""Employee master data: CRUD, encryption, masking, reveal audit, RBAC."""

from sqlalchemy import select

import app.db as app_db
from app.models import AuditEvent, Employee
from tests.conftest import gen_bank_account, gen_tax_id, gen_taj


def employee_payload(**overrides) -> dict:
    payload = {
        "email": "uj.dolgozo@example.com",
        "last_name": "Kovács",
        "first_name": "Anna",
        "mother_name": "Szabó Mária",
        "birth_place": "Budapest",
        "birth_date": "1992-04-13",
        "hire_date": "2026-01-05",
        "job_title": "raktáros",
        "feor_code": "9223",
        "tax_id": gen_tax_id(),
        "taj": gen_taj(),
        "bank_account": gen_bank_account(),
        "wage_amount": "450000 HUF/hó",
    }
    payload.update(overrides)
    return payload


async def test_create_employee_encrypts_and_masks(client, admin):
    _, headers = admin
    res = await client.post("/api/employees", json=employee_payload(), headers=headers)
    assert res.status_code == 201, res.text
    body = res.json()
    # maszkolt formában jön vissza, plaintext sosem
    assert body["tax_id_masked"].startswith("•••")
    assert "tax_id" not in body
    assert body["has_wage"] is True

    # az adatbázisban titkosítva van — a nyers bájtok nem tartalmazzák az értéket
    factory = app_db.get_session_factory()
    async with factory() as session:
        emp = (await session.execute(select(Employee))).scalars().first()
        assert emp.tax_id_encrypted is not None
        assert gen_tax_id().encode() not in emp.tax_id_encrypted


async def test_deactivating_employee_disables_login(client, admin):
    """H2: a dolgozó inaktiválása letiltja a login-fiókot is."""
    _, headers = admin
    created = (
        await client.post(
            "/api/employees", json=employee_payload(email="kilepo@example.com"), headers=headers
        )
    ).json()
    emp_id = created["id"]
    password = created["generated_password"]
    assert password  # rendszer-generált, egyszer látszik

    creds = {"email": "kilepo@example.com", "password": password}
    assert (await client.post("/api/auth/login", json=creds)).status_code == 200

    upd = await client.patch(f"/api/employees/{emp_id}", json={"status": "inactive"}, headers=headers)
    assert upd.status_code == 200
    assert (await client.post("/api/auth/login", json=creds)).status_code == 401

    # visszaaktiválás újra engedi a belépést
    await client.patch(f"/api/employees/{emp_id}", json={"status": "active"}, headers=headers)
    assert (await client.post("/api/auth/login", json=creds)).status_code == 200


async def test_admin_reset_password(client, admin):
    """M1: admin új ideiglenes jelszót ad, a régi munkamenet/jelszó érvénytelen."""
    _, headers = admin
    created = (
        await client.post(
            "/api/employees", json=employee_payload(email="reset@example.com"), headers=headers
        )
    ).json()
    emp_id = created["id"]
    old_pw = created["generated_password"]

    creds_old = {"email": "reset@example.com", "password": old_pw}
    assert (await client.post("/api/auth/login", json=creds_old)).status_code == 200

    res = await client.post(f"/api/employees/{emp_id}/reset-password", headers=headers)
    assert res.status_code == 200
    new_pw = res.json()["generated_password"]
    assert new_pw and new_pw != old_pw

    assert (await client.post("/api/auth/login", json=creds_old)).status_code == 401
    creds_new = {"email": "reset@example.com", "password": new_pw}
    assert (await client.post("/api/auth/login", json=creds_new)).status_code == 200


async def test_invalid_tax_id_rejected(client, admin):
    _, headers = admin
    res = await client.post(
        "/api/employees", json=employee_payload(tax_id="8123456789"), headers=headers
    )
    assert res.status_code == 422
    assert "tax_id" in res.json()["detail"]["fields"]


async def test_invalid_taj_rejected(client, admin):
    _, headers = admin
    res = await client.post(
        "/api/employees", json=employee_payload(taj="123456789"), headers=headers
    )
    assert res.status_code == 422


async def test_invalid_bank_account_rejected(client, admin):
    _, headers = admin
    res = await client.post(
        "/api/employees", json=employee_payload(bank_account="1234567812345678"), headers=headers
    )
    assert res.status_code == 422


async def test_duplicate_email_conflict(client, admin):
    _, headers = admin
    await client.post("/api/employees", json=employee_payload(), headers=headers)
    res = await client.post("/api/employees", json=employee_payload(), headers=headers)
    assert res.status_code == 409


async def test_reveal_returns_plaintext_and_audits(client, admin):
    _, headers = admin
    tax_id = gen_tax_id(5)
    created = await client.post(
        "/api/employees", json=employee_payload(tax_id=tax_id), headers=headers
    )
    emp_id = created.json()["id"]

    res = await client.get(f"/api/employees/{emp_id}/reveal", headers=headers)
    assert res.status_code == 200
    assert res.json()["tax_id"] == tax_id
    assert res.json()["wage_amount"] == "450000 HUF/hó"

    factory = app_db.get_session_factory()
    async with factory() as session:
        actions = (
            (await session.execute(select(AuditEvent.action))).scalars().all()
        )
    assert "employee.reveal" in actions


async def test_employee_role_cannot_list_or_reveal(client, admin, employee_user):
    _, admin_headers = admin
    _, emp_headers, _ = employee_user
    created = await client.post(
        "/api/employees", json=employee_payload(), headers=admin_headers
    )
    emp_id = created.json()["id"]

    assert (await client.get("/api/employees", headers=emp_headers)).status_code == 403
    assert (
        await client.get(f"/api/employees/{emp_id}/reveal", headers=emp_headers)
    ).status_code == 403


async def test_manager_can_list_but_not_create(client, admin, manager):
    _, admin_headers = admin
    _, mgr_headers = manager
    await client.post("/api/employees", json=employee_payload(), headers=admin_headers)
    assert (await client.get("/api/employees", headers=mgr_headers)).status_code == 200
    res = await client.post("/api/employees", json=employee_payload(
        email="masik@example.com"), headers=mgr_headers)
    assert res.status_code == 403


async def test_patch_updates_fields_and_sensitive(client, admin):
    _, headers = admin
    created = await client.post("/api/employees", json=employee_payload(), headers=headers)
    emp_id = created.json()["id"]

    new_taj = gen_taj(7)
    res = await client.patch(
        f"/api/employees/{emp_id}",
        json={"job_title": "műszakvezető", "taj": new_taj},
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["job_title"] == "műszakvezető"

    reveal = await client.get(f"/api/employees/{emp_id}/reveal", headers=headers)
    assert reveal.json()["taj"] == new_taj


async def test_unknown_employee_404(client, admin):
    _, headers = admin
    assert (await client.get("/api/employees/nem-uuid", headers=headers)).status_code == 404
    assert (
        await client.get(
            "/api/employees/00000000-0000-0000-0000-000000000000", headers=headers
        )
    ).status_code == 404
