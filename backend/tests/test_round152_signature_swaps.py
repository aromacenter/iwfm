"""152. kör:

(1) x-presso 7ca9c6f6 (újranyitva): a hivatalos árajánlat-PDF-re az ajánlatot
    készítő (kiküldő) felhasználó elmentett aláírása kerül a nevével; ha neki
    nincs, a beállításokban feltöltött (ügyvezetői); kiküldéskor jelzés megy,
    ha egyik sincs.
(2) x-presso 56276b1d: gépcserék kereshető listája — átadás módja (személyesen
    / futárszolgálattal), szűrők, utólagos jelölés, a jegyzőkönyv-PDF is mutatja."""

from __future__ import annotations

import io

from pypdf import PdfReader

from tests.test_round145_quote_pdf import PNG_1PX, _setup


def _text(pdf_bytes: bytes) -> str:
    return " ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf_bytes)).pages)


def _fake_mail(monkeypatch):
    import app.api.tasks as tasks_mod

    sent: list = []

    async def fake_send(smtp, to, subject, body):
        sent.append((to, subject, body))
        return True

    async def fake_smtp(db):
        return object()

    monkeypatch.setattr(tasks_mod, "send_email", fake_send)
    monkeypatch.setattr(tasks_mod, "load_smtp_config", fake_smtp)
    return sent


def _capture_pdf_settings(monkeypatch):
    """A PDF-építőnek átadott beállítások elfogása (melyik aláírás megy rá)."""
    import app.services.wfm.quote_pdf as qp

    seen: list[dict] = []
    real = qp.build_quote_pdf

    def spy(data, settings):
        seen.append(dict(settings or {}))
        return real(data, settings)

    monkeypatch.setattr(qp, "build_quote_pdf", spy)
    return seen


async def test_quote_pdf_uses_senders_signature(client, admin, manager, monkeypatch):
    _, adm = admin
    mgr_user, mgr = manager
    _emp_h, task_id = await _setup(client, mgr)
    sent = _fake_mail(monkeypatch)
    seen = _capture_pdf_settings(monkeypatch)

    # se saját, se céges aláírás: a kiküldés megy, de jelez
    res = await client.post(f"/api/tasks/{task_id}/worksheet/send-quote", json={"to": "ugyfel@example.com"}, headers=mgr)
    assert res.status_code == 200, res.text
    assert res.json()["quote_signature_missing"] is True
    token = sent[0][2].split("/munkalap-ajanlat/")[1].split()[0]
    pdf = await client.get(f"/api/public/worksheet-quote/{token}/pdf?option=1")
    assert pdf.status_code == 200 and seen[-1].get("signature_bytes") is None

    # a kiküldő elmenti a saját aláírását → az kerül a lapra, a nevével
    res = await client.put("/api/auth/me/signature", json={"signature": PNG_1PX}, headers=mgr)
    assert res.status_code == 200 and res.json()["has_signature"] is True
    pdf = await client.get(f"/api/public/worksheet-quote/{token}/pdf?option=1")
    assert pdf.status_code == 200
    assert seen[-1]["signature_bytes"] and seen[-1]["signer_name"] == mgr_user.display_name
    text = _text(pdf.content)
    assert mgr_user.display_name in text and "az ajánlatot készítette" in text
    # újraküldésnél már nincs hiány-jelzés
    res = await client.post(f"/api/tasks/{task_id}/worksheet/send-quote", json={"to": "ugyfel@example.com"}, headers=mgr)
    assert res.json()["quote_signature_missing"] is False
    # a belső (szerkesztői) PDF ugyanezt adja
    assert (await client.get(f"/api/tasks/{task_id}/worksheet/quote-pdf?option=1", headers=mgr)).status_code == 200
    assert seen[-1]["signer_name"] == mgr_user.display_name

    # ha a kiküldő törli a saját aláírását, a beállított (ügyvezetői) aláírás él
    cur = (await client.get("/api/settings/worksheet", headers=adm)).json()
    res = await client.put("/api/settings/worksheet", json={
        **{k: cur[k] for k in ("company_name", "company_address", "footer_text", "customer_footer_text", "intake_footer_text",
                               "survey_fee", "accent_color", "show_materials", "show_hours", "show_client_signature",
                               "show_comments", "photo_policy", "photo_min")},
        "signer_name": "Szabó László", "signer_title": "ügyvezető", "signature": PNG_1PX,
    }, headers=adm)
    assert res.status_code == 200, res.text
    await client.put("/api/auth/me/signature", json={"signature": None}, headers=mgr)
    pdf = await client.get(f"/api/public/worksheet-quote/{token}/pdf?option=1")
    assert seen[-1]["signature_bytes"] and seen[-1]["signer_name"] == "Szabó László"
    assert "Szabó László" in _text(pdf.content) and "ügyvezető" in _text(pdf.content)
    res = await client.post(f"/api/tasks/{task_id}/worksheet/send-quote", json={"to": "ugyfel@example.com"}, headers=mgr)
    assert res.json()["quote_signature_missing"] is False  # a céges aláírás megvan


