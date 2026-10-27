"""112. kör (hibajegyek 2026-10-27): végösszeg-kerekítés (5 Ft készpénznél,
egész Ft egyébként) + kerekítés-sor a bizonylaton, számlázói bizonylatszám,
gép-soros adagár a pillanatképen, elengedett készlethiány kedvezményként,
átadott kávé 0 Ft-os sora szerződés nélkül is, kassza-mozgások, portál-link
e-mailben."""
from __future__ import annotations

from datetime import date

from tests.test_consignment import make_product
from tests.test_inventory import make_partner
from tests.test_machine_settlement import _machine, _partner


async def test_gross_rounding_cash_5ft_and_transfer_1ft(client, manager):
    _, mgr = manager
    partner = await make_partner(client, mgr, name="Kerek Bt.")
    # 33 Ft/adag × 7 g: 0.7 kg = 100 adag → 3300 nettó → 4191 bruttó → 4190 (5 Ft)
    product = await make_product(client, mgr, price_per_portion=33.0)
    await client.post(f"/api/partners/{partner['id']}/stock/replenish",
                      json={"product_id": product["id"], "quantity": 1.0}, headers=mgr)
    res = await client.post("/api/settlements", json={
        "partner_id": partner["id"], "payment_method": "cash",
        "lines": [{"product_id": product["id"], "physical_qty": 0.3}],
    }, headers=mgr)
    assert res.status_code == 201, res.text
    out = res.json()
    assert out["total_net"] == 3300 and out["total_gross"] == 4190 and out["total_gross"] % 5 == 0
    assert abs(out["rounding_adjust"] - (-1.0)) < 0.01
    # a nyugtán és az A4-en is látszik a kerekítés-sor
    for fmt in ("a4", "80"):
        pdf = await client.get(f"/api/settlements/{out['id']}/pdf?format={fmt}", headers=mgr)
        assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    # átutalás: egész forintra — 0.71 kg → 101.43 adag... itt 0.33 kg: 47.14 adag
    partner2 = await make_partner(client, mgr, name="Utalós Bt.")
    await client.post(f"/api/partners/{partner2['id']}/stock/replenish",
                      json={"product_id": product["id"], "quantity": 1.0}, headers=mgr)
    res = await client.post("/api/settlements", json={
        "partner_id": partner2["id"], "payment_method": "transfer",
        "lines": [{"product_id": product["id"], "physical_qty": 0.67}],
    }, headers=mgr)
    assert res.status_code == 201, res.text
    g = res.json()["total_gross"]
    assert g == int(g)
    from app.api.consignment import round_gross

    assert round_gross(93177.68, "cash") == 93180 and round_gross(138370.84, "cash") == 138370
    assert round_gross(93177.5, "transfer") == 93178 and round_gross(93177.49, "transfer") == 93177
    assert round_gross(12.5, "cod") == 15 and round_gross(12.49, "cod") == 10


async def test_machine_line_snapshot_uses_contract_price(client, manager):
    """4061c36e: a bizonylat sorában a tényleges (gép/szerződéses) adagár
    szerepel, nem a termék alapára."""
    _, mgr = manager
    partner = await _partner(client, mgr, "Fodrász Bolt")
    product = await make_product(client, mgr, price_per_portion=200.0, grams_per_portion=7)
    # mint az éles JURA: összesítő (0 Ft kontroll) + 120 Ft-os szerződéses számláló
    asset = await _machine(client, mgr, partner, "GEP-120", counter=0,
                           counter_count=2, counters=[0, 0],
                           default_product_id=product["id"], counter_prices=[0.0, 120.0])
    await client.post(f"/api/partners/{partner['id']}/stock/replenish",
                      json={"product_id": product["id"], "quantity": 2.0}, headers=mgr)
    res = await client.post("/api/settlements", json={
        "partner_id": partner["id"], "payment_method": "cash",
        "lines": [{"product_id": product["id"], "physical_qty": 1.4}],
        "machines": [{"asset_id": asset["id"], "new_counters": [74, 74]}],
    }, headers=mgr)
    assert res.status_code == 201, res.text
    line = next(x for x in res.json()["lines"] if x["product_name"] == product["name"])
    assert line["portions"] == 74 and abs(line["amount_net"] - 8880) < 0.01
    assert abs(line["price_per_portion"] - 120.0) < 0.01


