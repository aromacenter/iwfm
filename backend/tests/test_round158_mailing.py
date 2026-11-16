"""158. kör: Levélküldés — egy címre vagy minden e-mailes partnernek, kampány-naplóval."""

from __future__ import annotations

from app.services.wfm import email_service
from app.services.wfm.email_service import SmtpConfig


def _fake_smtp(monkeypatch, sent: list, fail: set[str] = frozenset()):
    async def load(db):
        return SmtpConfig(host="smtp.example.com", port=587, username=None, password=None, from_address="noreply@example.com", use_tls=True)

    async def send(config, to, subject, body, attachments=None, html=None):
        sent.append((to, subject, body, attachments, html))
        return to not in fail

    monkeypatch.setattr(email_service, "load_smtp_config", load)
    monkeypatch.setattr(email_service, "send_email", send)


async def test_recipients_and_campaigns(client, admin, manager, monkeypatch):
    _, adm = admin
    _, mgr = manager
    sent: list = []
    _fake_smtp(monkeypatch, sent, fail={"rossz@example.com"})
    # partnerek: egy kétcímes, egy cím nélküli, egy inaktív, egy duplikált cím
    for name, email, active in (("Alfa Kávézó", "alfa@example.com, info@alfa.example.com", True),
                                ("Béta Büfé", None, True), ("Gamma Trafik", "gamma@example.com", False),
                                ("Delta Bolt", "ALFA@example.com", True), ("Rossz Címes", "rossz@example.com", True)):
        res = await client.post("/api/partners", json={"name": name, "contact_email": email, "contact_name": "Kapcsolat"}, headers=adm)
        assert res.status_code == 201, res.text
        if not active:
            pid = res.json()["id"]
            assert (await client.patch(f"/api/partners/{pid}", json={"name": name, "contact_email": email, "contact_name": "Kapcsolat", "is_active": False}, headers=adm)).status_code == 200

    # csak admin
    assert (await client.get("/api/mailing/recipients", headers=mgr)).status_code == 403
    r = (await client.get("/api/mailing/recipients", headers=adm)).json()
    assert r["count"] == 3 and r["smtp_ready"] is True and r["sender"] == "noreply@example.com"
    assert sorted(x["email"].lower() for x in r["sample"]) == ["alfa@example.com", "info@alfa.example.com", "rossz@example.com"]

    # egy címre: azonnal, az eredménnyel
    res = await client.post("/api/mailing/send", json={"mode": "single", "to": "valaki@example.com", "subject": "Szia", "body": "Teszt levél."}, headers=adm)
    assert res.status_code == 201, res.text
    c = res.json()
    assert c["status"] == "done" and c["sent_count"] == 1 and c["failed_count"] == 0 and sent[-1][0] == "valaki@example.com"
    assert sent[-1][2].startswith("Teszt levél.")
    # hibás cím
    assert (await client.post("/api/mailing/send", json={"mode": "single", "to": "nemcim", "subject": "x", "body": "y"}, headers=adm)).status_code == 422

    # minden partnernek: háttérben, a napló lezárul, a bukott cím listázva
    res = await client.post("/api/mailing/send", json={"mode": "partners", "subject": "Hírlevél", "body": "Kedves Partnerünk!"}, headers=adm)
    assert res.status_code == 201, res.text
    camps = (await client.get("/api/mailing/campaigns", headers=adm)).json()
    camp = next(x for x in camps if x["subject"] == "Hírlevél")
    assert camp["recipient_count"] == 3 and camp["status"] == "done"
    assert camp["sent_count"] == 2 and camp["failed_count"] == 1 and camp["failed"] == ["rossz@example.com"]
    tos = {s[0] for s in sent if s[1] == "Hírlevél"}
    assert tos == {"alfa@example.com", "info@alfa.example.com", "rossz@example.com"}

    # próbalevél a saját címre
    res = await client.post("/api/mailing/test", json={"mode": "single", "subject": "Szia", "body": "Teszt"}, headers=adm)
    assert res.status_code == 200, res.text
    assert sent[-1][1] == "[PRÓBA] Szia"


async def test_html_and_attachments(client, admin, monkeypatch):
    import base64

    from app.api.mailing import _html_to_text

    assert _html_to_text("<p>Szia <b>Pali</b>!</p><ul><li>egy</li><li>kett&ouml;</li></ul>") == "Szia Pali!\n• egy\n• kettö"
    _, adm = admin
    sent: list = []
    _fake_smtp(monkeypatch, sent)
    pdf = base64.b64encode(b"%PDF-1.4 teszt").decode()
    res = await client.post("/api/mailing/send", json={
        "mode": "single", "to": "a@example.com", "subject": "Ajánlat", "html": True,
        "body": "<p>Kedves <b>Partnerünk</b>!</p><p>Mellékelve az ajánlat.</p>",
        "attachments": [{"name": "ajanlat.pdf", "mime": "application/pdf", "data": "data:application/pdf;base64," + pdf}],
    }, headers=adm)
    assert res.status_code == 201, res.text
    c = res.json()
    assert c["is_html"] is True and c["attachment_names"] == ["ajanlat.pdf"] and c["sent_count"] == 1
    to, subject, text, attachments, html = sent[-1]
    assert text.startswith("Kedves Partnerünk!\nMellékelve az ajánlat.") and "<b>Partnerünk</b>" in html and html.startswith("<!doctype html>")
    assert attachments == [("ajanlat.pdf", b"%PDF-1.4 teszt", "application", "pdf")]
    # túl nagy csatolmány
    big = base64.b64encode(b"x" * (9 * 1024 * 1024)).decode()
    res = await client.post("/api/mailing/send", json={"mode": "single", "to": "a@example.com", "subject": "x", "body": "y",
                                                       "attachments": [{"name": "nagy.bin", "mime": "application/octet-stream", "data": big}]}, headers=adm)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "mailing.attachments_too_big"


async def test_mailing_requires_smtp(client, admin, monkeypatch):
    _, adm = admin

    async def none(db):
        return None

    monkeypatch.setattr(email_service, "load_smtp_config", none)
    assert (await client.get("/api/mailing/recipients", headers=adm)).json()["smtp_ready"] is False
    res = await client.post("/api/mailing/send", json={"mode": "single", "to": "a@example.com", "subject": "x", "body": "y"}, headers=adm)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "settings.email_not_configured"
