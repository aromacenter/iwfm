"""90. kör: gépcsere függő elszámolással, számláló-nullázás, bérelt gép,
többcímes partner-email, késedelmi felár, saját költség jog nélkül."""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime, timedelta

from tests.test_machine_settlement import _machine, _partner


async def test_swap_pending_billed_on_next_settlement(client, manager):
    """Gépcserekor a záró állások függőbe kerülnek, és a következő elszámolás
    számlázza a cseréig lefőzött adagokat (73cbc5ee)."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(client, mgr, "Cserés Bolt")
    product = await make_product(client, mgr, price_per_portion=50.0, grams_per_portion=7)
    old = await _machine(client, mgr, partner, "CSERE-REGI", counter=1000,
                         default_product_id=product["id"])
    res = await client.post(
        "/api/assets",
        json={"barcode": "CSERE-UJ", "name": "Cseregép", "counter": 0},
        headers=mgr,
    )
    new = res.json()
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 10.0},
        headers=mgr,
    )

    # csere: a régi záró állása 1150, a cseregép 5-ről indul
    res = await client.post(
        f"/api/assets/{old['id']}/swap",
        json={"replacement_asset_id": new["id"], "old_counter": 1150, "new_counter": 5},
        headers=mgr,
    )
    assert res.status_code == 200, res.text

    # kontextus: a leszerelt gép függő sora a záró állással
    ctx = (
        await client.get(f"/api/partners/{partner['id']}/settlement-context", headers=mgr)
    ).json()
    swapped = [m for m in ctx["machines"] if m.get("swapped")]
    assert len(swapped) == 1
    assert swapped[0]["barcode"] == "CSERE-REGI"
    assert swapped[0]["swap_final_counter"] == 1150
    assert swapped[0]["prev_counter"] == 1000
    # a cseregép normál sora is ott van, 5-ös induló állással
    normal = next(m for m in ctx["machines"] if m["barcode"] == "CSERE-UJ")
    assert normal["prev_counter"] == 5

    # elszámolás: a leszerelt gép 150 adagja számlázódik (1150−1000)
    res = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "cash",
            "lines": [{"product_id": product["id"], "physical_qty": 8.9}],
            "machines": [{"asset_id": old["id"], "new_counter": 1150,
                          "service_portions": 0}],
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    m = res.json()["machines"][0]
    assert m["portions_billed"] == 150

    # a függő csere lezárult — másodszor már nem elszámolható
    ctx = (
        await client.get(f"/api/partners/{partner['id']}/settlement-context", headers=mgr)
    ).json()
    assert not [m for m in ctx["machines"] if m.get("swapped")]


async def test_reset_counters_except_control(client, manager):
    """Számláló-nullázás: minden állás nullázódik a kontroll (0 Ft-os) kivételével,
    és a következő elszámolás nulláról indul (74e44b49)."""
    _, mgr = manager
    res = await client.post(
        "/api/assets",
        json={"barcode": "NULLAZ-1", "name": "Nullázós gép", "counter_count": 3,
              "counters": [800, 400, 1200], "counter_prices": [50, 60, 0]},
        headers=mgr,
    )
    assert res.status_code in (200, 201), res.text
    asset = res.json()
    res = await client.post(f"/api/assets/{asset['id']}/reset-counters", headers=mgr)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["counters"] == [0, 0, 1200]  # a kontroll állás megmarad
    assert out["counter"] == 1200


async def test_rented_asset_label_without_owner_text(client, admin, manager):
    """Bérelt gép címkéjén nincs tulajdonos-felirat (8b6098c9)."""
    _, adm = admin
    _, mgr = manager
    res = await client.post(
        "/api/assets",
        json={"barcode": "BERELT-1", "name": "Bérelt gép", "tangible": True,
              "rented": True},
        headers=mgr,
    )
    assert res.status_code in (200, 201), res.text
    rented = res.json()
    assert rented["rented"] is True
    key = (await client.post("/api/print-jobs/agent-key", headers=adm)).json()["key"]
    await client.post("/api/print-jobs", json={"ids": [rented["id"]]}, headers=mgr)
    jobs = (await client.get("/api/print-agent/jobs", headers={"X-Agent-Key": key})).json()["jobs"]
    payload = next(j["payload"] for j in jobs if "BERELT-1" in j["label"])
    assert "tulajdona" not in payload

    # bérelt + ügyfél gépe egyszerre: 422
    res = await client.post(
        "/api/assets",
        json={"barcode": "BERELT-X", "name": "Rossz", "rented": True,
              "customer_owned": True},
        headers=mgr,
    )
    assert res.status_code == 422


async def test_partner_multi_email(client, manager):
    """Több e-mail cím vesszővel/pontosvesszővel; hibás cím 422 (300f6d05)."""
    _, mgr = manager
    res = await client.post(
        "/api/partners",
        json={"name": "Többcímes Bolt",
              "contact_email": "elso@bolt.hu; masodik@bolt.hu, harmadik@kozpont.hu"},
        headers=mgr,
    )
    assert res.status_code in (200, 201), res.text
    assert res.json()["contact_email"] == "elso@bolt.hu, masodik@bolt.hu, harmadik@kozpont.hu"

    res = await client.post(
        "/api/partners",
        json={"name": "Hibás Email Bolt", "contact_email": "nem email cim"},
        headers=mgr,
    )
    assert res.status_code == 422

    from app.services.wfm.email_service import split_addresses

    assert split_addresses("a@x.hu; b@y.hu, c@z.hu") == ["a@x.hu", "b@y.hu", "c@z.hu"]


async def test_late_fee_on_overdue_debt(client, manager):
    """30+ napja lejárt tartozásnál +10% felár minden tételre, ÁSZF-hivatkozással;
    pipával kikapcsolható (9c7f54e5)."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = await _partner(client, mgr, "Késedelmes Bolt")
    product = await make_product(client, mgr, price_per_portion=100.0)
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 10.0},
        headers=mgr,
    )

    # régi, 40 napja lejárt, fizetetlen elszámolás közvetlenül a DB-be
    import app.db as app_db
    from app.models import Settlement

    factory = app_db.get_session_factory()
    async with factory() as session:
        session.add(Settlement(
            id=_uuid.uuid4(), partner_id=_uuid.UUID(partner["id"]),
            settled_by_name="Teszt", payment_method="transfer",
            total_net=1000.0, total_gross=1270.0, invoiced=False,
            payment_status="none", paid_amount=0.0,
            due_date=(datetime.now(UTC) - timedelta(days=40)).date(),
            created_at=datetime.now(UTC) - timedelta(days=50),
        ))
        await session.commit()

    # felár alkalmazva: 2 kg fogyás ≈ 286 adag… egyszerűbb: fix leltár
    res = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "cash",
            "lines": [{"product_id": product["id"], "physical_qty": 9.0}],
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    s = res.json()
    assert s["late_fee_pct"] == 10
    assert "ÁSZF" in (s["note"] or "")
    assert all("késedelmi felár" in ln["product_name"] for ln in s["lines"])

    # kikapcsolva: nincs felár
    res = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "cash",
            "lines": [{"product_id": product["id"], "physical_qty": 8.5}],
            "late_fee_waived": True,
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    s2 = res.json()
    assert s2["late_fee_pct"] is None
    assert not any("késedelmi felár" in ln["product_name"] for ln in s2["lines"])


