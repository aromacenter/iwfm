"""Munkalap-árajánlat folyamat: a szervizes SOSEM látja az ügyfél-árakat;
ajánlat-küldés linkkel, publikus megtekintés (cost_net nélkül), elfogadás
után csak a kiválasztott opció marad."""

from __future__ import annotations

from tests.conftest import make_employee_record, make_user


async def _setup(client, mgr):
    res = await client.post(
        "/api/assets",
        json={"barcode": "QUOTE-1", "name": "Ajánlatos Gép", "manufacturer": "Jura"},
        headers=mgr,
    )
    asset = res.json()
    user, emp_headers = await make_user(email="quoteguy@example.com", role="szervizes")
    emp = await make_employee_record(user, last_name="Ajánlat", first_name="Ede")
    res = await client.post(
        "/api/tasks",
        json={"title": "Ajánlatos javítás", "employee_id": str(emp.id),
              "due_date": "2026-09-15", "external_service": True,
              "asset_id": asset["id"], "client_name": "Ajánlat Kft."},
        headers=mgr,
    )
    return emp_headers, res.json()["id"]


async def test_quote_flow_and_price_hiding(client, admin, manager, monkeypatch):
    _, adm = admin
    _, mgr = manager
    emp_headers, task_id = await _setup(client, mgr)

    # szervizes felviszi a konstrukciókat a saját díjaival
    res = await client.put(
        f"/api/me/tasks/{task_id}/worksheet",
        json={
            "work_description": "diagnózis kész",
            "repair_options": [
                {"name": "Felújított alkatrésszel", "cost_net": 12000},
                {"name": "Új alkatrésszel", "cost_net": 22000},
            ],
            "works": [], "materials": [],
        },
        headers=emp_headers,
    )
    assert res.status_code == 200, res.text

    # képviselő beárazza
    res = await client.put(
        f"/api/tasks/{task_id}/worksheet",
        json={
            "work_description": "diagnózis kész",
            "repair_options": [
                {"name": "Felújított alkatrésszel", "cost_net": 12000, "price_net": 19000},
                {"name": "Új alkatrésszel", "cost_net": 22000, "price_net": 32000},
            ],
            "works": [], "materials": [],
            "customer_note": "Belső ügyfél-jegyzet",
        },
        headers=mgr,
    )
    assert res.status_code == 200, res.text

    # a SZERVIZES nem láthatja az ügyfél-árakat és az ügyfél-jegyzetet
    ws = (await client.get(f"/api/me/tasks/{task_id}/worksheet", headers=emp_headers)).json()
    assert all(w["price_net"] is None for w in ws["repair_options"])
    assert ws["customer_note"] is None
    assert ws["repair_options"][0]["cost_net"] == 12000  # a sajátját látja

    # a szervizes mentése nem törli a beárazást
    res = await client.put(
        f"/api/me/tasks/{task_id}/worksheet",
        json={
            "work_description": "diagnózis kész v2",
            "repair_options": [
                {"name": "Felújított alkatrésszel", "cost_net": 12000},
                {"name": "Új alkatrésszel", "cost_net": 22000},
            ],
            "works": [], "materials": [],
        },
        headers=emp_headers,
    )
    assert res.status_code == 200
    ws_mgr = (await client.get(f"/api/tasks/{task_id}/worksheet", headers=mgr)).json()
    assert {w["name"]: w["price_net"] for w in ws_mgr["repair_options"]} == {
        "Felújított alkatrésszel": 19000, "Új alkatrésszel": 32000,
    }

    # e-mail küldés elfogása
    sent: list[dict] = []

    async def fake_send(smtp, to, subject, body, attachments=None):
        sent.append({"to": to, "subject": subject, "body": body})
        return True

    async def fake_smtp(db):
        return {"host": "x"}

    import app.api.tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "send_email", fake_send)
    monkeypatch.setattr(tasks_mod, "load_smtp_config", fake_smtp)

    res = await client.post(
        f"/api/tasks/{task_id}/worksheet/send-quote",
        json={"to": "ugyfel@example.com"},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["quote_status"] == "sent"
    assert sent and "munkalap-ajanlat/" in sent[0]["body"]
    token = sent[0]["body"].split("munkalap-ajanlat/")[1].splitlines()[0].strip()

    # publikus nézet: csak ügyfél-árak, cost_net sehol
    res = await client.get(f"/api/public/worksheet-quote/{token}")
    assert res.status_code == 200
    pub = res.json()
    assert pub["serial"].startswith("KSZ-")
    assert {o["name"] for o in pub["options"]} == {"Felújított alkatrésszel", "Új alkatrésszel"}
    assert all("cost_net" not in o for o in pub["options"])
    assert pub["options"][0]["price_gross"] is not None

    # elfogadás → csak a kiválasztott opció marad
    res = await client.post(
        f"/api/public/worksheet-quote/{token}/accept",
        json={"option_name": "Felújított alkatrésszel", "accepted_by": "Kovács Anna"},
    )
    assert res.status_code == 200, res.text
    ws_mgr = (await client.get(f"/api/tasks/{task_id}/worksheet", headers=mgr)).json()
    assert ws_mgr["quote_status"] == "accepted"
    assert ws_mgr["quote_selected_name"] == "Felújított alkatrésszel"
    assert [w["name"] for w in ws_mgr["repair_options"]] == ["Felújított alkatrésszel"]

    # a szervizes látja az elfogadott állapotot — de árat továbbra sem
    ws = (await client.get(f"/api/me/tasks/{task_id}/worksheet", headers=emp_headers)).json()
    assert ws["quote_status"] == "accepted"
    assert ws["quote_selected_name"] == "Felújított alkatrésszel"
    assert ws["repair_options"][0]["price_net"] is None

    # második elfogadás tiltott
    res = await client.post(
        f"/api/public/worksheet-quote/{token}/accept",
        json={"option_name": "Felújított alkatrésszel", "accepted_by": "X"},
    )
    assert res.status_code == 422


async def test_quote_decline_with_survey_fee(client, admin, manager, monkeypatch):
    """A "nem kérem a javítást" opció: felmérési díj kerül a munkalapra."""
    _, adm = admin
    _, mgr = manager
    emp_headers, task_id = await _setup(client, mgr)

    # egyedi felmérési díj beállítása adminból
    ws_set = (await client.get("/api/settings/worksheet", headers=adm)).json()
    assert ws_set["survey_fee_default"] == 5000
    res = await client.put(
        "/api/settings/worksheet",
        json={"accent_color": "#1e40af", "survey_fee": 7500},
        headers=adm,
    )
    assert res.status_code == 200 and res.json()["survey_fee"] == 7500

    await client.put(
        f"/api/tasks/{task_id}/worksheet",
        json={"work_description": "diagnózis",
              "repair_options": [{"name": "Javítás", "cost_net": 10000, "price_net": 20000}],
              "works": [], "materials": []},
        headers=mgr,
    )

    sent: list[dict] = []

    async def fake_send(smtp, to, subject, body, attachments=None):
        sent.append({"body": body})
        return True

    async def fake_smtp(db):
        return {"host": "x"}

    import app.api.tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "send_email", fake_send)
    monkeypatch.setattr(tasks_mod, "load_smtp_config", fake_smtp)
    res = await client.post(
        f"/api/tasks/{task_id}/worksheet/send-quote",
        json={"to": "ugyfel2@example.com"}, headers=mgr,
    )
    assert res.status_code == 200
    token = sent[0]["body"].split("munkalap-ajanlat/")[1].splitlines()[0].strip()

    # publikus nézetben ott a felmérési díj
    pub = (await client.get(f"/api/public/worksheet-quote/{token}")).json()
    assert pub["survey_fee_net"] == 7500

    # elutasítás → declined + felmérési díj a works közt, konstrukciók törölve
    res = await client.post(
        f"/api/public/worksheet-quote/{token}/accept",
        json={"decline": True, "accepted_by": "Nem Kérő Béla"},
    )
    assert res.status_code == 200 and res.json()["declined"] is True
    ws = (await client.get(f"/api/tasks/{task_id}/worksheet", headers=mgr)).json()
    assert ws["quote_status"] == "declined"
    assert ws["repair_options"] == []
    fee_rows = [w for w in ws["works"] if "Felmérési díj" in w["name"]]
    assert fee_rows and fee_rows[0]["price_net"] == 7500
    # a szervizes a díjat nem látja, csak az elutasított állapotot
    ws_self = (await client.get(f"/api/me/tasks/{task_id}/worksheet", headers=emp_headers)).json()
    assert ws_self["quote_status"] == "declined"
    assert all(w["price_net"] is None for w in ws_self["works"])
