"""Beállítások: SMTP konfiguráció (titkosított jelszó, maszkolt GET) + skillek."""

from sqlalchemy import select

import app.db as app_db
from app.models import EmailSettings


async def test_email_settings_roundtrip_password_never_returned(client, admin):
    _, headers = admin
    res = await client.put(
        "/api/settings/email",
        json={
            "enabled": True,
            "host": "smtp.example.com",
            "port": 587,
            "username": "noreply@example.com",
            "password": "szuper-titkos-jelszo",
            "from_address": "noreply@example.com",
            "use_tls": True,
        },
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["has_password"] is True
    assert "password" not in body  # a jelszó SOSEM megy vissza

    # titkosítva tárolódik
    factory = app_db.get_session_factory()
    async with factory() as session:
        row = (await session.execute(select(EmailSettings))).scalar_one()
        assert row.password_encrypted is not None
        assert b"szuper-titkos-jelszo" not in row.password_encrypted

    # üres jelszóval mentve a régi megmarad
    res2 = await client.put(
        "/api/settings/email",
        json={
            "enabled": True,
            "host": "smtp.example.com",
            "port": 587,
            "username": "noreply@example.com",
            "from_address": "noreply@example.com",
            "use_tls": True,
        },
        headers=headers,
    )
    assert res2.json()["has_password"] is True


async def test_email_test_requires_config(client, admin):
    _, headers = admin
    res = await client.post(
        "/api/settings/email/test", json={"to": "admin@example.com"}, headers=headers
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "settings.email_not_configured"


async def test_email_settings_admin_only(client, manager):
    _, headers = manager
    assert (await client.get("/api/settings/email", headers=headers)).status_code == 403


async def test_skills_crud_and_assignment(client, admin):
    _, headers = admin
    # létrehozás
    s1 = await client.post("/api/settings/skills", json={"name": "targoncavezető"}, headers=headers)
    s2 = await client.post("/api/settings/skills", json={"name": "pénztár"}, headers=headers)
    assert s1.status_code == 201 and s2.status_code == 201

    # duplikált név
    dup = await client.post("/api/settings/skills", json={"name": "pénztár"}, headers=headers)
    assert dup.status_code == 409

    # lista
    skills = (await client.get("/api/settings/skills", headers=headers)).json()
    assert [s["name"] for s in skills] == ["pénztár", "targoncavezető"]

    # dolgozóhoz rendelés
    emp = await client.post(
        "/api/employees",
        json={
            "email": "skilles@example.com",
            "last_name": "Skill",
            "first_name": "Soma",
            "hire_date": "2026-01-01",
            "skill_ids": [s1.json()["id"], s2.json()["id"]],
        },
        headers=headers,
    )
    assert emp.status_code == 201
    assert sorted(s["name"] for s in emp.json()["skills"]) == ["pénztár", "targoncavezető"]

    # csere PATCH-csel
    patched = await client.patch(
        f"/api/employees/{emp.json()['id']}",
        json={"skill_ids": [s1.json()["id"]]},
        headers=headers,
    )
    assert [s["name"] for s in patched.json()["skills"]] == ["targoncavezető"]

    # skill törlés → lekerül a dolgozóról is
    await client.delete(f"/api/settings/skills/{s1.json()['id']}", headers=headers)
    detail = await client.get(f"/api/employees/{emp.json()['id']}", headers=headers)
    assert detail.json()["skills"] == []


async def test_skills_create_admin_only(client, manager):
    _, headers = manager
    # manager láthatja a listát…
    assert (await client.get("/api/settings/skills", headers=headers)).status_code == 200
    # …de nem hozhat létre
    assert (
        await client.post("/api/settings/skills", json={"name": "x"}, headers=headers)
    ).status_code == 403
