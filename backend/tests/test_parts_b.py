"""Alkatrész-raktár B fázis (1d553d2e): szerelői autós raktár, beépítés
munkalapra (csak saját autóból, saját feladatra), storno, munkalap-kép + PDF,
jóváhagyásos kiadás-kérés (kér → kiad helykódról → nyugtáz / elutasít /
visszavon), min. készlet Telegram-riasztás."""
from __future__ import annotations

from datetime import date

from tests.conftest import make_employee_record, make_user


async def _emp(email: str):
    user, headers = await make_user(email=email, role="employee")
    emp = await make_employee_record(user)
    return user, headers, emp


async def _setup(client, adm, owner_user):
    """Központi raktár (A,B × 10) + a szerelő autója + 1 cikk (min. 2),
    5 db vételezve A-1-re, 4 db áthelyezve az autóra."""
    central = (await client.post("/api/parts/warehouses", json={"name": "Főraktár", "columns": ["A", "B"], "rows": 10}, headers=adm)).json()
    van = (await client.post("/api/parts/warehouses", json={"name": "Kiss autó", "kind": "vehicle", "owner_user_id": str(owner_user.id), "columns": [], "rows": 1}, headers=adm)).json()
    part = (await client.post("/api/parts", json={"sku": "BOIL-TERM", "name": "Bojler-termosztát 125°C", "min_stock": 2, "price_net": 4500, "cost_net": 3100}, headers=adm)).json()
    res = await client.post("/api/parts/receipt", json={"part_id": part["id"], "warehouse_id": central["id"], "location": "A-1", "quantity": 5}, headers=adm)
    assert res.status_code == 201, res.text
    res = await client.post("/api/parts/transfer", json={"part_id": part["id"], "from_warehouse_id": central["id"], "from_location": "A-1", "to_warehouse_id": van["id"], "quantity": 4}, headers=adm)
    assert res.status_code == 201, res.text
    return central, van, part


async def _task(client, adm, emp):
    res = await client.post("/api/tasks", json={"title": "Bojler javítás", "employee_id": str(emp.id), "due_date": date.today().isoformat()}, headers=adm)
    assert res.status_code == 201, res.text
    return res.json()


