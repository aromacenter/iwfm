"""Auth flow: bootstrap, login, throttle, session."""

from tests.conftest import make_user


async def test_bootstrap_creates_first_admin(client):
    res = await client.post(
        "/api/auth/bootstrap",
        json={"email": "first@example.com", "password": "Nagyon-Eros-Jelszo-1", "display_name": "Első Admin"},
    )
    assert res.status_code == 200
    assert res.json()["role"] == "admin"

    # másodszor már zárva
    res2 = await client.post(
        "/api/auth/bootstrap",
        json={"email": "second@example.com", "password": "Nagyon-Eros-Jelszo-2", "display_name": "X"},
    )
    assert res2.status_code == 403


async def test_login_ok_and_me(client):
    await make_user(email="login@example.com", role="manager", password="Titkos-Jelszo-99")
    res = await client.post(
        "/api/auth/login", json={"email": "login@example.com", "password": "Titkos-Jelszo-99"}
    )
    assert res.status_code == 200
    # cookie-alapú session működik
    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "login@example.com"


async def test_login_bad_password_uniform_error(client):
    await make_user(email="victim@example.com", role="employee")
    res = await client.post(
        "/api/auth/login", json={"email": "victim@example.com", "password": "rossz-jelszo-123"}
    )
    assert res.status_code == 401
    # nem létező email is ugyanazt a hibát adja (user enumeration ellen)
    res2 = await client.post(
        "/api/auth/login", json={"email": "nincs@example.com", "password": "rossz-jelszo-123"}
    )
    assert res2.status_code == 401
    assert res.json()["detail"]["code"] == res2.json()["detail"]["code"]


async def test_login_throttled_after_5_failures(client):
    await make_user(email="brute@example.com", role="employee")
    for _ in range(5):
        await client.post(
            "/api/auth/login", json={"email": "brute@example.com", "password": "wrong-pass-123"}
        )
    res = await client.post(
        "/api/auth/login", json={"email": "brute@example.com", "password": "wrong-pass-123"}
    )
    assert res.status_code == 429


async def test_me_requires_auth(client):
    res = await client.get("/api/auth/me")
    assert res.status_code == 401


async def test_logout_invalidates_token_server_side(client):
    """A kijelentkezés a token_version emelésével az ellopott/megőrzött
    tokent is érvényteleníti — nem csak a sütit törli."""
    _, headers = await make_user(
        email="kilepo@example.com", role="manager", password="Titkos-Jelszo-77"
    )
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200
    res = await client.post("/api/auth/logout", headers=headers)
    assert res.status_code == 200
    # ugyanaz a Bearer token többé nem érvényes
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401


async def test_login_email_throttle_ip_independent():
    """IP-váltogatással (hamis X-Forwarded-For) sem lehet korlátlanul
    próbálkozni egy fiókra: fiókonkénti, IP-független korlát is él."""
    from app.api import auth as auth_module

    auth_module._failed_logins.clear()
    for _ in range(auth_module._MAX_EMAIL_ATTEMPTS):
        auth_module._record_failure("email:celpont@example.com")
    assert auth_module._throttled(
        "email:celpont@example.com",
        auth_module._MAX_EMAIL_ATTEMPTS,
        auth_module._EMAIL_WINDOW_SECONDS,
    )
    auth_module._failed_logins.clear()


async def test_inactive_user_rejected(client):
    import app.db as app_db
    from sqlalchemy import update

    from app.models import User

    user, headers = await make_user(email="inaktiv@example.com", role="employee")
    factory = app_db.get_session_factory()
    async with factory() as session:
        await session.execute(update(User).where(User.id == user.id).values(is_active=False))
        await session.commit()
    res = await client.get("/api/auth/me", headers=headers)
    assert res.status_code == 401
