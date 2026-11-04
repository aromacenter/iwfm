"""145. kör (x-presso 7ca9c6f6): hivatalos javítási árajánlat — sorszám a
kiküldéskor (AJ-ÉÉÉÉ-NNNN), konstrukciónként A4 PDF (10 munkanap érvényesség,
aláírás + bélyegző a beállításokból), PO-szám az elfogadó oldalon és utólagos
szerkesztése, ajánlat-sorszám + PO a munkalap-PDF-en és a számla megjegyzésében."""

from __future__ import annotations

import base64
import io
from datetime import date

from pypdf import PdfReader

from tests.conftest import make_employee_record, make_user

# 1×1 px átlátszó PNG (aláírás/bélyegző helyett)
PNG_1PX = "data:image/png;base64," + base64.b64encode(
    bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")
).decode()


def _text(pdf_bytes: bytes) -> str:
    return " ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf_bytes)).pages)


async def _setup(client, mgr):
    asset = (await client.post("/api/assets", json={"barcode": "QPDF-1", "name": "Nébih gép", "manufacturer": "Expobar", "serial_number": "SN-NEBIH-1"}, headers=mgr)).json()
    user, emp_headers = await make_user(email="quotepdf@example.com", role="szervizes")
    emp = await make_employee_record(user, last_name="Ajánlat", first_name="Ede")
    task = (await client.post("/api/tasks", json={
        "title": "Javítás — Nébih", "employee_id": str(emp.id), "due_date": "2026-11-15",
        "external_service": True, "asset_id": asset["id"], "client_name": "Nébih",
    }, headers=mgr)).json()
    res = await client.put(f"/api/tasks/{task['id']}/worksheet", json={
        "work_description": "diagnózis kész", "works": [], "materials": [],
        "repair_options": [
            {"name": "Felújított szivattyúval", "cost_net": 12000, "price_net": 19000},
            {"name": "Új szivattyúval", "cost_net": 22000, "price_net": 32000},
        ],
        "public_note": "A felújított szivattyúra 6 hónap, az újra 12 hónap garancia.",
    }, headers=mgr)
    assert res.status_code == 200, res.text
    return emp_headers, task["id"]


def test_add_working_days():
    from app.services.wfm.quote_pdf import add_working_days

    assert add_working_days(date(2026, 11, 4), 10) == date(2026, 11, 18)  # szerda + 10 munkanap
    assert add_working_days(date(2026, 11, 6), 1) == date(2026, 11, 9)  # péntek → hétfő


