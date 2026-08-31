"""Átvétel modul: elismervény sorszámozás, PDF, szerkeszthető záradék, és a
garanciális feltételek az ügyfél-munkalapon."""

from __future__ import annotations

from tests.conftest import make_employee_record, make_user


async def test_intake_flow_and_clause(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    res = await client.post(
        "/api/assets",
        json={"barcode": "INTAKE-1", "name": "Behozott Gép", "serial_number": "SN-9",
              "manufacturer": "Jura", "customer_owned": True},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    asset = res.json()

    # szervizes szerepkör is rögzíthet átvételt
    _, sz = await make_user(email="atvevo@example.com", role="szervizes")
    res = await client.post(
        "/api/intakes",
        json={"asset_id": asset["id"], "client_name": "Kovács Ügyfél",
              "client_company": "Kovács Kft.", "client_email": "kovacs@example.com",
              "client_address": "1111 Budapest, Fő u. 1.",
              "accessories": "víztartály, tápkábel", "faults": "nem melegít"},
        headers=sz,
    )
    assert res.status_code == 201, res.text
    intake = res.json()
    assert intake["serial"].startswith("AT-")
    assert intake["asset_manufacturer"] == "Jura"
    assert intake["client_name"] == "Kovács Ügyfél"
    assert intake["client_company"] == "Kovács Kft."
    assert intake["client_email"] == "kovacs@example.com"
    assert intake["client_address"] == "1111 Budapest, Fő u. 1."
    assert intake["received_at"]

    rows = (await client.get("/api/intakes", headers=sz)).json()
    assert any(r["serial"] == intake["serial"] for r in rows)

    # állapot-fotós átvétel: tárolás + galéria + kép-visszakérés
    import base64 as _b64

    jpg = "data:image/jpeg;base64," + _b64.b64encode(b"\xff\xd8\xff fake-jpg").decode()
    res = await client.post(
        "/api/intakes",
        json={"asset_id": asset["id"], "client_name": "Fotós Ügyfél",
              "faults": "törött víztartály", "photos": [jpg, jpg]},
        headers=sz,
    )
    assert res.status_code == 201, res.text
    fotos = res.json()
    assert fotos["photo_count"] == 2
    ids = (await client.get(f"/api/intakes/{fotos['id']}/photos", headers=sz)).json()
    assert len(ids) == 2
    res = await client.get(
        f"/api/intakes/{fotos['id']}/photos/{ids[0]['id']}", headers=sz
    )
    assert res.status_code == 200
    assert res.content.startswith(b"\xff\xd8\xff")
    # rossz formátum: érthető hiba
    res = await client.post(
        "/api/intakes",
        json={"asset_id": asset["id"], "photos": ["data:text/html;base64,PGI+"]},
        headers=sz,
    )
    assert res.status_code == 422

    res = await client.get(f"/api/intakes/{intake['id']}/pdf", headers=sz)
    assert res.status_code == 200 and res.content[:4] == b"%PDF"

    # a záradék és a garanciaszöveg adminból szerkeszthető
    ws = (await client.get("/api/settings/worksheet", headers=adm)).json()
    assert "60 napig" in ws["intake_footer_default"]
    assert "Garanciális feltételek" in ws["customer_footer_default"]
    res = await client.put(
        "/api/settings/worksheet",
        json={"accent_color": "#1e40af", "intake_footer_text": "Egyedi záradék.",
              "customer_footer_text": "Egyedi garancia."},
        headers=adm,
    )
    assert res.status_code == 200, res.text
    assert res.json()["intake_footer_text"] == "Egyedi záradék."
    res = await client.get(f"/api/intakes/{intake['id']}/pdf", headers=sz)
    assert res.status_code == 200

    # törlés csak delete-joggal (szervizesnek nincs)
    res = await client.delete(f"/api/intakes/{intake['id']}", headers=sz)
    assert res.status_code == 403
    res = await client.delete(f"/api/intakes/{intake['id']}", headers=adm)
    assert res.status_code == 204


async def test_new_automation_triggers_registered(client, admin):
    """Az új triggerek (feladat kiosztva, munkalap aláírva, gép átvéve) a
    trigger-listában és a Telegram-sablonok közt is szerepelnek."""
    from app.services.wfm.automation import TRIGGERS
    from app.services.wfm.telegram import EVENT_TEMPLATES

    for ev in ("task.assigned", "worksheet.signed", "intake.created"):
        assert ev in TRIGGERS, ev
        assert ev in EVENT_TEMPLATES, ev
    _, adm = admin
    res = await client.get("/api/automation/triggers", headers=adm)
    assert res.status_code == 200
    assert "intake.created" in res.json()["triggers"]


async def test_customer_worksheet_pdf_still_builds_with_warranty(client, admin, manager):
    """A KSZ ügyfél-példány (garanciális lábléccel) hibamentesen készül el."""
    _, adm = admin
    _, mgr = manager
    res = await client.post(
        "/api/assets", json={"barcode": "WARR-1", "name": "Garanciás Gép"}, headers=mgr
    )
    asset = res.json()
    user, _h = await make_user(email="warrguy@example.com", role="szervizes")
    emp = await make_employee_record(user, last_name="Warr", first_name="Géza")
    res = await client.post(
        "/api/tasks",
        json={"title": "Garancia teszt", "employee_id": str(emp.id),
              "due_date": "2026-09-10", "external_service": True,
              "asset_id": asset["id"]},
        headers=mgr,
    )
    task_id = res.json()["id"]
    await client.put(
        f"/api/tasks/{task_id}/worksheet",
        json={"work_description": "belső", "materials": [],
              "customer_note": "Ügyfélnek szánt megjegyzés"},
        headers=mgr,
    )
    res = await client.get(
        f"/api/tasks/{task_id}/worksheet/pdf?variant=customer", headers=mgr
    )
    assert res.status_code == 200 and res.content[:4] == b"%PDF"


async def test_intake_task_link(client, manager):
    """Atvetelbol kiadott munkalap: a kapcsolat tarolodik, az atvetel-lista
    jelzi (task_id) — a felulet igy nem engedi ujra kiadni."""
    from tests.conftest import make_employee_record, make_user
    from tests.test_service import make_asset

    _, mgr = manager
    asset = await make_asset(client, mgr, barcode="AT-LINK-1")
    intake = (
        await client.post(
            "/api/intakes",
            json={"asset_id": asset["id"], "client_name": "Linkes Lajos",
                  "faults": "csopog"},
            headers=mgr,
        )
    ).json()
    listed = (await client.get("/api/intakes", headers=mgr)).json()
    row = next(r for r in listed if r["id"] == intake["id"])
    assert row["task_id"] is None

    emp_user, _h = await make_user(email="linkes@example.com", role="employee")
    emp = await make_employee_record(emp_user)
    task = (
        await client.post(
            "/api/tasks",
            json={"title": "Javitas — csopogo gep", "employee_id": str(emp.id),
                  "due_date": "2026-09-02", "external_service": True,
                  "asset_id": asset["id"], "intake_id": intake["id"]},
            headers=mgr,
        )
    ).json()
    listed2 = (await client.get("/api/intakes", headers=mgr)).json()
    row2 = next(r for r in listed2 if r["id"] == intake["id"])
    assert row2["task_id"] == task["id"]
    assert row2["worksheet_serial"] == task["worksheet_serial"]
    assert row2["worksheet_completed"] is False
