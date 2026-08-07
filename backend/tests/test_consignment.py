"""Bizomány: termékek, partner külső raktár feltöltés, kg→adag számítás, RBAC."""

from tests.test_inventory import make_partner


async def make_product(client, headers, **kw) -> dict:
    body = {"name": "Házi keverék kávé", "grams_per_portion": 7, "price_per_portion": 50.0}
    body.update(kw)
    res = await client.post("/api/products", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


async def test_product_crud(client, manager):
    _, mgr = manager
    prod = await make_product(client, mgr, price_per_portion=60.0)
    assert prod["grams_per_portion"] == 7
    assert prod["vat_percent"] == 27

    upd = await client.patch(
        f"/api/products/{prod['id']}",
        json={"name": "Prémium kávé", "grams_per_portion": 8, "price_per_portion": 70.0},
        headers=mgr,
    )
    assert upd.status_code == 200
    assert upd.json()["name"] == "Prémium kávé"
    assert upd.json()["grams_per_portion"] == 8

    listed = (await client.get("/api/products", headers=mgr)).json()
    assert len(listed) == 1


async def test_replenish_and_portions(client, manager):
    """1 kg / 7 g/adag = 142 adag (lefelé kerekítve)."""
    _, mgr = manager
    partner = await make_partner(client, mgr)
    product = await make_product(client, mgr)

    res = await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 1.0},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["quantity"] == 1.0
    assert body["portions_available"] == 142  # floor(1000/7)

    # újabb feltöltés összeadódik
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 0.5},
        headers=mgr,
    )
    stock = (await client.get(f"/api/partners/{partner['id']}/stock", headers=mgr)).json()
    assert len(stock) == 1
    assert stock[0]["quantity"] == 1.5
    assert stock[0]["portions_available"] == 214  # floor(1500/7)


async def test_consignment_requires_manager(client, employee_user):
    _, emp_headers, _ = employee_user
    assert (await client.get("/api/products", headers=emp_headers)).status_code == 403
    assert (
        await client.post("/api/products", json={"name": "X"}, headers=emp_headers)
    ).status_code == 403


async def _setup_settlement(client, mgr, *, physical=0.3, qty=1.0):
    """Partner + termék + feltöltés + elszámolás. 1 kg-ból 0.3 kg maradt →
    0.7 kg fogyás = 100 adag (7 g/adag) × 50 Ft = 5000 Ft nettó."""
    partner = await make_partner(client, mgr, name="Kávézó Bt.")
    product = await make_product(client, mgr)
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": qty},
        headers=mgr,
    )
    res = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "cash",
            "lines": [{"product_id": product["id"], "physical_qty": physical}],
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    return partner, product, res.json()


async def test_settlement_flow(client, manager):
    _, mgr = manager
    partner, product, body = await _setup_settlement(client, mgr)

    line = body["lines"][0]
    assert line["previous_qty"] == 1.0
    assert line["physical_qty"] == 0.3
    assert abs(line["consumed_qty"] - 0.7) < 1e-9
    assert abs(line["portions"] - 100.0) < 0.01  # 700 g / 7 g
    assert abs(line["amount_net"] - 5000.0) < 0.01
    assert body["settled_by_name"]  # a bejelentkezett user neve rögzül
    assert body["payment_method"] == "cash"
    assert body["invoiced"] is False

    # a könyv szerinti készlet a fizikai leltárra állt
    stock = (await client.get(f"/api/partners/{partner['id']}/stock", headers=mgr)).json()
    assert stock[0]["quantity"] == 0.3

    # lista + részletek
    listed = (await client.get("/api/settlements", headers=mgr)).json()
    assert len(listed) == 1 and listed[0]["partner_name"] == "Kávézó Bt."
    detail = (await client.get(f"/api/settlements/{body['id']}", headers=mgr)).json()
    assert len(detail["lines"]) == 1


