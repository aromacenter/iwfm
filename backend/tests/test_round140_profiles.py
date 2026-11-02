"""140. kör: iparág-profil + példány-szintű modul-kapcsolók — rejtés a licencen
belül, függőség visszahozza, az API-kapu is tiltja, az üzemeltetői licenc-frissítés
nem törli a rejtést; profil-választás egy lépésben."""

from __future__ import annotations

from tests.test_parts import _enable_modules


async def test_hidden_modules_and_industry_profile(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    # licenc: minden modul (plan xl, lista nélkül)
    await _enable_modules(None)
    res = await client.get("/api/settings/modules", headers=adm)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["licensed"] is None and body["hidden"] == [] and body["effective"] is None
    assert "tools" in body["catalog"] and body["requires"]["tools"] == ["employees"]

    # a cég elrejti a szerszámokat + a dolgozókat → a dolgozók visszajönnek (tasks igényli)
    res = await client.put("/api/settings/modules", json={"hidden": ["tools", "employees"]}, headers=adm)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["hidden"] == ["employees", "tools"]
    assert "tools" not in body["effective"] and "employees" in body["effective"]
    # az API-kapu is tilt: szerszámok 403, feladatok mennek
    assert (await client.get("/api/tools", headers=mgr)).status_code == 403
    assert (await client.get("/api/tasks", headers=mgr)).status_code == 200
    # a státusz-végpont (menü) ugyanazt mutatja
    st = (await client.get("/api/settings/license/status", headers=adm)).json()
    assert st["modules"] is not None and "tools" not in st["modules"]
    # ismeretlen kulcs → 422
    assert (await client.put("/api/settings/modules", json={"hidden": ["nincs_ilyen"]}, headers=adm)).status_code == 422
    # nem admin → 403
    assert (await client.put("/api/settings/modules", json={"hidden": []}, headers=mgr)).status_code == 403

    # iparág-profil: HR → csak a HR-modulok (és függőségeik) maradnak
    res = await client.post("/api/settings/industry-profile", json={"profile": "hr"}, headers=adm)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["profile"] == "hr"
    assert "settlements" in body["hidden"] and "service" in body["hidden"]
    assert set(body["effective"]) >= {"employees", "schedule", "attendance", "timeoff", "payroll"}
    assert "settlements" not in body["effective"]
    assert (await client.get("/api/settlements", headers=mgr)).status_code == 403
    assert (await client.post("/api/settings/industry-profile", json={"profile": "bank"}, headers=adm)).status_code == 422

    # az üzemeltetői licenc-frissítés (enabled_modules) nem törli a céges rejtést
    await _enable_modules(["employees", "tasks", "service", "machines", "partners", "settlements", "products"])
    body = (await client.get("/api/settings/modules", headers=adm)).json()
    assert body["profile"] == "hr" and "settlements" in body["hidden"]
    assert "settlements" not in body["effective"] and "employees" in body["effective"]

    # rejtés visszavonása → a licencelt lista a hatályos
    res = await client.put("/api/settings/modules", json={"hidden": []}, headers=adm)
    assert res.json()["hidden"] == [] and "settlements" in res.json()["effective"]
