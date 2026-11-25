"""173. kör — x-presso 8fb91fca: kötelező számláló-állás átvételkor/átadáskor
(elismervényeken, lefőzött adag); 5dfe9c67: szerviztörténet a gép történetében."""

from __future__ import annotations

from tests.conftest import make_employee_record
from tests.test_inventory import asset_payload


async def _intake(client, mgr, asset_id, **extra):
    body = {"asset_id": asset_id, "client_name": "Teszt Ügyfél", "client_phone": "+36301234567",
            "client_email": "ugyfel@example.com", "faults": "nem melegít"}
    body.update(extra)
    return await client.post("/api/intakes", json=body, headers=mgr)


async def test_intake_requires_nonzero_counter_and_prints_it(client, manager):
    mgr_user, mgr = manager
    await make_employee_record(mgr_user, last_name="Szerviz", first_name="Soma")
    a = (await client.post("/api/assets", json=asset_payload(barcode="CNT0001", serial_number="SN-CNT-1", customer_owned=True), headers=mgr)).json()
    # hiányzó / nulla számláló → 422
    res = await _intake(client, mgr, a["id"])
    assert res.status_code == 422 and res.json()["detail"]["code"] == "intake.counter_required"
    res = await _intake(client, mgr, a["id"], counter=0)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "intake.counter_required"
    res = await _intake(client, mgr, a["id"], counter=12345)
    assert res.status_code == 201, res.text
    it = res.json()
    assert it["counter"] == 12345 and it["counter_text"] == "12 345"
    # az elismervény PDF tartalmazza (a kirajzolt szöveg tömörítve van → a mező a listában is látszik)
    rows = (await client.get("/api/intakes", headers=mgr)).json()
    assert next(r for r in rows if r["id"] == it["id"])["counter_text"] == "12 345"
    pdf = await client.get(f"/api/intakes/{it['id']}/pdf", headers=mgr)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    # a munkalap leírásában is ott a számláló
    ws = await client.post(f"/api/intakes/{it['id']}/worksheet", headers=mgr)
    assert ws.status_code in (200, 201), ws.text
    assert "Számláló-állás átvételkor: 12 345" in (ws.json().get("description") or "")

    # több számlálós gép: számlálónként kell
    b = (await client.post("/api/assets", json=asset_payload(barcode="CNT0002", serial_number="SN-CNT-2", customer_owned=True, counter_count=2, counter_names=["Kávé", "Tea"]), headers=mgr)).json()
    res = await _intake(client, mgr, b["id"], counter=500)
    assert res.status_code == 422 and res.json()["detail"]["counters"] == 2
    res = await _intake(client, mgr, b["id"], counters=[400, 0])
    assert res.status_code == 422
    res = await _intake(client, mgr, b["id"], counters=[400, 100])
    assert res.status_code == 201, res.text
    assert res.json()["counter"] == 500 and res.json()["counter_text"] == "Kávé: 400 · Tea: 100 (összesen 500)"


async def test_handover_requires_counter_and_history_shows_it(client, manager):
    mgr_user, mgr = manager
    await make_employee_record(mgr_user, last_name="Szerviz", first_name="Soma")
    a = (await client.post("/api/assets", json=asset_payload(barcode="CNT0003", serial_number="SN-CNT-3", customer_owned=True), headers=mgr)).json()
    it = (await _intake(client, mgr, a["id"], counter=1000)).json()
    task = (await client.post(f"/api/intakes/{it['id']}/worksheet", headers=mgr)).json()
    # munkalap a feladathoz (a kézi lezárás helyett közvetlenül), hogy az átadás-sor létezzen
    res = await client.put(f"/api/tasks/{task['id']}/worksheet", json={
        "work_description": "Fűtőbetét csere", "materials": [], "works": [{"name": "Fűtőbetét csere", "amount_net": 15000}],
        "technician_name": "Szerviz Soma", "solution_text": "kicseréltük a fűtőbetétet",
    }, headers=mgr)
    assert res.status_code in (200, 201), res.text
    det = (await client.get(f"/api/tasks/{task['id']}/handover", headers=mgr)).json()
    assert det["intake_counter"] == 1000 and det["intake_counter_text"] == "1 000" and det["counter_count"] == 1
    # átadás számláló nélkül → 422; kisebb is elfogadott (csak nulla tilos), de akkor nincs lefőzött adag
    res = await client.post(f"/api/tasks/{task['id']}/handover", json={"payment_method": "cash", "discount": True}, headers=mgr)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "handover.counter_required"
    res = await client.post(f"/api/tasks/{task['id']}/handover", json={"payment_method": "cash", "discount": True, "counter": 1250}, headers=mgr)
    assert res.status_code == 200, res.text
    det = (await client.get(f"/api/tasks/{task['id']}/handover", headers=mgr)).json()
    assert det["handover_counter"] == 1250 and det["brewed_portions"] == 250
    pdf = await client.get(f"/api/tasks/{task['id']}/handover/receipt.pdf", headers=mgr)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    # szerviztörténet a gép történetében: átvétel + munkalap, ki/mikor/mit
    full = (await client.get(f"/api/assets/{a['id']}", headers=mgr)).json()
    hist = full["service_history"]
    kinds = [h["kind"] for h in hist]
    assert "intake" in kinds and "task" in kinds
    tk = next(h for h in hist if h["kind"] == "task")
    assert tk["serial"] and tk["who"] == "Szerviz Soma" and "fűtőbetét" in (tk["detail"] or "").lower()
    assert tk["status"] == "done" and tk["handed_over_at"] and tk["counter_text"] == "1 250"
    ik = next(h for h in hist if h["kind"] == "intake")
    assert ik["serial"].startswith("AT-") and ik["counter_text"] == "1 000" and ik["who"]
