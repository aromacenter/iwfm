"""Modul-kapcsolók: NULL lista = minden megy; üres/részleges lista = a
kikapcsolt modul routere 403-mal zárva."""

from __future__ import annotations

from tests.test_license import OP, _arm_operator

from app.core.config import get_settings


async def test_default_modules_env(client, admin, manager, monkeypatch):
    """Licenc-sor nélkül a WFM_DEFAULT_MODULES env dönt: üres = csak alap,
    lista = a felsoroltak; env nélkül minden megy (saját példány)."""
    _, adm = admin
    _, mgr = manager
    settings = get_settings()
    _arm_operator(monkeypatch)

    monkeypatch.setattr(settings, "default_modules", "", raising=False)
    res = await client.get("/api/gls", headers=mgr)
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "license.module_disabled"
    status = (await client.get("/api/settings/license/status", headers=adm)).json()
    assert status["modules"] == []

    monkeypatch.setattr(settings, "default_modules", "gls, billing")
    assert (await client.get("/api/gls", headers=mgr)).status_code == 200
    status = (await client.get("/api/settings/license/status", headers=adm)).json()
    assert sorted(status["modules"]) == ["billing", "gls"]

    # a licenc-sor explicit "minden modul" (["*"]) az env-et is felülírja
    await client.put(
        "/api/operator/license",
        json={"plan": "xl", "valid_until": None, "enabled_modules": ["*"]},
        headers=OP,
    )
    monkeypatch.setattr(settings, "default_modules", "")
    assert (await client.get("/api/gls", headers=mgr)).status_code == 200
    status = (await client.get("/api/settings/license/status", headers=adm)).json()
    assert status["modules"] is None


async def test_module_switches(client, admin, manager, monkeypatch):
    _, adm = admin
    _, mgr = manager
    _arm_operator(monkeypatch)

    # licenc-sor nélkül (X-Presso mód): minden modul él
    res = await client.get("/api/gls", headers=mgr)
    assert res.status_code == 200
    res = await client.get("/api/settings/billingo", headers=adm)
    assert res.status_code == 200

    # csak-alap licenc: az extra modulok teljes routere zárva
    res = await client.put(
        "/api/operator/license",
        json={"plan": "m", "valid_until": None, "enabled_modules": []},
        headers=OP,
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["modules"] == []
    assert "gls" in out["all_modules"]

    for path in ("/api/gls",):
        res = await client.get(path, headers=mgr)
        assert res.status_code == 403, f"{path}: {res.status_code}"
        assert res.json()["detail"]["code"] == "license.module_disabled"
    res = await client.get("/api/settings/billingo", headers=adm)
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "license.module_disabled"

    # ismeretlen modul-név: validációs hiba
    res = await client.put(
        "/api/operator/license",
        json={"plan": "m", "valid_until": None, "enabled_modules": ["kavefozo"]},
        headers=OP,
    )
    assert res.status_code == 422

    # gls bekapcsolva: a GLS megy, a Billingó továbbra sem
    await client.put(
        "/api/operator/license",
        json={"plan": "m", "valid_until": None, "enabled_modules": ["gls"]},
        headers=OP,
    )
    assert (await client.get("/api/gls", headers=mgr)).status_code == 200
    assert (await client.get("/api/settings/billingo", headers=adm)).status_code == 403

    # a mező NÉLKÜLI mentés (példány-beli licenc-kártya) NEM piszkálja a modulokat
    res = await client.put(
        "/api/operator/license", json={"plan": "m", "valid_until": None}, headers=OP
    )
    assert res.json()["modules"] == ["gls"]
    assert (await client.get("/api/settings/billingo", headers=adm)).status_code == 403

    # NULL = megint minden (és más tesztet sem zavarunk)
    res = await client.put(
        "/api/operator/license",
        json={"plan": "xl", "valid_until": None, "enabled_modules": None},
        headers=OP,
    )
    assert res.json()["modules"] is None
    assert (await client.get("/api/settings/billingo", headers=adm)).status_code == 200
