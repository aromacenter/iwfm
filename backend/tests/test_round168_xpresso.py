"""168. kör (x-presso jegyek): gépcsere leolvashatatlan számlálóval (9aee0a88) és
készlet-ellenőrzési feladat a fix bérleti számlázás napján (125b8394)."""

from __future__ import annotations

from datetime import date, timedelta

from tests.conftest import make_employee_record
from tests.test_inventory import asset_payload, make_partner
from tests.test_round102 import no_invoice  # noqa: F401  (fixture)


async def test_swap_with_unreadable_counter_waits_for_service(client, manager):
    mgr_user, mgr = manager
    await make_employee_record(mgr_user, last_name="Csere", first_name="Béla")
    partner = await make_partner(client, mgr, name="Zárlatos Kft.")
    old = (await client.post("/api/assets", json=asset_payload(barcode="99900011122", serial_number="SN-OLD-Z", tangible=True, counter=1000), headers=mgr)).json()
    assert (await client.post(f"/api/assets/{old['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr)).status_code == 200
    new = (await client.post("/api/assets", json=asset_payload(barcode="99900033344", serial_number="SN-NEW-Z", tangible=True), headers=mgr)).json()
    # csere: a számláló nem olvasható → az előző állás a helyőrző, függő rögzítés
    res = await client.post(f"/api/assets/{old['id']}/swap", json={"replacement_asset_id": new["id"], "counter_unreadable": True, "new_counter": 0}, headers=mgr)
    assert res.status_code == 200, res.text
    ctx = (await client.get(f"/api/partners/{partner['id']}/settlement-context", headers=mgr)).json()
    m = next(x for x in ctx["machines"] if x["asset_id"] == old["id"])
    assert m["swapped"] is True and m["swap_counter_pending"] is True and m["swap_final_counter"] == 1000
    # a csere-feladat leírása figyelmeztet
    tasks = (await client.get("/api/me/tasks", headers=mgr)).json()
    items = tasks if isinstance(tasks, list) else tasks.get("items", [])
    task = next(t for t in items if "Gépcsere" in t["title"])
    assert "nem volt leolvasható" in (task["description"] or "") and task["asset"]["swap_counter_pending"] is True
    # elszámolás nem zárható le, amíg a szerviz nem rögzíti
    res = await client.post("/api/settlements", json={"partner_id": partner["id"], "payment_method": "cash", "machines": [{"asset_id": old["id"], "new_counter": 1000}], "lines": []}, headers=mgr)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "settlement.swap_counter_pending"
    # a szerelő a munkalapon rögzíti: kisebb az előzőnél → 422; jó → rendben
    assert (await client.post(f"/api/me/tasks/{task['id']}/swap-counter", json={"counter": 900}, headers=mgr)).status_code == 422
    res = await client.post(f"/api/me/tasks/{task['id']}/swap-counter", json={"counter": 1250}, headers=mgr)
    assert res.status_code == 200, res.text
    assert res.json()["asset"]["swap_counter_pending"] is False
    # másodszor már nincs függő rögzítés
    assert (await client.post(f"/api/assets/{old['id']}/swap-counter", json={"counter": 1300}, headers=mgr)).status_code == 422
    ctx = (await client.get(f"/api/partners/{partner['id']}/settlement-context", headers=mgr)).json()
    m = next(x for x in ctx["machines"] if x["asset_id"] == old["id"])
    assert m["swap_counter_pending"] is False and m["swap_final_counter"] == 1250
    # az elszámolás-kapu kinyílt: a gép újra elszámolható (a lefőzött adag = 1250 − 1000)
    assert m["prev_counter"] == 1000


async def test_fixed_rental_creates_restock_task(client, manager, no_invoice):
    import app.db as app_db
    from app.services.wfm.rental_billing import next_workday, run_fixed_rental_billing
    from tests.test_round102 import _mk_rental_partner  # noqa: F401

    mgr_user, mgr = manager
    emp = await make_employee_record(mgr_user, last_name="Képviselő", first_name="Kata")
    today = date.today()
    p = await _mk_rental_partner(client, mgr, "Bérlő Feladatos", machines=1)
    # a képviselő a manager (saját kartonnal)
    res = await client.patch(f"/api/partners/{p['id']}", json={"name": p["name"], "tax_number": p.get("tax_number"), "agent_user_id": str(mgr_user.id)}, headers=mgr)
    assert res.status_code == 200, res.text
    assert (await client.post(f"/api/partners/{p['id']}/contracts", json={
        "valid_from": "2026-01-01", "no_minimum": True, "fixed_rental": True, "billing_day": min(today.day, 28),
        "payment_method": "transfer", "payment_terms_days": 8,
    }, headers=mgr)).status_code == 201
    factory = app_db.get_session_factory()
    async with factory() as db:
        assert await run_fixed_rental_billing(db) == 1
    tasks = (await client.get("/api/me/tasks", headers=mgr)).json()
    items = tasks if isinstance(tasks, list) else tasks.get("items", [])
    task = next(t for t in items if t["title"].startswith("Készlet-ellenőrzés és feltöltés: Bérlő Feladatos"))
    assert task["due_date"] == next_workday(today).isoformat() and "töltsd fel" in task["description"]
    # ugyanabban a hónapban nem duplázódik
    async with factory() as db:
        await run_fixed_rental_billing(db)
    items2 = (await client.get("/api/me/tasks", headers=mgr)).json()
    items2 = items2 if isinstance(items2, list) else items2.get("items", [])
    assert sum(1 for t in items2 if t["title"].startswith("Készlet-ellenőrzés és feltöltés: Bérlő Feladatos")) == 1
    # munkanap-számítás: szombat → hétfő
    sat = today + timedelta(days=(5 - today.weekday()) % 7)
    assert next_workday(sat).weekday() == 0 or next_workday(sat) == sat  # áthelyezett munkanap lehet
    void = emp
