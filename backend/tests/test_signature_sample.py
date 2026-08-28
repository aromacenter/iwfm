"""Aláírás-minta (dolgozói profil) + kötelező aláíró-név az ügyfél/partner
aláírásoknál."""

from __future__ import annotations

import base64

from tests.conftest import make_employee_record, make_user

PNG = "data:image/png;base64," + base64.b64encode(
    b"\x89PNG\r\n\x1a\n0000000000000000"
).decode()


async def test_signature_sample_roundtrip(client, manager):
    _, mgr = manager
    # mentés
    res = await client.put("/api/auth/me/signature", json={"signature": PNG}, headers=mgr)
    assert res.status_code == 200, res.text
    assert res.json()["has_signature"] is True
    # visszaolvasás
    res = await client.get("/api/auth/me/signature", headers=mgr)
    assert res.json()["signature"] == PNG
    # rossz formátum
    res = await client.put(
        "/api/auth/me/signature",
        json={"signature": "data:text/plain;base64,aGVsbG8="}, headers=mgr,
    )
    assert res.status_code == 422
    # törlés
    res = await client.put("/api/auth/me/signature", json={"signature": None}, headers=mgr)
    assert res.json()["has_signature"] is False
    res = await client.get("/api/auth/me/signature", headers=mgr)
    assert res.json()["signature"] is None


async def test_client_signature_requires_typed_name(client, manager):
    """Ügyfél-aláírás begépelt név nélkül 422; névvel megy, és a név a
    munkalap-válaszban is megjelenik."""
    _, mgr = manager
    user, emp_headers = await make_user(email="signer@example.com", role="employee")
    emp = await make_employee_record(user, last_name="Aláíró", first_name="Ede")
    res = await client.post(
        "/api/tasks",
        json={"title": "Aláírós feladat", "employee_id": str(emp.id),
              "due_date": "2026-09-30"},
        headers=mgr,
    )
    task_id = res.json()["id"]

    base = {"work_description": "kész", "works": [], "repair_options": [], "materials": []}
    res = await client.put(
        f"/api/me/tasks/{task_id}/worksheet",
        json={**base, "client_signature": PNG},
        headers=emp_headers,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "worksheet.signer_name_required"

    res = await client.put(
        f"/api/me/tasks/{task_id}/worksheet",
        json={**base, "client_signature": PNG, "client_signer_name": "Vevő Vince"},
        headers=emp_headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["client_signer_name"] == "Vevő Vince"
    assert res.json()["has_client_signature"] is True
