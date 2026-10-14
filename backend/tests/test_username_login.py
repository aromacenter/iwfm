"""Belépés felhasználónévvel (email helyett) — minden szerepkörre:
létrehozás csak felhasználónévvel, login mindkét azonosítóval, egyediség,
felhasználónév-csere és törlés PATCH-csel."""

from __future__ import annotations


async def _create(client, adm, **extra):
    body = {
        "last_name": "Teszt", "first_name": "Béla", "role": "uzletkoto",
        "hire_date": "2026-01-01", "initial_password": "Nagyon-Titkos-12",
        **extra,
    }
    return await client.post("/api/employees", json=body, headers=adm)


async def test_username_only_account_and_login(client, admin):
    _, adm = admin

    # se email, se felhasználónév → 422
    res = await _create(client, adm)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "employee.login_id_required"

    # csak felhasználónévvel létrehozható (email nélkül)
    res = await _create(client, adm, username="Teszt.Bela")
    assert res.status_code == 201, res.text
    emp = res.json()
    assert emp["email"] is None
    assert emp["username"] == "teszt.bela"  # kisbetűsítve

    # belépés felhasználónévvel (kis-nagybetű mindegy)
    res = await client.post(
        "/api/auth/login",
        json={"email": "Teszt.Bela", "password": "Nagyon-Titkos-12"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["username"] == "teszt.bela"

    # duplikált felhasználónév → 409
    res = await _create(client, adm, username="teszt.bela", first_name="Máz")
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "employee.username_taken"

    # érvénytelen felhasználónév (@ vagy túl rövid) → 422
    res = await _create(client, adm, username="a@b", first_name="Rossz")
    assert res.status_code == 422
    res = await _create(client, adm, username="ab", first_name="Rövid")
    assert res.status_code == 422


async def test_email_login_still_works_and_username_patch(client, admin, manager):
    mgr_user, _ = manager
    _, adm = admin

    # a meglévő email-es belépés változatlan
    res = await client.post(
        "/api/auth/login",
        json={"email": mgr_user.email, "password": "Passw0rd-12345"},
    )
    assert res.status_code == 200, res.text

    # email + felhasználónév együtt is mehet; PATCH-csel cserélhető/törölhető
    created = (
        await _create(client, adm, email="kettos@example.com", username="kettos")
    ).json()
    res = await client.patch(
        f"/api/employees/{created['id']}", json={"username": "uj.nev"}, headers=adm,
    )
    assert res.status_code == 200, res.text
    assert res.json()["username"] == "uj.nev"
    # a régi névvel már nem, az újjal igen
    res = await client.post(
        "/api/auth/login", json={"email": "kettos", "password": "Nagyon-Titkos-12"},
    )
    assert res.status_code == 401
    res = await client.post(
        "/api/auth/login", json={"email": "uj.nev", "password": "Nagyon-Titkos-12"},
    )
    assert res.status_code == 200
    # törlés "-" jellel → marad az email-es belépés
    res = await client.patch(
        f"/api/employees/{created['id']}", json={"username": "-"}, headers=adm,
    )
    assert res.status_code == 200
    assert res.json()["username"] is None
    res = await client.post(
        "/api/auth/login",
        json={"email": "kettos@example.com", "password": "Nagyon-Titkos-12"},
    )
    assert res.status_code == 200