async def test_settlement_bad_payment_rejected(client, manager):
    _, mgr = manager
    partner = await make_partner(client, mgr, name="Rossz Kft.")
    product = await make_product(client, mgr, name="Teszt kávé")
    res = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "bitcoin",
            "lines": [{"product_id": product["id"], "physical_qty": 0}],
        },
        headers=mgr,
    )
    assert res.status_code == 422


async def test_invoice_requires_billingo_config(client, manager):
    """A „Kiszámlázott” gomb Billingó-konfiguráció nélkül 422-t ad, és a
    settlement invoiced=False marad."""
    _, mgr = manager
    _, _, body = await _setup_settlement(client, mgr)

    res = await client.post(f"/api/settlements/{body['id']}/invoice", headers=mgr)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "settings.billingo_not_configured"

    detail = (await client.get(f"/api/settlements/{body['id']}", headers=mgr)).json()
    assert detail["invoiced"] is False


async def test_billingo_settings_roundtrip(client, admin):
    _, headers = admin
    res = await client.put(
        "/api/settings/billingo",
        json={"enabled": True, "api_key": "titkos-kulcs", "block_id": 42, "test_mode": True},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["has_api_key"] is True
    assert body["test_mode"] is True
    assert "api_key" not in body  # a kulcs sosem megy vissza


async def test_bulk_delete_products_and_stock_guard(client, manager, admin):
    """Termék tömeges törlés: kint lévő készletű blokkolva, üres törölhető."""
    _, mgr = manager
    _, adm = admin
    partner = await make_partner(client, mgr, name="Raktáras Bt.")
    stocked = await make_product(client, mgr, name="Kint lévő kávé")
    empty = await make_product(client, mgr, name="Üres termék")
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": stocked["id"], "quantity": 1.0},
        headers=mgr,
    )

    res = await client.post(
        "/api/products/bulk-delete",
        json={"ids": [stocked["id"], empty["id"]]},
        headers=adm,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["deleted"] == 1
    assert len(body["blocked"]) == 1
    assert body["blocked"][0]["code"] == "product.has_stock"

    names = {p["name"] for p in (await client.get("/api/products", headers=mgr)).json()}
    assert "Üres termék" not in names and "Kint lévő kávé" in names


async def test_bulk_delete_partner_guards(client, manager, admin):
    """Partner törlés: elszámolásos partner blokkolva, sima törölhető."""
    _, mgr = manager
    _, adm = admin
    _, _, settlement = await _setup_settlement(client, mgr)  # 'Kávézó Bt.' elszámolással
    plain = await make_partner(client, mgr, name="Sima Kft.")

    res = await client.post(
        "/api/partners/bulk-delete",
        json={"ids": [settlement["partner_id"], plain["id"]]},
        headers=adm,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["deleted"] == 1
    assert body["blocked"][0]["code"] == "partner.has_settlements"

    names = {p["name"] for p in (await client.get("/api/partners", headers=mgr)).json()}
    assert "Sima Kft." not in names and "Kávézó Bt." in names


async def test_bulk_delete_settlement_restores_stock(client, manager, admin):
    """Elszámolás törlése: a fogyás visszakerül a készletbe; számlázott blokkolt."""
    import uuid as _uuid

    from sqlalchemy import select as _select

    import app.db as app_db
    from app.models import Settlement

    _, mgr = manager
    _, adm = admin
    partner, product, body = await _setup_settlement(client, mgr)  # 1.0 → 0.3, fogyás 0.7

    res = await client.post(
        "/api/settlements/bulk-delete", json={"ids": [body["id"]]}, headers=adm
    )
    assert res.status_code == 200, res.text
    assert res.json()["deleted"] == 1

    # a készlet visszaállt az elszámolás előtti 1.0 kg-ra
    stock = (await client.get(f"/api/partners/{partner['id']}/stock", headers=mgr)).json()
    assert abs(stock[0]["quantity"] - 1.0) < 1e-9
    assert (await client.get("/api/settlements", headers=mgr)).json() == []

    # új elszámolás, kézzel számlázottra állítva → blokkolt
    res2 = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "card",
            "lines": [{"product_id": product["id"], "physical_qty": 0.5}],
        },
        headers=mgr,
    )
    sid = res2.json()["id"]
    factory = app_db.get_session_factory()
    async with factory() as session:
        s = (
            await session.execute(_select(Settlement).where(Settlement.id == _uuid.UUID(sid)))
        ).scalar_one()
        s.invoiced = True
        await session.commit()

    res3 = await client.post(
        "/api/settlements/bulk-delete", json={"ids": [sid]}, headers=adm
    )
    assert res3.json()["deleted"] == 0
    assert res3.json()["blocked"][0]["code"] == "settlement.invoiced"


PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


async def test_settlement_receipt_pdf_and_signature(client, manager):
    """Bizonylat PDF letölthető; a partner-aláírás rögzíthető és a PDF-be kerül."""
    _, mgr = manager
    _, _, body = await _setup_settlement(client, mgr)

    res = await client.get(f"/api/settlements/{body['id']}/pdf", headers=mgr)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/pdf"
    assert res.content[:5] == b"%PDF-"

    # érvénytelen aláírás-adat
    bad = await client.post(
        f"/api/settlements/{body['id']}/signature",
        json={"signature": "data:text/plain;base64,aGVsbG8hIGl0J3Mgbm90IGEgcG5n"},
        headers=mgr,
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "settlement.bad_signature"

    ok = await client.post(
        f"/api/settlements/{body['id']}/signature",
        json={"signature": PNG_DATA_URL},
        headers=mgr,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["has_signature"] is True

    # aláírással is generálható a PDF
    res2 = await client.get(f"/api/settlements/{body['id']}/pdf", headers=mgr)
    assert res2.status_code == 200


async def test_settlement_receipt_email_requires_config(client, manager):
    """Email-küldés: cím nélkül settlement.no_email, SMTP nélkül
    settings.email_not_configured."""
    _, mgr = manager
    _, _, body = await _setup_settlement(client, mgr)  # partner email: p@example.com

    # cím nélkül a partner kapcsolattartói emailje a default → SMTP-hiba jön
    res = await client.post(
        f"/api/settlements/{body['id']}/receipt-email", json={}, headers=mgr
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "settings.email_not_configured"

    # email nélküli partnernél cím sincs → settlement.no_email
    no_mail = (
        await client.post("/api/partners", json={"name": "Némacím Bt."}, headers=mgr)
    ).json()
    prod = await make_product(client, mgr, name="Néma kávé")
    await client.post(
        f"/api/partners/{no_mail['id']}/stock/replenish",
        json={"product_id": prod["id"], "quantity": 1.0},
        headers=mgr,
    )
    s2 = (
        await client.post(
            "/api/settlements",
            json={
                "partner_id": no_mail["id"],
                "payment_method": "cash",
                "lines": [{"product_id": prod["id"], "physical_qty": 0.5}],
            },
            headers=mgr,
        )
    ).json()
    res2 = await client.post(
        f"/api/settlements/{s2['id']}/receipt-email", json={}, headers=mgr
    )
    assert res2.status_code == 422
    assert res2.json()["detail"]["code"] == "settlement.no_email"


async def test_due_settlements(client, manager):
    """Esedékesek: készletes, sosem elszámolt partner szerepel; a most
    elszámolt (0 napja) nem éri el a 30 napos küszöböt."""
    _, mgr = manager
    # sosem elszámolt, de van kint készlete
    waiting = await make_partner(client, mgr, name="Váró Bt.")
    product = await make_product(client, mgr, name="Váró kávé")
    await client.post(
        f"/api/partners/{waiting['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 2.0},
        headers=mgr,
    )
    # most elszámolt partner
    await _setup_settlement(client, mgr)
    # készlet és elszámolás nélküli partner — nem releváns
    await make_partner(client, mgr, name="Üres Kft.")

    due = (await client.get("/api/settlements/due?days=30", headers=mgr)).json()
    names = [d["name"] for d in due]
    assert "Váró Bt." in names
    assert "Kávézó Bt." not in names  # ma volt elszámolva
    assert "Üres Kft." not in names
    row = next(d for d in due if d["name"] == "Váró Bt.")
    assert row["last_settlement_at"] is None
    assert row["days_since"] is None
    assert row["stock_products"] == 1

    # 1 napos küszöbbel sem jelenik meg a ma elszámolt (0 nap < 1 nap)
    due1 = (await client.get("/api/settlements/due?days=1", headers=mgr)).json()
    assert "Kávézó Bt." not in [d["name"] for d in due1]


async def test_low_stock_alert(client, manager):
    """Küszöb alatti készlet jelez; küszöb nélküli termék sosem."""
    _, mgr = manager
    partner = await make_partner(client, mgr, name="Fogyó Bt.")
    watched = await make_product(client, mgr, name="Figyelt kávé", low_stock_threshold=0.5)
    silent = await make_product(client, mgr, name="Néma kávé")  # nincs küszöb

    for product in (watched, silent):
        await client.post(
            f"/api/partners/{partner['id']}/stock/replenish",
            json={"product_id": product["id"], "quantity": 2.0},
            headers=mgr,
        )
    assert (await client.get("/api/products/low-stock", headers=mgr)).json() == []

    # elszámolás után 0.3 kg marad a figyelt termékből → riasztás
    await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "cash",
            "lines": [
                {"product_id": watched["id"], "physical_qty": 0.3},
                {"product_id": silent["id"], "physical_qty": 0.2},
            ],
        },
        headers=mgr,
    )
    alerts = (await client.get("/api/products/low-stock", headers=mgr)).json()
    assert len(alerts) == 1
    assert alerts[0]["product_name"] == "Figyelt kávé"
    assert alerts[0]["partner_name"] == "Fogyó Bt."
    assert abs(alerts[0]["quantity"] - 0.3) < 1e-9
    assert alerts[0]["threshold"] == 0.5


async def test_partner_price_override(client, manager):
    """Partner-ár: a felülírt ár érvényes a készletnézetben és az
    elszámolásban; törlés után visszaáll az alapár."""
    _, mgr = manager
    partner = await make_partner(client, mgr, name="Kedvezményes Kft.")
    product = await make_product(client, mgr)  # alapár: 50 Ft/adag

    res = await client.put(
        f"/api/partners/{partner['id']}/prices/{product['id']}",
        json={"price_per_portion": 40.0},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["price_per_portion"] == 40.0
    assert res.json()["base_price_per_portion"] == 50.0

    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 1.0},
        headers=mgr,
    )
    stock = (await client.get(f"/api/partners/{partner['id']}/stock", headers=mgr)).json()
    assert stock[0]["price_per_portion"] == 40.0
    assert stock[0]["has_price_override"] is True

    # elszámolás a partner-árral: 0.7 kg = 100 adag × 40 Ft = 4000 Ft
    body = (
        await client.post(
            "/api/settlements",
            json={
                "partner_id": partner["id"],
                "payment_method": "cash",
                "lines": [{"product_id": product["id"], "physical_qty": 0.3}],
            },
            headers=mgr,
        )
    ).json()
    assert abs(body["lines"][0]["amount_net"] - 4000.0) < 0.01
    assert body["lines"][0]["price_per_portion"] == 40.0

    # felülírás törlése → alapár
    cleared = await client.delete(
        f"/api/partners/{partner['id']}/prices/{product['id']}", headers=mgr
    )
    assert cleared.status_code == 200
    assert cleared.json()["price_per_portion"] is None
    listed = (await client.get(f"/api/partners/{partner['id']}/prices", headers=mgr)).json()
    row = next(r for r in listed if r["product_id"] == product["id"])
    assert row["price_per_portion"] is None

    stock2 = (await client.get(f"/api/partners/{partner['id']}/stock", headers=mgr)).json()
    assert stock2[0]["price_per_portion"] == 50.0
    assert stock2[0]["has_price_override"] is False


