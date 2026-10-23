"""RBAC v2: saját szerepkör létrehozása és személyre szabása, művelet-szintű
tiltás (megtekintés engedett, módosítás tiltott), tiltás naplózása,
vezetői szintű saját szerepkör a „csak vezető” szabályoknál."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from tests.conftest import make_employee_record, make_user


async def _audit_denied(client, adm):
    rows = (await client.get("/api/audit?limit=50", headers=adm)).json()
    items = rows if isinstance(rows, list) else rows.get("items", [])
    return [r for r in items if r.get("action") == "auth.denied"]


async def test_custom_role_actions_and_denied_log(client, admin, manager):
    _, adm = admin
    _, mgr = manager

    # saját szerepkör: auditor — a manager jogaival indul, 0-s (dolgozói) szinten
    res = await client.post(
        "/api/settings/roles",
        json={"key": "auditor", "name": "Auditor", "description": "Csak néz, nem nyúl.", "level": 0, "copy_from": "manager"},
        headers=adm,
    )
    assert res.status_code == 201, res.text
    payload = res.json()
    assert any(r["key"] == "auditor" and not r["is_system"] for r in payload["roles"])
    assert "tasks" in payload["matrix"]["auditor"]
    # duplikált kulcs / beépített kulcs → 409
    assert (await client.post("/api/settings/roles", json={"key": "auditor", "name": "x"}, headers=adm)).status_code == 409
    assert (await client.post("/api/settings/roles", json={"key": "manager", "name": "x"}, headers=adm)).status_code == 409
    # a szerepkör-lista (dolgozó-űrlaphoz) bárkinek
    keys = {r["key"] for r in (await client.get("/api/settings/roles", headers=mgr)).json()}
    assert {"admin", "manager", "uzletkoto", "szervizes", "employee", "auditor"} <= keys

    # felhasználó az új szerepkörben (a users.role bármely roles.key lehet)
    aud_user, aud = await make_user(email="auditor@example.com", role="auditor")
    me = (await client.get("/api/auth/me", headers=aud)).json()
    assert me["role"] == "auditor" and me["role_name"] == "Auditor" and me["level"] == 0
    assert "tasks" in me["permissions"] and me["denied_actions"] == []

    # feladatok: megtekintés engedett, létrehozás/módosítás/törlés tiltott
    res = await client.put(
        "/api/settings/permissions",
        json={"matrix": {"auditor": payload["matrix"]["auditor"]}, "actions": {"auditor": {"tasks": ["view"]}}},
        headers=adm,
    )
    assert res.status_code == 200, res.text
    assert res.json()["actions"]["auditor"]["tasks"] == ["view"]
    me = (await client.get("/api/auth/me", headers=aud)).json()
    assert set(me["denied_actions"]) == {"tasks.create", "tasks.edit", "tasks.delete"}

    emp_user, _ = await make_user(email="rbac-emp@example.com", role="employee")
    emp = await make_employee_record(emp_user)
    assert (await client.get("/api/tasks", headers=aud)).status_code == 200
    res = await client.post(
        "/api/tasks", json={"title": "Tiltott", "employee_id": str(emp.id), "due_date": date.today().isoformat()}, headers=aud,
    )
    assert res.status_code == 403, res.text
    assert res.json()["detail"] == {"code": "auth.forbidden_action", "feature": "tasks", "action": "create"}
    # a tiltás naplózva (ki, mit, hol)
    denied = await _audit_denied(client, adm)
    assert denied and denied[0]["detail"]["feature"] == "tasks" and denied[0]["detail"]["action"] == "create"
    assert denied[0]["detail"]["path"] == "/api/tasks" and denied[0]["detail"]["role"] == "auditor"

    # funkció teljes megvonása → megtekintés is 403, és a napló nő
    res = await client.put("/api/settings/permissions", json={"matrix": {"auditor": ["dashboard"]}}, headers=adm)
    assert res.status_code == 200
    assert (await client.get("/api/tasks", headers=aud)).status_code == 403
    assert len(await _audit_denied(client, adm)) >= 2

    # oldal-szintű tévedés jelzése a felületről → naplósor
    res = await client.post("/api/auth/denied", json={"path": "/feladatok", "feature": "tasks"}, headers=aud)
    assert res.status_code == 200
    assert any(d["detail"].get("method") == "PAGE" for d in await _audit_denied(client, adm))

    # törlés: használatban → 409; beépített → 422; az admin nem szerkeszthető szintre
    res = await client.delete("/api/settings/roles/auditor", headers=adm)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "settings.role_in_use"
    assert (await client.delete("/api/settings/roles/manager", headers=adm)).status_code == 422
    # átnevezés megy
    res = await client.patch("/api/settings/roles/auditor", json={"name": "Belső auditor"}, headers=adm)
    assert res.status_code == 200 and any(r["key"] == "auditor" and r["name"] == "Belső auditor" for r in res.json()["roles"])
    # nem admin nem kezelhet szerepkört
    assert (await client.post("/api/settings/roles", json={"key": "hacker", "name": "x"}, headers=mgr)).status_code == 403


async def test_manager_level_custom_role(client, admin):
    _, adm = admin
    res = await client.post(
        "/api/settings/roles",
        json={"key": "vezeto2", "name": "Vezető 2", "level": 1, "copy_from": "manager"}, headers=adm,
    )
    assert res.status_code == 201, res.text
    v_user, v = await make_user(email="vezeto2@example.com", role="vezeto2")
    assert (await client.get("/api/auth/me", headers=v)).json()["level"] == 1
    # vezetői szintű szabály: raktárat létrehozhat (csak admin/manager-szint), kassza-betétet rögzíthet
    assert (await client.post("/api/warehouses", json={"name": "V2 raktár"}, headers=v)).status_code == 201
    assert (await client.post("/api/settlements/expenses", json={"amount_gross": 100, "entry_type": "deposit"}, headers=v)).status_code == 201
    # 0-s szintű saját szerepkör ugyanitt 403
    await client.post("/api/settings/roles", json={"key": "nezelodo", "name": "Nézelődő", "level": 0, "copy_from": "manager"}, headers=adm)
    _, n = await make_user(email="nezelodo@example.com", role="nezelodo")
    assert (await client.post("/api/warehouses", json={"name": "N raktár"}, headers=n)).status_code == 403
    # admin-szintű végpont a vezetői saját szerepkörnek sem jár
    assert (await client.get("/api/settings/permissions", headers=v)).status_code == 403
    # a szerepkör törölhető, ha már senki nincs benne
    from app import db as app_db
    from app.models import User

    async with app_db.get_session_factory()() as session:
        u = (await session.execute(select(User).where(User.email == "nezelodo@example.com"))).scalar_one()
        u.role = "employee"
        await session.commit()
    assert (await client.delete("/api/settings/roles/nezelodo", headers=adm)).status_code == 200
