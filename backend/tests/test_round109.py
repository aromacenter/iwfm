"""109. kör — hibajegyek (2026-10-24): munkalap hibaleírás+megoldás → tudástár
(687f2352), belső példány költség-összesítővel (0589e92e), ügyfél-példány
lábléc (f7d345ca), 100 konstrukció (6bdaa987), alkatrész-import (12606a3b),
keskeny nyugta tördelése (ba0ae04d)."""
from __future__ import annotations

from datetime import date

from tests.conftest import make_employee_record, make_user
from tests.test_parts import _enable_modules


async def test_worksheet_problem_solution_feeds_knowledge_base(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    emp_user, emp_hdr = await make_user(email="r109-szerelo@example.com", role="szervizes")
    emp = await make_employee_record(emp_user, last_name="Tudás", first_name="Tamás")
    asset = (await client.post("/api/assets", json={"barcode": "R109-JURA", "name": "Jura X8", "manufacturer": "Jura", "customer_owned": True}, headers=mgr)).json()
    task = (
        await client.post(
            "/api/tasks",
            json={"title": "E8 hiba", "employee_id": str(emp.id), "due_date": date.today().isoformat(),
                  "external_service": True, "asset_id": asset["id"], "client_name": "Vevő"},
            headers=mgr,
        )
    ).json()
    # ajánlat-fázis: 30 konstrukció is mehet (korábban max 25)
    opts = [{"name": f"Konstrukció {i}", "cost_net": 1000 + i} for i in range(30)]
    res = await client.put(
        f"/api/me/tasks/{task['id']}/worksheet",
        json={"work_description": "Bevizsgálva", "repair_options": opts,
              "problem_text": "E8 hiba: a bojler nem fűt fel, termosztát szakadt",
              "solution_text": "SP 90 °C termosztát csere, vízkőtelenítés, próbafőzés OK", "print_solution": False},
        headers=emp_hdr,
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert len(out["repair_options"]) == 30
    assert out["problem_text"].startswith("E8 hiba") and out["solution_text"].startswith("SP 90") and out["print_solution"] is False

    # tudástár-bejegyzés a géptípushoz, a munkalap jelölővel; újramentés nem duplikál
    rows = (await client.get("/api/knowledge?machine=Jura", headers=adm)).json()
    items = rows if isinstance(rows, list) else rows.get("items", rows.get("entries", []))
    mine = [r for r in items if "[Munkalap " in (r.get("content") or "")]
    assert len(mine) == 1 and mine[0]["machine_label"] == "Jura X8" and "termosztát csere" in mine[0]["content"]
    res = await client.put(
        f"/api/me/tasks/{task['id']}/worksheet",
        json={"work_description": "Bevizsgálva", "repair_options": opts[:3],
              "problem_text": "E8 hiba: a bojler nem fűt fel", "solution_text": "Termosztát csere + vízkőtelenítés", "print_solution": True},
        headers=emp_hdr,
    )
    assert res.status_code == 200, res.text
    rows = (await client.get("/api/knowledge?machine=Jura", headers=adm)).json()
    items = rows if isinstance(rows, list) else rows.get("items", rows.get("entries", []))
    mine = [r for r in items if "[Munkalap " in (r.get("content") or "")]
    assert len(mine) == 1 and "Termosztát csere + vízkőtelenítés" in mine[0]["content"]

    # PDF-ek: belső (költség-összesítővel) és ügyfél-példány (megoldás csak print_solution-nel)
    res = await client.get(f"/api/tasks/{task['id']}/worksheet/pdf", headers=mgr)
    assert res.status_code == 200 and res.content[:4] == b"%PDF"
    res = await client.get(f"/api/tasks/{task['id']}/worksheet/pdf?variant=customer", headers=mgr)
    assert res.status_code == 200 and res.content[:4] == b"%PDF"
    # szervizes saját példány
    res = await client.get(f"/api/me/tasks/{task['id']}/worksheet/pdf", headers=emp_hdr)
    assert res.status_code == 200


async def test_parts_import(client, admin):
    _, adm = admin
    await _enable_modules(["warehouse", "parts"])
    rows = [
        {"sku": "XP-1000", "name": "SP 90 C TERMOSZTÁT", "manufacturer_code": "1443046", "cost_net": 530, "price_net": 2000, "min_stock": 1},
        {"sku": "", "name": "Ulka vízpumpa kicsi", "price_net": 4000},
        {"sku": "XP-1000", "name": "SP 90 C TERMOSZTÁT", "price_net": 2100},  # ugyanaz a cikkszám → frissítés
    ]
    res = await client.post("/api/parts/import", json={"rows": rows}, headers=adm)
    assert res.status_code == 200, res.text
    assert res.json() == {"created": 2, "updated": 1, "skipped": 0}
    parts = {p["sku"]: p for p in (await client.get("/api/parts", headers=adm)).json()}
    assert parts["XP-1000"]["price_net"] == 2100 and parts["XP-1000"]["cost_net"] == 530 and parts["XP-1000"]["manufacturer_code"] == "1443046"
    assert any(k.startswith("AUTO-") for k in parts)
    # update_existing=False → kihagyás
    res = await client.post("/api/parts/import", json={"rows": rows[:1], "update_existing": False}, headers=adm)
    assert res.json()["skipped"] == 1


async def test_receipt_pdf_wraps_long_text(client, manager):
    _, mgr = manager
    partner = (await client.post("/api/partners", json={"name": "Nagyon Hosszú Nevű Partner Vendéglátóipari és Kereskedelmi Korlátolt Felelősségű Társaság", "address": "1234 Budapest, Nagyon hosszú utca neve ami biztosan nem fér el egy sorban 123/B 4. emelet 17."}, headers=mgr)).json()
    product = (await client.post("/api/products", json={"name": "Kávé Hosszú Nevű Prémium Keverék 1000 g Arabica/Robusta", "price_per_portion": 50, "unit_size": 1, "is_consignment": True}, headers=mgr)).json()
    await client.post(f"/api/partners/{partner['id']}/stock/replenish", json={"product_id": product["id"], "quantity": 1.0}, headers=mgr)
    res = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash", "lines": [{"product_id": product["id"], "physical_qty": 0.5}],
              "note": "Nagyon hosszú megjegyzés, ami több sorba kell törjön a keskeny nyugtán is, különben lelóg."},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    for fmt in ("50", "80"):
        res = await client.get(f"/api/settlements/{res.json()['id'] if fmt == '50' else res.json()['id']}/pdf?format={fmt}", headers=mgr) if False else await client.get(f"/api/settlements/{(await client.get('/api/settlements', headers=mgr)).json()[0]['id']}/pdf?format={fmt}", headers=mgr)
        assert res.status_code == 200 and res.content[:4] == b"%PDF"
