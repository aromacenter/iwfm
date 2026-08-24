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
              "accessories": "víztartály, tápkábel", "faults": "nem melegít"},
        headers=sz,
    )
    assert res.status_code == 201, res.text
    intake = res.json()
    assert intake["serial"].startswith("AT-")
    assert intake["asset_manufacturer"] == "Jura"
    assert intake["client_name"] == "Kovács Ügyfél"
    assert intake["received_at"]

    rows = (await client.get("/api/intakes", headers=sz)).json()
    assert any(r["serial"] == intake["serial"] for r in rows)

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