async def test_quote_sent_before_round152_finds_sender_from_audit(client, manager, monkeypatch):
    """Korábban kiküldött ajánlat (a munkalapon még nincs kiküldő): a naplóból
    derül ki, ki küldte — az ő aláírása kerül a PDF-re."""
    import app.db as app_db
    from sqlalchemy import update

    from app.models import Worksheet

    mgr_user, mgr = manager
    _emp_h, task_id = await _setup(client, mgr)
    sent = _fake_mail(monkeypatch)
    seen = _capture_pdf_settings(monkeypatch)
    res = await client.post(f"/api/tasks/{task_id}/worksheet/send-quote", json={"to": "regi@example.com"}, headers=mgr)
    assert res.status_code == 200, res.text
    token = sent[0][2].split("/munkalap-ajanlat/")[1].split()[0]
    # a régi állapot utánzása: a kiküldő nincs a munkalapon, csak a naplóban
    async with app_db.get_session_factory()() as s:
        await s.execute(update(Worksheet).values(quote_sent_by=None))
        await s.commit()
    await client.put("/api/auth/me/signature", json={"signature": PNG_1PX}, headers=mgr)
    pdf = await client.get(f"/api/public/worksheet-quote/{token}/pdf?option=1")
    assert pdf.status_code == 200
    assert seen[-1]["signature_bytes"] and seen[-1]["signer_name"] == mgr_user.display_name


