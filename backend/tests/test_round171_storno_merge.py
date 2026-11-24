"""171. kör: azonos adószámú, saját szerződéses címek egy számlán; számla sztornó indokkal."""

from __future__ import annotations

from datetime import date

from tests.test_round102 import _mk_rental_partner, no_invoice  # noqa: F401

# ruff: noqa: F811 — a no_invoice fixture paraméterként is szerepel


async def _contract(client, mgr, partner_id, fee, day, merge=True, **extra):
    body = {
        "valid_from": "2026-01-01", "no_minimum": True, "fixed_rental": True, "billing_day": day,
        "rent_fee": fee, "rental_merge_company": merge, "payment_method": "transfer", "payment_terms_days": 8,
    }
    body.update(extra)
    res = await client.post(f"/api/partners/{partner_id}/contracts", json=body, headers=mgr)
    assert res.status_code == 201, res.text
    return res.json()


async def test_same_tax_own_contracts_billed_on_one_invoice(client, manager, no_invoice):
    import app.db as app_db
    from app.services.wfm.rental_billing import run_fixed_rental_billing

    _, mgr = manager
    day = min(date.today().day, 28)
    # három cím azonos adószámmal, mindháromnak saját fix szerződése, mindegyik összevonásra állítva
    a = await _mk_rental_partner(client, mgr, "3 tarka macska Margit krt", tax="22222222-2-22", machines=1, fee=0)
    b = await _mk_rental_partner(client, mgr, "3 tarka macska Pozsonyi út", tax="22222222-2-22", machines=0)
    c = await _mk_rental_partner(client, mgr, "Három Tarka Macska Oktogon", tax="22222222-2-22", machines=0)
    # egy negyedik cím ugyanazzal az adószámmal, de MÁS számlázási nappal → külön számla
    d = await _mk_rental_partner(client, mgr, "3 tarka macska Külön", tax="22222222-2-22", machines=0)
    # egy ötödik, saját szerződés NÉLKÜLI társcím gépdíjjal → gépenkénti tétel a fő számlán (régi viselkedés)
    e = await _mk_rental_partner(client, mgr, "3 tarka macska Telephely", tax="22222222-2-22", machines=1, fee=5000)
    await _contract(client, mgr, a["id"], 25000, day, valid_from="2020-01-01")
    await _contract(client, mgr, b["id"], 25000, day)
    await _contract(client, mgr, c["id"], 70000, day)
    other_day = day - 1 if day > 1 else day + 1
    await _contract(client, mgr, d["id"], 10000, other_day)

    d_due = other_day <= date.today().day  # a más napos szerződés ma esedékes-e (külön számla)
    factory = app_db.get_session_factory()
    async with factory() as db:
        created = await run_fixed_rental_billing(db)
    assert created == (2 if d_due else 1)  # a három + a telephely EGY számlán; a más napos külön
    assert len(no_invoice) == created
    rd = (await client.get(f"/api/settlements?partner_id={d['id']}", headers=mgr)).json()
    assert len(rd if isinstance(rd, list) else rd["items"]) == (1 if d_due else 0)
    rows = (await client.get(f"/api/settlements?partner_id={a['id']}", headers=mgr)).json()
    rows = rows if isinstance(rows, list) else rows["items"]
    assert len(rows) == 1
    s = rows[0]
    assert s["total_net"] == 25000 + 25000 + 70000 + 5000
    det = (await client.get(f"/api/settlements/{s['id']}", headers=mgr)).json()
    names = [ln["product_name"] for ln in det["lines"]]
    assert len(names) == 4
    assert any("Pozsonyi" in n for n in names) and any("Oktogon" in n for n in names) and any("Telephely" in n for n in names)
    assert "összevont számla" in (det["note"] or "")
    # a társ-címeknek NEM készült külön elszámolás
    for p in (b, c, e):
        r = (await client.get(f"/api/settlements?partner_id={p['id']}", headers=mgr)).json()
        assert (r if isinstance(r, list) else r["items"]) == []
    # a havi őr mindhárom szerződésen beállt: másodszor nem fut le
    async with factory() as db:
        assert await run_fixed_rental_billing(db) == 0


async def test_merge_off_keeps_separate_invoices(client, manager, no_invoice):
    import app.db as app_db
    from app.services.wfm.rental_billing import run_fixed_rental_billing

    _, mgr = manager
    day = min(date.today().day, 28)
    a = await _mk_rental_partner(client, mgr, "Külön A", tax="33333333-3-33", machines=0)
    b = await _mk_rental_partner(client, mgr, "Külön B", tax="33333333-3-33", machines=0)
    await _contract(client, mgr, a["id"], 1000, day, merge=True)
    await _contract(client, mgr, b["id"], 2000, day, merge=False)  # B nem kérte az összevonást
    factory = app_db.get_session_factory()
    async with factory() as db:
        assert await run_fixed_rental_billing(db) == 2


