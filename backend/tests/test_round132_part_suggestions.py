"""132. kör: alkatrész-ajánló hibakódból — előzmény ugyanazon géptípusról ugyanazzal
a kóddal, kompatibilis cikkek, tudástár, autós készlet jelzése."""

from __future__ import annotations

from datetime import date

from tests.test_inventory import asset_payload
from tests.test_parts import _enable_modules
from tests.test_parts_b import _emp, _setup


async def _machine_task(client, adm, emp, asset_id: str, title: str):
    res = await client.post("/api/tasks", json={
        "title": title, "employee_id": str(emp.id), "due_date": date.today().isoformat(), "asset_id": asset_id,
    }, headers=adm)
    assert res.status_code == 201, res.text
    return res.json()


async def test_part_suggestions_from_error_code(client, admin):
    _, adm = admin
    await _enable_modules(["parts", "tasks", "machines", "service", "employees", "support"])
    user, emp_h, emp = await _emp("szerelo@example.com")
    _central, _van, part = await _setup(client, adm, user)  # BOIL-TERM, 4 db az autón
    # második cikk: kompatibilisnek jelölve, nincs az autón
    pump = (await client.post("/api/parts", json={
        "sku": "PUMP-ULKA", "name": "Szivattyú Ulka EX5", "compatible_models": ["Jura X8", "Jura E8"],
    }, headers=adm)).json()

    # két azonos típusú gép (Jura X8)
    a1 = (await client.post("/api/assets", json=asset_payload(barcode="JURA-X8-1", name="X8 Professional", manufacturer="Jura", model="X8"), headers=adm)).json()
    a2 = (await client.post("/api/assets", json=asset_payload(barcode="JURA-X8-2", name="X8 Professional", manufacturer="Jura", model="X8"), headers=adm)).json()

    # korábbi munkalap az 1. gépen: E8 hiba → termosztát beépítve
    old = await _machine_task(client, adm, emp, a1["id"], "Régi javítás")
    res = await client.put(f"/api/me/tasks/{old['id']}/worksheet", json={
        "work_description": "Termosztát csere", "problem_text": "E8 hibakód, nem melegít", "solution_text": "Bojler-termosztát csere megoldotta",
    }, headers=emp_h)
    assert res.status_code == 200, res.text
    res = await client.post("/api/parts/use", json={"task_id": old["id"], "part_id": part["id"], "quantity": 1}, headers=emp_h)
    assert res.status_code == 201, res.text
    # másik korábbi munkalap más kóddal → nem számít
    other = await _machine_task(client, adm, emp, a1["id"], "Más hiba")
    await client.put(f"/api/me/tasks/{other['id']}/worksheet", json={"work_description": "Tömítés", "problem_text": "E3 csöpög"}, headers=emp_h)
    await client.post("/api/parts/use", json={"task_id": other["id"], "part_id": pump["id"], "quantity": 1}, headers=emp_h)

    # tudástár-bejegyzés a típushoz
    res = await client.post("/api/knowledge", json={"machine_label": "Jura X8 Professional", "title": "E8 gyakori oka", "content": "E8-nál először a bojler-termosztátot nézd."}, headers=adm)
    assert res.status_code in (200, 201), res.text

    # új feladat a 2. gépen, ugyanazzal a kóddal
    new = await _machine_task(client, adm, emp, a2["id"], "Új javítás")
    res = await client.get(f"/api/me/tasks/{new['id']}/part-suggestions?q=err%20E8%20nem%20melegit", headers=emp_h)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["machine_label"] == "Jura X8 Professional"
    assert body["codes"] == ["ERR8", "E8"] or "E8" in body["codes"]
    hist = body["history"]
    assert [h["sku"] for h in hist] == ["BOIL-TERM"]
    assert hist[0]["times"] == 1 and hist[0]["on_vehicle"] == 3.0  # 4 − 1 beépítve
    comp = body["compatible"]
    assert [c["sku"] for c in comp] == ["PUMP-ULKA"] and comp[0]["on_vehicle"] == 0
    # a kézi bejegyzés + a régi munkalap megoldásából automatikusan készült (kb_auto) is E8-as
    assert "E8 gyakori oka" in [k["title"] for k in body["knowledge"]]

    # kód nélkül: a problem_text-ből sem jön → nincs előzmény, de kompatibilis + tudástár van
    res = await client.get(f"/api/me/tasks/{new['id']}/part-suggestions", headers=emp_h)
    body = res.json()
    assert body["codes"] == [] and body["history"] == []
    assert [c["sku"] for c in body["compatible"]] == ["PUMP-ULKA"]
    assert len(body["knowledge"]) >= 1

    # idegen feladat → 404
    _u2, h2, _e2 = await _emp("masik@example.com")
    assert (await client.get(f"/api/me/tasks/{new['id']}/part-suggestions?q=E8", headers=h2)).status_code == 404
