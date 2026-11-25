"""182. kör: Telegram-összekapcsolás egyszeri kóddal — saját kód, vezetői kód
a dolgozónak, lejárt kód, egyszer használatos, a törzsszám nem köt össze."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from tests.conftest import make_employee_record, make_user


async def _bot(monkeypatch, updates):
    import app.services.wfm.telegram as tg_mod

    sent: list[tuple[str, str]] = []

    async def fake_send(config, chat_id, text):
        sent.append((str(chat_id), text))
        return True

    async def fake_fetch(config, offset):
        return updates

    monkeypatch.setattr(tg_mod, "send_telegram", fake_send)
    monkeypatch.setattr(tg_mod, "fetch_updates", fake_fetch)
    return tg_mod, sent


async def test_link_with_one_time_code_only(client, admin, manager, monkeypatch):
    import app.db as app_db
    from app.models import Employee

    _, adm = admin
    _, mgr = manager
    res = await client.put("/api/settings/notifications", json={
        "daily_enabled": False, "send_hour": 6, "weekly_backup": False, "auto_receipt": False,
        "tg_enabled": True, "tg_token": "123:abc", "tg_chat_ids": "-100555",
    }, headers=adm)
    assert res.status_code == 200, res.text
    emp_user, emp_h = await make_user(email="kodos@example.com", role="employee")
    emp = await make_employee_record(emp_user, employee_code="424242")
    # a dolgozó saját kódot kér → a kartonra kerül
    r = await client.post("/api/auth/me/telegram-code", headers=emp_h)
    assert r.status_code == 200 and len(r.json()["code"]) == 6 and r.json()["ttl_minutes"] == 10
    code = r.json()["code"]
    factory = app_db.get_session_factory()
    async with factory() as db:
        e = (await db.execute(select(Employee).where(Employee.id == emp.id))).scalar_one()
        assert e.tg_link_code == code
    # törzsszám: ismeretlen; a kód: összekapcsol; ugyanaz a kód másodszor: már nem él
    tg_mod, sent = await _bot(monkeypatch, [
        {"update_id": 1, "message": {"chat": {"id": 501, "type": "private"}, "text": "424242"}},
        {"update_id": 2, "message": {"chat": {"id": 501, "type": "private"}, "text": code}},
        {"update_id": 3, "message": {"chat": {"id": 502, "type": "private"}, "text": code}},
    ])
    async with factory() as db:
        assert await tg_mod.process_updates(db) == 1
    assert any(c == "501" and "Ismeretlen vagy lejárt" in t for c, t in sent)
    assert any(c == "501" and "Összekapcsolva" in t for c, t in sent)
    assert any(c == "502" and "Ismeretlen vagy lejárt" in t for c, t in sent)
    assert (await client.get("/api/auth/me", headers=emp_h)).json()["telegram_linked"] is True
    async with factory() as db:
        e = (await db.execute(select(Employee).where(Employee.id == emp.id))).scalar_one()
        assert e.telegram_chat_id == "501" and e.tg_link_code is None

    # vezetői kód egy másik dolgozónak; lejárt kód nem köt össze
    u2, _h2 = await make_user(email="masik@example.com", role="employee")
    emp2 = await make_employee_record(u2, employee_code="434343")
    assert (await client.post(f"/api/employees/{emp2.id}/telegram-code", headers=emp_h)).status_code == 403
    r = await client.post(f"/api/employees/{emp2.id}/telegram-code", headers=mgr)
    assert r.status_code == 200
    code2 = r.json()["code"]
    async with factory() as db:
        e2 = (await db.execute(select(Employee).where(Employee.id == emp2.id))).scalar_one()
        e2.tg_link_code_expires = datetime.now(UTC) - timedelta(minutes=1)
        await db.commit()
    tg_mod, sent = await _bot(monkeypatch, [{"update_id": 4, "message": {"chat": {"id": 503, "type": "private"}, "text": code2}}])
    async with factory() as db:
        assert await tg_mod.process_updates(db) == 0
    assert any(c == "503" and "lejárt" in t for c, t in sent)
    # friss kód → megy
    code3 = (await client.post(f"/api/employees/{emp2.id}/telegram-code", headers=mgr)).json()["code"]
    tg_mod, sent = await _bot(monkeypatch, [{"update_id": 5, "message": {"chat": {"id": 503, "type": "private"}, "text": f"/start {code3}"}}])
    async with factory() as db:
        assert await tg_mod.process_updates(db) == 1
    # /start kód nélkül: útmutató
    tg_mod, sent = await _bot(monkeypatch, [{"update_id": 6, "message": {"chat": {"id": 504, "type": "private"}, "text": "/start"}}])
    async with factory() as db:
        assert await tg_mod.process_updates(db) == 0
    assert any(c == "504" and "kérj egy kódot" in t for c, t in sent)
