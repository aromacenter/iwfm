"""CashBook könyvelési feladás: beállítások, OSA-XML, push-flow, modul-kapu."""

from __future__ import annotations

from tests.test_license import OP, _arm_operator


async def _setup_cashbook(client, adm):
    res = await client.put(
        "/api/settings/cashbook",
        json={"enabled": True, "api_key": "cb-titok-1",
              "test_mode": True,
              "supplier_name": "X-Presso Coffee Kft.",
              "supplier_tax_number": "12345678-2-42",
              "supplier_zip": "1108", "supplier_city": "Budapest",
              "supplier_street": "Bányató utca 13",
              "ledger_cash": "3811", "ledger_bank": "3841"},
        headers=adm,
    )
    assert res.status_code == 200, res.text
    return res.json()


async def test_cashbook_settings_roundtrip(client, admin):
    _, adm = admin
    out = await _setup_cashbook(client, adm)
    assert out["has_api_key"] is True
    assert out["supplier_tax_number"] == "12345678-2-42"
    assert out["ledger_cash"] == "3811"
    got = (await client.get("/api/settings/cashbook", headers=adm)).json()
    assert got["enabled"] is True and got["has_api_key"] is True
    assert "cb-titok" not in str(got)  # a kulcs SOHA nem jön vissza

    # törlés kötőjellel
    res = await client.put(
        "/api/settings/cashbook",
        json={"enabled": False, "api_key": "-"},
        headers=adm,
    )
    assert res.json()["has_api_key"] is False


async def test_cashbook_module_gate(client, admin, monkeypatch):
    """A cashbook modul kikapcsolva → a beállítás-végpont 403-at ad."""
    _, adm = admin
    _arm_operator(monkeypatch)
    res = await client.put(
        "/api/operator/license",
        json={"plan": "m", "enabled_modules": ["billing"]},
        headers=OP,
    )
    assert res.status_code == 200, res.text
    denied = await client.get("/api/settings/cashbook", headers=adm)
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "license.module_disabled"


async def test_cashbook_push_flow(client, admin, manager, monkeypatch):
    """Számlázás után automatikus feladás; fizetéskor kiegyenlítéssel megy
    újra (placeholder PDF-fel), az XML minden kötelező blokkot tartalmaz."""
    from tests.test_consignment import make_product

    from app.services.wfm import cashbook_service

    _, adm = admin
    _, mgr = manager
    await _setup_cashbook(client, adm)
    # a számlázó (Billingó) nincs beállítva → a számlát kézzel jelöljük;
    # a CashBook-push a kézi végponton megy.

    partner = (
        await client.post(
            "/api/partners",
            json={"name": "Könyvelt Bisztró", "tax_number": "87654321-2-13"},
            headers=mgr,
        )
    ).json()
    product = await make_product(client, mgr, price_per_portion=100.0, grams_per_portion=7)
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 1.0},
        headers=mgr,
    )
    settlement = (
        await client.post(
            "/api/settlements",
            json={"partner_id": partner["id"], "payment_method": "transfer",
                  "lines": [{"product_id": product["id"], "physical_qty": 0.3}]},
            headers=mgr,
        )
    ).json()

    sent: list[dict] = []

    async def fake_post_pool(settings, xml, pdf_field):
        sent.append({"xml": xml, "pdf": pdf_field})
        return "hash-" + str(len(sent))

    monkeypatch.setattr(cashbook_service, "_post_pool", fake_post_pool)

    res = await client.post(f"/api/settlements/{settlement['id']}/cashbook", headers=mgr)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["cashbook_status"] == "sent"
    assert out["cashbook_sent_at"] is not None

    xml = sent[0]["xml"]
    assert "<base:taxpayerId>12345678</base:taxpayerId>" in xml  # szállító
    assert "<customerVatStatus>DOMESTIC</customerVatStatus>" in xml
    assert "<base:taxpayerId>87654321</base:taxpayerId>" in xml  # vevő
    assert "<invoiceGrossAmount>" in xml
    assert "<invoicePayments>" not in xml  # még nincs fizetve
    assert len(sent[0]["pdf"]) > 100  # valódi PDF base64-ben

    # fizetve jelölés nem megy számla nélkül → előbb "számlázott" státusz kell;
    # a kézi push fizetett állapotot a mark-paid után küldene — szimuláljuk:
    # jelöljük fizetettnek közvetlenül a kézi push-sal (mark-paid invoiced-et
    # vár, ezért itt a push-t hívjuk újra, miután kézzel fizetettre állítjuk)
    import app.db as app_db
    from datetime import UTC, datetime
    from app.models import Settlement as SettlementModel

    factory = app_db.get_session_factory()
    async with factory() as session:
        row = await session.get(SettlementModel, __import__("uuid").UUID(settlement["id"]))
        row.payment_status = "paid"
        row.paid_at = datetime.now(UTC)
        await session.commit()

    res2 = await client.post(f"/api/settlements/{settlement['id']}/cashbook", headers=mgr)
    assert res2.status_code == 200, res2.text
    assert res2.json()["cashbook_status"] == "paid_sent"
    xml2 = sent[1]["xml"]
    assert "<invoicePayments>" in xml2
    assert "<locationCode>B</locationCode>" in xml2  # átutalás → bank
    assert "<locationLedger>3841</locationLedger>" in xml2
    assert sent[1]["pdf"] == "PDF"  # újraküldésnél placeholder


async def test_cashbook_not_configured(client, admin, manager):
    from tests.test_consignment import make_product

    _, adm = admin
    _, mgr = manager
    # modul be, de kulcs nincs
    partner = (
        await client.post("/api/partners", json={"name": "Kulcstalan Kft."}, headers=mgr)
    ).json()
    product = await make_product(client, mgr, price_per_portion=50.0, grams_per_portion=7)
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 1.0},
        headers=mgr,
    )
    settlement = (
        await client.post(
            "/api/settlements",
            json={"partner_id": partner["id"], "payment_method": "cash",
                  "lines": [{"product_id": product["id"], "physical_qty": 0.5}]},
            headers=mgr,
        )
    ).json()
    res = await client.post(f"/api/settlements/{settlement['id']}/cashbook", headers=mgr)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "settings.cashbook_not_configured"
