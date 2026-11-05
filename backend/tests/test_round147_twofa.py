"""147. kör: kétlépcsős belépés — jelszó + Telegram-kód (tartalék: e-mail).
Módok (off / linked / required), hibás kód és próbálkozás-korlát, lejárat,
megbízható eszköz, újraküldés, beállítás-védelem, üzemeltetői vészkapcsoló."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import make_employee_record, make_user

PW = "Titkos-Jelszo-99"


@pytest.fixture()
def channels(monkeypatch):
    """Telegram + e-mail küldés elfogása; a konfigurációk kapcsolhatók."""
    from app.services.wfm import email_service as es
    from app.services.wfm import telegram as tg

    state = {"tg": True, "smtp": True, "tg_ok": True, "sent": []}

    async def fake_tg_config(db):
        return {"token": "x"} if state["tg"] else None

    async def fake_send_telegram(config, chat_id, text):
        if not state["tg_ok"]:
            return False
        state["sent"].append(("telegram", chat_id, text))
        return True

    async def fake_smtp(db):
        return object() if state["smtp"] else None

    async def fake_send_email(config, to, subject, body, attachments=None):
        state["sent"].append(("email", to, body))
        return True

    monkeypatch.setattr(tg, "load_telegram_config", fake_tg_config)
    monkeypatch.setattr(tg, "send_telegram", fake_send_telegram)
    monkeypatch.setattr(es, "load_smtp_config", fake_smtp)
    monkeypatch.setattr(es, "send_email", fake_send_email)
    return state


def _code(state) -> str:
    return re.search(r"\b(\d{6})\b", state["sent"][-1][2]).group(1)


async def _login(client, email):
    client.cookies.clear()
    return await client.post("/api/auth/login", json={"email": email, "password": PW})


async def test_twofa_linked_mode_flow(client, admin, channels):
    _, adm = admin
    user, _h = await make_user(email="ketlepcsos@example.com", role="employee", password=PW)
    await make_employee_record(user, telegram_chat_id="555001")
    await make_user(email="nincs-telegram@example.com", role="employee", password=PW)

    # kikapcsolva: sima belépés
    res = await _login(client, "ketlepcsos@example.com")
    assert res.status_code == 200 and res.json()["email"] == "ketlepcsos@example.com"

    # bekapcsolás: 'linked'
    res = await client.put("/api/settings/login-2fa", json={"mode": "linked"}, headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()["mode"] == "linked" and res.json()["telegram_linked"] == 1

    # akinek nincs Telegramja, jelszóval belép
    res = await _login(client, "nincs-telegram@example.com")
    assert res.status_code == 200 and res.json()["email"] == "nincs-telegram@example.com"

    # akinek van: kód megy a Telegramra, süti még nincs
    res = await _login(client, "ketlepcsos@example.com")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["twofa_required"] is True and body["channel"] == "telegram" and "email" not in body
    assert channels["sent"][-1][0] == "telegram" and channels["sent"][-1][1] == "555001"
    assert (await client.get("/api/auth/me")).status_code == 401
    cid = body["challenge_id"]

    # hibás kód: 401 + hátralévő próbák
    res = await client.post("/api/auth/login/verify", json={"challenge_id": cid, "code": "000000" if _code(channels) != "000000" else "111111"})
    assert res.status_code == 401 and res.json()["detail"]["code"] == "auth.twofa_bad_code" and res.json()["detail"]["attempts_left"] == 4

    # túl korai újraküldés: 429
    assert (await client.post("/api/auth/login/resend", json={"challenge_id": cid})).status_code == 429

    # jó kód + megbízható eszköz → belépve, a következő belépés nem kér kódot
    res = await client.post("/api/auth/login/verify", json={"challenge_id": cid, "code": _code(channels), "trust_device": True})
    assert res.status_code == 200, res.text
    assert res.json()["email"] == "ketlepcsos@example.com"
    assert (await client.get("/api/auth/me")).status_code == 200
    trusted = client.cookies.get("wfm_trusted")
    assert trusted
    # a felhasznált kihívás nem használható újra
    assert (await client.post("/api/auth/login/verify", json={"challenge_id": cid, "code": _code(channels)})).status_code == 401

    client.cookies.clear()
    client.cookies.set("wfm_trusted", trusted)
    res = await client.post("/api/auth/login", json={"email": "ketlepcsos@example.com", "password": PW})
    assert res.status_code == 200 and res.json().get("twofa_required") is None and res.json()["email"] == "ketlepcsos@example.com"

    # a megbízható süti másik fiókra nem érvényes
    other, _ = await make_user(email="masik@example.com", role="employee", password=PW)
    await make_employee_record(other, telegram_chat_id="555002", last_name="Másik", first_name="Béla")
    client.cookies.clear()
    client.cookies.set("wfm_trusted", trusted)
    res = await client.post("/api/auth/login", json={"email": "masik@example.com", "password": PW})
    assert res.json().get("twofa_required") is True


async def test_twofa_attempt_limit_and_expiry(client, admin, channels):
    import app.db as app_db
    from sqlalchemy import select

    from app.models import LoginChallenge

    _, adm = admin
    user, _h = await make_user(email="probalgato@example.com", role="employee", password=PW)
    await make_employee_record(user, telegram_chat_id="555010")
    assert (await client.put("/api/settings/login-2fa", json={"mode": "linked"}, headers=adm)).status_code == 200

    cid = (await _login(client, "probalgato@example.com")).json()["challenge_id"]
    good = _code(channels)
    bad = "000000" if good != "000000" else "111111"
    for i in range(5):
        res = await client.post("/api/auth/login/verify", json={"challenge_id": cid, "code": bad})
        assert res.status_code == 401
    # 5 hibás után a JÓ kód sem megy
    res = await client.post("/api/auth/login/verify", json={"challenge_id": cid, "code": good})
    assert res.status_code == 401 and res.json()["detail"]["code"] == "auth.twofa_expired"

    # lejárt kihívás
    cid = (await _login(client, "probalgato@example.com")).json()["challenge_id"]
    good = _code(channels)
    factory = app_db.get_session_factory()
    async with factory() as s:
        ch = (await s.execute(select(LoginChallenge).where(LoginChallenge.id == __import__("uuid").UUID(cid)))).scalar_one()
        assert ch.code_hash != good and len(ch.code_hash) == 64  # csak hash tárolódik
        ch.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await s.commit()
    res = await client.post("/api/auth/login/verify", json={"challenge_id": cid, "code": good})
    assert res.status_code == 401 and res.json()["detail"]["code"] == "auth.twofa_expired"
    assert (await client.post("/api/auth/login/verify", json={"challenge_id": "nem-uuid-xx", "code": "123456"})).status_code == 401


async def test_twofa_required_mode_email_fallback_and_guards(client, admin, channels, monkeypatch):
    from app.core.config import get_settings

    admin_user, adm = admin
    # kötelező mód: a beállító adminnak kell csatorna — e-mail-küldés nélkül és Telegram nélkül tiltva
    channels["smtp"] = False
    res = await client.put("/api/settings/login-2fa", json={"mode": "required"}, headers=adm)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "settings.twofa_admin_no_channel"
    # bot nélkül be sem kapcsolható a 'linked'
    channels["tg"] = False
    res = await client.put("/api/settings/login-2fa", json={"mode": "linked"}, headers=adm)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "settings.twofa_no_channel"
    channels["tg"] = True
    channels["smtp"] = True
    res = await client.put("/api/settings/login-2fa", json={"mode": "required"}, headers=adm)
    assert res.status_code == 200 and res.json()["mode"] == "required"

    # Telegram nélküli fiók: e-mailben kapja a kódot
    await make_user(email="emailes@example.com", role="employee", password=PW)
    res = await _login(client, "emailes@example.com")
    assert res.status_code == 200 and res.json()["channel"] == "email" and res.json()["hint"].endswith("@example.com")
    assert channels["sent"][-1][0] == "email" and channels["sent"][-1][1] == "emailes@example.com"
    res = await client.post("/api/auth/login/verify", json={"challenge_id": res.json()["challenge_id"], "code": _code(channels)})
    assert res.status_code == 200

    # Telegram-küldési hiba → e-mail tartalék
    tg_user, _ = await make_user(email="tg-hibas@example.com", role="employee", password=PW)
    await make_employee_record(tg_user, telegram_chat_id="555020", last_name="Hibás", first_name="Tódor")
    channels["tg_ok"] = False
    res = await _login(client, "tg-hibas@example.com")
    assert res.status_code == 200 and res.json()["channel"] == "email"
    channels["tg_ok"] = True

    # se Telegram, se e-mail-küldés → kötelező módban nincs belépés
    channels["smtp"] = False
    res = await _login(client, "emailes@example.com")
    assert res.status_code == 403 and res.json()["detail"]["code"] == "auth.twofa_unavailable"

    # üzemeltetői vészkapcsoló: kikapcsolja → újra megy a jelszavas belépés
    monkeypatch.setattr(get_settings(), "operator_token", "flotta-titok")
    res = await client.put("/api/operator/login-2fa", json={"mode": "off"}, headers={"X-Operator-Token": "flotta-titok"})
    assert res.status_code == 200 and res.json()["mode"] == "off"
    res = await _login(client, "emailes@example.com")
    assert res.status_code == 200 and res.json()["email"] == "emailes@example.com"
    # jogok: nem admin nem állíthatja
    _, emp_h = await make_user(email="sima-2fa@example.com", role="employee", password=PW)
    assert (await client.put("/api/settings/login-2fa", json={"mode": "off"}, headers=emp_h)).status_code == 403
    assert (await client.put("/api/operator/login-2fa", json={"mode": "off"})).status_code == 403
