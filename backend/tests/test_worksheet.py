"""Munkalap: kitöltés (dolgozó+vezető), sorszám, aláírás-validálás, PDF, IDOR."""

import base64
from datetime import date

from tests.conftest import make_employee_record, make_user

# 1x1 px átlátszó PNG — érvényes aláírás-minta
TINY_PNG = "data:image/png;base64," + base64.b64encode(
    base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
).decode()


async def task_for(client, mgr, emp, title="Szerelés"):
    res = await client.post(
        "/api/tasks",
        json={"title": title, "employee_id": str(emp.id), "due_date": date.today().isoformat()},
        headers=mgr,
    )
    return res.json()


def ws_payload(**kw) -> dict:
    base = {
        "work_description": "Kicseréltem a kapcsolót és teszteltem az áramkört.",
        "materials": [{"name": "kapcsoló", "qty": "1", "unit": "db"}],
        "hours_spent": 1.5,
        "client_name": "Minta Kft.",
        "client_location": "Budapest, Fő u. 1.",
        "employee_signature": TINY_PNG,
        "client_signature": TINY_PNG,
    }
    base.update(kw)
    return base


async def make_emp(email="lapos@example.com"):
    user, headers = await make_user(email=email, role="employee")
    emp = await make_employee_record(user)
    return emp, headers


