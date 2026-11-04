"""144. kör (x-presso jegyek): Billingó-partner „adószámmal rendelkezik” jelölés
(e0a4e310), szerződés számla-referencia a számla-megjegyzésben (94970648),
havi bérleti díjas egy-tételes számlasor (d5d54ecf), átvétel-munkalap végpont
feladat-jog nélkül (25fb7952)."""

from __future__ import annotations

from tests.test_consignment import make_product


async def test_billingo_partner_tax_type(monkeypatch):
    from app.models import Partner
    from app.services.wfm import billingo_service as bs

    calls: list[tuple[str, str, dict | None]] = []

    async def fake_api(api_key, method, path, json_body=None):
        calls.append((method, path, json_body))
        if method == "GET":
            return {"data": []}
        return {"id": 91}

    monkeypatch.setattr(bs, "_api", fake_api)
    p = Partner(name="Abraziv Kft.", address_zip="6000", address_city="Kecskemét", address_street="Paul Lechler",
                address_number="3.", tax_number="10504275-4-03")
    assert await bs._find_or_create_billingo_partner("k", p) == 91
    body = calls[-1][2]
    assert body["taxcode"] == "10504275-4-03" and body["tax_type"] == "HAS_TAX_NUMBER"

    # meglévő Billingó-partner adószámmal, de jelölés nélkül → PUT pótolja a tax_type-ot
    calls.clear()

    async def api_existing(api_key, method, path, json_body=None):
        calls.append((method, path, json_body))
        if method == "GET":
            return {"data": [{"id": 5, "name": "Abraziv Kft.", "taxcode": "10504275-4-03", "tax_type": "NO_TAX_NUMBER",
                              "address": {"country_code": "HU", "post_code": "6000", "city": "Kecskemét", "address": "Paul Lechler 3."}}]}
        return {"id": 5}

    monkeypatch.setattr(bs, "_api", api_existing)
    assert await bs._find_or_create_billingo_partner("k", p) == 5
    put = next(c for c in calls if c[0] == "PUT")
    assert put[1] == "/partners/5" and put[2]["tax_type"] == "HAS_TAX_NUMBER" and put[2]["taxcode"] == "10504275-4-03"

    # már jó a jelölés → nincs PUT
    calls.clear()

    async def api_ok(api_key, method, path, json_body=None):
        calls.append((method, path, json_body))
        if method == "GET":
            return {"data": [{"id": 5, "name": "Abraziv Kft.", "taxcode": "10504275-4-03", "tax_type": "HAS_TAX_NUMBER",
                              "emails": ["a@b.hu"], "address": {"country_code": "HU", "post_code": "6000", "city": "Kecskemét", "address": "Paul Lechler 3."}}]}
        return {"id": 5}

    monkeypatch.setattr(bs, "_api", api_ok)
    p.contact_email = "a@b.hu"
    assert await bs._find_or_create_billingo_partner("k", p) == 5
    assert not any(c[0] == "PUT" for c in calls)


