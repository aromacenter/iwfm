"""150. kör (biztonsági javítás): a kétlépcsős belépés küldési hibánál zárva
marad — „összekapcsoltaknak” módban sem enged be jelszóval, ha a kód nem megy
ki —, és fiókonként korlátos a kód-kérések száma (kód-áradat ellen)."""

from __future__ import annotations

import pytest

from tests.conftest import make_employee_record, make_user

PW = "Titkos-Jelszo-99"


@pytest.fixture()
def channels(monkeypatch):
    from app.services.wfm import email_service as es
    from app.services.wfm import telegram as tg

    state = {"tg_ok": True, "smtp": False, "sent": []}

    async def fake_tg_config(db):
        return {"token": "x"}

    async def fake_send_telegram(config, chat_id, text):
        if not state["tg_ok"]:
            return False
        state["sent"].append(("telegram", chat_id, text))
        return True

    async def fake_smtp_config(db):
        return {"host": "smtp.example.com"} if state["smtp"] else None

    async def fake_send_email(config, to, subject, body, attachments=None):
        state["sent"].append(("email", to, body))
        return True

    monkeypatch.setattr(tg, "load_telegram_config", fake_tg_config)
    monkeypatch.setattr(tg, "send_telegram", fake_send_telegram)
    monkeypatch.setattr(es, "load_smtp_config", fake_smtp_config)
    monkeypatch.setattr(es, "send_email", fake_send_email)
    return state


async def _login(client, email):
    client.cookies.clear()
    return await client.post("/api/auth/login", json={"email": email, "password": PW})


async def test_linked_mode_send_failure_fails_closed(client, channels):
    _, adm = await make_user(email="admin150@example.com", role="admin", password=PW)
    user, _ = await make_user(email="osszekapcsolt@example.com", role="employee", password=PW)
    await make_employee_record(user, telegram_chat_id="555150")
    await make_user(email="nincs-tg150@example.com", role="employee", password=PW)

    res = await client.put("/api/settings/login-2fa", json={"mode": "linked"}, headers=adm)
    assert res.status_code == 200, res.text

    # rendben küldhető: kód-lépés jön
    res = await _login(client, "osszekapcsolt@example.com")
    assert res.status_code == 200 and res.json()["twofa_required"] is True

    # a Telegram-küldés elakad (pl. a bot túlterhelve), e-mail tartalék nincs →
    # NINCS belépés jelszóval, és munkamenet-süti sem jár
    channels["tg_ok"] = False
    res = await _login(client, "osszekapcsolt@example.com")
    assert res.status_code == 503 and res.json()["detail"]["code"] == "auth.twofa_send_failed"
    assert not res.cookies
    assert (await client.get("/api/auth/me")).status_code == 401

    # akinek nincs összekapcsolt Telegramja, annak ebben a módban továbbra is elég a jelszó
    res = await _login(client, "nincs-tg150@example.com")
    assert res.status_code == 200 and res.json()["email"] == "nincs-tg150@example.com"

    # ha van e-mail tartalék, a Telegram-hiba e-mailes kódra vált (nem kizárás)
    channels["smtp"] = True
    res = await _login(client, "osszekapcsolt@example.com")
    assert res.status_code == 200 and res.json()["twofa_required"] is True and res.json()["channel"] == "email"

    # a naplóban ott a sikertelen küldés
    logs = (await client.get("/api/audit?action=auth.twofa_send_failed", headers=adm)).json()
    rows = logs if isinstance(logs, list) else logs.get("items", [])
    assert any(r.get("action") == "auth.twofa_send_failed" for r in rows)


async def test_challenge_rate_limit_per_account(client, channels):
    from app.services.wfm import login_2fa as twofa

    _, adm = await make_user(email="admin150b@example.com", role="admin", password=PW)
    user, _ = await make_user(email="aradat@example.com", role="employee", password=PW)
    await make_employee_record(user, telegram_chat_id="555151")
    other, _ = await make_user(email="masik150@example.com", role="employee", password=PW)
    await make_employee_record(other, telegram_chat_id="555152", employee_code="150152")
    res = await client.put("/api/settings/login-2fa", json={"mode": "linked"}, headers=adm)
    assert res.status_code == 200, res.text

    for _ in range(twofa.CHALLENGE_LIMIT):
        res = await _login(client, "aradat@example.com")
        assert res.status_code == 200 and res.json()["twofa_required"] is True
    sent_before = len(channels["sent"])
    res = await _login(client, "aradat@example.com")
    assert res.status_code == 429 and res.json()["detail"]["code"] == "auth.rate_limited"
    assert len(channels["sent"]) == sent_before  # több kód nem megy ki
    # a korlát fiókonkénti: másik fiók zavartalanul kap kódot
    res = await _login(client, "masik150@example.com")
    assert res.status_code == 200 and res.json()["twofa_required"] is True