async def test_employee_fills_worksheet(client, manager):
    _, mgr = manager
    emp, emp_h = await make_emp()
    task = await task_for(client, mgr, emp)

    res = await client.put(
        f"/api/me/tasks/{task['id']}/worksheet", json=ws_payload(), headers=emp_h
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["serial"].startswith("ML-")
    assert body["has_employee_signature"] is True
    assert body["materials"][0]["name"] == "kapcsoló"
    # a mentett aláírás visszajön (újranyitáskor a felület megjeleníti),
    # és üres/None aláírással mentve NEM törlődik
    assert body["employee_signature"].startswith("data:image/png;base64,")
    res = await client.put(
        f"/api/me/tasks/{task['id']}/worksheet",
        json={**ws_payload(), "employee_signature": None, "client_signature": ""},
        headers=emp_h,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["has_employee_signature"] is True
    assert body["has_client_signature"] is True

    # frissítés: a sorszám marad
    res2 = await client.put(
        f"/api/me/tasks/{task['id']}/worksheet",
        json=ws_payload(work_description="Javítva + bővítve."),
        headers=emp_h,
    )
    assert res2.json()["serial"] == body["serial"]
    assert res2.json()["work_description"] == "Javítva + bővítve."


async def test_serials_increment(client, manager):
    _, mgr = manager
    emp, emp_h = await make_emp()
    t1 = await task_for(client, mgr, emp, title="Első")
    t2 = await task_for(client, mgr, emp, title="Második")
    s1 = (
        await client.put(f"/api/me/tasks/{t1['id']}/worksheet", json=ws_payload(), headers=emp_h)
    ).json()["serial"]
    s2 = (
        await client.put(f"/api/me/tasks/{t2['id']}/worksheet", json=ws_payload(), headers=emp_h)
    ).json()["serial"]
    assert s1 != s2
    assert int(s2.split("-")[-1]) == int(s1.split("-")[-1]) + 1


async def test_bad_signature_rejected(client, manager):
    _, mgr = manager
    emp, emp_h = await make_emp()
    task = await task_for(client, mgr, emp)
    res = await client.put(
        f"/api/me/tasks/{task['id']}/worksheet",
        json=ws_payload(employee_signature="data:text/html;base64,PHNjcmlwdD4="),
        headers=emp_h,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "worksheet.bad_signature"


async def test_worksheet_idor_protected(client, manager):
    _, mgr = manager
    emp1, _ = await make_emp(email="sajatlap@example.com")
    _, other_h = await make_emp(email="masiklap@example.com")
    task = await task_for(client, mgr, emp1)
    res = await client.put(
        f"/api/me/tasks/{task['id']}/worksheet", json=ws_payload(), headers=other_h
    )
    assert res.status_code == 404


async def test_manager_pdf_download(client, manager):
    _, mgr = manager
    emp, emp_h = await make_emp()
    task = await task_for(client, mgr, emp)
    await client.put(f"/api/me/tasks/{task['id']}/worksheet", json=ws_payload(), headers=emp_h)

    res = await client.get(f"/api/tasks/{task['id']}/worksheet/pdf", headers=mgr)
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content[:5] == b"%PDF-"
    assert "ML-" in res.headers["content-disposition"]


async def test_employee_can_download_own_pdf(client, manager):
    """A dolgozó letöltheti a SAJÁT munkalapja PDF-jét (megosztáshoz)."""
    _, mgr = manager
    emp, emp_h = await make_emp(email="sajatpdf@example.com")
    task = await task_for(client, mgr, emp)
    await client.put(f"/api/me/tasks/{task['id']}/worksheet", json=ws_payload(), headers=emp_h)

    res = await client.get(f"/api/me/tasks/{task['id']}/worksheet/pdf", headers=emp_h)
    assert res.status_code == 200, res.text
    assert res.content[:5] == b"%PDF-"


async def test_worksheet_from_photo_validation(client, manager):
    """AI nélkül 422 (nincs konfig); hibás kép 422 (bad_photo)."""
    _, mgr = manager
    emp, _ = await make_emp(email="foto@example.com")
    task = await task_for(client, mgr, emp)

    no_ai = await client.post(
        f"/api/tasks/{task['id']}/worksheet/from-photo",
        json={"image": TINY_PNG},
        headers=mgr,
    )
    assert no_ai.status_code == 422
    assert no_ai.json()["detail"]["code"] == "settings.ai_not_configured"

    bad = await client.post(
        f"/api/tasks/{task['id']}/worksheet/from-photo",
        json={"image": "nem-data-url"},
        headers=mgr,
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "tasks.bad_photo"


async def test_worksheet_email_requires_smtp(client, manager):
    """Email-küldés SMTP nélkül 422-t ad (a beállítás hiányzik)."""
    _, mgr = manager
    emp, emp_h = await make_emp(email="emailpdf@example.com")
    task = await task_for(client, mgr, emp)
    await client.put(f"/api/me/tasks/{task['id']}/worksheet", json=ws_payload(), headers=emp_h)

    res = await client.post(
        f"/api/tasks/{task['id']}/worksheet/email",
        json={"to": "ugyfel@example.com"},
        headers=mgr,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "settings.email_not_configured"


async def test_task_creation_issues_worksheet(client, manager):
    """A feladat létrehozása eleve online munkalapot állít ki."""
    _, mgr = manager
    emp, _ = await make_emp(email="kiallit@example.com")
    res = await client.post(
        "/api/tasks",
        json={
            "title": "Kazáncsere",
            "employee_id": str(emp.id),
            "due_date": date.today().isoformat(),
            "client_name": "Minta Bt.",
            "client_location": "Győr, Ipar u. 3.",
        },
        headers=mgr,
    )
    assert res.status_code == 201
    body = res.json()
    assert body["worksheet_serial"].startswith("ML-")
    assert body["worksheet_completed"] is False  # még üres — a dolgozó tölti ki

    ws = (await client.get(f"/api/tasks/{body['id']}/worksheet", headers=mgr)).json()
    assert ws["client_name"] == "Minta Bt."
    assert ws["client_location"] == "Győr, Ipar u. 3."
    assert ws["work_description"] == ""

    # a kiállított (üres) munkalap is nyomtatható
    pdf = await client.get(f"/api/tasks/{body['id']}/worksheet/pdf", headers=mgr)
    assert pdf.status_code == 200
    assert pdf.content[:5] == b"%PDF-"


async def test_worksheet_completed_after_employee_fill(client, manager):
    _, mgr = manager
    emp, emp_h = await make_emp(email="kitolt@example.com")
    task = await task_for(client, mgr, emp)
    assert task["worksheet_completed"] is False
    await client.put(f"/api/me/tasks/{task['id']}/worksheet", json=ws_payload(), headers=emp_h)
    listed = (await client.get("/api/tasks", headers=mgr)).json()
    assert listed[0]["worksheet_completed"] is True


async def test_manager_can_edit_worksheet(client, manager):
    _, mgr = manager
    emp, emp_h = await make_emp()
    task = await task_for(client, mgr, emp)
    await client.put(f"/api/me/tasks/{task['id']}/worksheet", json=ws_payload(), headers=emp_h)
    res = await client.put(
        f"/api/tasks/{task['id']}/worksheet",
        json=ws_payload(hours_spent=3.0),
        headers=mgr,
    )
    assert res.status_code == 200
    assert res.json()["hours_spent"] == 3.0


async def test_employee_cannot_download_pdf(client, manager):
    _, mgr = manager
    emp, emp_h = await make_emp()
    task = await task_for(client, mgr, emp)
    await client.put(f"/api/me/tasks/{task['id']}/worksheet", json=ws_payload(), headers=emp_h)
    assert (
        await client.get(f"/api/tasks/{task['id']}/worksheet/pdf", headers=emp_h)
    ).status_code == 403


async def test_task_list_shows_worksheet_serial(client, manager):
    _, mgr = manager
    emp, emp_h = await make_emp()
    task = await task_for(client, mgr, emp)
    await client.put(f"/api/me/tasks/{task['id']}/worksheet", json=ws_payload(), headers=emp_h)
    listed = (await client.get("/api/tasks", headers=mgr)).json()
    assert listed[0]["worksheet_serial"].startswith("ML-")