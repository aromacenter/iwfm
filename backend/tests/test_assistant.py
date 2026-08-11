"""AI-asszisztens: eszköz-hurok, jogosultságok, események."""

from __future__ import annotations

import app.db as app_db
from app.core.crypto import encrypt_pii
from app.services.wfm import assistant


async def _enable_ai() -> None:
    """Teszt-AI beállítása (anthropic + kamu kulcs) közvetlenül a DB-ben."""
    from app.models import AISettings

    factory = app_db.get_session_factory()
    async with factory() as session:
        session.add(
            AISettings(
                id=1,
                active_provider="anthropic",
                anthropic_key_encrypted=encrypt_pii("test-key"),
            )
        )
        await session.commit()


def _script(monkeypatch, steps: list[tuple[str, list[dict]]]) -> None:
    """A modell-hívások szkriptelése: minden hívás a következő (szöveg,
    tool-hívások) párost adja."""
    calls = {"i": 0}

    async def fake_step(api_key, model, system, messages, tools):
        text, tool_calls = steps[min(calls["i"], len(steps) - 1)]
        calls["i"] += 1
        raw = [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]}
               for c in tool_calls]
        return text, tool_calls, raw

    monkeypatch.setattr(assistant, "_anthropic_step", fake_step)


async def test_chat_requires_ai(client, admin):
    _, adm = admin
    res = await client.post(
        "/api/assistant/chat",
        json={"messages": [{"role": "user", "content": "Szia!"}]},
        headers=adm,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "assistant.ai_not_configured"


async def test_chat_tool_creates_ticket(client, admin, monkeypatch):
    from tests.test_service import make_asset

    _, adm = admin
    await _enable_ai()
    asset = await make_asset(client, adm, barcode="AI-GEP-1")

    _script(monkeypatch, [
        ("", [{"id": "t1", "name": "create_service_ticket",
               "args": {"title": "Nem ad ki kávét", "machine_barcode": "AI-GEP-1",
                        "priority": "high"}}]),
        ("Felvettem a szervizjegyet.", []),
    ])
    res = await client.post(
        "/api/assistant/chat",
        json={"messages": [{"role": "user", "content": "vegyel fel jegyet az AI-GEP-1 gephez"}]},
        headers=adm,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "Felvettem" in body["reply"]
    assert any("Szervizjegy" in e["label"] for e in body["events"])

    tickets = (await client.get("/api/service", headers=adm)).json()
    assert len(tickets) == 1
    assert tickets[0]["priority"] == "high"
    assert "AI-GEP-1" in (tickets[0]["asset_label"] or "")
    assert asset["id"]  # a gép létezik


async def test_chat_tool_permission_denied(client, employee_user, monkeypatch):
    _user, headers, _emp = employee_user
    await _enable_ai()
    _script(monkeypatch, [
        ("", [{"id": "t1", "name": "create_service_ticket", "args": {"title": "Próba"}}]),
        ("Sajnos ehhez nincs jogosultságod.", []),
    ])
    res = await client.post(
        "/api/assistant/chat",
        json={"messages": [{"role": "user", "content": "vegyel fel jegyet"}]},
        headers=headers,
    )
    assert res.status_code == 200
    # nem jött létre jegy, és nincs "created" esemény
    assert res.json()["events"] == []


async def test_chat_navigate_event(client, admin, monkeypatch):
    _, adm = admin
    await _enable_ai()
    _script(monkeypatch, [
        ("", [{"id": "t1", "name": "navigate", "args": {"path": "/szerviz"}}]),
        ("Megnyitottam a szerviz oldalt.", []),
    ])
    res = await client.post(
        "/api/assistant/chat",
        json={"messages": [{"role": "user", "content": "nyisd meg a szervizt"}]},
        headers=adm,
    )
    assert res.status_code == 200
    events = res.json()["events"]
    assert events and events[0]["kind"] == "navigate" and events[0]["path"] == "/szerviz"


async def test_navigate_rejects_unknown_path():
    result, event = await assistant._t_navigate(None, None, {"path": "https://gonosz.hu"})
    assert "error" in result and event is None


async def test_counter_tool_updates_asset(client, admin, monkeypatch):
    from tests.test_service import make_asset

    _, adm = admin
    await _enable_ai()
    asset = await make_asset(client, adm, barcode="AI-GEP-2")
    _script(monkeypatch, [
        ("", [{"id": "t1", "name": "report_counter",
               "args": {"machine_barcode": "AI-GEP-2", "counter": 4321}}]),
        ("Rögzítettem: 4321.", []),
    ])
    res = await client.post(
        "/api/assistant/chat",
        json={"messages": [{"role": "user", "content": "az AI-GEP-2 szamlaloja 4321"}]},
        headers=adm,
    )
    assert res.status_code == 200
    detail = (await client.get(f"/api/assets/{asset['id']}", headers=adm)).json()
    assert detail["counter"] == 4321
    assert any(m["action"] == "counter" for m in detail["movements"])


async def test_fill_worksheet_by_serial(client, admin, manager, monkeypatch):
    """Munkalap kitöltése sorszám alapján (tasks-joggal)."""
    from tests.conftest import make_employee_record, make_user

    _, adm = admin
    await _enable_ai()
    emp_user, _h = await make_user(email="szerelo@example.com", role="employee")
    emp = await make_employee_record(emp_user, last_name="Szerelő", first_name="Pál")

    created = await client.post(
        "/api/tasks",
        json={"title": "Vízkőtelenítés", "employee_id": str(emp.id),
              "due_date": "2026-08-11"},
        headers=adm,
    )
    assert created.status_code == 201, created.text
    serial = created.json()["worksheet_serial"]

    _script(monkeypatch, [
        ("", [{"id": "t1", "name": "fill_worksheet",
               "args": {"worksheet_serial": serial,
                        "work_description": "Vízkőtelenítés elvégezve, gép tesztelve.",
                        "hours": 2,
                        "materials": [{"name": "Vízkőoldó", "qty": "1", "unit": "db"}]}}]),
        ("Kitöltöttem a munkalapot.", []),
    ])
    res = await client.post(
        "/api/assistant/chat",
        json={"messages": [{"role": "user", "content": f"töltsd ki a {serial} munkalapot"}]},
        headers=adm,
    )
    assert res.status_code == 200, res.text
    assert any(serial in e["label"] for e in res.json()["events"])

    ws = (
        await client.get(f"/api/tasks/{created.json()['id']}/worksheet", headers=adm)
    ).json()
    assert "Vízkőtelenítés elvégezve" in ws["work_description"]
    assert ws["hours_spent"] == 2
    assert ws["materials"][0]["name"] == "Vízkőoldó"