async def test_expense_self_without_perm(client, manager):
    """Saját költséget bármely bejelentkezett dolgozó rögzíthet; betét/kivét
    továbbra is invoicing-jogos (590a9b99)."""
    from tests.conftest import make_user

    _, szerviz_hdr = await make_user(email="koltseges@example.com", role="szervizes")
    res = await client.post(
        "/api/settlements/expenses",
        json={"amount_gross": 2500, "note": "Alkatrész csavar",
              "supplier": "Csavar Kft", "receipt_no": "SZ-123"},
        headers=szerviz_hdr,
    )
    assert res.status_code == 201, res.text

    res = await client.post(
        "/api/settlements/expenses",
        json={"amount_gross": 1000, "entry_type": "deposit"},
        headers=szerviz_hdr,
    )
    assert res.status_code == 403

    # beszállító-javaslatok a korábbi tételekből
    res = await client.get("/api/stats/cash/suppliers", headers=szerviz_hdr)
    assert res.status_code == 200
    assert "Csavar Kft" in res.json()


def test_merge_emails_dedup():
    """a3ea5d99: cimlistak egyesitese duplikatum nelkul — az atvetelin
    rogzitett cim a kezi/ajanlat-cim melle kerul."""
    from app.api.tasks import _merge_emails

    assert _merge_emails("kezi@x.hu", "atvetel@y.hu") == "kezi@x.hu, atvetel@y.hu"
    assert _merge_emails("a@x.hu; b@y.hu", "A@X.HU") == "a@x.hu, b@y.hu"
    assert _merge_emails(None, "csak@atvetel.hu") == "csak@atvetel.hu"
    assert _merge_emails(None, None) == ""


