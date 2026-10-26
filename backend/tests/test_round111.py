"""111. kör (2026-10-26, újranyitott jegyek): átadás-számla egy tétellel
(„Kávéfőző gép javítás (Munkalap: …)”), Billingó-küldés és számla-link
(3fe1fdcb); térkép: cím nélküli partnerek listája címmel (7836fee3)."""
from __future__ import annotations

from tests.test_handover import _prepare


async def test_handover_single_invoice_item_sent_and_link(client, admin, manager, monkeypatch):
    _, mgr = manager
    from app.services.wfm import billingo_service, invoicing

    calls: list[dict] = []
    sent: list[tuple[str, list[str]]] = []

    async def fake_invoice(db, partner, *, serial, items, payment_method, vat_percent=27, due_days=None):
        calls.append({"serial": serial, "items": items, "pm": payment_method, "due_days": due_days})
        return "DOC-111", "invoice"

    async def fake_send(db, document_id, emails, company=None):
        sent.append((document_id, emails))

    async def fake_url(db, document_id, company=None):
        return f"https://app.billingo.hu/document/{document_id}"

    monkeypatch.setattr(billingo_service, "create_handover_invoice", fake_invoice)
    monkeypatch.setattr(billingo_service, "send_document_email", fake_send)
    monkeypatch.setattr(billingo_service, "get_document_public_url", fake_url)

    task_id, _ = await _prepare(client, mgr, monkeypatch, "R111-A")
    # a partnernek van e-mailje → a Billingó küldi ki
    partners = (await client.get("/api/partners", headers=mgr)).json()
    p = next(x for x in partners if x["name"] == "Átadós Kft.")
    res = await client.patch(f"/api/partners/{p['id']}", json={"name": p["name"], "contact_email": "vevo@example.com, konyveles@example.com"}, headers=mgr)
    assert res.status_code == 200, res.text

    res = await client.post(f"/api/tasks/{task_id}/handover", json={"payment_method": "transfer", "due_days": 8}, headers=mgr)
    assert res.status_code == 200, res.text
    # EGY tétel, a munkalap számával, a teljes nettóval (20000 = 14000 + 6000)
    items = calls[-1]["items"]
    assert len(items) == 1 and items[0]["amount_net"] == 20000 and "Munkalap:" in items[0]["name"] and "Kávéfőző gép javítás" in items[0]["name"]
    assert sent and sent[-1] == ("DOC-111", ["vevo@example.com", "konyveles@example.com"])
    assert res.json()["invoice_sent_to"] == ["vevo@example.com", "konyveles@example.com"]
    # számla-link a munkalaphoz
    res = await client.get(f"/api/tasks/{task_id}/handover/invoice-link", headers=mgr)
    assert res.status_code == 200 and res.json()["url"].endswith("/DOC-111")
    ws = (await client.get(f"/api/tasks/{task_id}/worksheet", headers=mgr)).json()
    assert ws["handover_document_id"] == "DOC-111"
    # számla nélküli munkalap → 404
    task2, _ = await _prepare(client, mgr, monkeypatch, "R111-B")
    assert (await client.get(f"/api/tasks/{task2}/handover/invoice-link", headers=mgr)).status_code == 404


async def test_billingo_partner_completion(monkeypatch):
    from app.models import Partner
    from app.services.wfm import billingo_service as bs

    calls: list[tuple[str, str, dict | None]] = []

    async def fake_api(api_key, method, path, json_body=None):
        calls.append((method, path, json_body))
        if method == "GET":
            return {"data": [{"id": 5, "name": "X-Presso Coffee Kft.", "taxcode": "", "emails": [], "address": {"country_code": "HU", "post_code": "", "city": "", "address": ""}}]}
        return {"id": 5}

    monkeypatch.setattr(bs, "_api", fake_api)
    p = Partner(name="X-Presso Coffee Kft.", tax_number="12345678-2-42", contact_email="iroda@x-presso.hu", address="1024, Budapest, Lövőház u. 12.")
    assert await bs._find_or_create_billingo_partner("k", p) == 5
    put = [c for c in calls if c[0] == "PUT"]
    assert put and put[0][1] == "/partners/5"
    body = put[0][2]
    assert body["taxcode"] == "12345678-2-42" and body["emails"] == ["iroda@x-presso.hu"]
    assert body["address"]["city"] == "Budapest" and body["address"]["post_code"] == "1024"


async def test_map_rows_carry_address(client, manager):
    _, mgr = manager
    await client.post("/api/partners", json={"name": "Cím nélküli", "address": "2022, Tahitótfalu, Nemtom 12."}, headers=mgr)
    res = await client.get("/api/stats/map", headers=mgr)
    assert res.status_code in (200, 403)  # a stats modul nélkül 403 — akkor a mezőt a modell-szinten ellenőrizzük
    if res.status_code == 200:
        row = next(r for r in res.json() if r["name"] == "Cím nélküli")
        assert row["address"] == "2022, Tahitótfalu, Nemtom 12."
    from app.api.stats import MapPartnerOut

    assert "address" in MapPartnerOut.model_fields
