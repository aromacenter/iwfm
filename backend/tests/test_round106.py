"""106. kör: bérexport TAJ-szűrés (c6af18d8), vezetői távollét-rögzítés
(2e9c7b96), térkép minden aktív partnerrel + geokódolás (d650f1d5),
raktár-modul Flotta-kapcsoló."""
from __future__ import annotations

from datetime import date, timedelta

from tests.conftest import gen_taj


async def _enable_modules(modules):
    import app.db as app_db
    from app.models import LicenseSettings
    from app.services.wfm import license as license_service

    factory = app_db.get_session_factory()
    async with factory() as session:
        await session.merge(LicenseSettings(id=1, plan="xl", enabled_modules=modules))
        await session.commit()
    license_service.invalidate_cache()


async def test_payroll_skips_employees_without_taj(client, admin):
    """c6af18d8: a külsős szervizesek / külsős cégek emberei (nincs TAJ) nem
    kerülnek a bérexportba; a TAJ-os dolgozó igen."""
    _, adm = admin
    for name, extra in (("Belsős", {"taj": gen_taj(5)}), ("Külsős", {})):
        res = await client.post(
            "/api/employees",
            json={"email": f"{name.lower()}@example.com", "last_name": name, "first_name": "Szervizes",
                  "hire_date": "2026-01-05", **extra},
            headers=adm,
        )
        assert res.status_code == 201, res.text
    res = await client.get(
        "/api/payroll/export?period_start=2026-09-01&period_end=2026-09-30&format=csv", headers=adm
    )
    assert res.status_code == 200
    text = res.content.decode("utf-8-sig")
    assert "Belsős Szervizes" in text
    assert "Külsős Szervizes" not in text


async def test_timeoff_auto_approve_by_manager(client, admin):
    """2e9c7b96: a vezető közvetlenül rögzít betegszabadságot / szabadnapot —
    napra vagy időszakra, utólag is — és az azonnal jóváhagyott."""
    _, adm = admin
    emp = (
        await client.post(
            "/api/employees",
            json={"email": "beteg@example.com", "last_name": "Beteg", "first_name": "Bence", "hire_date": "2026-01-05"},
            headers=adm,
        )
    ).json()
    past = date.today() - timedelta(days=10)
    res = await client.post(
        "/api/time-off",
        json={"employee_id": emp["id"], "type": "sick", "start_date": past.isoformat(),
              "end_date": (past + timedelta(days=2)).isoformat(), "reason": "influenza", "auto_approve": True},
        headers=adm,
    )
    assert res.status_code == 201, res.text
    row = res.json()
    assert row["status"] == "approved" and row["decided_at"] is not None
    # egy napos szabadnap előre
    future = date.today() + timedelta(days=30)
    res = await client.post(
        "/api/time-off",
        json={"employee_id": emp["id"], "type": "annual", "start_date": future.isoformat(),
              "end_date": future.isoformat(), "auto_approve": True},
        headers=adm,
    )
    assert res.status_code == 201 and res.json()["status"] == "approved"
    # auto_approve nélkül továbbra is függő
    res = await client.post(
        "/api/time-off",
        json={"employee_id": emp["id"], "type": "other", "start_date": (future + timedelta(days=5)).isoformat(),
              "end_date": (future + timedelta(days=5)).isoformat()},
        headers=adm,
    )
    assert res.status_code == 201 and res.json()["status"] == "pending"
    approved = (await client.get("/api/time-off?status=approved", headers=adm)).json()
    assert sum(1 for r in approved if r["employee_id"] == emp["id"]) == 2


async def test_map_lists_all_active_partners_and_geocode_missing(client, manager):
    """d650f1d5: a gép nélküli aktív partner is a térképen (contracted=False,
    machines=0); a hiányzó koordináták geokódolása végpont a hiányzók számát adja."""
    from tests.test_inventory import make_partner

    _, mgr = manager
    partner = await make_partner(client, mgr, name="Gép Nélkül Kft.")
    rows = (await client.get("/api/stats/map", headers=mgr)).json()
    row = next(r for r in rows if r["id"] == partner["id"])
    assert row["machines"] == 0 and row["contracted"] is False
    res = await client.post("/api/stats/map/geocode-missing", headers=mgr)
    assert res.status_code == 200
    body = res.json()
    assert body["missing"] >= 1 and body["queued"] == 0  # tesztben WFM_GEOCODE=0 → nem indul


async def test_warehouse_module_switch(client, admin):
    """Raktár-modul: Flotta-kapcsolóval kikapcsolva 403 (license.module_disabled),
    bekapcsolva elérhető."""
    _, adm = admin
    await _enable_modules([])
    res = await client.get("/api/warehouses", headers=adm)
    assert res.status_code == 403 and res.json()["detail"]["code"] == "license.module_disabled"
    res = await client.get("/api/stock-requests", headers=adm)
    assert res.status_code == 403
    await _enable_modules(["warehouse"])
    res = await client.get("/api/warehouses", headers=adm)
    assert res.status_code == 200, res.text
