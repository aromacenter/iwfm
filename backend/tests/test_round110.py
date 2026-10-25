"""110. kör — hibajegyek (2026-10-25): Billingó partner-törzs üres mezők nélkül +
érthető hibaüzenet (3fe1fdcb), ajánlat több e-mail címre (db347fbd), az
ügyfél-példányon a szervizes megjegyzése külön szakasz (06b87417)."""
from __future__ import annotations

from datetime import date

from tests.conftest import make_employee_record, make_user


def test_parse_free_address():
    from app.services.wfm.billingo_service import _parse_free_address as p

    assert p("1024, Budapest, Lövőház u. 12.") == ("1024", "Budapest", "Lövőház u. 12.")
    assert p("2011 Budakalász, Tanító utca 38") == ("2011", "Budakalász", "Tanító utca 38")
    assert p("csak egy szöveg") == (None, None, "csak egy szöveg")
    assert p(None) == (None, None, None)


async def test_billingo_partner_payload_without_empty_fields(monkeypatch):
    from app.models import Partner
    from app.services.wfm import billingo_service as bs

    calls: list[tuple[str, str, dict | None]] = []

    async def fake_api(api_key, method, path, json_body=None):
        calls.append((method, path, json_body))
        if method == "GET":
            return {"data": []}
        return {"id": 77}

    monkeypatch.setattr(bs, "_api", fake_api)
    # átvételből jövő ügyfél: csak név + egysoros cím, se adószám, se e-mail
    p = Partner(name="X-Presso Coffee Kft.", address="1024, Budapest, Lövőház u. 12.")
    assert await bs._find_or_create_billingo_partner("k", p) == 77
    body = calls[-1][2]
    assert body["name"] == "X-Presso Coffee Kft."
    assert body["address"] == {"country_code": "HU", "post_code": "1024", "city": "Budapest", "address": "Lövőház u. 12."}
    assert "taxcode" not in body and "emails" not in body  # üres mezőt nem küldünk (ez volt a 422)

    # 422 az adószámra → második kör csak név + cím
    calls.clear()
    state = {"n": 0}

    async def flaky_api(api_key, method, path, json_body=None):
        calls.append((method, path, json_body))
        if method == "GET":
            return {"data": []}
        state["n"] += 1
        if state["n"] == 1:
            raise bs.BillingoApiError(422, "/partners", '{"message":"Validation failed","errors":{"taxcode":["invalid"]}}')
        return {"id": 78}

    monkeypatch.setattr(bs, "_api", flaky_api)
    p2 = Partner(name="Teszt Kft.", address_zip="1111", address_city="Budapest", address_street="Fő u.", address_number="1", tax_number="12345678-2-42", contact_email="a@b.hu")
    assert await bs._find_or_create_billingo_partner("k", p2) == 78
    assert "taxcode" in calls[-2][2] and set(calls[-1][2].keys()) == {"name", "address"}
    err = bs.BillingoApiError(422, "/partners", '{"message":"Validation failed","errors":{"taxcode":["invalid"]}}')
    assert err.message() == "Validation failed — taxcode: invalid"


async def test_quote_multiple_recipients_and_customer_remark_section(client, manager, monkeypatch):
    _, mgr = manager
    emp_user, emp_hdr = await make_user(email="r110-szerelo@example.com", role="szervizes")
    emp = await make_employee_record(emp_user)
    task = (
        await client.post(
            "/api/tasks",
            json={"title": "Ajánlat teszt", "employee_id": str(emp.id), "due_date": date.today().isoformat(), "external_service": True, "client_name": "Vevő"},
            headers=mgr,
        )
    ).json()
    res = await client.put(
        f"/api/me/tasks/{task['id']}/worksheet",
        json={"work_description": "Belső leírás", "repair_options": [{"name": "Alap", "cost_net": 1000}],
              "public_note": "Gyakrabban kell a KPE-t tisztítani", "solution_text": "Kazán csere", "print_solution": True},
        headers=emp_hdr,
    )
    assert res.status_code == 200, res.text

    sent: list[str] = []

    async def fake_send(smtp, to, subject, body, attachments=None):
        sent.append(to)
        return True

    async def fake_smtp(db):
        return {"host": "x"}

    import app.api.tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "send_email", fake_send)
    monkeypatch.setattr(tasks_mod, "load_smtp_config", fake_smtp)
    # a képviselő beárazza a konstrukciót (ajánlat csak árral küldhető)
    res = await client.put(
        f"/api/tasks/{task['id']}/worksheet",
        json={"work_description": "Belső leírás", "repair_options": [{"name": "Alap", "cost_net": 1000, "price_net": 5000}]},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    # rossz cím → 422; több cím pontosvesszővel/vesszővel → egy küldés mindkettőre
    res = await client.post(f"/api/tasks/{task['id']}/worksheet/send-quote", json={"to": "nem-email"}, headers=mgr)
    assert res.status_code == 422
    res = await client.post(f"/api/tasks/{task['id']}/worksheet/send-quote", json={"to": "szerviz@x-presso.hu; kaveslaci@gmail.com"}, headers=mgr)
    assert res.status_code == 200, res.text
    assert res.json()["quote_email"] == "szerviz@x-presso.hu, kaveslaci@gmail.com"
    assert sent and "kaveslaci@gmail.com" in sent[-1] and "szerviz@x-presso.hu" in sent[-1]

    # ügyfél-példány: a szervizes megjegyzése külön szakaszban, nem az „Elvégzett munka” alatt
    captured: dict = {}
    real = tasks_mod.build_worksheet_pdf  # a tasks modul közvetlenül importálja

    def spy(data, settings=None):
        captured.update(data)
        return real(data, settings)

    monkeypatch.setattr(tasks_mod, "build_worksheet_pdf", spy)
    res = await client.get(f"/api/tasks/{task['id']}/worksheet/pdf?variant=customer", headers=mgr)
    assert res.status_code == 200 and res.content[:4] == b"%PDF"
    assert captured["public_remark"] == "Gyakrabban kell a KPE-t tisztítani"
    assert captured["work_description"] == "" and captured["solution_text"] == "Kazán csere"