async def test_company_technician_breakdown(client, manager):
    """c7ef9ce9 v2: alvallalkozo CEG cegnevvel; a munkalapon rogzitett
    szerelo szerinti bontas a dij-osszesitoben."""
    from tests.conftest import make_employee_record, make_user

    _, mgr = manager
    emp_user, emp_hdr = await make_user(email="cegszerviz@example.com", role="szervizes")
    emp = await make_employee_record(emp_user)
    res = await client.patch(
        f"/api/employees/{emp.id}",
        json={"is_contractor": True, "is_company": True,
              "company_name": "GepDoktor Kft."},
        headers=mgr,
    )
    # admin-only PATCH eseten manager 403 lehet — akkor adminnal kene; itt
    # elfogadjuk a 200-at vagy atallitjuk kozvetlenul.
    if res.status_code != 200:
        import app.db as app_db
        from app.models import Employee as _E
        from sqlalchemy import select as _sel

        factory = app_db.get_session_factory()
        async with factory() as session:
            row = (await session.execute(_sel(_E).where(_E.id == emp.id))).scalar_one()
            row.is_contractor = True
            row.is_company = True
            row.company_name = "GepDoktor Kft."
            await session.commit()

    task = (
        await client.post(
            "/api/tasks",
            json={"title": "Ceges javitas", "employee_id": str(emp.id),
                  "due_date": "2026-10-08", "external_service": True},
            headers=mgr,
        )
    ).json()
    res = await client.put(
        f"/api/me/tasks/{task['id']}/worksheet",
        json={"work_description": "Javitva.", "technician_name": "Kiss Bela",
              "works": [{"name": "Javitas", "cost_net": 12000}]},
        headers=emp_hdr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["technician_name"] == "Kiss Bela"

    # atvetel (pickup) → bekerul a dij-osszesitobe
    res = await client.post(
        f"/api/tasks/{task['id']}/worksheet/picked-up", json={}, headers=mgr
    )
    # SMTP nincs a tesztben — a pickup 422-t adhat; a fees a picked_up-ra szur,
    # ezert kozvetlenul allitjuk be.
    if res.status_code != 200:
        import app.db as app_db
        from datetime import UTC as _UTC, datetime as _dt
        from app.models import Worksheet as _W
        from sqlalchemy import select as _sel
        import uuid as _u

        factory = app_db.get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(_sel(_W).where(_W.task_id == _u.UUID(task["id"])))
            ).scalar_one()
            row.picked_up_at = _dt.now(_UTC)
            await session.commit()

    fees = (await client.get("/api/tasks/service-handover/fees", headers=mgr)).json()
    frow = next(r for r in fees if r["employee_id"] == str(emp.id))
    assert frow["employee_name"] == "GepDoktor Kft."
    assert frow["technicians"][0]["name"] == "Kiss Bela"
    assert frow["technicians"][0]["fee_total"] == 12000


async def test_task_list_client_and_overview_repairs(client, manager):
    """077af934: a feladat-lista adja az ugyfel nevet; a partner-adatlap
    overview-ja a javitas-elozmenyeket."""
    from tests.conftest import make_employee_record, make_user
    from tests.test_machine_settlement import _partner

    _, mgr = manager
    emp_user, _ = await make_user(email="overview-szerviz@example.com", role="szervizes")
    emp = await make_employee_record(emp_user)
    partner = await _partner(client, mgr, "Javitasos Bolt")
    task = (
        await client.post(
            "/api/tasks",
            json={"title": "Overview teszt javitas", "employee_id": str(emp.id),
                  "due_date": "2026-10-08", "external_service": True,
                  "client_name": "Javitasos Bolt"},
            headers=mgr,
        )
    ).json()
    rows = (await client.get("/api/tasks", headers=mgr)).json()
    trow = next(r for r in rows if r["id"] == task["id"])
    assert trow["client_name"] == "Javitasos Bolt"

    ov = (await client.get(f"/api/partners/{partner['id']}/overview", headers=mgr)).json()
    assert any(r["task_id"] == task["id"] for r in ov["repairs"])


async def test_quote_history_endpoint(client, manager, admin):
    """d925d451: az ajanlat-elozmeny (osszes opcio + ki/mikor dontott)
    visszanezheto a dontes utan is."""
    from tests.conftest import make_employee_record, make_user

    _, mgr = manager
    emp_user, emp_hdr = await make_user(email="elozmeny@example.com", role="szervizes")
    emp = await make_employee_record(emp_user)
    task = (
        await client.post(
            "/api/tasks",
            json={"title": "Elozmeny teszt", "employee_id": str(emp.id),
                  "due_date": "2026-10-10", "external_service": True},
            headers=mgr,
        )
    ).json()
    res = await client.put(
        f"/api/me/tasks/{task['id']}/worksheet",
        json={"work_description": "Bevizsgalva.",
              "repair_options": [
                  {"name": "Olcso javitas", "cost_net": 5000, "price_net": 15000},
                  {"name": "Teljes felujitas", "cost_net": 20000, "price_net": 45000},
              ]},
        headers=emp_hdr,
    )
    assert res.status_code == 200, res.text

    # dontes elott: 404
    res = await client.get(f"/api/tasks/{task['id']}/worksheet/quote-history", headers=mgr)
    assert res.status_code == 404

    res = await client.post(
        f"/api/tasks/{task['id']}/quote/accept-internal",
        json={"option_name": "Olcso javitas"},
        headers=mgr,
    )
    assert res.status_code == 200, res.text

    hist = (await client.get(f"/api/tasks/{task['id']}/worksheet/quote-history", headers=mgr)).json()
    assert hist["status"] == "accepted"
    assert hist["selected"] == "Olcso javitas"
    assert "belső döntés" in hist["accepted_by"]
    names = [o["name"] for o in hist["options"]]
    assert "Olcso javitas" in names and "Teljes felujitas" in names