async def test_parts_use_on_worksheet(client, admin, monkeypatch):
    _, adm = admin
    user, emp_h, emp = await _emp("szerelo@example.com")
    _, other_h, other_emp = await _emp("masik@example.com")  # nincs autós raktára
    central, van, part = await _setup(client, adm, user)
    task = await _task(client, adm, emp)

    # Telegram: parts.low bepipálva, küldés mockolva
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

    # (a feladat létrehozásakor a munkalap sorszámmal együtt létrejön — a
    # beépítés rögtön mehet; a 409 parts.worksheet_missing csak örökölt eset)
    # akinek nincs autója: 422; idegen feladatra: 404
    res = await client.post("/api/parts/use", json={"task_id": task["id"], "part_id": part["id"], "quantity": 1}, headers=other_h)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "parts.no_vehicle"
    other_task = await _task(client, adm, other_emp)
    res = await client.post("/api/parts/use", json={"task_id": task["id"], "part_id": part["id"], "quantity": 1}, headers=other_h)
    assert res.status_code == 422
    res = await client.post("/api/parts/use", json={"task_id": other_task["id"], "part_id": part["id"], "quantity": 1}, headers=emp_h)
    assert res.status_code == 404

    # beépítés 1 db a saját autóból: ár a cikktörzsből; autó 4 → 3; összes 5 → 4
    res = await client.post("/api/parts/use", json={"task_id": task["id"], "part_id": part["id"], "quantity": 1}, headers=emp_h)
    assert res.status_code == 201, res.text
    used = res.json()
    assert len(used) == 1 and used[0]["quantity"] == 1 and used[0]["price_net"] == 4500 and used[0]["cost_net"] == 3100
    assert used[0]["warehouse_name"] == "Kiss autó"
    # a dolgozói munkalap-mentés nem bántja a beépítéseket
    res = await client.put(f"/api/me/tasks/{task['id']}/worksheet", json={"work_description": "Termosztát csere", "hours_spent": 1.5}, headers=emp_h)
    assert res.status_code == 200, res.text
    assert len(res.json()["used_parts"]) == 1
    # + 2 db: autó 1, összes 2 (= min, még nincs riasztás)
    res = await client.post("/api/parts/use", json={"task_id": task["id"], "part_id": part["id"], "quantity": 2}, headers=emp_h)
    assert res.status_code == 201 and len(res.json()) == 2
    assert sent == []
    # több, mint ami az autón van: 409
    res = await client.post("/api/parts/use", json={"task_id": task["id"], "part_id": part["id"], "quantity": 5}, headers=emp_h)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "parts.insufficient_stock"
    # a szerelő nem választhat más raktárt — a warehouse_id-t a szerver figyelmen
    # kívül hagyja (saját autó marad); összes 2 → 1 < min → EGY riasztás
    res = await client.post("/api/parts/use", json={"task_id": task["id"], "part_id": part["id"], "quantity": 1, "warehouse_id": central["id"], "location": "A-1"}, headers=emp_h)
    assert res.status_code == 201, res.text
    assert all(u["warehouse_name"] == "Kiss autó" for u in res.json()) and len(res.json()) == 3
    assert len(sent) == 1 and "BOIL-TERM" in sent[0] and "min. alatt" in sent[0], sent
    # admin a központ A-1 helyéről, felülírt árral — a küszöb már át volt lépve: nincs 2. riasztás
    res = await client.post("/api/parts/use", json={"task_id": task["id"], "part_id": part["id"], "quantity": 1, "warehouse_id": central["id"], "location": "A-1", "price_net": 4000}, headers=adm)
    assert res.status_code == 201, res.text
    assert len(sent) == 1
    admin_line = next(u for u in res.json() if u["warehouse_name"] == "Főraktár")
    assert admin_line["price_net"] == 4000

    # munkalap-kép (dolgozó + vezető) + PDF
    me = (await client.get(f"/api/me/tasks/{task['id']}/worksheet", headers=emp_h)).json()
    assert len(me["used_parts"]) == 4
    boss = (await client.get(f"/api/tasks/{task['id']}/worksheet", headers=adm)).json()
    assert sum(u["quantity"] for u in boss["used_parts"]) == 5
    res = await client.get(f"/api/tasks/{task['id']}/worksheet/pdf", headers=adm)
    assert res.status_code == 200 and res.headers["content-type"] == "application/pdf"

    # storno: a szerelő csak a saját autós sorát vonhatja vissza; kétszer nem
    res = await client.delete(f"/api/parts/use/{admin_line['movement_id']}", headers=emp_h)
    assert res.status_code == 404
    res = await client.delete(f"/api/parts/use/{used[0]['movement_id']}", headers=emp_h)
    assert res.status_code == 200, res.text
    assert len(res.json()) == 3
    res = await client.delete(f"/api/parts/use/{used[0]['movement_id']}", headers=emp_h)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "parts.already_reversed"
    stock = (await client.get(f"/api/parts/stock?warehouse_id={van['id']}", headers=adm)).json()
    assert stock[0]["quantity"] == 1  # 4 − 1 − 2 − 1 + 1 (storno)
    mv = (await client.get(f"/api/parts/movements?part_id={part['id']}", headers=adm)).json()
    assert mv[0]["kind"] == "use" and mv[0]["quantity_delta"] == 1 and mv[0]["note"] == "storno"

    # saját raktár-nézet: készlet + katalógus ár nélkül
    mine = (await client.get("/api/parts/my-warehouse", headers=emp_h)).json()
    assert mine["warehouse_name"] == "Kiss autó" and mine["stock"][0]["quantity"] == 1 and mine["stock"][0]["low"] is True
    assert mine["catalog"][0]["sku"] == "BOIL-TERM" and "price_net" not in mine["catalog"][0]
    assert (await client.get("/api/parts/my-warehouse", headers=other_h)).json()["warehouse_id"] is None