async def test_waived_shortage_shown_as_discount(client, manager):
    """4061c36e: 0 Ft-ra írt készlethiány → a sor neve mutatja az elengedett értéket."""
    _, mgr = manager
    partner = await make_partner(client, mgr, name="Jófej Bolt")
    product = await make_product(client, mgr, price_per_portion=100.0, grams_per_portion=7,
                                 purchase_price=8000.0)
    await client.post(f"/api/partners/{partner['id']}/stock/replenish",
                      json={"product_id": product["id"], "quantity": 2.0}, headers=mgr)
    # számláló szerint 100 adag = 0.7 kg → várt maradék 1.3 kg; mért 1.0 → 0.3 kg hiány
    res = await client.post("/api/settlements", json={
        "partner_id": partner["id"], "payment_method": "cash",
        "lines": [{"product_id": product["id"], "physical_qty": 1.0, "counter_portions": 100, "kg_price": 0}],
    }, headers=mgr)
    assert res.status_code == 201, res.text
    short = next(x for x in res.json()["lines"] if x["product_name"].startswith("Készlethiány"))
    assert short["amount_net"] == 0
    assert "kedvezményként elengedve" in short["product_name"] and "2 400 Ft" in short["product_name"]


async def test_handover_coffee_zero_line_without_contract(client, manager, monkeypatch):
    """3075a933: az átadott kávé 0 Ft-os sora normás szerződés NÉLKÜL is
    létrejön, és a Billingó-tételek közé is bekerül kg-ban."""
    _, mgr = manager
    partner = await make_partner(client, mgr, name="Király Bolt")
    coffee = await make_product(client, mgr, price_per_portion=91.0)
    await client.post(f"/api/partners/{partner['id']}/stock/replenish",
                      json={"product_id": coffee["id"], "quantity": 3.0}, headers=mgr)
    res = await client.post("/api/settlements", json={
        "partner_id": partner["id"], "payment_method": "cash",
        "lines": [{"product_id": coffee["id"], "physical_qty": 0.0}],
        "handovers": [{"product_id": coffee["id"], "quantity": 8.0}],
    }, headers=mgr)
    assert res.status_code == 201, res.text
    zero = next(x for x in res.json()["lines"] if "utólagos adagelszámolásra átadva" in x["product_name"])
    assert zero["amount_net"] == 0 and abs(zero["portions"] - 8.0) < 0.01
    # Billingó-tételek
    from sqlalchemy import select

    from app.db import get_session_factory
    from app.models import Settlement
    from app.services.wfm.billingo_service import settlement_invoice_items

    async with get_session_factory()() as db:
        import uuid as _uuid

        s = (await db.execute(select(Settlement).where(Settlement.id == _uuid.UUID(res.json()["id"])))).scalar_one()
        items = await settlement_invoice_items(db, s)
    kg = next(i for i in items if "utólagos adagelszámolásra" in i["name"])
    assert kg["unit"] == "kg" and kg["quantity"] == 8.0 and kg["unit_price"] == 0.0


