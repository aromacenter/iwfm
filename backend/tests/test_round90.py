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
