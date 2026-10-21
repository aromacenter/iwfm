"""ERP-kiegészítő körök (1d553d2e, 2026-10-21): gép-történet + garancia +
sorozatszám, QR-címkék + szkennelés, autónkénti min/max + feltöltőlista +
tömeges kérés, beszállítók + rendelés + vételezés rendelésre + újrarendelés,
mozgó átlagár + készletérték, visszáru, kitek, leltár-munkamenet (vak,
okkód, jóváhagyás), ABC, riportok, raktáronkénti jog, timer időtípusok +
korrekció + bér-CSV + mért idő átvétele."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from tests.conftest import make_employee_record, make_user
from tests.test_parts import _enable_modules
from tests.test_parts_b import _emp, _setup, _task


async def test_round1_history_labels_levels(client, admin):
    _, adm = admin
    user, emp_h, emp = await _emp("r1@example.com")
    central, van, part = await _setup(client, adm, user)  # központ A-1: 1 db, autó: 4 db
    # garancia 12 hónap a cikken
    res = await client.patch(f"/api/parts/{part['id']}", json={"sku": "BOIL-TERM", "name": "Bojler-termosztát 125°C", "min_stock": 2, "price_net": 4500, "cost_net": 3100, "warranty_months": 12, "compatible_models": ["JL30", "JL35"]}, headers=adm)
    assert res.status_code == 200 and res.json()["warranty_months"] == 12 and res.json()["compatible_models"] == ["JL30", "JL35"]
    # gép + feladat géppel
    asset = (await client.post("/api/assets", json={"barcode": "JL30-0001", "name": "Jetinno JL30"}, headers=adm)).json()
    res = await client.post("/api/tasks", json={"title": "Bojler csere", "employee_id": str(emp.id), "due_date": date.today().isoformat(), "asset_id": asset["id"]}, headers=adm)
    assert res.status_code == 201, res.text
    task = res.json()
    # beépítés sorozatszámmal → garancia a cikkből, gép a munkalapból
    res = await client.post("/api/parts/use", json={"task_id": task["id"], "part_id": part["id"], "quantity": 1, "serial_no": "SN-777"}, headers=emp_h)
    assert res.status_code == 201, res.text
    line = res.json()[0]
    assert line["serial_no"] == "SN-777" and line["warranty_until"] is not None
    assert date.fromisoformat(line["warranty_until"]) > date.today() + timedelta(days=300)
    # gép-történet
    hist = (await client.get(f"/api/parts/asset/{asset['id']}/history", headers=adm)).json()
    assert len(hist) == 1 and hist[0]["serial_no"] == "SN-777" and hist[0]["warranty_active"] is True and hist[0]["worksheet_serial"].startswith("ML-")
    # lejáró garanciák: 30 napon belül nincs, 400 napon belül igen
    assert (await client.get("/api/parts/warranty?days=30", headers=adm)).json() == []
    exp = (await client.get("/api/parts/warranty?days=400", headers=adm)).json()
    assert len(exp) == 1 and exp[0]["asset_barcode"] == "JL30-0001"
    # storno után eltűnik a történetből
    res = await client.delete(f"/api/parts/use/{line['movement_id']}", headers=emp_h)
    assert res.status_code == 200
    assert (await client.get(f"/api/parts/asset/{asset['id']}/history", headers=adm)).json() == []
    # a PDF-en a sorozatszám is látszik (smoke)
    assert (await client.get(f"/api/tasks/{task['id']}/worksheet/pdf", headers=adm)).status_code == 200

    # címkék: helykódok + cikkek
    res = await client.get(f"/api/parts/labels.pdf?warehouse_id={central['id']}&parts=all", headers=adm)
    assert res.status_code == 200 and res.headers["content-type"] == "application/pdf" and res.content[:4] == b"%PDF"
    assert (await client.get("/api/parts/labels.pdf", headers=adm)).status_code == 422
    # szkennelés
    s = (await client.get(f"/api/parts/scan?code=PL|{central['id']}|a-1", headers=emp_h)).json()
    assert s["kind"] == "location" and s["location"] == "A-1" and s["warehouse_name"] == "Főraktár"
    s = (await client.get("/api/parts/scan?code=PP|boil-term", headers=emp_h)).json()
    assert s["kind"] == "part" and s["part_id"] == part["id"]
    assert (await client.get("/api/parts/scan?code=nincs-ilyen", headers=emp_h)).status_code == 404

    # autónkénti min/max → feltöltőlista → tömeges kérés
    res = await client.put(f"/api/parts/warehouses/{van['id']}/levels", json=[{"part_id": part["id"], "min_qty": 5, "max_qty": 8}], headers=adm)
    assert res.status_code == 200 and res.json()[0]["quantity"] == 4 and res.json()[0]["suggested"] == 4
    rep = (await client.get("/api/parts/my-replenish", headers=emp_h)).json()
    assert len(rep) == 1 and rep[0]["suggested"] == 4
    res = await client.post("/api/parts/requests/bulk", json={"items": [{"part_id": r["part_id"], "quantity": r["suggested"]} for r in rep]}, headers=emp_h)
    assert res.status_code == 201 and res.json()["created"] == 1
    reqs = (await client.get("/api/parts/requests", headers=emp_h)).json()
    assert reqs[0]["quantity"] == 4 and reqs[0]["note"] == "feltöltőlista"


async def test_round2_procurement_valuation_returns_kits(client, admin):
    _, adm = admin
    user, emp_h, emp = await _emp("r2@example.com")
    central, van, part = await _setup(client, adm, user)  # vételezés 5 db egységár nélkül → átlagár még nincs
    assert (await client.get("/api/parts", headers=adm)).json()[0]["avg_cost"] is None
    # beszállító + árlista
    res = await client.post("/api/parts/suppliers", json={"name": "Jetinno Europe", "lead_days": 5}, headers=adm)
    assert res.status_code == 201, res.text
    sup = res.json()
    res = await client.put(f"/api/parts/suppliers/{sup['id']}/prices", json=[{"part_id": part["id"], "price_net": 2900, "supplier_sku": "JT-125", "moq": 4}], headers=adm)
    assert res.status_code == 200 and res.json()["prices"][0]["price_net"] == 2900
    await client.patch(f"/api/parts/{part['id']}", json={"sku": "BOIL-TERM", "name": "Bojler-termosztát 125°C", "min_stock": 10, "price_net": 4500, "cost_net": 3100, "supplier_id": sup["id"], "reorder_qty": 20}, headers=adm)

    # újrarendelési javaslat: 5 db < min 10 → cél 20 − 5 = 15 (MOQ 4 alatt nem) · ár 2900 · beszállító
    sug = (await client.get("/api/parts/reorder", headers=adm)).json()
    assert len(sug) == 1 and sug[0]["suggested_qty"] == 15 and sug[0]["unit_cost"] == 2900 and sug[0]["supplier_name"] == "Jetinno Europe" and sug[0]["lead_days"] == 5
    # javaslatból piszkozat-rendelés
    res = await client.post("/api/parts/reorder/create-orders", headers=adm)
    assert res.status_code == 201 and len(res.json()) == 1
    po = res.json()[0]
    assert po["number"].startswith("PO-") and po["status"] == "draft" and po["items"][0]["qty_ordered"] == 15 and po["total_net"] == 15 * 2900
    # üres rendelés 422; szerkesztés piszkozatban; megrendelés → ordered; úton lévő csökkenti a javaslatot
    assert (await client.post("/api/parts/purchase-orders", json={"items": []}, headers=adm)).status_code == 422
    res = await client.patch(f"/api/parts/purchase-orders/{po['id']}", json={"supplier_id": sup["id"], "items": [{"part_id": part["id"], "qty_ordered": 10}]}, headers=adm)
    assert res.status_code == 200 and res.json()["items"][0]["unit_cost"] == 2900
    res = await client.post(f"/api/parts/purchase-orders/{po['id']}/order", headers=adm)
    assert res.json()["status"] == "ordered"
    assert (await client.patch(f"/api/parts/purchase-orders/{po['id']}", json={"items": [{"part_id": part["id"], "qty_ordered": 1}]}, headers=adm)).status_code == 422
    sug = (await client.get("/api/parts/reorder", headers=adm)).json()
    assert sug[0]["on_order"] == 10 and sug[0]["suggested_qty"] == 5
    # részleges vételezés rendelésre: 6 db @2900 A-2-re → partial; átlagár: (5×3100 + 6×2900)/11
    item_id = res.json()["items"][0]["id"]
    res = await client.post(f"/api/parts/purchase-orders/{po['id']}/receive", json={"warehouse_id": central["id"], "lines": [{"item_id": item_id, "quantity": 6, "location": "A-2"}], "document_no": "SZL-99"}, headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "partial" and res.json()["items"][0]["qty_received"] == 6
    p = next(x for x in (await client.get("/api/parts", headers=adm)).json() if x["id"] == part["id"])
    assert p["total_qty"] == 11 and abs(p["avg_cost"] - round((5 * 3100 + 6 * 2900) / 11, 2)) < 0.01 and p["cost_net"] == 2900
    mv = (await client.get(f"/api/parts/movements?part_id={part['id']}", headers=adm)).json()
    assert mv[0]["kind"] == "receipt" and mv[0]["document_no"] == "SZL-99"
    # maradék 4 → received
    res = await client.post(f"/api/parts/purchase-orders/{po['id']}/receive", json={"warehouse_id": central["id"], "lines": [{"item_id": item_id, "quantity": 4, "location": "A-2"}]}, headers=adm)
    assert res.json()["status"] == "received" and res.json()["received_at"]
    assert (await client.post(f"/api/parts/purchase-orders/{po['id']}/cancel", headers=adm)).status_code == 422

    # készletérték (az átlagár a 2. vételezéssel tovább mozgott)
    p = next(x for x in (await client.get("/api/parts", headers=adm)).json() if x["id"] == part["id"])
    val = (await client.get("/api/parts/valuation", headers=adm)).json()
    assert val["total_items"] == 1 and abs(val["total_value"] - round(15 * p["avg_cost"], 2)) < 0.5 and len(val["by_warehouse"]) == 2
    assert (await client.get("/api/parts/export.csv?what=valuation", headers=adm)).status_code == 200

    # csomagolási egység: 1 karton = 10 db, vételezés 2 kartonban @5000/karton → +20 db, egységár 500
    await client.patch(f"/api/parts/{part['id']}", json={"sku": "BOIL-TERM", "name": "Bojler-termosztát 125°C", "min_stock": 10, "price_net": 4500, "cost_net": 2900, "supplier_id": sup["id"], "pack_unit": "karton", "pack_qty": 10}, headers=adm)
    res = await client.post("/api/parts/receipt", json={"part_id": part["id"], "warehouse_id": central["id"], "location": "B-1", "quantity": 2, "unit_cost": 5000, "in_packs": True}, headers=adm)
    assert res.status_code == 201 and res.json()["quantity"] == 20
    p = next(x for x in (await client.get("/api/parts", headers=adm)).json() if x["id"] == part["id"])
    assert p["total_qty"] == 35 and p["cost_net"] == 500

    # karantén raktár + visszáru: 2 db A-2-ről a karanténba, onnan vissza a beszállítónak; csere érkezik
    q = (await client.post("/api/parts/warehouses", json={"name": "Karantén", "kind": "quarantine", "columns": [], "rows": 1}, headers=adm)).json()
    assert q["kind"] == "quarantine"
    res = await client.post("/api/parts/transfer", json={"part_id": part["id"], "from_warehouse_id": central["id"], "from_location": "A-2", "to_warehouse_id": q["id"], "quantity": 2}, headers=adm)
    assert res.status_code == 201
    res = await client.post("/api/parts/returns", json={"part_id": part["id"], "warehouse_id": q["id"], "quantity": 2, "supplier_id": sup["id"], "serial_no": "SN-1", "reason": "hibás termosztát"}, headers=adm)
    assert res.status_code == 201, res.text
    ret = res.json()
    assert ret["status"] == "open" and ret["supplier_name"] == "Jetinno Europe"
    assert next(x for x in (await client.get("/api/parts", headers=adm)).json() if x["id"] == part["id"])["total_qty"] == 33
    mv = (await client.get(f"/api/parts/movements?warehouse_id={q['id']}", headers=adm)).json()
    assert mv[0]["kind"] == "return" and mv[0]["quantity_delta"] == -2 and mv[0]["serial_no"] == "SN-1"
    assert (await client.post(f"/api/parts/returns/{ret['id']}/status", json={"status": "sent"}, headers=adm)).json()["status"] == "sent"
    res = await client.post(f"/api/parts/returns/{ret['id']}/status", json={"status": "replaced", "warehouse_id": central["id"], "location": "A-2"}, headers=adm)
    assert res.json()["status"] == "replaced" and res.json()["decided_at"]
    assert next(x for x in (await client.get("/api/parts", headers=adm)).json() if x["id"] == part["id"])["total_qty"] == 35
    assert (await client.post(f"/api/parts/returns/{ret['id']}/status", json={"status": "credited"}, headers=adm)).status_code == 422

    # kit: 2 cikk, beépítés a szerelő autójáról (az autón 4 BOIL-TERM, 0 tömítés → 409; feltöltés után OK)
    toml = (await client.post("/api/parts", json={"sku": "TOML-O", "name": "O-gyűrű szett", "unit": "csomag", "price_net": 1500, "cost_net": 800}, headers=adm)).json()
    res = await client.post("/api/parts/kits", json={"sku": "kit-boiler", "name": "Bojler-szett", "items": [{"part_id": part["id"], "quantity": 1}, {"part_id": toml["id"], "quantity": 1}]}, headers=adm)
    assert res.status_code == 201 and res.json()["sku"] == "KIT-BOILER" and len(res.json()["items"]) == 2
    kit = res.json()
    task = await _task(client, adm, emp)
    res = await client.post("/api/parts/use-kit", json={"task_id": task["id"], "kit_id": kit["id"]}, headers=emp_h)
    assert res.status_code == 409 and res.json()["detail"]["sku"] == "TOML-O"
    await client.post("/api/parts/receipt", json={"part_id": toml["id"], "warehouse_id": van["id"], "quantity": 3, "unit_cost": 800}, headers=adm)
    res = await client.post("/api/parts/use-kit", json={"task_id": task["id"], "kit_id": kit["id"]}, headers=emp_h)
    assert res.status_code == 201 and len(res.json()) == 2 and {u["sku"] for u in res.json()} == {"BOIL-TERM", "TOML-O"}
    assert (await client.get("/api/parts/kits", headers=emp_h)).json()[0]["sku"] == "KIT-BOILER"


async def test_round3_stocktake_abc_reports_rights(client, admin):
    _, adm = admin
    user, emp_h, emp = await _emp("r3@example.com")
    mgr_user, mgr_h = await make_user(email="raktaros@example.com", role="manager")
    central, van, part = await _setup(client, adm, user)  # központ A-1: 1, autó: 4
    task = await _task(client, adm, emp)
    await client.post("/api/parts/use", json={"task_id": task["id"], "part_id": part["id"], "quantity": 2}, headers=emp_h)

    # raktáronkénti jog: a központot csak a raktáros kezelheti (az admin mindig)
    res = await client.patch(f"/api/parts/warehouses/{central['id']}", json={"name": "Főraktár", "columns": ["A", "B"], "rows": 10, "managers": [str(mgr_user.id)]}, headers=adm)
    assert res.status_code == 200 and res.json()["managers"] == [str(mgr_user.id)]
    other_mgr, other_h = await make_user(email="masikvezeto@example.com", role="manager")
    res = await client.post("/api/parts/receipt", json={"part_id": part["id"], "warehouse_id": central["id"], "location": "A-1", "quantity": 1}, headers=other_h)
    assert res.status_code == 403 and res.json()["detail"]["code"] == "parts.warehouse_forbidden"
    assert (await client.post("/api/parts/receipt", json={"part_id": part["id"], "warehouse_id": central["id"], "location": "A-1", "quantity": 1, "unit_cost": 3100}, headers=mgr_h)).status_code == 201
    # a dolgozónak nincs parts joga a kezelő-végpontokra
    assert (await client.get("/api/parts/suppliers", headers=emp_h)).status_code == 403

    # korrekció okkóddal
    res = await client.post("/api/parts/adjust", json={"part_id": part["id"], "warehouse_id": central["id"], "location": "A-1", "quantity": 1, "reason": "damage", "note": "törött"}, headers=adm)
    assert res.status_code == 200
    mv = (await client.get(f"/api/parts/movements?part_id={part['id']}", headers=adm)).json()
    assert mv[0]["kind"] == "adjust" and mv[0]["reason"] == "damage"

    # ABC: csak ez a cikk fogyott → A
    res = await client.post("/api/parts/abc/recompute", headers=adm)
    assert res.status_code == 200 and res.json()["A"] == 1
    assert next(x for x in (await client.get("/api/parts", headers=adm)).json() if x["id"] == part["id"])["abc_class"] == "A"

    # leltár-munkamenet (vak): nyitva a nyilvántartott rejtve; számolás; beküldés → eltérés látszik; jóváhagyás okkóddal
    res = await client.post("/api/parts/stocktakes", json={"warehouse_id": central["id"], "name": "Őszi A", "blind": True, "scope": "A", "threshold_pct": 10}, headers=adm)
    assert res.status_code == 201, res.text
    st = res.json()
    assert st["status"] == "open" and st["total_rows"] == 1 and st["rows"][0]["expected"] is None and st["rows"][0]["location"] == "A-1"
    assert (await client.post("/api/parts/stocktakes", json={"warehouse_id": central["id"], "name": "üres", "scope": "B"}, headers=adm)).status_code == 422
    res = await client.put(f"/api/parts/stocktakes/{st['id']}/counts", json={"counts": [{"part_id": part["id"], "location": "A-1", "counted": 3}]}, headers=adm)
    assert res.json()["counted_rows"] == 1 and res.json()["rows"][0]["expected"] is None
    res = await client.post(f"/api/parts/stocktakes/{st['id']}/submit", headers=adm)
    assert res.json()["status"] == "submitted" and res.json()["rows"][0]["expected"] == 1 and res.json()["rows"][0]["delta"] == 2 and res.json()["rows"][0]["over_threshold"] is True
    assert (await client.put(f"/api/parts/stocktakes/{st['id']}/counts", json={"counts": [{"part_id": part["id"], "location": "A-1", "counted": 9}]}, headers=adm)).status_code == 422
    res = await client.post(f"/api/parts/stocktakes/{st['id']}/approve", json={"reasons": [{"part_id": part["id"], "location": "A-1", "reason": "theft"}]}, headers=adm)
    assert res.status_code == 200 and res.json()["status"] == "approved" and res.json()["rows"][0]["reason"] == "theft"
    mv = (await client.get(f"/api/parts/movements?part_id={part['id']}", headers=adm)).json()
    assert mv[0]["kind"] == "adjust" and mv[0]["quantity_delta"] == 2 and mv[0]["reason"] == "theft" and "Őszi A" in mv[0]["note"]
    lst = (await client.get("/api/parts/stocktakes?status=approved", headers=adm)).json()
    assert len(lst) == 1 and lst[0]["diff_rows"] == 1

    # riportok
    today = date.today().isoformat()
    rep = (await client.get(f"/api/parts/reports/consumption?from={today}&to={today}&group=part", headers=adm)).json()
    assert len(rep) == 1 and rep[0]["quantity"] == 2 and rep[0]["revenue"] == 9000 and rep[0]["cost"] == 6200
    rep = (await client.get(f"/api/parts/reports/consumption?from={today}&to={today}&group=employee", headers=adm)).json()
    assert rep[0]["label"] == "Teszt Elek"
    health = (await client.get("/api/parts/reports/health", headers=adm)).json()
    row = next(h for h in health if h["part_id"] == part["id"])
    assert row["used_90d"] == 2 and row["dead"] is False and row["abc_class"] == "A" and row["turnover"] is not None

    # timer: időtípusok + korrekció + bér-CSV + mért idő átvétele
    await _enable_modules(["parts", "worktime"])
    t2 = await _task(client, adm, emp)
    assert (await client.post(f"/api/worktime/{t2['id']}/start", headers=emp_h)).json()["mode"] == "work"
    assert (await client.post(f"/api/worktime/{t2['id']}/mode:travel", headers=emp_h)).json()["mode"] == "travel"
    assert (await client.post(f"/api/worktime/{t2['id']}/mode:bad", headers=emp_h)).status_code == 404
    assert (await client.post(f"/api/worktime/{t2['id']}/mode:work", headers=emp_h)).json()["mode"] == "work"
    await client.put(f"/api/me/tasks/{t2['id']}/worksheet", json={"work_description": "x", "hours_spent": 1.0}, headers=emp_h)
    assert (await client.post(f"/api/worktime/{t2['id']}/stop", headers=emp_h)).json()["state"] == "stopped"
    res = await client.post(f"/api/worktime/correction/{t2['id']}/request", json={"seconds": 1800, "note": "elfelejtettem szünetre nyomni, de fordítva"}, headers=emp_h)
    assert res.status_code == 200 and res.json()["correction_status"] == "pending"
    assert (await client.post(f"/api/worktime/correction/{t2['id']}/request", json={"seconds": 60, "note": "még egy"}, headers=emp_h)).status_code == 422
    corr = (await client.get("/api/worktime/corrections/list", headers=adm)).json()
    assert len(corr) == 1 and corr[0]["seconds"] == 1800 and corr[0]["name"] == "Teszt Elek"
    res = await client.post(f"/api/worktime/corrections/{corr[0]['timer_id']}/approve", headers=adm)
    assert res.status_code == 200 and res.json()["status"] == "approved"
    d0, d1 = (date.today() - timedelta(days=1)).isoformat(), (date.today() + timedelta(days=1)).isoformat()
    stats = (await client.get(f"/api/worktime/stats/summary?from={d0}&to={d1}", headers=adm)).json()
    row = next(r for r in stats["rows"] if r["task_id"] == t2["id"])
    assert row["correction_seconds"] == 1800 and row["active_seconds"] >= 1800 and row["correction_status"] == "approved"
    assert stats["by_employee"][0]["correction_seconds"] == 1800
    res = await client.get(f"/api/worktime/stats/payroll.csv?from={d0}&to={d1}", headers=adm)
    assert res.status_code == 200 and "Teszt Elek" in res.content.decode("utf-8") and res.content.decode("utf-8").startswith("﻿Dolgozó")
    res = await client.post(f"/api/worktime/apply-measured/{t2['id']}/run", headers=adm)
    assert res.status_code == 200 and res.json()["hours_spent"] == 0.5  # 1800 s → 0,5 óra (negyedórára kerekítve)
    assert (await client.get(f"/api/tasks/{t2['id']}/worksheet", headers=adm)).json()["hours_spent"] == 0.5


async def test_polish_permission_inheritance_and_alerts(client, admin, monkeypatch):
    """Mentett (régi) jogosultság-mátrix parts nélkül → az elszámolás-jog
    örökíti a raktáros jogot; visszáru és negatív korrekció min. alatt →
    Telegram parts.low (csak a küszöb átlépésekor)."""
    _, adm = admin
    mgr_user, mgr_h = await make_user(email="regimatrix@example.com", role="manager")
    res = await client.put("/api/settings/permissions", json={"matrix": {"manager": ["dashboard", "settlements"]}}, headers=adm)
    assert res.status_code == 200, res.text
    assert (await client.get("/api/parts/suppliers", headers=mgr_h)).status_code == 200
    res = await client.put("/api/settings/permissions", json={"matrix": {"manager": ["dashboard"]}}, headers=adm)
    assert res.status_code == 200
    assert (await client.get("/api/parts/suppliers", headers=mgr_h)).status_code == 403

    user, emp_h, emp = await _emp("alert@example.com")
    central, van, part = await _setup(client, adm, user)  # összes 5, min 2
    res = await client.put(
        "/api/settings/notifications",
        json={"daily_enabled": False, "send_hour": 6, "weekly_backup": False, "auto_receipt": False, "wa_enabled": False,
              "tg_enabled": True, "tg_token": "123:abc", "tg_chat_ids": "-100555", "tg_events": ["parts.low"]},
        headers=adm,
    )
    assert res.status_code == 200, res.text
    sent: list[str] = []

    async def fake_send(config, chat_id, text):
        sent.append(text)
        return True

    import app.services.wfm.telegram as tg_mod

    monkeypatch.setattr(tg_mod, "send_telegram", fake_send)
    # korrekció 4 → 2 az autón: összes 3, még min (2) fölött → nincs riasztás
    res = await client.post("/api/parts/adjust", json={"part_id": part["id"], "warehouse_id": van["id"], "quantity": 2, "reason": "damage"}, headers=adm)
    assert res.status_code == 200 and sent == []
    # visszáru 2 db az autóról: összes 1 < 2 → EGY riasztás
    res = await client.post("/api/parts/returns", json={"part_id": part["id"], "warehouse_id": van["id"], "quantity": 2, "reason": "hibás"}, headers=adm)
    assert res.status_code == 201, res.text
    assert len(sent) == 1 and "min. alatt" in sent[0]
    # további csökkenés már a küszöb alatt: nincs második
    res = await client.post("/api/parts/adjust", json={"part_id": part["id"], "warehouse_id": central["id"], "location": "A-1", "quantity": 0, "reason": "theft"}, headers=adm)
    assert res.status_code == 200 and len(sent) == 1
    # export: sorozatszám / okkód oszlop
    body = (await client.get("/api/parts/export.csv?what=movements", headers=adm)).content.decode("utf-8")
    assert "Okkód" in body and ";theft" in body