async def test_invoice_stores_document_number_and_round(client, manager, admin, monkeypatch):
    """83ef525b: a számlázói bizonylatszám tárolódik, a bizonylaton látszik;
    09637c9b: a Billingó-bizonylat 5 Ft-ra kerekít készpénznél + megjegyzés."""
    _, mgr = manager
    _, adm = admin
    from app.services.wfm import billingo_service as bs

    calls: list = []

    async def fake_api(api_key, method, path, json_body=None):
        calls.append((method, path, json_body))
        if method == "GET" and path.startswith("/partners"):
            return {"data": []}
        if method == "POST" and path == "/partners":
            return {"id": 5}
        if method == "POST" and path == "/documents":
            return {"id": 777, "invoice_number": "XPSZLA/000011", "gross_total": 6350}
        if method == "GET" and path == "/documents/777":
            return {"id": 777, "invoice_number": "XPSZLA/000011"}
        return {}

    monkeypatch.setattr(bs, "_api", fake_api)
    res = await client.put("/api/settings/billingo", json={"enabled": True, "api_key": "k", "block_id": 1, "test_mode": False}, headers=adm)
    assert res.status_code == 200, res.text
    partner = await make_partner(client, mgr, name="Számlás Bolt")
    product = await make_product(client, mgr, price_per_portion=50.0)
    await client.post(f"/api/partners/{partner['id']}/stock/replenish",
                      json={"product_id": product["id"], "quantity": 1.0}, headers=mgr)
    res = await client.post("/api/settlements", json={
        "partner_id": partner["id"], "payment_method": "cash",
        "lines": [{"product_id": product["id"], "physical_qty": 0.3}],
    }, headers=mgr)
    assert res.status_code == 201, res.text
    out = res.json()
    if not out["invoiced"]:
        out = (await client.post(f"/api/settlements/{out['id']}/invoice", headers=mgr)).json()
    assert out["invoiced"] and out["billingo_document_number"] == "XPSZLA/000011"
    doc = next(c for c in calls if c[0] == "POST" and c[1] == "/documents")[2]
    assert doc["round"] == "five" and "5 forintra kerekítve" in doc["comment"]
    pdf = await client.get(f"/api/settlements/{out['id']}/pdf?format=50", headers=mgr)
    assert pdf.status_code == 200
    listed = (await client.get(f"/api/settlements?partner_id={partner['id']}", headers=mgr)).json()
    assert listed[0]["billingo_document_number"] == "XPSZLA/000011"


async def test_cash_movements_listed(client, manager):
    """dbd12c93: a saját kassza az időszak mozgásait tételesen adja."""
    from tests.test_round95 import _add_expense

    mgr_user, mgr = manager
    await _add_expense(mgr_user.id, date.today(), 700.0)
    partner = await make_partner(client, mgr, name="Mozgás Bt.")
    product = await make_product(client, mgr, price_per_portion=50.0)
    await client.post(f"/api/partners/{partner['id']}/stock/replenish",
                      json={"product_id": product["id"], "quantity": 1.0}, headers=mgr)
    await client.post("/api/settlements", json={
        "partner_id": partner["id"], "payment_method": "cash",
        "lines": [{"product_id": product["id"], "physical_qty": 0.3}],
    }, headers=mgr)
    reg = (await client.get("/api/stats/cash/me", headers=mgr)).json()
    kinds = {m["kind"] for m in reg["movements"]}
    assert "revenue" in kinds and "deposit" in kinds
    rev = next(m for m in reg["movements"] if m["kind"] == "revenue")
    assert rev["label"] == "Mozgás Bt." and rev["amount"] == 6350
    # időszak-szűrő: holnaptól semmi
    from datetime import timedelta

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    reg = (await client.get(f"/api/stats/cash/me?date_from={tomorrow}", headers=mgr)).json()
    assert reg["movements"] == []


async def test_portal_link_send_email(client, manager, monkeypatch):
    """26e6a4fc: portál-link e-mailben a partner címeire (SMTP nélkül 422)."""
    _, mgr = manager
    from app.core.config import get_settings
    from app.services.wfm import email_service

    partner = (await client.post("/api/partners", json={"name": "Linkes Bt."}, headers=mgr)).json()
    res = await client.post(f"/api/partners/{partner['id']}/portal-link/send", headers=mgr)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "portal.no_email"
    await client.patch(f"/api/partners/{partner['id']}", json={"name": "Linkes Bt.", "contact_email": "a@example.com, b@example.com"}, headers=mgr)
    res = await client.post(f"/api/partners/{partner['id']}/portal-link/send", headers=mgr)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "email.not_configured"
    sent: list = []

    async def fake_send(config, to, subject, body, attachments=None):
        sent.append((to, subject, body))
        return True

    monkeypatch.setattr(email_service, "send_email", fake_send)
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.example.com", raising=False)
    monkeypatch.setattr(get_settings(), "smtp_from", "noreply@example.com", raising=False)
    res = await client.post(f"/api/partners/{partner['id']}/portal-link/send", headers=mgr)
    assert res.status_code == 200, res.text
    assert res.json()["sent_to"] == ["a@example.com", "b@example.com"]
    assert "/portal/" in sent[-1][2] and "Linkes Bt." in sent[-1][1]
