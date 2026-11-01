"""121. kör (x-presso jegy f8e12fda): kihelyezési / visszavételi / csere jegyzőkönyv —
a mozgás automatikusan jegyzőkönyvet készít (KH- / VV- / CS- sorszám), A4 PDF-ben
letölthető, helyszíni aláírással névvel és időbélyeggel, a partnernek e-mailben küldhető."""

from __future__ import annotations

import base64
import io

from pypdf import PdfReader

from tests.test_inventory import asset_payload, make_partner

def _png() -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (120, 40), (0, 0, 0, 255)).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


PNG = _png()


def _text(pdf: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


async def test_deploy_return_swap_create_protocols(client, manager, monkeypatch):
    _, mgr = manager
    res = await client.post("/api/partners", json={
        "name": "Pláza Kávézó Kft.", "contact_name": "Kis Pál", "contact_email": "p@example.com",
        "tax_number": "12345678-2-42", "address": "1052 Budapest, Váci u. 1.",
    }, headers=mgr)
    assert res.status_code == 201, res.text
    partner = res.json()
    asset = (await client.post("/api/assets", json=asset_payload(counters=[1200, 300], counter_count=2), headers=mgr)).json()

    # kihelyezés helyszíni aláírással
    dep = await client.post(f"/api/assets/{asset['id']}/deploy", json={
        "partner_id": partner["id"], "note": "első kihelyezés",
        "protocol": {"on_site": True, "signer_name": "Kiss Pál", "signature": PNG, "location": "Pláza, 2. emelet", "counters": [1200, 300]},
    }, headers=mgr)
    assert dep.status_code == 200, dep.text
    body = dep.json()
    assert body["protocol_serial"].startswith("KH-") and body["protocol_serial"].endswith("-0001")
    pid = body["protocol_id"]
    p = (await client.get(f"/api/asset-protocols/{pid}", headers=mgr)).json()
    assert p["kind"] == "deploy" and p["on_site"] is True and p["signer_name"] == "Kiss Pál" and p["signed_at"] is not None
    assert p["location"] == "Pláza, 2. emelet" and p["partner_name"] == "Pláza Kávézó Kft."
    pdf = await client.get(f"/api/asset-protocols/{pid}/pdf", headers=mgr)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    txt = _text(pdf.content)
    assert "KIHELYEZÉSI JEGYZŐKÖNYV" in txt and "SZERZŐDÉS MELLÉKLETE" in txt
    assert "Pláza Kávézó Kft." in txt and "12345678-2-42" in txt and "Kiss Pál" in txt
    assert "1 200" in txt and "Aláírva:" in txt and "Pláza, 2. emelet" in txt

    # csere: a leszerelt és a cseregép is a lapon, CS- sorszám
    repl = (await client.post("/api/assets", json=asset_payload(barcode="ESZ-000999", serial_number="SN-1", name="Cseregép"), headers=mgr)).json()
    sw = await client.post(f"/api/assets/{asset['id']}/swap", json={
        "replacement_asset_id": repl["id"], "old_counters": [1500, 420], "new_counter": 10,
        "protocol": {"on_site": False, "location": "Pláza"},
    }, headers=mgr)
    assert sw.status_code == 200, sw.text
    assert sw.json()["protocol_serial"].startswith("CS-")
    stxt = _text((await client.get(f"/api/asset-protocols/{sw.json()['protocol_id']}/pdf", headers=mgr)).content)
    assert "GÉPCSERE JEGYZŐKÖNYV" in stxt and "Leszerelt gép" in stxt and "Cseregép (kihelyezve)" in stxt
    assert "1 500" in stxt and "ESZ-000999" in stxt
    assert "Helyszíni készítés:" in stxt and chr(10) + "nem" + chr(10) in stxt  # címke és érték külön sorban

    # visszavétel: VV- sorszám, a partner a pillanatképből (már nincs a gépen)
    ret = await client.post(f"/api/assets/{repl['id']}/return", json={"protocol": {"counters": [55]}}, headers=mgr)
    assert ret.status_code == 200, ret.text
    assert ret.json()["protocol_serial"].startswith("VV-")
    rp = (await client.get(f"/api/asset-protocols/{ret.json()['protocol_id']}", headers=mgr)).json()
    assert rp["partner_name"] == "Pláza Kávézó Kft." and rp["kind"] == "return"
    assert "VISSZAVÉTELI JEGYZŐKÖNYV" in _text((await client.get(f"/api/asset-protocols/{rp['id']}/pdf", headers=mgr)).content)

    # lista partnerre: 3 jegyzőkönyv, sorszámok év szerint számozva
    rows = (await client.get(f"/api/asset-protocols?partner_id={partner['id']}", headers=mgr)).json()
    assert len(rows) == 3 and {r["kind"] for r in rows} == {"deploy", "swap", "return"}
    assert (await client.get("/api/asset-protocols?partner_id=nem-uuid", headers=mgr)).status_code == 422
    assert (await client.get("/api/asset-protocols/00000000-0000-0000-0000-000000000000", headers=mgr)).status_code == 404


async def test_protocol_email_send(client, manager, monkeypatch):
    _, mgr = manager
    partner = await make_partner(client, mgr, name="Levél Kft.")
    asset = (await client.post("/api/assets", json=asset_payload(), headers=mgr)).json()

    sent: list = []

    async def fake_send(config, to, subject, body, attachments=None):
        sent.append({"to": to, "subject": subject, "att": [a[0] for a in (attachments or [])]})
        return True

    from app.services.wfm import email_service

    monkeypatch.setattr(email_service, "send_email", fake_send)
    monkeypatch.setattr(email_service, "load_smtp_config", lambda db: _cfg())

    async def _cfg():
        return object()

    # küldés a mozgással együtt (send_email: true) — a partner címére
    dep = await client.post(f"/api/assets/{asset['id']}/deploy", json={
        "partner_id": partner["id"], "protocol": {"send_email": True},
    }, headers=mgr)
    assert dep.status_code == 200, dep.text
    assert dep.json()["protocol_emailed_to"] == "p@example.com"
    assert sent and sent[0]["to"] == "p@example.com" and sent[0]["subject"].startswith("Kihelyezési jegyzőkönyv")
    assert sent[0]["att"][0].endswith(".pdf")
    pid = dep.json()["protocol_id"]
    assert (await client.get(f"/api/asset-protocols/{pid}", headers=mgr)).json()["emailed_at"] is not None

    # utólagos küldés megadott címre
    res = await client.post(f"/api/asset-protocols/{pid}/send", json={"to": "konyveles@example.com"}, headers=mgr)
    assert res.status_code == 200, res.text
    assert sent[-1]["to"] == "konyveles@example.com"
    assert res.json()["emailed_to"] == "konyveles@example.com"

    # nincs SMTP → 422
    monkeypatch.setattr(email_service, "load_smtp_config", lambda db: _none())

    async def _none():
        return None

    res = await client.post(f"/api/asset-protocols/{pid}/send", json={}, headers=mgr)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "protocol.not_sent"
