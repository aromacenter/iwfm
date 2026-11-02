"""141. kör: az operator /status egészség-jelei a Flotta-pultnak — 7 napos
aktivitás (belépés, elszámolás, feladat), utolsó aktivitás, nyitott hibajegyek."""

from __future__ import annotations

from datetime import date

from tests.test_license import OP, _arm_operator
from tests.test_tasks import make_emp


async def test_operator_status_health_signals(client, admin, manager, monkeypatch):
    _, adm = admin
    _, mgr = manager
    _arm_operator(monkeypatch)
    st = (await client.get("/api/operator/status", headers=OP)).json()
    assert "activity" in st and st["open_bugs"] == 0
    assert st["activity"]["logins_7d"] == 0  # a fixture-ök nem a login-végponton lépnek be
    assert st["activity"]["tasks_7d"] == 0 and st["activity"]["settlements_7d"] == 0

    # egy feladat + egy nyitott hibajegy → a jelek követik
    emp, _h = await make_emp(email="aktiv@example.com")
    res = await client.post("/api/tasks", json={"title": "Aktivitás", "employee_id": str(emp.id), "due_date": date.today().isoformat()}, headers=mgr)
    assert res.status_code == 201, res.text
    res = await client.post("/api/bugs", json={"title": "Elromlott valami", "description": "Részletes leírás a hibáról.", "page_url": "/gepek"}, headers=mgr)
    assert res.status_code in (200, 201), res.text

    st = (await client.get("/api/operator/status", headers=OP)).json()
    assert st["activity"]["tasks_7d"] == 1 and st["activity"]["settlements_7d"] == 0
    assert st["activity"]["last_activity_at"]  # az audit-napló már tartalmaz eseményt
    assert st["open_bugs"] == 1
