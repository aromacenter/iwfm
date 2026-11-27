"""190. kör: nyitott belső munkalap átadása külső szerviznek egy gombbal —
új KSZ-feladat a szervizesnek (gép, hibaleírás, fotók), az eredeti lezárva
hivatkozással; csak alvállalkozónak, csak belső, nyitott munkalapról."""

from __future__ import annotations

import base64
from datetime import date

from tests.conftest import make_employee_record, make_user


async def test_handoff_internal_worksheet_to_contractor(client, admin, manager, monkeypatch):
    import app.db as app_db
    from sqlalchemy import select

    from app.models import Worksheet, WorksheetPhoto

    _, adm = admin
    _, mgr = manager
    inner_user, _ = await make_user(email="belso190@example.com", role="szervizes")
    inner = await make_employee_record(inner_user, last_name="Belső", first_name="Béla")
    res = await client.post("/api/employees", json={
        "last_name": "Külsős", "first_name": "Kázmér", "email": "kulsos190@example.com", "hire_date": "2024-01-01",
        "weekly_hours": 40, "is_contractor": True,
    }, headers=adm)
    assert res.status_code == 201, res.text
    contractor = res.json()
    asset = (await client.post("/api/assets", json={"barcode": "HO-190", "name": "Jura Z10", "serial_number": "SN-190"}, headers=mgr)).json()

    res = await client.post("/api/tasks", json={
        "title": "Nem fűt a Z10", "employee_id": str(inner.id), "due_date": date.today().isoformat(),
        "asset_id": asset["id"], "description": "Az ügyfél szerint reggel óta hideg.",
    }, headers=mgr)
    assert res.status_code == 201, res.text
    task = res.json()
    assert task["worksheet_serial"].startswith("ML-")
    res = await client.put(f"/api/tasks/{task['id']}/worksheet", json={
        "work_description": "Bevizsgálva", "materials": [], "problem_text": "Fűtőbetét szakadt, cserére szorul.",
    }, headers=mgr)
    assert res.status_code == 200, res.text
    # fotó a belső munkalapon
    async with app_db.get_session_factory()() as db:
        ws = (await db.execute(select(Worksheet).where(Worksheet.serial == task["worksheet_serial"]))).scalar_one()
        db.add(WorksheetPhoto(worksheet_id=ws.id, image=base64.b64decode("iVBORw0KGgo="), mime="image/png"))
        await db.commit()

    # nem alvállalkozó → 422
    res = await client.post(f"/api/tasks/{task['id']}/handoff-external", json={"employee_id": str(inner.id)}, headers=mgr)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "tasks.handoff_not_contractor"

    res = await client.post(f"/api/tasks/{task['id']}/handoff-external", json={
        "employee_id": contractor["id"], "note": "Garanciális lehet, kérlek nézd meg a szivattyút is.",
    }, headers=mgr)
    assert res.status_code == 201, res.text
    new = res.json()
    assert new["worksheet_serial"].startswith("KSZ-") and new["worksheet_external"] is True
    assert new["employee_id"] == contractor["id"] and new["title"] == "Nem fűt a Z10"
    assert "Fűtőbetét szakadt" in (new["description"] or "") and "Belső munkalapból átadva: " + task["worksheet_serial"] in new["description"]
    assert "Garanciális lehet" in new["description"]
    # az eredeti lezárult hivatkozással
    old = next(t for t in (await client.get("/api/tasks", headers=mgr)).json() if t["id"] == task["id"])
    assert old["status"] == "done"
    old_ws = (await client.get(f"/api/tasks/{task['id']}/worksheet", headers=mgr)).json()
    assert new["worksheet_serial"] in (old_ws.get("solution_text") or "")
    # fotó és hibaleírás átment, a gép ugyanaz
    new_ws = (await client.get(f"/api/tasks/{new['id']}/worksheet", headers=mgr)).json()
    assert new_ws["problem_text"] == "Fűtőbetét szakadt, cserére szorul." and new_ws["external_service"] is True
    async with app_db.get_session_factory()() as db:
        nw = (await db.execute(select(Worksheet).where(Worksheet.serial == new["worksheet_serial"]))).scalar_one()
        assert str(nw.asset_id) == asset["id"]
        assert len((await db.execute(select(WorksheetPhoto).where(WorksheetPhoto.worksheet_id == nw.id))).scalars().all()) == 1
    # KSZ-ről nem adható tovább ezzel a gombbal; lezárt sem
    res = await client.post(f"/api/tasks/{new['id']}/handoff-external", json={"employee_id": contractor["id"]}, headers=mgr)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "tasks.handoff_not_internal"
    res = await client.post(f"/api/tasks/{task['id']}/handoff-external", json={"employee_id": contractor["id"]}, headers=mgr)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "tasks.handoff_closed"
    # napló
    log = (await client.get("/api/audit?action=task.handoff_external", headers=adm)).json()
    rows = log if isinstance(log, list) else log.get("items", [])
    assert rows and rows[0]["detail"]["to"] == new["worksheet_serial"] and rows[0]["detail"]["photos"] == 1
