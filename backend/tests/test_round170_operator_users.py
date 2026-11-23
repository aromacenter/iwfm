"""170. kör: fiók-kérés a Flotta-pultból → admin jóváhagyása; üzemeltetői fiók
(admin jogkör, piros sáv, licencen kívül); megszüntetés értesítővel; egygombos belépés."""

from __future__ import annotations

from app.core.config import get_settings

OK = {"X-Operator-Token": "flotta-titok"}


async def _arm(monkeypatch):
    monkeypatch.setattr(get_settings(), "operator_token", "flotta-titok")


async def test_request_approve_operator_account_flow(client, admin, monkeypatch):
    _, adm = admin
    await _arm(monkeypatch)

    # token nélkül zárva
    assert (await client.get("/api/operator/users")).status_code == 403
    out = (await client.get("/api/operator/users", headers=OK)).json()
    assert [u["email"] for u in out["users"]] == ["admin@example.com"] and out["requests"] == []
    assert "password" not in str(out) and "hash" not in str(out)

    # a pult kérést ad le (nem hoz létre fiókot) — az adminnak felugrik
    res = await client.post("/api/operator/users", json={
        "last_name": "X-systems", "first_name": "Ügyfélszolgálat", "email": "Support@X-Systems.hu",
        "role": "employee", "note": "Beállítás-segítséghez."}, headers=OK)
    assert res.status_code == 202, res.text
    req = res.json()["request"]
    assert req["status"] == "pending" and req["is_operator"] is True and req["role"] == "admin"
    assert len((await client.get("/api/employees", headers=adm)).json()) == 0
    popup = (await client.get("/api/user-requests", headers=adm)).json()["items"]
    assert [p["id"] for p in popup] == [req["id"]] and popup[0]["note"] == "Beállítás-segítséghez."

    # ugyanarra az azonosítóra nincs második függő kérés
    res = await client.post("/api/operator/users", json={"last_name": "X", "first_name": "Y", "email": "support@x-systems.hu"}, headers=OK)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "user_request.duplicate"

    # nem admin nem dönthet
    from tests.conftest import make_user
    _, emp_h = await make_user(email="dolgozo@example.com", role="employee")
    assert (await client.post(f"/api/user-requests/{req['id']}/approve", headers=emp_h)).status_code == 403

    # az admin elfogad: fiók admin jogkörrel + karton + jelszó-beállító link
    res = await client.post(f"/api/user-requests/{req['id']}/approve", headers=adm)
    assert res.status_code == 200, res.text
    out = res.json()
    assert "/jelszo?token=" in out["reset_url"] and out["request"]["status"] == "approved"
    assert (await client.get("/api/user-requests", headers=adm)).json()["items"] == []
    # másodszor már nem fogadható el
    assert (await client.post(f"/api/user-requests/{req['id']}/approve", headers=adm)).status_code == 409

    users = (await client.get("/api/operator/users", headers=OK)).json()
    op = next(u for u in users["users"] if u["email"] == "support@x-systems.hu")
    assert op["role"] == "admin" and op["is_operator"] is True and op["from_operator"] is True and op["has_employee"] is True
    # az üzemeltetői fiók nem számít a licencbe (admin + dolgozo = 2)
    assert users["usage"]["users"] == 2
    assert users["requests"][0]["status"] == "approved" and users["requests"][0]["reset_url"]

    # a link a rendes jelszó-beállítóval működik; a /me jelzi az üzemeltetői fiókot
    token = out["reset_url"].split("token=")[1]
    assert (await client.post("/api/auth/reset-password", json={"token": token, "password": "Uj-jelszo-12345"})).status_code == 200
    res = await client.post("/api/auth/login", json={"email": "support@x-systems.hu", "password": "Uj-jelszo-12345"})
    assert res.status_code == 200, res.text
    assert res.json()["is_operator"] is True and res.json()["role"] == "admin"
    me_admin = (await client.get("/api/auth/me", headers=adm)).json()
    assert me_admin["is_operator"] is False

    # a cég adminja nem fokozhatja le az üzemeltetői fiókot
    emp_row = next(e for e in (await client.get("/api/employees", headers=adm)).json() if e["last_name"] == "X-systems")
    res = await client.patch(f"/api/employees/{emp_row['id']}", json={"role": "employee"}, headers=adm)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "employee.operator_role_locked"

    # egygombos belépés a pultból: egyszer használatos link
    res = await client.post(f"/api/operator/users/{op['id']}/login-link", headers=OK)
    assert res.status_code == 200, res.text
    ot = res.json()["url"].split("ot=")[1]
    res = await client.post("/api/auth/login/token", json={"token": ot})
    assert res.status_code == 200, res.text
    assert res.json()["is_operator"] is True and "set-cookie" in {k.lower() for k in res.headers}
    res = await client.post("/api/auth/login/token", json={"token": ot})
    assert res.status_code == 422 and res.json()["detail"]["code"] == "auth.login_link_invalid"
    # nem üzemeltetői fiókra nincs belépő link (az admin nem a pultból jött → 404)
    assert (await client.post(f"/api/operator/users/{me_admin['id']}/login-link", headers=OK)).status_code == 404

    # megszüntetés a pultból: letiltva, kiléptetve, az adminnak értesítő ugrik fel
    res = await client.delete(f"/api/operator/users/{op['id']}", headers=OK)
    assert res.status_code == 200, res.text
    assert res.json()["user"]["is_active"] is False and res.json()["notice"]["kind"] == "delete"
    res = await client.post("/api/auth/login", json={"email": "support@x-systems.hu", "password": "Uj-jelszo-12345"})
    assert res.status_code in (401, 403)
    notices = (await client.get("/api/user-requests", headers=adm)).json()["items"]
    assert len(notices) == 1 and notices[0]["kind"] == "delete" and notices[0]["status"] == "done"
    assert (await client.post(f"/api/user-requests/{notices[0]['id']}/ack", headers=adm)).status_code == 200
    assert (await client.get("/api/user-requests", headers=adm)).json()["items"] == []
    assert (await client.delete(f"/api/operator/users/{op['id']}", headers=OK)).status_code == 409

    # napló: a kérés, a jóváhagyás, a belépő link és a megszüntetés is szerepel
    res = await client.get("/api/audit?action=user.", headers=adm)
    acts = {a["action"] for a in (res.json() if isinstance(res.json(), list) else res.json().get("items", []))}
    assert {"user.request_operator", "user.request_approved", "user.login_link_operator", "user.deactivate_operator"} <= acts


