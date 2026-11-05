"""148. kör: saját szám a karton nélküli (admin/vezetői) fiókoknak is — 6 jegyű
fiók-szám a /me-ben, Telegram-összekapcsolás a bottal ugyanúgy a számmal,
eltérítés-védelem, a kétlépcsős belépés a fiók-szintű kapcsolatot is használja,
saját kapcsolat bontása; kartonos fióknál a szám a törzsszám."""

from __future__ import annotations

import re

from tests.conftest import make_employee_record, make_user

PW = "Titkos-Jelszo-99"


async def test_admin_own_number_telegram_link_and_twofa(client, monkeypatch):
    import app.services.wfm.telegram as tg_mod
    from app import db as app_db

    admin_user, adm = await make_user(email="foadmin@example.com", role="admin", password=PW, display_name="Fő Admin")

    # saját szám: 6 jegy, stabil; kezdetben nincs Telegram
    me = (await client.get("/api/auth/me", headers=adm)).json()
    code = me["user_code"]
    assert re.fullmatch(r"\d{6}", code) and me["telegram_linked"] is False
    assert (await client.get("/api/auth/me", headers=adm)).json()["user_code"] == code

    # kartonos fiók: a száma a törzsszáma
    emp_user, emp_h = await make_user(email="kartonos@example.com", role="employee", password=PW)
    await make_employee_record(emp_user, employee_code="654321")
    assert (await client.get("/api/auth/me", headers=emp_h)).json()["user_code"] == "654321"
    # egy másik karton nélküli fiók más számot kap
    _, mgr_h = await make_user(email="vezeto@example.com", role="manager", password=PW)
    mgr_code = (await client.get("/api/auth/me", headers=mgr_h)).json()["user_code"]
    assert re.fullmatch(r"\d{6}", mgr_code) and mgr_code not in (code, "654321")

    # Telegram-bot bekapcsolva
    res = await client.put("/api/settings/notifications", json={
        "daily_enabled": False, "send_hour": 6, "weekly_backup": False, "auto_receipt": False,
        "tg_enabled": True, "tg_token": "123:abc", "tg_chat_ids": "-100555",
    }, headers=adm)
    assert res.status_code == 200, res.text

    sent: list[tuple[str, str]] = []

    async def fake_send(config, chat_id, text):
        sent.append((str(chat_id), text))
        return True

    monkeypatch.setattr(tg_mod, "send_telegram", fake_send)

    async def fetch_link(config, offset):
        return [{"update_id": 20, "message": {"chat": {"id": 888, "type": "private"}, "text": code}}]

    monkeypatch.setattr(tg_mod, "fetch_updates", fetch_link)
    factory = app_db.get_session_factory()
    async with factory() as session:
        assert await tg_mod.process_updates(session) == 1
    assert sent[-1][0] == "888" and "belépési kódokat" in sent[-1][1] and "Fő Admin" in sent[-1][1]
    assert (await client.get("/api/auth/me", headers=adm)).json()["telegram_linked"] is True

    # eltérítés: másik csevegés ugyanazzal a számmal → nem írja felül, mindkettő értesül
    async def fetch_hijack(config, offset):
        return [{"update_id": 21, "message": {"chat": {"id": 999, "type": "private"}, "text": code}}]

    monkeypatch.setattr(tg_mod, "fetch_updates", fetch_hijack)
    async with factory() as session:
        assert await tg_mod.process_updates(session) == 0
    assert any(c == "999" and "már össze van kapcsolva" in t for c, t in sent)
    assert any(c == "888" and "Valaki a te számoddal" in t for c, t in sent)

    # kétlépcsős belépés: az admin a fiók-szintű Telegramjára kapja a kódot
    res = await client.put("/api/settings/login-2fa", json={"mode": "linked"}, headers=adm)
    assert res.status_code == 200 and res.json()["telegram_linked"] == 1
    client.cookies.clear()
    res = await client.post("/api/auth/login", json={"email": "foadmin@example.com", "password": PW})
    assert res.status_code == 200 and res.json()["twofa_required"] is True and res.json()["channel"] == "telegram"
    assert sent[-1][0] == "888"
    login_code = re.search(r"\b(\d{6})\b", sent[-1][1]).group(1)
    res = await client.post("/api/auth/login/verify", json={"challenge_id": res.json()["challenge_id"], "code": login_code})
    assert res.status_code == 200 and res.json()["email"] == "foadmin@example.com" and res.json()["user_code"] == code

    # saját kapcsolat bontása → linked módban újra elég a jelszó
    res = await client.post("/api/auth/me/telegram-unlink")
    assert res.status_code == 200
    assert (await client.get("/api/auth/me")).json()["telegram_linked"] is False
    client.cookies.clear()
    res = await client.post("/api/auth/login", json={"email": "foadmin@example.com", "password": PW})
    assert res.status_code == 200 and res.json().get("twofa_required") is None

    # a törzsszám-generátor nem oszt ki már foglalt fiók-számot
    import secrets

    from app.services.wfm import codes

    seq = iter([int(code) - 100000, 222222 - 100000])
    monkeypatch.setattr(secrets, "randbelow", lambda n: next(seq))
    async with factory() as session:
        assert await codes.generate_unique_employee_code(session) == "222222"
