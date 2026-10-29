"""116. kör: minden menüpont kapcsolható — router-szintű modul-kapcsolók a
teljes katalógusra, függőség-lezárással (feladat → dolgozók stb.)."""

from __future__ import annotations

from tests.test_license import OP, _arm_operator


async def _set_modules(client, mods):
    res = await client.put(
        "/api/operator/license",
        json={"plan": "xl", "valid_until": None, "enabled_modules": mods},
        headers=OP,
    )
    assert res.status_code == 200, res.text
    return res.json()


async def test_every_menu_is_switchable(client, admin, manager, monkeypatch):
    _, adm = admin
    _, mgr = manager
    _arm_operator(monkeypatch)

    # csak a feladatok: a dolgozók (függőség) velük jönnek, a többi zárva
    status = await _set_modules(client, ["tasks"])
    assert sorted(status["modules"]) == ["employees", "tasks"]
    assert (await client.get("/api/tasks", headers=mgr)).status_code == 200
    assert (await client.get("/api/employees", headers=mgr)).status_code == 200
    for path in ("/api/shifts", "/api/partners", "/api/assets", "/api/products",
                 "/api/settlements", "/api/service", "/api/intakes", "/api/quotes",
                 "/api/audit", "/api/geo/zip/1111", "/api/time-off"):
        res = await client.get(path, headers=adm)
        assert res.status_code == 403, (path, res.status_code, res.text[:120])
        assert res.json()["detail"]["code"] == "license.module_disabled", path

    # az átadás külön kapcsoló a feladatokon belül
    assert (await client.get("/api/tasks/handovers/list", headers=mgr)).status_code == 403
    assert (await client.get("/api/tasks/service-handover/list", headers=mgr)).status_code == 403
    status = await _set_modules(client, ["handover", "pickup"])
    assert "tasks" in status["modules"] and "employees" in status["modules"]
    assert (await client.get("/api/tasks/handovers/list", headers=mgr)).status_code == 200
    assert (await client.get("/api/tasks/service-handover/list", headers=mgr)).status_code == 200

    # a vezérlőpult és a beállítások mindig mennek (alap)
    assert (await client.get("/api/dashboard", headers=adm)).status_code == 200
    assert (await client.get("/api/settings/license/status", headers=adm)).status_code == 200

    # szerviz → gépek + partnerek lezárása; elszámolás → partnerek + termékek
    status = await _set_modules(client, ["service", "settlements"])
    assert {"service", "machines", "partners", "settlements", "products"} <= set(status["modules"])
    assert (await client.get("/api/assets", headers=mgr)).status_code == 200
    assert (await client.get("/api/products", headers=mgr)).status_code == 200
    assert (await client.get("/api/tasks", headers=mgr)).status_code == 403

    # minden modul (X-Presso mód) → semmi sincs zárva
    status = await _set_modules(client, ["*"])
    assert status["modules"] is None
    assert (await client.get("/api/shifts", headers=adm)).status_code != 403

    # ismeretlen kulcs: 422
    res = await client.put(
        "/api/operator/license",
        json={"plan": "xl", "valid_until": None, "enabled_modules": ["tasks", "nincsilyen"]},
        headers=OP,
    )
    assert res.status_code == 422


async def test_operator_status_has_catalog(client, admin, monkeypatch):
    _arm_operator(monkeypatch)
    st = (await client.get("/api/operator/status", headers=OP)).json()
    assert "tasks" in st["module_catalog"] and "calendar" in st["module_catalog"]
    assert st["module_requires"]["tasks"] == ["employees"]


def test_close_modules():
    from app.services.wfm.license import close_modules

    assert close_modules(["payroll"]) == ["employees", "attendance", "payroll"]
    assert close_modules(["gls"]) == ["gls"]
    assert close_modules(["delivery"]) == ["partners", "products", "delivery"]
    assert close_modules(["nincs"]) == []