async def test_request_decline_withdraw_and_license(client, admin, monkeypatch):
    _, adm = admin
    await _arm(monkeypatch)

    # ügyfél-fiók kérése (nem üzemeltetői): a licenc számít; S sáv = 2 fiók
    assert (await client.put("/api/operator/license", json={"plan": "s", "valid_until": None, "customer_name": None}, headers=OK)).status_code == 200
    res = await client.post("/api/operator/users", json={"last_name": "Egy", "first_name": "Ede", "username": "ede", "is_operator": False, "role": "manager"}, headers=OK)
    assert res.status_code == 202, res.text
    r1 = res.json()["request"]
    assert r1["role"] == "manager" and r1["is_operator"] is False
    # elutasítás indokkal
    res = await client.post(f"/api/user-requests/{r1['id']}/decline", json={"reason": "Nem kértük."}, headers=adm)
    assert res.status_code == 200 and res.json()["request"]["status"] == "declined"
    reqs = (await client.get("/api/operator/users", headers=OK)).json()["requests"]
    assert reqs[0]["status"] == "declined" and reqs[0]["decline_reason"] == "Nem kértük."

    # visszavonás a pultból
    res = await client.post("/api/operator/users", json={"last_name": "Két", "first_name": "Kata", "username": "kata", "is_operator": False}, headers=OK)
    r2 = res.json()["request"]
    assert (await client.delete(f"/api/operator/users/requests/{r2['id']}", headers=OK)).status_code == 200
    assert (await client.get("/api/user-requests", headers=adm)).json()["items"] == []
    assert (await client.delete(f"/api/operator/users/requests/{r2['id']}", headers=OK)).status_code == 409

    # licenc-limit: 1 admin + 1 jóváhagyott = 2, a harmadik ügyfél-fiók kérése már 422
    res = await client.post("/api/operator/users", json={"last_name": "Hár", "first_name": "Hanna", "username": "hanna", "is_operator": False}, headers=OK)
    assert res.status_code == 202
    assert (await client.post(f"/api/user-requests/{res.json()['request']['id']}/approve", headers=adm)).status_code == 200
    res = await client.post("/api/operator/users", json={"last_name": "Négy", "first_name": "Nóra", "username": "nora", "is_operator": False}, headers=OK)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "license.user_limit"
    # az üzemeltetői fiók viszont a betelt licencbe is befér
    res = await client.post("/api/operator/users", json={"last_name": "X-systems", "first_name": "Support", "username": "xsupport"}, headers=OK)
    assert res.status_code == 202, res.text
    assert (await client.post(f"/api/user-requests/{res.json()['request']['id']}/approve", headers=adm)).status_code == 200
    out = (await client.get("/api/operator/users", headers=OK)).json()
    assert out["usage"]["users"] == 2 and out["max_users"] == 2

    # hiányzó azonosító / rossz szerepkör
    res = await client.post("/api/operator/users", json={"last_name": "X", "first_name": "Y"}, headers=OK)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "employee.login_id_required"
    res = await client.post("/api/operator/users", json={"last_name": "X", "first_name": "Y", "username": "zz", "is_operator": False, "role": "nincs_ilyen"}, headers=OK)
    assert res.status_code == 422


async def test_request_needs_an_admin(client, monkeypatch):
    """Üres példányon (még nincs admin) nincs kinek jóváhagynia → 422."""
    await _arm(monkeypatch)
    res = await client.post("/api/operator/users", json={"last_name": "X", "first_name": "Y", "username": "nincsadmin"}, headers=OK)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "user_request.no_admin"
