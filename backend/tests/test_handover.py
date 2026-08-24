"""Átadás-folyamat: várólistára kerülés a gép elhozása után, fizetés +
Billingó-számla (kedvezménynél számla NÉLKÜL), gép átadott státusz, a
munkalap/feladat lezárása."""

from __future__ import annotations

from tests.conftest import make_employee_record, make_user


async def _prepare(client, mgr, monkeypatch, barcode="HND-1"):
    res = await client.post("/api/partners", json={"name": "Átadós Kft."}, headers=mgr)
    partner = res.json()
    res = await client.post(
        "/api/assets",
        json={"barcode": barcode, "name": "Átadós Gép", "manufacturer": "Saeco",
              "customer_owned": True},
        headers=mgr,
    )
    asset = res.json()
    await client.post(
        f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]},
        headers=mgr,
    )
    user, _h = await make_user(email=f"hnd-{barcode}@example.com", role="szervizes")
    emp = await make_employee_record(user, last_name="Átadó", first_name="Ede")
    res = await client.post(
        "/api/tasks",
        json={"title": "Átadás teszt", "employee_id": str(emp.id),
              "due_date": "2026-09-30", "external_service": True,
              "asset_id": asset["id"], "client_name": "Vevő Béla"},
        headers=mgr,
    )
    task_id = res.json()["id"]
    # árazott tételek (a mi áraink)
    await client.put(
        f"/api/tasks/{task_id}/worksheet",
        json={"work_description": "kész",
              "works": [{"name": "Szivattyú csere", "cost_net": 8000, "price_net": 14000}],
              "repair_options": [], "materials": [],
              "maintenance_fee": 6000},
        headers=mgr,
    )

    # elhozás (e-mail elfogva)
    async def fake_send(smtp, to, subject, body, attachments=None):
        return True

    async def fake_smtp(db):
        return {"host": "x"}

    import app.api.tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "send_email", fake_send)
    monkeypatch.setattr(tasks_mod, "load_smtp_config", fake_smtp)
    res = await client.post(
        f"/api/tasks/{task_id}/worksheet/picked-up",
        json={"to": "vevo@example.com"}, headers=mgr,
    )
    assert res.status_code == 200
    return task_id, asset


async def test_handover_with_invoice(client, admin, manager, monkeypatch):
    _, mgr = manager
    task_id, asset = await _prepare(client, mgr, monkeypatch, "HND-1")

    # a várólistán szerepel, a mi árainkkal
    rows = (await client.get("/api/tasks/handovers/list", headers=mgr)).json()
    row = next(r for r in rows if r["task_id"] == task_id)
    assert row["total_net"] == 20000  # 14000 munka + 6000 karbantartás
    assert {i["name"] for i in row["items"]} == {"Szivattyú csere", "Karbantartási díj"}

    # Billingó elfogása
    calls: list[dict] = []

    async def fake_invoice(db, partner, *, serial, items, payment_method, vat_percent=27):
        calls.append({"serial": serial, "items": items, "pm": payment_method})
        return "DOC-H1", "invoice"

    from app.services.wfm import billingo_service

    monkeypatch.setattr(billingo_service, "create_handover_invoice", fake_invoice)

    res = await client.post(
        f"/api/tasks/{task_id}/handover",
        json={"payment_method": "card", "discount": False}, headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["document_id"] == "DOC-H1"
    assert calls[0]["pm"] == "card"

    # a gép átadott, a feladat done, a lista üres
    res = await client.get(f"/api/tasks/{task_id}/handover", headers=mgr)
    assert res.json()["handed_over_at"] is not None
    rows = (await client.get("/api/tasks/handovers/list", headers=mgr)).json()
    assert all(r["task_id"] != task_id for r in rows)
    a = next(
        x for x in (await client.get("/api/assets", headers=mgr)).json()
        if x["id"] == asset["id"]
    )
    assert a["status"] == "handed_over"
    # ismételt átadás tiltott
    res = await client.post(
        f"/api/tasks/{task_id}/handover",
        json={"payment_method": "cash", "discount": False}, headers=mgr,
    )
    assert res.status_code == 422


async def test_handover_discount_no_invoice(client, admin, manager, monkeypatch):
    _, mgr = manager
    task_id, _asset = await _prepare(client, mgr, monkeypatch, "HND-2")

    called: list[str] = []

    async def fake_invoice(*a, **k):
        called.append("x")
        return "DOC-X", "invoice"

    from app.services.wfm import billingo_service

    monkeypatch.setattr(billingo_service, "create_handover_invoice", fake_invoice)
    res = await client.post(
        f"/api/tasks/{task_id}/handover",
        json={"payment_method": "cash", "discount": True}, headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["document_id"] is None
    assert called == []  # kedvezménynél SEMMILYEN számla nem készül
    detail = (await client.get(f"/api/tasks/{task_id}/handover", headers=mgr)).json()
    assert detail["handover_discount"] is True