async def test_receivables_and_mark_paid(client, manager):
    """Kintlévőségek: kézzel számlázottra állított utalásos elszámolás
    megjelenik, lejárat számítódik, fizetve-jelöléssel eltűnik."""
    import uuid as _uuid
    from datetime import date as _date, timedelta as _td

    from sqlalchemy import select as _select

    import app.db as app_db
    from app.models import Settlement

    _, mgr = manager
    partner, product, _first = await _setup_settlement(client, mgr)
    res = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"],
            "payment_method": "transfer",
            "lines": [{"product_id": product["id"], "physical_qty": 0.1}],
        },
        headers=mgr,
    )
    sid = res.json()["id"]

    # nem számlázott → mark-paid 422
    denied = await client.post(f"/api/settlements/{sid}/mark-paid", headers=mgr)
    assert denied.status_code == 422
    assert denied.json()["detail"]["code"] == "settlement.not_invoiced"

    # kézzel számlázottra állítjuk 5 napja lejárt határidővel
    factory = app_db.get_session_factory()
    async with factory() as session:
        s = (
            await session.execute(_select(Settlement).where(Settlement.id == _uuid.UUID(sid)))
        ).scalar_one()
        s.invoiced = True
        s.payment_status = "outstanding"
        s.due_date = _date.today() - _td(days=5)
        await session.commit()

    recv = (await client.get("/api/settlements/receivables", headers=mgr)).json()
    assert len(recv) == 1
    assert recv[0]["days_overdue"] == 5
    assert recv[0]["partner_name"] == "Kávézó Bt."

    ok = await client.post(f"/api/settlements/{sid}/mark-paid", headers=mgr)
    assert ok.status_code == 200
    assert ok.json()["payment_status"] == "paid"
    assert ok.json()["paid_at"] is not None

    assert (await client.get("/api/settlements/receivables", headers=mgr)).json() == []

    # kétszer nem jelölhető
    again = await client.post(f"/api/settlements/{sid}/mark-paid", headers=mgr)
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "settlement.already_paid"