async def test_invoice_storno_with_reason(client, admin, monkeypatch):
    import app.db as app_db
    from app.models import Partner, Settlement
    from app.services.wfm import invoicing

    _, adm = admin
    calls: list = []

    async def fake_cancel(db, document_id, reason, company=None, recipients=None):
        calls.append((document_id, reason, company, recipients))
        return "STORNO-1", "XPSZLA/ST/000001"

    monkeypatch.setattr(invoicing, "cancel_invoice", fake_cancel)
    factory = app_db.get_session_factory()
    async with factory() as db:
        p = Partner(name="Sztornó Kft.")
        db.add(p)
        await db.flush()
        s = Settlement(
            partner_id=p.id, settled_by_name="Teszt", payment_method="transfer", total_net=1000, total_gross=1270,
            invoiced=True, billingo_document_id="DOC-1", billingo_status="invoice", billingo_document_number="XPSZLA/000001",
            payment_status="outstanding", invoicing_company="xp", invoice_emails="szamla@sztorno.hu",
        )
        db.add(s)
        await db.commit()
        sid = str(s.id)

    # csak admin sztornózhat — a vezető sem
    from tests.conftest import make_user
    _, mgr_h = await make_user(email="vezeto@example.com", role="manager")
    res = await client.post(f"/api/settlements/{sid}/invoice-storno", json={"reason": "Vezetőként próbálom."}, headers=mgr_h)
    assert res.status_code == 403
    # indok kötelező
    res = await client.post(f"/api/settlements/{sid}/invoice-storno", json={"reason": "x"}, headers=adm)
    assert res.status_code == 422
    res = await client.post(f"/api/settlements/{sid}/invoice-storno", json={"reason": "Téves összeg került a számlára."}, headers=adm)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["invoiced"] is False and out["billingo_status"] == "storno" and out["payment_status"] == "none"
    assert out["storno_document_number"] == "XPSZLA/ST/000001" and out["storno_original_number"] == "XPSZLA/000001"
    assert out["storno_reason"].startswith("Téves") and out["storno_by_name"] and out["storno_at"]
    assert calls == [("DOC-1", "Téves összeg került a számlára.", "xp", ["szamla@sztorno.hu"])]
    # másodszor nincs mit sztornózni
    res = await client.post(f"/api/settlements/{sid}/invoice-storno", json={"reason": "Még egyszer."}, headers=adm)
    assert res.status_code == 404 and res.json()["detail"]["code"] == "settlement.not_invoiced"
    # napló
    res = await client.get("/api/audit?action=settlement.invoice_storno", headers=adm)
    items = res.json() if isinstance(res.json(), list) else res.json().get("items", [])
    assert any(a["action"] == "settlement.invoice_storno" and a["detail"]["reason"].startswith("Téves") for a in items)


async def test_storno_provider_error_is_reported(client, admin, monkeypatch):
    import app.db as app_db
    from app.models import Partner, Settlement
    from app.services.wfm import invoicing

    _, adm = admin

    async def boom(db, document_id, reason, company=None, recipients=None):
        raise RuntimeError("Billingó 422 /documents/x/cancel: already cancelled")

    monkeypatch.setattr(invoicing, "cancel_invoice", boom)
    factory = app_db.get_session_factory()
    async with factory() as db:
        p = Partner(name="Hibás Kft.")
        db.add(p)
        await db.flush()
        s = Settlement(partner_id=p.id, settled_by_name="T", payment_method="cash", total_net=1, total_gross=1.27,
                       invoiced=True, billingo_document_id="DOC-2", billingo_status="invoice", payment_status="paid")
        db.add(s)
        await db.commit()
        sid = str(s.id)
    res = await client.post(f"/api/settlements/{sid}/invoice-storno", json={"reason": "Próba indok"}, headers=adm)
    assert res.status_code == 502 and res.json()["detail"]["code"] == "settlement.storno_failed"
    # a számla érintetlen maradt
    det = (await client.get(f"/api/settlements/{sid}", headers=adm)).json()
    assert det["invoiced"] is True and det["storno_at"] is None


async def test_billingo_partner_lookup_prefers_matching_taxcode(monkeypatch):
    """Zália-eset: két azonos nevű Billingó-karton, az egyiken nincs adószám — az
    adószámmal egyezőt kell használni, nem a találati sorrend szerinti elsőt."""
    from app.models import Partner
    from app.services.wfm import billingo_service as bs

    calls: list = []

    async def fake_api(api_key, method, path, json_body=None):
        calls.append((method, path, json_body))
        if method == "GET":
            return {"data": [
                {"id": 11, "name": "Zália Dohány Betéti Társaság", "taxcode": "", "address": {"city": "Budapest", "address": "Kapos u. 33"}},
                {"id": 12, "name": "Zália Dohány Betéti Társaság", "taxcode": "24336541-2-43", "tax_type": "HAS_TAX_NUMBER", "address": {"city": "Budapest", "address": "Kapos u. 33"}},
            ]}
        return {"id": 99}

    monkeypatch.setattr(bs, "_api", fake_api)
    p = Partner(name="Oktogon Dohánybolt", company_name="Zália Dohány Betéti Társaság", tax_number="24336541-2-43",
                address_zip="1214", address_city="Budapest", address_street="Kapos u.", address_number="33")
    assert await bs._find_or_create_billingo_partner("k", p) == 12
    assert not any(m == "PUT" for m, _p, _b in calls)  # a jó kartonon nincs mit pótolni

    # csak adószám nélküli karton van → PUT-tal pótoljuk; ha a pótlás elbukik, a számla nem készülhet
    calls.clear()

    async def api_one_card(api_key, method, path, json_body=None):
        calls.append((method, path, json_body))
        if method == "GET":
            return {"data": [{"id": 11, "name": "Zália Dohány Betéti Társaság", "taxcode": "", "address": {"city": "Budapest", "address": "Kapos u. 33"}}]}
        if method == "PUT":
            raise bs.BillingoApiError(422, path, '{"message":"Validation failed","errors":{"taxcode":["invalid"]}}')
        return {"id": 99}

    monkeypatch.setattr(bs, "_api", api_one_card)
    import pytest

    with pytest.raises(bs.BillingoApiError):
        await bs._find_or_create_billingo_partner("k", p)
    assert any(m == "PUT" and b.get("taxcode") == "24336541-2-43" and b.get("tax_type") == "HAS_TAX_NUMBER" for m, _p, b in calls)