async def test_official_quote_pdf_po_and_references(client, admin, manager, monkeypatch):
    import app.api.tasks as tasks_mod

    _, adm = admin
    _, mgr = manager
    _emp_h, task_id = await _setup(client, mgr)

    # aláíró + aláírás + bélyegző a beállításokban
    cur = (await client.get("/api/settings/worksheet", headers=adm)).json()
    res = await client.put("/api/settings/worksheet", json={
        **{k: cur[k] for k in ("company_name", "company_address", "footer_text", "customer_footer_text", "intake_footer_text",
                               "survey_fee", "accent_color", "show_materials", "show_hours", "show_client_signature",
                               "show_comments", "photo_policy", "photo_min")},
        "company_name": "X-Presso Coffee Kft.",
        "signer_name": "Szabó László", "signer_title": "ügyvezető",
        "signature": PNG_1PX, "stamp": PNG_1PX,
    }, headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()["has_signature"] and res.json()["has_stamp"] and res.json()["signer_name"] == "Szabó László"
    assert (await client.get("/api/settings/worksheet/signature", headers=mgr)).status_code == 200
    assert (await client.get("/api/settings/worksheet/stamp", headers=mgr)).status_code == 200

    # kiküldés előtt nincs sorszám és nincs PDF
    ws = (await client.get(f"/api/tasks/{task_id}/worksheet", headers=mgr)).json()
    assert ws["quote_serial"] is None
    assert (await client.get(f"/api/tasks/{task_id}/worksheet/quote-pdf?option=1", headers=mgr)).status_code == 422

    sent: list = []

    async def fake_send(smtp, to, subject, body):
        sent.append((to, subject, body))
        return True

    async def fake_smtp(db):
        return object()

    monkeypatch.setattr(tasks_mod, "send_email", fake_send)
    monkeypatch.setattr(tasks_mod, "load_smtp_config", fake_smtp)
    res = await client.post(f"/api/tasks/{task_id}/worksheet/send-quote", json={"to": "nebih@example.com"}, headers=mgr)
    assert res.status_code == 200, res.text
    assert res.json()["quote_serial"] == f"AJ-{date.today().year}-0001"
    token = sent[0][2].split("/munkalap-ajanlat/")[1].split()[0]

    # publikus nézet: sorszám + érvényesség; PDF konstrukciónként
    pub = (await client.get(f"/api/public/worksheet-quote/{token}")).json()
    assert pub["quote_serial"] == f"AJ-{date.today().year}-0001" and pub["valid_until"] and pub["po_number"] is None
    pdf1 = await client.get(f"/api/public/worksheet-quote/{token}/pdf?option=1")
    assert pdf1.status_code == 200 and pdf1.content.startswith(b"%PDF")
    t1 = _text(pdf1.content)
    assert "JAVÍTÁSI ÁRAJÁNLAT" in t1 and f"AJ-{date.today().year}-0001 / 1" in t1
    assert "Felújított szivattyúval" in t1 and "19 000 Ft" in t1 and "24 130 Ft" in t1  # nettó + bruttó
    assert "Szabó László" in t1 and "ügyvezető" in t1 and pub["valid_until"] in t1 and "Nébih" in t1
    pdf2 = await client.get(f"/api/public/worksheet-quote/{token}/pdf?option=2")
    assert "Új szivattyúval" in _text(pdf2.content) and "32 000 Ft" in _text(pdf2.content)
    assert (await client.get(f"/api/public/worksheet-quote/{token}/pdf?option=3")).status_code == 404

    # elfogadás PO-számmal
    res = await client.post(f"/api/public/worksheet-quote/{token}/accept", json={
        "option_name": "Új szivattyúval", "accepted_by": "Kovács Anna", "po_number": "PO-2026-0471",
    })
    assert res.status_code == 200, res.text
    ws = (await client.get(f"/api/tasks/{task_id}/worksheet", headers=mgr)).json()
    assert ws["po_number"] == "PO-2026-0471" and ws["quote_serial"] == f"AJ-{date.today().year}-0001"
    pub = (await client.get(f"/api/public/worksheet-quote/{token}")).json()
    assert pub["po_number"] == "PO-2026-0471"
    # elfogadás után a PDF az elfogadott konstrukciót mutatja, az elfogadóval
    t_acc = _text((await client.get(f"/api/public/worksheet-quote/{token}/pdf?option=1")).content)
    assert "Új szivattyúval" in t_acc and "Elfogadva: Kovács Anna" in t_acc and "PO-2026-0471" in t_acc

    # PO utólagos javítása a cégnél; a munkalap-PDF-en hivatkozás
    res = await client.patch(f"/api/tasks/{task_id}/worksheet/po", json={"po_number": "PO-2026-0472"}, headers=mgr)
    assert res.status_code == 200 and res.json()["po_number"] == "PO-2026-0472"
    wpdf = await client.get(f"/api/tasks/{task_id}/worksheet/pdf", headers=mgr)
    assert wpdf.status_code == 200
    wt = _text(wpdf.content)
    assert f"AJ-{date.today().year}-0001" in wt and "PO-2026-0472" in wt

    # a számla megjegyzése
    from app.api.tasks import _handover_invoice_comment
    from app.models import Worksheet

    fake = Worksheet(serial="KSZ-2026-0001", quote_serial="AJ-2026-0001", po_number="PO-1")
    assert _handover_invoice_comment(fake) == "Munkalap: KSZ-2026-0001 · Elfogadott árajánlat: AJ-2026-0001 · Megrendelői PO-szám: PO-1"

    # a belső PDF-végpont ugyanazt adja; a szervizes nem éri el
    assert (await client.get(f"/api/tasks/{task_id}/worksheet/quote-pdf?option=1", headers=mgr)).status_code == 200
    assert (await client.get(f"/api/tasks/{task_id}/worksheet/quote-pdf?option=1", headers=_emp_h)).status_code == 403