async def test_parts_requests_flow(client, admin, monkeypatch):
    _, adm = admin
    user, emp_h, emp = await _emp("kero@example.com")
    _, other_h, _ = await _emp("idegen@example.com")
    central, van, part = await _setup(client, adm, user)

    res = await client.put(
        "/api/settings/notifications",
        json={"daily_enabled": False, "send_hour": 6, "weekly_backup": False, "auto_receipt": False, "wa_enabled": False,
              "tg_enabled": True, "tg_token": "123:abc", "tg_chat_ids": "-100555", "tg_events": ["parts.request"]},
        headers=adm,
    )
    assert res.status_code == 200, res.text
    sent: list[str] = []

    async def fake_send(config, chat_id, text):
        sent.append(text)
        return True

    import app.services.wfm.telegram as tg_mod

    monkeypatch.setattr(tg_mod, "send_telegram", fake_send)

    # autó nélkül nem lehet kérni
    res = await client.post("/api/parts/requests", json={"part_id": part["id"], "quantity": 2}, headers=other_h)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "parts.no_vehicle"
    # kérés → pending + Telegram
    res = await client.post("/api/parts/requests", json={"part_id": part["id"], "quantity": 1, "note": "két bojleres javítás"}, headers=emp_h)
    assert res.status_code == 201, res.text
    req = res.json()
    assert req["status"] == "pending" and req["warehouse_name"] == "Kiss autó" and req["requester_name"] == "kero"
    assert len(sent) == 1 and "kero" in sent[0] and "két bojleres" in sent[0]

    # listák: a szerelő csak a sajátját, az admin mindent; idegen nem nyugtázhat / vonhat vissza
    assert len((await client.get("/api/parts/requests", headers=emp_h)).json()) == 1
    assert (await client.get("/api/parts/requests", headers=other_h)).json() == []
    assert len((await client.get("/api/parts/requests?status=pending", headers=adm)).json()) == 1
    assert (await client.post(f"/api/parts/requests/{req['id']}/cancel", headers=other_h)).status_code == 404
    assert (await client.post(f"/api/parts/requests/{req['id']}/receive", headers=emp_h)).status_code == 422

    # kiadás: rossz helykód 422, több mint a készlet 409, A-1-ről OK → issued, készlet mozog
    res = await client.post(f"/api/parts/requests/{req['id']}/issue", json={"from_warehouse_id": central["id"], "from_location": "Z-9"}, headers=adm)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "parts.bad_location"
    res = await client.post(f"/api/parts/requests/{req['id']}/issue", json={"from_warehouse_id": central["id"], "from_location": "A-1", "quantity": 9}, headers=adm)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "parts.insufficient_stock"
    res = await client.post(f"/api/parts/requests/{req['id']}/issue", json={"from_warehouse_id": central["id"], "from_location": "A-1"}, headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "issued" and res.json()["issued_from_location"] == "A-1" and res.json()["issued_quantity"] == 1
    stock = {(s["warehouse_id"], s["location"]): s["quantity"] for s in (await client.get("/api/parts/stock", headers=adm)).json()}
    assert (central["id"], "A-1") not in stock  # 1 − 1 = 0 → a /stock nem listázza
    assert stock[(van["id"], "")] == 5
    mv = (await client.get(f"/api/parts/movements?part_id={part['id']}", headers=adm)).json()
    assert [m["kind"] for m in mv[:2]] == ["issue", "issue"] and mv[0]["document_no"].startswith("REQ-")
    # kétszer nem adható ki
    res = await client.post(f"/api/parts/requests/{req['id']}/issue", json={"from_warehouse_id": central["id"], "from_location": "A-1"}, headers=adm)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "parts.request_decided"

    # nyugtázás a szerelőnél
    res = await client.post(f"/api/parts/requests/{req['id']}/receive", headers=emp_h)
    assert res.status_code == 200 and res.json()["status"] == "received" and res.json()["received_at"]
    mine = (await client.get("/api/parts/my-warehouse", headers=emp_h)).json()
    assert mine["stock"][0]["quantity"] == 5 and mine["requests"][0]["status"] == "received"

    # elutasítás + visszavonás
    r2 = (await client.post("/api/parts/requests", json={"part_id": part["id"], "quantity": 1}, headers=emp_h)).json()
    res = await client.post(f"/api/parts/requests/{r2['id']}/decline", json={"note": "nincs készleten"}, headers=adm)
    assert res.status_code == 200 and res.json()["status"] == "declined" and res.json()["decision_note"] == "nincs készleten"
    r3 = (await client.post("/api/parts/requests", json={"part_id": part["id"], "quantity": 1}, headers=emp_h)).json()
    res = await client.post(f"/api/parts/requests/{r3['id']}/cancel", headers=emp_h)
    assert res.status_code == 200 and res.json()["status"] == "cancelled"
    # a szerelő nem adhat ki / utasíthat el (settlements jog kell)
    assert (await client.post(f"/api/parts/requests/{r3['id']}/decline", json={}, headers=emp_h)).status_code == 403
