"""172. kör: az automata fix bérleti számlázás első futása a létrehozás utáni első
számlázási nap; minden számlát kiküld a Billingó (automata is), újrapróbálással."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from tests.test_round102 import _mk_rental_partner, no_invoice  # noqa: F401

# ruff: noqa: F811


def test_first_billing_date():
    from app.services.wfm.rental_billing import first_billing_date as f

    assert f(5, date(2026, 11, 16)) == date(2026, 12, 5)   # elmúlt → következő hónap
    assert f(5, date(2026, 11, 5)) == date(2026, 11, 5)    # aznap → aznap
    assert f(20, date(2026, 11, 16)) == date(2026, 11, 20)  # még jön ebben a hónapban
    assert f(5, date(2026, 12, 16)) == date(2027, 1, 5)    # évváltás
    assert f(31, date(2026, 2, 1)) == date(2026, 2, 28)    # hónap vége


async def test_contract_created_after_billing_day_bills_next_month(client, manager, no_invoice):
    import app.db as app_db
    from app.services.wfm.rental_billing import run_fixed_rental_billing

    _, mgr = manager
    today = date.today()
    p = await _mk_rental_partner(client, mgr, "Késői Szerződő", tax="44444444-4-44", machines=1, fee=10000)
    # a számlázás napja tegnap (vagy 1-je → holnap): a MA létrehozott szerződés ebben a hónapban nem számláz
    day = today.day - 1 if today.day > 1 else 2
    res = await client.post(f"/api/partners/{p['id']}/contracts", json={
        "valid_from": "2026-01-01", "no_minimum": True, "fixed_rental": True, "billing_day": min(day, 28),
        "rent_fee": 10000, "payment_method": "transfer", "payment_terms_days": 8,
    }, headers=mgr)
    assert res.status_code == 201, res.text
    factory = app_db.get_session_factory()
    async with factory() as db:
        assert await run_fixed_rental_billing(db) == 0
    assert no_invoice == []
    # a következő hónap számlázási napján már esedékes
    from app.services.wfm.rental_billing import _is_due, _local_now, contract_start, first_billing_date
    from sqlalchemy import select

    from app.models import PartnerContract

    async with factory() as db:
        c = (await db.execute(select(PartnerContract))).scalars().first()
        assert c.created_at is not None and contract_start(c) == today
        nxt = first_billing_date(int(c.billing_day), today)
        assert nxt > today
        assert _is_due(c, nxt, _local_now(None).replace(year=nxt.year, month=nxt.month, day=nxt.day))


async def test_auto_invoice_is_sent_and_retried(client, manager, no_invoice, monkeypatch):
    import app.db as app_db
    from app.services.wfm import invoicing, rental_billing
    from app.services.wfm.rental_billing import resend_unsent_invoices, run_fixed_rental_billing

    _, mgr = manager
    today = date.today()
    sent: list = []
    state = {"fail": True}

    async def fake_send(db, document_id, emails, company=None):
        if state["fail"]:
            raise RuntimeError("Billingó 500")
        sent.append((document_id, list(emails), company))

    monkeypatch.setattr(invoicing, "send_invoice_email", fake_send)
    p = await _mk_rental_partner(client, mgr, "Levelező Bérlő", tax="55555555-5-55", machines=1, fee=12000)
    res = await client.patch(f"/api/partners/{p['id']}", json={"name": p["name"], "tax_number": "55555555-5-55", "contact_email": "szamla@berlo.hu, konyveles@berlo.hu"}, headers=mgr)
    assert res.status_code == 200, res.text
    assert (await client.post(f"/api/partners/{p['id']}/contracts", json={
        "valid_from": "2026-01-01", "no_minimum": True, "fixed_rental": True, "billing_day": min(today.day, 28),
        "rent_fee": 12000, "payment_method": "transfer", "payment_terms_days": 8,
    }, headers=mgr)).status_code == 201
    factory = app_db.get_session_factory()
    async with factory() as db:
        assert await run_fixed_rental_billing(db) == 1
    # a számla elkészült, de a küldés elbukott → receipt_sent_at üres
    s = (await client.get(f"/api/settlements?partner_id={p['id']}", headers=mgr)).json()[0]
    assert s["invoiced"] is True and s["receipt_sent_at"] is None and sent == []
    # újrapróbálás: óránként egyszer — az első próba azonnal mehet
    rental_billing._resend_last_try.clear()
    state["fail"] = False
    async with factory() as db:
        assert await resend_unsent_invoices(db) == 1
    assert sent == [("INV-TEST-1", ["szamla@berlo.hu", "konyveles@berlo.hu"], "xp")] or sent[0][1] == ["szamla@berlo.hu", "konyveles@berlo.hu"]
    s = (await client.get(f"/api/settlements?partner_id={p['id']}", headers=mgr)).json()[0]
    assert s["receipt_sent_at"] is not None
    # másodszor már nincs mit küldeni
    async with factory() as db:
        assert await resend_unsent_invoices(db) == 0
    # a 48 órán túli számlát nem bolygatja
    async with factory() as db:
        assert await resend_unsent_invoices(db, now=datetime.now(UTC) + timedelta(hours=72)) == 0


async def test_auto_invoice_sent_immediately_when_billingo_ok(client, manager, no_invoice, monkeypatch):
    import app.db as app_db
    from app.services.wfm import invoicing
    from app.services.wfm.rental_billing import run_fixed_rental_billing

    _, mgr = manager
    today = date.today()
    sent: list = []

    async def fake_send(db, document_id, emails, company=None):
        sent.append(list(emails))

    monkeypatch.setattr(invoicing, "send_invoice_email", fake_send)
    a = await _mk_rental_partner(client, mgr, "Fő cím", tax="66666666-6-66", machines=0)
    b = await _mk_rental_partner(client, mgr, "Társ cím", tax="66666666-6-66", machines=0)
    for pr, mail in ((a, "fo@ceg.hu"), (b, "tars@ceg.hu")):
        res = await client.patch(f"/api/partners/{pr['id']}", json={"name": pr["name"], "tax_number": "66666666-6-66", "contact_email": mail}, headers=mgr)
        assert res.status_code == 200, res.text
    for pid, fee in ((a["id"], 1000), (b["id"], 2000)):
        assert (await client.post(f"/api/partners/{pid}/contracts", json={
            "valid_from": "2026-01-01", "no_minimum": True, "fixed_rental": True, "billing_day": min(today.day, 28),
            "rent_fee": fee, "rental_merge_company": True, "payment_method": "transfer", "payment_terms_days": 8,
        }, headers=mgr)).status_code == 201
    factory = app_db.get_session_factory()
    async with factory() as db:
        assert await run_fixed_rental_billing(db) == 1
    # összevont számla: mindkét cím e-mailje címzett
    assert sent == [["fo@ceg.hu", "tars@ceg.hu"]]