async def _swap(client, mgr, n: int, partner_name: str, **protocol):
    partner = (await client.post("/api/partners", json={"name": partner_name}, headers=mgr)).json()
    old = (await client.post("/api/assets", json={
        "barcode": f"CS152-REGI-{n}", "name": "Saeco Royal", "serial_number": f"SN-REGI-{n}"}, headers=mgr)).json()
    res = await client.post(f"/api/assets/{old['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr)
    assert res.status_code == 200, res.text
    new = (await client.post("/api/assets", json={
        "barcode": f"CS152-UJ-{n}", "name": "Jetinno JL30", "serial_number": f"SN-UJ-{n}"}, headers=mgr)).json()
    res = await client.post(f"/api/assets/{old['id']}/swap", json={
        "replacement_asset_id": new["id"], "note": "hibás daráló", "protocol": protocol or None,
    }, headers=mgr)
    assert res.status_code == 200, res.text
    return partner, old, new


async def test_swap_list_delivery_mode_search_and_pdf(client, manager, employee_user):
    _, mgr = manager
    await _swap(client, mgr, 1, "Árvíztűrő Kávézó", delivery_mode="courier", delivery_ref="GLS 12345678")
    await _swap(client, mgr, 2, "Belvárosi Büfé", delivery_mode="personal")
    await _swap(client, mgr, 3, "Csendes Cukrászda")  # jelöletlen (mint a korábbi cserék)

    # csak a cserék (a kihelyezési jegyzőkönyvek nem)
    swaps = (await client.get("/api/asset-protocols?kind=swap", headers=mgr)).json()
    assert len(swaps) == 3 and all(s["kind"] == "swap" and s["serial"].startswith("CS-") for s in swaps)
    by_partner = {s["partner_name"]: s for s in swaps}
    a = by_partner["Árvíztűrő Kávézó"]
    assert a["delivery_mode"] == "courier" and a["delivery_ref"] == "GLS 12345678"
    assert a["asset_barcode"] == "CS152-REGI-1" and a["replacement_barcode"] == "CS152-UJ-1"
    assert a["asset_serial"] == "SN-REGI-1" and a["replacement_name"] == "Jetinno JL30" and a["replacement_serial"] == "SN-UJ-1"
    assert by_partner["Belvárosi Büfé"]["delivery_mode"] == "personal"
    assert by_partner["Csendes Cukrászda"]["delivery_mode"] is None
    all_rows = (await client.get("/api/asset-protocols", headers=mgr)).json()
    assert len(all_rows) == 6  # 3 kihelyezés + 3 csere — a régi hívás változatlanul mindent ad

    async def find(query: str) -> list[str]:
        rows = (await client.get(f"/api/asset-protocols?kind=swap&{query}", headers=mgr)).json()
        return sorted(r["partner_name"] for r in rows)

    # keresés: partner (ékezet nélkül is), vonalkód, gyári szám, futár, sorszám
    assert await find("q=arvizturo") == ["Árvíztűrő Kávézó"]
    assert await find("q=CS152-UJ-2") == ["Belvárosi Büfé"]
    assert await find("q=sn-regi-3") == ["Csendes Cukrászda"]
    assert await find("q=gls") == ["Árvíztűrő Kávézó"]
    assert await find(f"q={a['serial']}") == ["Árvíztűrő Kávézó"]
    assert await find("q=jetinno büfé") == ["Belvárosi Büfé"]  # több szó: mindnek egyeznie kell
    assert await find("q=nincs-ilyen") == []
    # szűrés az átadás módjára és dátumra
    assert await find("delivery_mode=courier") == ["Árvíztűrő Kávézó"]
    assert await find("delivery_mode=personal") == ["Belvárosi Büfé"]
    assert await find("delivery_mode=unset") == ["Csendes Cukrászda"]
    assert len(await find("date_from=2020-01-01&date_to=2100-01-01")) == 3
    assert await find("date_to=2020-01-01") == []
    assert (await client.get("/api/asset-protocols?kind=swap&delivery_mode=drone", headers=mgr)).status_code == 422

    # utólagos jelölés a korábbi (jelöletlen) cserén
    c = by_partner["Csendes Cukrászda"]
    res = await client.patch(f"/api/asset-protocols/{c['id']}", json={"delivery_mode": "courier", "delivery_ref": "MPL"}, headers=mgr)
    assert res.status_code == 200 and res.json()["delivery_mode"] == "courier" and res.json()["delivery_ref"] == "MPL"
    assert await find("delivery_mode=unset") == []
    assert (await client.patch(f"/api/asset-protocols/{c['id']}", json={"delivery_mode": "teleport"}, headers=mgr)).status_code == 422
    # jog nélkül se lista, se módosítás
    _u, emp_h, _e = employee_user
    assert (await client.get("/api/asset-protocols?kind=swap", headers=emp_h)).status_code == 403
    assert (await client.patch(f"/api/asset-protocols/{c['id']}", json={"delivery_mode": "personal"}, headers=emp_h)).status_code == 403

    # a jegyzőkönyv-PDF mutatja az átadás módját
    pdf = await client.get(f"/api/asset-protocols/{a['id']}/pdf", headers=mgr)
    assert pdf.status_code == 200
    text = _text(pdf.content)
    assert "Átadás módja" in text and "futárszolgálattal" in text and "GLS 12345678" in text
    pdf = await client.get(f"/api/asset-protocols/{by_partner['Belvárosi Büfé']['id']}/pdf", headers=mgr)
    assert "személyesen" in _text(pdf.content)
