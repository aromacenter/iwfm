"""Licenc-réteg: sávok, fiók/dolgozó-limitek, lejárat + türelmi idő,
csak-olvasás mód a türelmi idő után."""

from __future__ import annotations

from datetime import date, timedelta

from tests.test_employees import employee_payload

from app.core.config import get_settings
from app.services.wfm.license import GRACE_DAYS

OP = {"X-Operator-Token": "teszt-operator"}


def _arm_operator(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "operator_token", "teszt-operator", raising=False)


async def test_license_default_unlimited(client, admin):
    _, adm = admin
    res = await client.get("/api/settings/license/status", headers=adm)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["plan"] == "xl"
    assert out["max_users"] is None and out["max_employees"] is None
    assert out["state"] == "ok"
    assert out["usage"]["users"] >= 1


async def test_license_limits_block_creation(client, admin, manager, monkeypatch):
    _, adm = admin
    _, mgr = manager
    _arm_operator(monkeypatch)

    # a példányból a licenc NEM módosítható — csak az üzemeltetői végponton át
    res = await client.put(
        "/api/settings/license", json={"plan": "s", "valid_until": None}, headers=adm
    )
    assert res.status_code == 405

    status = (await client.get("/api/settings/license/status", headers=adm)).json()
    users_now = status["usage"]["users"]
    employees_now = status["usage"]["employees"]

    # fiók-limit: pontosan a mostani létszám — új dolgozó (=új fiók) már nem fér
    res = await client.put(
        "/api/operator/license",
        json={"plan": "s", "valid_until": None, "max_users_override": users_now,
              "max_employees_override": 1000},
        headers=OP,
    )
    assert res.status_code == 200, res.text
    res = await client.post(
        "/api/employees", json=employee_payload(email="limit1@example.com"), headers=adm
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "license.user_limit"

    # dolgozó-limit külön: előbb felvenni egy dolgozót (XL alatt), majd a
    # keretet pont a mostani létszámra húzni — a következő már nem fér
    await client.put(
        "/api/operator/license", json={"plan": "xl", "valid_until": None}, headers=OP
    )
    res = await client.post(
        "/api/employees", json=employee_payload(email="elso@example.com"), headers=adm
    )
    assert res.status_code == 201, res.text
    employees_now = (
        await client.get("/api/settings/license/status", headers=adm)
    ).json()["usage"]["employees"]
    assert employees_now >= 1
    res = await client.put(
        "/api/operator/license",
        json={"plan": "s", "valid_until": None, "max_users_override": 1000,
              "max_employees_override": employees_now},
        headers=OP,
    )
    assert res.status_code == 200, res.text
    assert res.json()["max_users"] == 1000, res.text
    res = await client.post(
        "/api/employees", json=employee_payload(email="limit2@example.com"), headers=adm
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "license.employee_limit"

    # bővítés (XL) után már mehet
    await client.put(
        "/api/operator/license", json={"plan": "xl", "valid_until": None}, headers=OP
    )
    res = await client.post(
        "/api/employees", json=employee_payload(email="limit3@example.com"), headers=adm
    )
    assert res.status_code == 201, res.text


async def test_license_expiry_read_only(client, admin, monkeypatch):
    _, adm = admin
    _arm_operator(monkeypatch)

    # türelmi időn belül: figyelmeztetés van, írás még megy
    grace_date = (date.today() - timedelta(days=1)).isoformat()
    res = await client.put(
        "/api/operator/license", json={"plan": "xl", "valid_until": grace_date},
        headers=OP,
    )
    assert res.status_code == 200
    assert res.json()["state"] == "grace"
    res = await client.post(
        "/api/employees", json=employee_payload(email="grace@example.com"), headers=adm
    )
    assert res.status_code == 201, res.text

    # türelmi időn túl: minden írás 423, az olvasás és a licenc-oldal működik
    expired_date = (date.today() - timedelta(days=GRACE_DAYS + 2)).isoformat()
    await client.put(
        "/api/operator/license", json={"plan": "xl", "valid_until": expired_date},
        headers=OP,
    )
    res = await client.post(
        "/api/employees", json=employee_payload(email="tiltva@example.com"), headers=adm
    )
    assert res.status_code == 423
    assert res.json()["detail"]["code"] == "license.expired"
    res = await client.get("/api/employees", headers=adm)
    assert res.status_code == 200

    # hosszabbítás a licenc-végponton át megengedett — utána újra él a rendszer
    res = await client.put(
        "/api/operator/license", json={"plan": "xl", "valid_until": None}, headers=OP
    )
    assert res.status_code == 200
    assert res.json()["state"] == "ok"
    res = await client.post(
        "/api/employees", json=employee_payload(email="ujra.el@example.com"), headers=adm
    )
    assert res.status_code == 201, res.text