async def test_contract_invoice_reference_and_monthly_rent_line(client, manager):
    import app.db as app_db
    from sqlalchemy import select

    from app.models import Partner, Settlement
    from app.services.wfm import billingo_service as bs

    _, mgr = manager
    partner = (await client.post("/api/partners", json={"name": "Strabag Telephely"}, headers=mgr)).json()
    coffee = await make_product(client, mgr, price_per_portion=100.0, grams_per_portion=7)
    cream = await make_product(client, mgr, name="Tejszín", price_per_portion=226.0, unit="db", is_consignment=False)
    await client.post(f"/api/partners/{partner['id']}/stock/replenish", json={"product_id": coffee["id"], "quantity": 1.0}, headers=mgr)

    # szerződés: referencia + havi bérleti díjas számlasor
    res = await client.post(f"/api/partners/{partner['id']}/contracts", json={
        "valid_from": "2026-01-01", "no_minimum": True,
        "invoice_reference": "STRABAG-PO-2026-77", "invoice_monthly_rent": True,
    }, headers=mgr)
    assert res.status_code == 201, res.text
    cid = res.json()["id"]
    assert res.json()["invoice_reference"] == "STRABAG-PO-2026-77" and res.json()["invoice_monthly_rent"] is True
    listed = (await client.get(f"/api/partners/{partner['id']}/contracts", headers=mgr)).json()[0]
    assert listed["invoice_reference"] == "STRABAG-PO-2026-77"

    res = await client.post("/api/settlements", json={
        "partner_id": partner["id"], "payment_method": "transfer",
        "lines": [{"product_id": coffee["id"], "physical_qty": 0.3}],
        "handovers": [{"product_id": cream["id"], "quantity": 2}],
    }, headers=mgr)
    assert res.status_code == 201, res.text
    sid = res.json()["id"]
    coffee_net = next(x["amount_net"] for x in res.json()["lines"] if "átadva" not in x["product_name"])
    assert coffee_net > 0

    factory = app_db.get_session_factory()
    async with factory() as db:
        s = (await db.execute(select(Settlement).where(Settlement.id == __import__("uuid").UUID(sid)))).scalar_one()
        p = (await db.execute(select(Partner).where(Partner.id == s.partner_id))).scalar_one()
        items = await bs.settlement_invoice_items(db, s)
        comment = await bs.settlement_invoice_comment(db, s, p)
    assert items[0]["name"] == bs.MONTHLY_RENT_LINE_NAME and items[0]["unit"] == "hónap" and items[0]["quantity"] == 1
    assert abs(items[0]["unit_price"] - coffee_net) < 0.01 and items[0]["vat"] == "27%"
    assert not any(i["name"] == bs.PORTION_LINE_NAME for i in items)
    assert any("Tejszín" in i["name"] for i in items)  # a nem-kávé tétel a nevén marad
    assert "Referencia: STRABAG-PO-2026-77" in comment

    # kapcsoló ki → a megszokott adagáras tétel
    res = await client.patch(f"/api/partners/{partner['id']}/contracts/{cid}", json={
        "valid_from": "2026-01-01", "no_minimum": True, "invoice_reference": None, "invoice_monthly_rent": False,
    }, headers=mgr)
    assert res.status_code == 200, res.text
    async with factory() as db:
        s = (await db.execute(select(Settlement).where(Settlement.id == __import__("uuid").UUID(sid)))).scalar_one()
        p = (await db.execute(select(Partner).where(Partner.id == s.partner_id))).scalar_one()
        items = await bs.settlement_invoice_items(db, s)
        comment = await bs.settlement_invoice_comment(db, s, p)
    assert any(i["name"] == bs.PORTION_LINE_NAME and i["unit"] == "adag" for i in items)
    assert "Referencia" not in comment


async def test_intake_worksheet_without_tasks_permission(client, admin):
    """25fb7952: az átvevőnek nincs feladat-joga (üzletkötő) → a munkalap-végpont
    neki is kiállítja a munkalapot (felelős: ő maga), másodszor ugyanazt adja."""
    from datetime import date

    from tests.conftest import make_employee_record, make_user

    _, adm = admin
    uk_user, uk = await make_user(email="szervizes-atvetel@example.com", role="szervizes")
    await make_employee_record(uk_user, last_name="Szakál", first_name="Tamás")
    assert (await client.post("/api/tasks", json={"title": "x", "employee_id": "y", "due_date": date.today().isoformat()}, headers=uk)).status_code in (403, 422)
    from tests.test_inventory import asset_payload

    cust = (await client.post("/api/assets", json=asset_payload(
        barcode="R144-CUST", serial_number="SN-R144", name="btr878", customer_owned=True, tangible=False,
    ), headers=adm)).json()
    res = await client.post("/api/intakes", json={
        "asset_id": cust["id"], "client_name": "Bejczi Bálint", "client_phone": "+36301234567", "client_email": "bejczi@example.com",
        "faults": "nem melegít",
    }, headers=uk)
    assert res.status_code == 201, res.text
    intake_id = res.json()["id"]
    res = await client.post(f"/api/intakes/{intake_id}/worksheet", headers=uk)
    assert res.status_code in (200, 201), res.text
    ws = res.json()
    assert ws["worksheet_serial"] and ws["employee_name"].startswith("Szakál")
    again = await client.post(f"/api/intakes/{intake_id}/worksheet", headers=uk)
    assert again.status_code in (200, 201) and again.json()["id"] == ws["id"]