async def test_sync_payment_requires_billingo(client, manager):
    import uuid as _uuid

    from sqlalchemy import select as _select

    import app.db as app_db
    from app.models import Settlement

    _, mgr = manager
    _, _, body = await _setup_settlement(client, mgr)
    sid = body["id"]

    # nem számlázott → 422 not_invoiced
    res = await client.post(f"/api/settlements/{sid}/sync-payment", headers=mgr)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "settlement.not_invoiced"

    factory = app_db.get_session_factory()
    async with factory() as session:
        s = (
            await session.execute(_select(Settlement).where(Settlement.id == _uuid.UUID(sid)))
        ).scalar_one()
        s.invoiced = True
        s.billingo_document_id = "12345"
        await session.commit()

    # Billingó-konfiguráció nélkül 422
    res2 = await client.post(f"/api/settlements/{sid}/sync-payment", headers=mgr)
    assert res2.status_code == 422
    assert res2.json()["detail"]["code"] == "settings.billingo_not_configured"


async def test_agent_summary(client, manager):
    """Üzletkötő-elszámolás: agents lista + fizetési módonkénti összesítés."""
    _, mgr = manager
    await _setup_settlement(client, mgr)

    agents = (await client.get("/api/settlements/agents", headers=mgr)).json()
    assert len(agents) == 1 and agents[0]["count"] == 1

    summary = (
        await client.get(
            "/api/settlements/summary", params={"settled_by": agents[0]["user_id"]}, headers=mgr
        )
    ).json()
    assert summary["count"] == 1
    assert summary["by_payment"]["cash"]["count"] == 1
    assert abs(summary["by_payment"]["cash"]["net"] - 5000.0) < 0.01
    assert summary["by_payment"]["card"]["count"] == 0
    assert abs(summary["total_gross"] - 6350.0) < 0.01  # 5000 * 1.27
