"""107. kör — hibajegyek (2026-10-22): átadás átutalással + határidő + kassza
(12a07799), szerelő-átadás átutalással (92ed8779), elszámolás számla-e-mailek
(b08956c7), átvétel saját tárgyi géppel + példányszám + automatikus munkalap
(dc134f9f, 17966bd9, 653a4e59), naptár átütemezés-indok + időkapu (700ab5b2),
kassza-jogok (5870270b, e16fec86), pénz-átadás címzettek (2817884b)."""
from __future__ import annotations

import uuid as _uuid
from datetime import date, timedelta

from sqlalchemy import select

from tests.conftest import make_employee_record, make_user
from tests.test_handover import _prepare


async def _expenses(client, hdr):
    return (await client.get("/api/settlements/expenses", headers=hdr)).json()


async def test_handover_transfer_due_days_and_cash_register(client, admin, manager, monkeypatch):
    _, mgr = manager
    from app.services.wfm import billingo_service

    calls: list[dict] = []

    async def fake_invoice(db, partner, *, serial, items, payment_method, vat_percent=27, due_days=None):
        calls.append({"pm": payment_method, "due_days": due_days})
        return "DOC-T1", "invoice"

    monkeypatch.setattr(billingo_service, "create_handover_invoice", fake_invoice)

    # átutalás 12 napos határidővel → számla átutalással, kassza-mozgás NINCS
    task_id, _ = await _prepare(client, mgr, monkeypatch, "R107-T")
    res = await client.post(
        f"/api/tasks/{task_id}/handover",
        json={"payment_method": "transfer", "discount": False, "due_days": 12}, headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert calls[-1] == {"pm": "transfer", "due_days": 12}
    assert await _expenses(client, mgr) == []

    # átutalás határidő nélkül → alapból 8 nap
    task_id, _ = await _prepare(client, mgr, monkeypatch, "R107-T8")
    res = await client.post(
        f"/api/tasks/{task_id}/handover",
        json={"payment_method": "transfer", "discount": False}, headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert calls[-1]["due_days"] == 8

    # készpénz → az átadó kasszájába bruttó betét kerül (20000 nettó × 1,27)
    task_id, _ = await _prepare(client, mgr, monkeypatch, "R107-C")
    res = await client.post(
        f"/api/tasks/{task_id}/handover",
        json={"payment_method": "cash", "discount": False}, headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert calls[-1]["due_days"] is None
    rows = await _expenses(client, mgr)
    assert len(rows) == 1 and rows[0]["entry_type"] == "deposit" and rows[0]["amount_gross"] == 25400.0
    assert "R107" not in rows[0]["note"] or "készpénz" in rows[0]["note"]

    # kártya → nincs kassza-mozgás
    task_id, _ = await _prepare(client, mgr, monkeypatch, "R107-K")
    res = await client.post(
        f"/api/tasks/{task_id}/handover",
        json={"payment_method": "card", "discount": False}, headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert len(await _expenses(client, mgr)) == 1

    # rossz fizetési mód / határidő → 422
    res = await client.post(f"/api/tasks/{task_id}/handover", json={"payment_method": "crypto"}, headers=mgr)
    assert res.status_code == 422
    res = await client.post(f"/api/tasks/{task_id}/handover", json={"payment_method": "transfer", "due_days": 0}, headers=mgr)
    assert res.status_code == 422


async def _external_task(client, mgr, emp, emp_hdr, title: str):
    task = (
        await client.post(
            "/api/tasks",
            json={"title": title, "employee_id": str(emp.id), "due_date": date.today().isoformat(),
                  "external_service": True},
            headers=mgr,
        )
    ).json()
    res = await client.put(
        f"/api/me/tasks/{task['id']}/worksheet",
        json={"work_description": "kész", "works": [{"name": "Javítás", "cost_net": 21000}]},
        headers=emp_hdr,
    )
    assert res.status_code == 200, res.text
    return task


async def test_service_pickup_transfer_vs_cash(client, manager):
    _, mgr = manager
    emp_user, emp_hdr = await make_user(email="r107-szerelo@example.com", role="szervizes")
    emp = await make_employee_record(emp_user, last_name="Külsős", first_name="Kálmán")

    # átutalás: a folyószámlára könyvelve, de a kasszából NEM megy ki pénz
    task = await _external_task(client, mgr, emp, emp_hdr, "Átutalásos")
    res = await client.post(
        "/api/tasks/service-handover/pickup",
        json={"task_ids": [task["id"]], "notify": False, "paid_amount": 21000, "payment_method": "transfer"},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["picked"] == 1
    assert await _expenses(client, mgr) == []

    # készpénz: költség a kasszában
    task = await _external_task(client, mgr, emp, emp_hdr, "Készpénzes")
    res = await client.post(
        "/api/tasks/service-handover/pickup",
        json={"task_ids": [task["id"]], "notify": False, "paid_amount": 21000},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    rows = await _expenses(client, mgr)
    assert len(rows) == 1 and rows[0]["entry_type"] == "expense" and rows[0]["amount_gross"] == 21000.0

    # a folyószámlán mindkét kifizetés ott van, az átutalásos megjelölve
    from app import db as app_db
    from app.models import TechLedger

    async with app_db.get_session_factory()() as session:
        notes = (
            await session.execute(select(TechLedger.note).where(TechLedger.employee_id == emp.id, TechLedger.kind == "payout"))
        ).scalars().all()
    assert len(notes) == 2 and sum("ÁTUTALÁS" in (n or "") for n in notes) == 1

    res = await client.post(
        "/api/tasks/service-handover/pickup",
        json={"task_ids": [task["id"]], "payment_method": "bitcoin"}, headers=mgr,
    )
    assert res.status_code == 422


async def test_settlement_invoice_emails(client, manager):
    _, mgr = manager
    partner = (await client.post("/api/partners", json={"name": "Számlás Kft.", "contact_email": "iroda@szamlas.hu"}, headers=mgr)).json()
    res = await client.post(
        f"/api/partners/{partner['id']}/contracts",
        json={"valid_from": "2020-01-01", "invoice_norma": 7}, headers=mgr,
    )
    assert res.status_code == 201, res.text
    ctx = (await client.get(f"/api/partners/{partner['id']}/settlement-context", headers=mgr)).json()
    assert ctx["invoice_norma"] == 7

    product = (await client.post("/api/products", json={"name": "Kávé R107", "price_per_portion": 50, "unit_size": 1, "is_consignment": True}, headers=mgr)).json()
    await client.post(f"/api/partners/{partner['id']}/stock/replenish", json={"product_id": product["id"], "quantity": 1.0}, headers=mgr)

    # érvénytelen cím → 422
    res = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash",
              "lines": [{"product_id": product["id"], "physical_qty": 0.5}],
              "invoice_emails": "konyveles@szamlas.hu, nem-email"},
        headers=mgr,
    )
    assert res.status_code == 422 and res.json()["detail"]["code"] == "settlement.bad_invoice_email"

    # két cím (pontosvesszővel is) → normalizálva mentve
    res = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash",
              "lines": [{"product_id": product["id"], "physical_qty": 0.5}],
              "invoice_emails": " konyveles@szamlas.hu ;boss@szamlas.hu "},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    assert res.json()["invoice_emails"] == "konyveles@szamlas.hu, boss@szamlas.hu"

    # üresen → a partner e-mailje marad az alap (None tárolva)
    res = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash",
              "lines": [{"product_id": product["id"], "physical_qty": 0.2}], "invoice_emails": ""},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    assert res.json()["invoice_emails"] is None


async def test_intake_own_machine_print_copies_auto_worksheet(client, manager):
    _, mgr = manager
    mgr_user, _ = await make_user(email="r107-intake-emp@example.com", role="employee")
    await make_employee_record(mgr_user, last_name="Első", first_name="Dolgozó")

    own = (await client.post("/api/assets", json={"barcode": "R107-OWN", "name": "Saját Gép", "tangible": True, "customer_owned": False}, headers=mgr)).json()
    cust = (await client.post("/api/assets", json={"barcode": "R107-CUST", "name": "Ügyfél Gépe", "customer_owned": True}, headers=mgr)).json()

    # saját tárgyi gép: elérhetőség nélkül is átvehető, 1 példány nyomtatás
    res = await client.post("/api/intakes", json={"asset_id": own["id"], "faults": "nem fűt"}, headers=mgr)
    assert res.status_code == 201, res.text
    own_intake = res.json()
    res = await client.post(f"/api/intakes/{own_intake['id']}/print", json={}, headers=mgr)
    assert res.status_code == 200, res.text
    assert res.json()["copies"] == 1

    # ügyfél gépe: elérhetőség kötelező, 2 példány
    res = await client.post("/api/intakes", json={"asset_id": cust["id"], "client_name": "Vevő"}, headers=mgr)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "intake.contact_required"
    res = await client.post(
        "/api/intakes",
        json={"asset_id": cust["id"], "client_name": "Vevő Vera", "client_company": "Vevő Kft.",
              "client_phone": "+3630999", "client_email": "vera@example.com", "faults": "folyik"},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    cust_intake = res.json()
    res = await client.post(f"/api/intakes/{cust_intake['id']}/print", json={}, headers=mgr)
    assert res.json()["copies"] == 2
    jobs = (await client.get("/api/print-jobs", headers=mgr)).json()["jobs"]
    assert sum(1 for j in jobs if j["label"].startswith(f"Elismervény {cust_intake['serial']}")) == 2

    # automatikus munkalap kérdés nélkül; ismételt hívás ugyanazt adja
    res = await client.post(f"/api/intakes/{cust_intake['id']}/worksheet", json={}, headers=mgr)
    assert res.status_code in (200, 201), res.text
    task = res.json()
    assert task["title"].startswith("Szerviz — Ügyfél Gépe") and task["intake_id"] == cust_intake["id"]
    assert task["description"] == "folyik" and task["client_name"] == "Vevő Kft."
    res = await client.post(f"/api/intakes/{cust_intake['id']}/worksheet", json={}, headers=mgr)
    assert res.status_code in (200, 201) and res.json()["id"] == task["id"]


async def test_task_reschedule_note_and_visit_window(client, manager):
    _, mgr = manager
    partner = (await client.post("/api/partners", json={"name": "Időkapus Kft."}, headers=mgr)).json()
    asset = (await client.post("/api/assets", json={"barcode": "R107-WIN", "name": "Kapus Gép"}, headers=mgr)).json()
    await client.post(f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr)
    emp_user, _ = await make_user(email="r107-cal@example.com", role="employee")
    emp = await make_employee_record(emp_user, last_name="Naptár", first_name="Nóra")
    today = date.today()
    task = (
        await client.post(
            "/api/tasks",
            json={"title": "Karbantartás", "employee_id": str(emp.id), "due_date": today.isoformat(), "asset_id": asset["id"]},
            headers=mgr,
        )
    ).json()

    # határidő-módosítás indok nélkül → 422
    new_due = (today + timedelta(days=3)).isoformat()
    res = await client.patch(f"/api/tasks/{task['id']}", json={"due_date": new_due}, headers=mgr)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "tasks.reschedule_note_required"
    res = await client.patch(f"/api/tasks/{task['id']}", json={"due_date": new_due, "reschedule_note": "ab"}, headers=mgr)
    assert res.status_code == 422
    # ugyanaz a dátum → nem átütemezés, indok nem kell
    res = await client.patch(f"/api/tasks/{task['id']}", json={"due_date": today.isoformat()}, headers=mgr)
    assert res.status_code == 200, res.text
    # indokkal → mentve, a naplóban a régi → új dátum
    res = await client.patch(f"/api/tasks/{task['id']}", json={"due_date": new_due, "reschedule_note": "ügyfél kérte"}, headers=mgr)
    assert res.status_code == 200, res.text
    assert res.json()["due_date"] == new_due
    assert res.json()["reschedule_note"] == f"{today.isoformat()} → {new_due}: ügyfél kérte"

    # időkapu a feladaton + a partnerhez mentve → a következő feladat is látja
    res = await client.patch(
        f"/api/tasks/{task['id']}", json={"visit_window": " 8–10 h ", "save_window_for_partner": True}, headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["visit_window"] == "8–10 h"
    assert res.json()["asset"]["partner_visit_window"] == "8–10 h"
    assert res.json()["asset"]["partner_id"] == partner["id"]
    task2 = (
        await client.post(
            "/api/tasks",
            json={"title": "Következő", "employee_id": str(emp.id), "due_date": today.isoformat(), "asset_id": asset["id"]},
            headers=mgr,
        )
    ).json()
    assert task2["visit_window"] is None and task2["asset"]["partner_visit_window"] == "8–10 h"
    # időkapu törlése csak a feladaton (a partneré marad)
    res = await client.patch(f"/api/tasks/{task['id']}", json={"visit_window": ""}, headers=mgr)
    assert res.json()["visit_window"] is None and res.json()["asset"]["partner_visit_window"] == "8–10 h"


async def test_cash_roles_and_central_recipients(client, admin, manager):
    adm_user, adm = admin
    mgr_user, mgr = manager
    uk_user, uk = await make_user(email="r107-uk@example.com", role="uzletkoto")
    uk2_user, uk2 = await make_user(email="r107-uk2@example.com", role="uzletkoto")

    # képviselő: költség igen, betét/kivét NEM
    res = await client.post("/api/settlements/expenses", json={"amount_gross": 500, "note": "parkolás"}, headers=uk)
    assert res.status_code == 201, res.text
    for kind in ("deposit", "withdrawal"):
        res = await client.post("/api/settlements/expenses", json={"amount_gross": 500, "entry_type": kind}, headers=uk)
        assert res.status_code == 403, res.text
    res = await client.post("/api/settlements/expenses", json={"amount_gross": 500, "entry_type": "deposit"}, headers=mgr)
    assert res.status_code == 201, res.text

    # központi áttekintés: a képviselő CSAK a sajátját látja (más user_id-t kérve is)
    res = await client.get(f"/api/stats/cash/overview?user_id={mgr_user.id}", headers=uk)
    assert res.status_code == 200, res.text
    ids = {r["user_id"] for r in res.json()["registers"]}
    assert ids == {str(uk_user.id)}
    res = await client.get("/api/stats/cash/overview", headers=adm)
    assert {str(uk_user.id), str(mgr_user.id)} <= {r["user_id"] for r in res.json()["registers"]}

    # pénz átadása: admin + manager + a MÁSIK képviselő, saját magam nem
    rows = (await client.get("/api/stats/cash/central-recipients", headers=uk)).json()
    ids = {r["user_id"] for r in rows}
    assert str(uk2_user.id) in ids and str(mgr_user.id) in ids and str(adm_user.id) in ids
    assert str(uk_user.id) not in ids
    assert [r["role"] for r in rows] == sorted([r["role"] for r in rows], key=lambda x: {"admin": 0, "manager": 1}.get(x, 2))
    _ = _uuid  # noqa
