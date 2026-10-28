"""113. kör (hibajegyek 2026-10-28): géptípus-tudatos tudásbázis + újragenerálás,
szervizes is kaphat autó-raktárt, szerelői átütemezés-kérés, helyszíni átvétel
képernyős aláírással, időbélyeggel és e-maillel."""

from __future__ import annotations

import base64
from datetime import date, timedelta

from tests.conftest import make_employee_record, make_user

PNG = "data:image/png;base64," + base64.b64encode(
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
).decode()


# ─────────────────────────── 1. tudásbázis ───────────────────────────

def test_kb_prompt_is_machine_category_aware():
    from app.services.wfm.kb_auto import _build_prompt

    p = _build_prompt("Di Giorgio Lab 100")
    assert "karos" in p.lower()
    assert "Géptípus" in p
    assert "Karos gépnél NINCS szemeskávé-tartály" in p
    hinted = _build_prompt("Di Giorgio Lab 100", "karos presszógép, kétkörös kazán")
    assert "kétkörös kazán" in hinted
    assert "felülír" in hinted


async def test_kb_regenerate_replaces_ai_entries_keeps_manual(client, manager, monkeypatch):
    _, mgr = manager
    from app.services.wfm import kb_auto

    # rossz, szuperautomatás AI-tartalom + egy kézi bejegyzés
    res = await client.post(
        "/api/knowledge",
        json={"machine_label": "Di Giorgio Lab 100", "title": "Kézi tipp",
              "content": "A csoportfej-tömítést félévente cseréljük."},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    from app.models import KnowledgeEntry
    from tests.conftest import app_db

    async with app_db.get_session_factory()() as s:
        s.add(KnowledgeEntry(machine_label="Di Giorgio Lab 100", title="Zacctartó tele",
                             content="Ürítsd a zacctartót.", source="ai"))
        await s.commit()

    seen: dict = {}

    async def fake_generate(db, prompt, **kw):
        seen["prompt"] = prompt
        return (
            '[{"code": "Géptípus", "title": "karos presszógép", "body": "Kézi karral, kazánnal."},'
            '{"code": "Nem gőzöl", "title": "Gőzpipa eltömődött", "body": "Tisztítsd a gőzpipa furatait."}]'
        )

    monkeypatch.setattr(kb_auto.ai_service, "generate", fake_generate)
    res = await client.post(
        "/api/knowledge/regenerate",
        json={"machine_label": "Di Giorgio Lab 100", "hint": "karos gép, nem automata"},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"deleted": 1, "created": 2}
    assert "karos gép, nem automata" in seen["prompt"]

    rows = (await client.get("/api/knowledge?machine=Di Giorgio", headers=mgr)).json()
    titles = {r["title"] for r in rows}
    assert "Kézi tipp" in titles
    assert "Nem gőzöl — Gőzpipa eltömődött" in titles
    assert not any("Zacctartó" in t for t in titles)


async def test_kb_regenerate_without_ai_is_422(client, manager, monkeypatch):
    _, mgr = manager
    from app.services.wfm import kb_auto

    async def no_ai(db, prompt, **kw):
        raise ValueError("AI nincs beállítva")

    monkeypatch.setattr(kb_auto.ai_service, "generate", no_ai)
    res = await client.post("/api/knowledge/regenerate", json={"machine_label": "Jura X8"}, headers=mgr)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "kb.ai_not_configured"


# ─────────────────────────── 2. raktár: szervizes is ───────────────────────────

async def test_assignable_users_include_szervizes(client, manager):
    _, mgr = manager
    await make_user(email="r113-szerelo@example.com", role="szervizes", display_name="Szerelő Samu")
    res = await client.get("/api/warehouses/assignable-users", headers=mgr)
    assert res.status_code == 200, res.text
    roles = {u["role"] for u in res.json()}
    assert "szervizes" in roles and "manager" in roles


async def test_van_warehouse_for_szervizes(client, manager):
    _, mgr = manager
    sz, _ = await make_user(email="r113-van@example.com", role="szervizes")
    res = await client.post(
        "/api/warehouses",
        json={"name": "Szerelő autó", "kind": "van", "user_id": str(sz.id)},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    assert res.json()["user_id"] == str(sz.id)


# ─────────────────────────── 3. átütemezés-kérés ───────────────────────────

async def _task_for(client, mgr, emp, title="Szerviz a partnernél"):
    res = await client.post(
        "/api/tasks",
        json={"title": title, "employee_id": str(emp.id), "due_date": date.today().isoformat()},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_reschedule_request_then_manager_accepts(client, manager):
    _, mgr = manager
    emp_user, emp_hdr = await make_user(email="r113-resch@example.com", role="szervizes")
    emp = await make_employee_record(emp_user)
    task = await _task_for(client, mgr, emp)

    # indok nélkül nem megy
    res = await client.post(f"/api/me/tasks/{task['id']}/reschedule-request", json={"note": "x"}, headers=emp_hdr)
    assert res.status_code == 422

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    res = await client.post(
        f"/api/me/tasks/{task['id']}/reschedule-request",
        json={"note": "Az ügyfél ma zárva, holnap tud fogadni.", "proposed_date": tomorrow},
        headers=emp_hdr,
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["reschedule_request_note"].startswith("Az ügyfél ma zárva")
    assert out["reschedule_request_date"] == tomorrow
    assert out["reschedule_requested_at"]
    assert out["due_date"] == date.today().isoformat()  # a feladat NEM mozdult
    assert any("Átütemezést kérek" in c["text"] for c in out["comments"])

    # a vezető listájában is látszik a kérés
    listed = (await client.get("/api/tasks", headers=mgr)).json()
    mine = next(t for t in listed if t["id"] == task["id"])
    assert mine["reschedule_request_note"]

    # elfogadás = határidő-módosítás indokkal → a kérés törlődik
    res = await client.patch(
        f"/api/tasks/{task['id']}",
        json={"due_date": tomorrow, "reschedule_note": "Szerelő kérése: ügyfél zárva"},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    acc = res.json()
    assert acc["due_date"] == tomorrow
    assert acc["reschedule_request_note"] is None
    assert acc["reschedule_request_date"] is None
    assert any("Átütemezés elfogadva" in c["text"] for c in acc["comments"])


async def test_reschedule_request_reject_and_withdraw(client, manager):
    _, mgr = manager
    emp_user, emp_hdr = await make_user(email="r113-rej@example.com", role="szervizes")
    emp = await make_employee_record(emp_user)
    task = await _task_for(client, mgr, emp, "Elutasítandó")

    res = await client.post(
        f"/api/tasks/{task['id']}/reschedule-request/reject", json={"note": "nincs"}, headers=mgr,
    )
    assert res.status_code == 404  # nincs függő kérés

    res = await client.post(
        f"/api/me/tasks/{task['id']}/reschedule-request", json={"note": "Beteg vagyok."}, headers=emp_hdr,
    )
    assert res.status_code == 200, res.text
    res = await client.post(
        f"/api/tasks/{task['id']}/reschedule-request/reject",
        json={"note": "Sürgős, más viszi helyetted"}, headers=mgr,
    )
    assert res.status_code == 200, res.text
    rej = res.json()
    assert rej["reschedule_request_note"] is None
    assert rej["due_date"] == date.today().isoformat()
    assert any("Átütemezés elutasítva: Sürgős" in c["text"] for c in rej["comments"])

    # visszavonás a szerelő oldaláról
    res = await client.post(
        f"/api/me/tasks/{task['id']}/reschedule-request", json={"note": "Mégis holnap."}, headers=emp_hdr,
    )
    assert res.status_code == 200
    res = await client.delete(f"/api/me/tasks/{task['id']}/reschedule-request", headers=emp_hdr)
    assert res.status_code == 200, res.text
    assert res.json()["reschedule_request_note"] is None

    # idegen feladatra nem kérhet
    other_user, other_hdr = await make_user(email="r113-other@example.com", role="szervizes")
    await make_employee_record(other_user)
    res = await client.post(
        f"/api/me/tasks/{task['id']}/reschedule-request", json={"note": "Nem az enyém."}, headers=other_hdr,
    )
    assert res.status_code == 404


def test_reschedule_event_registered():
    from app.services.wfm.automation import TRIGGERS

    assert "indok" in TRIGGERS["task.reschedule_requested"]
    assert "javasolt_datum" in TRIGGERS["task.reschedule_requested"]


# ─────────────────────────── 4. helyszíni átvétel ───────────────────────────

async def _intake_asset(client, mgr, barcode="R113-INT"):
    res = await client.post(
        "/api/assets",
        json={"barcode": barcode, "name": "Lab 100", "serial_number": "DG-1",
              "manufacturer": "Di Giorgio", "customer_owned": True},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_on_site_intake_requires_signature_and_name(client, manager):
    _, mgr = manager
    asset = await _intake_asset(client, mgr)
    base = {"asset_id": asset["id"], "client_name": "Helyszíni Ügyfél",
            "client_phone": "+36301112222", "client_email": "helyszin@example.com",
            "faults": "nem tart nyomást", "on_site": True}
    res = await client.post("/api/intakes", json=base, headers=mgr)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "intake.signature_required"

    res = await client.post("/api/intakes", json={**base, "client_signature": PNG}, headers=mgr)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "intake.signer_name_required"

    res = await client.post(
        "/api/intakes", json={**base, "client_signature": "data:image/jpeg;base64,xx", "client_signer_name": "Kiss Pál"},
        headers=mgr,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "intake.bad_signature"


async def test_on_site_intake_signed_pdf_and_email(client, manager, monkeypatch):
    _, mgr = manager
    asset = await _intake_asset(client, mgr, "R113-INT2")

    # a vezető mentett aláírás-mintája az átvevő aláírásaként kerül rá
    res = await client.put("/api/auth/me/signature", json={"signature": PNG}, headers=mgr)
    assert res.status_code == 200, res.text

    sent: list = []

    async def fake_send(config, to, subject, body, attachments=None):
        sent.append((to, subject, attachments))
        return True

    from app.api import intake as intake_api
    from app.services.wfm import email_service
    from app.services.wfm.email_service import SmtpConfig

    async def fake_cfg(db):
        return SmtpConfig(host="smtp.test", port=587, username=None, password=None,
                          from_address="noreply@test.hu", use_tls=False)

    monkeypatch.setattr(email_service, "load_smtp_config", fake_cfg)
    monkeypatch.setattr(email_service, "send_email", fake_send)

    res = await client.post(
        "/api/intakes",
        json={"asset_id": asset["id"], "client_name": "Aláíró Ügyfél",
              "client_phone": "+36301112223", "client_email": "alairo@example.com",
              "faults": "csöpög a csoportfej", "on_site": True,
              "client_signature": PNG, "client_signer_name": "Aláíró Ügyfél"},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    row = res.json()
    assert row["on_site"] is True
    assert row["client_signer_name"] == "Aláíró Ügyfél"
    assert row["signed_at"]

    # háttérben küldött e-mail (a teszt-kliens a BackgroundTasks-t a válasz után futtatja)
    assert sent, "az aláírt elismervény e-mailje nem ment ki"
    to, subject, attachments = sent[0]
    assert to == "alairo@example.com"
    assert row["serial"] in subject
    assert attachments and attachments[0][0] == f"{row['serial']}.pdf"
    assert attachments[0][1].startswith(b"%PDF")

    # a listában látszik az e-mail időbélyege
    rows = (await client.get("/api/intakes", headers=mgr)).json()
    mine = next(r for r in rows if r["id"] == row["id"])
    assert mine["emailed_at"]

    # a PDF megépül az aláírás-képekkel (nem dob)
    pdf = await client.get(f"/api/intakes/{row['id']}/pdf", headers=mgr)
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")

    # kézi újraküldés
    res = await client.post(f"/api/intakes/{row['id']}/email", json={}, headers=mgr)
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
    assert len(sent) == 2

    # A ténylegesen hívott PDF-építő megkapja az aláírásokat + időbélyeget
    seen = {}
    real = intake_api.build_intake_pdf

    def spy(data, settings=None):
        seen.update(data)
        return real(data, settings)

    monkeypatch.setattr(intake_api, "build_intake_pdf", spy)
    await client.get(f"/api/intakes/{row['id']}/pdf", headers=mgr)
    assert seen["client_signature"] == PNG
    assert seen["employee_signature"] == PNG
    assert seen["client_signer_name"] == "Aláíró Ügyfél"
    assert seen["on_site"] is True and seen["signed_at"]


async def test_intake_email_without_smtp_is_422(client, manager):
    _, mgr = manager
    asset = await _intake_asset(client, mgr, "R113-INT3")
    res = await client.post(
        "/api/intakes",
        json={"asset_id": asset["id"], "client_name": "Nem Helyszíni",
              "client_phone": "+3630", "client_email": "nh@example.com"},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    row = res.json()
    assert row["on_site"] is False and row["signed_at"] is None
    res = await client.post(f"/api/intakes/{row['id']}/email", json={}, headers=mgr)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "intake.smtp_missing"
