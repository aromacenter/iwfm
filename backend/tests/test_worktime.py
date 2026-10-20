"""Munkaidő-mérés (worktime modul, 1d553d2e C fázis): timer start/pause/
resume/stop idempotensen, a dolgozó számokat nem lát, a vezetői statisztika
írt óra vs. mért aktív idő; a tiszta logika szünet-számítása; modulkapcsoló."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from tests.conftest import make_employee_record, make_user
from tests.test_parts import _enable_modules


def test_worktime_service_math():
    from app.services.wfm import worktime_service as wt

    t0 = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
    s = wt.start([], t0)
    s = wt.start(s, t0 + timedelta(minutes=1))  # idempotens: nem nyit másodikat
    assert len(s) == 1
    s = wt.pause(s, t0 + timedelta(minutes=30))
    s = wt.pause(s, t0 + timedelta(minutes=31))  # idempotens
    assert wt.state_of(s) == "paused"
    s = wt.resume(s, t0 + timedelta(minutes=40))
    s = wt.stop(s, t0 + timedelta(minutes=100))
    assert wt.state_of(s) == "stopped"
    summ = wt.summarize(s)
    assert summ.active_seconds == 90 * 60 and summ.pause_seconds == 10 * 60 and summ.pause_count == 1
    assert [seg.kind for seg in summ.segments] == ["work", "pause", "work"]
    # nyitott szünet a stop-nál lezárul; a második munkamenet hozzáadódik
    s2 = wt.stop(wt.pause(wt.start(s, t0 + timedelta(hours=3)), t0 + timedelta(hours=3, minutes=10)), t0 + timedelta(hours=3, minutes=20))
    summ2 = wt.summarize(s2)
    assert summ2.session_count == 2 and summ2.active_seconds == 100 * 60 and summ2.pause_seconds == 20 * 60


async def test_worktime_timer_and_stats(client, admin):
    _, adm = admin
    await _enable_modules(["worktime", "parts"])
    user, emp_h = await make_user(email="timer@example.com", role="employee")
    emp = await make_employee_record(user, last_name="Kiss", first_name="Péter")
    _, other_h = await make_user(email="other@example.com", role="employee")
    await make_employee_record(_)
    task = (await client.post("/api/tasks", json={"title": "Bojler javítás", "employee_id": str(emp.id), "due_date": date.today().isoformat()}, headers=adm)).json()

    # nincs timer → none; idegen 404
    res = await client.get(f"/api/worktime/{task['id']}", headers=emp_h)
    assert res.status_code == 200 and res.json()["state"] == "none"
    assert (await client.post(f"/api/worktime/{task['id']}/start", headers=other_h)).status_code == 404
    assert (await client.post(f"/api/worktime/{task['id']}/fly", headers=emp_h)).status_code == 404

    # start → running (számok NÉLKÜL), pause → paused, resume, stop → stopped
    res = await client.post(f"/api/worktime/{task['id']}/start", headers=emp_h)
    assert res.status_code == 200, res.text
    st = res.json()
    assert st["state"] == "running" and st["started_at"] and set(st) == {"state", "started_at", "pause_count"}
    assert (await client.post(f"/api/worktime/{task['id']}/start", headers=emp_h)).json()["state"] == "running"
    assert (await client.post(f"/api/worktime/{task['id']}/pause", headers=emp_h)).json()["state"] == "paused"
    assert (await client.post(f"/api/worktime/{task['id']}/resume", headers=emp_h)).json()["pause_count"] == 1
    # a munkalapra írt óra
    res = await client.put(f"/api/me/tasks/{task['id']}/worksheet", json={"work_description": "csere", "hours_spent": 2.0}, headers=emp_h)
    assert res.status_code == 200 and "timer" not in {k for k, v in res.json().items() if v is not None}
    assert (await client.post(f"/api/worktime/{task['id']}/stop", headers=emp_h)).json()["state"] == "stopped"
    assert (await client.post(f"/api/worktime/{task['id']}/pause", headers=emp_h)).json()["state"] == "stopped"

    # vezetői munkalap-kép: timer összegzés számokkal
    boss = (await client.get(f"/api/tasks/{task['id']}/worksheet", headers=adm)).json()
    assert boss["timer"]["state"] == "stopped" and boss["timer"]["pause_count"] == 1 and boss["timer"]["session_count"] == 1
    assert boss["timer"]["first_started_at"] and boss["timer"]["last_ended_at"]

    # statisztika: a mai nap, szerelőnként + munkalaponként; employee szűrő; rossz tartomány 422
    # a gép óráján éjfél körül a helyi nap és a Budapest-nap eltérhet → ±1 nap
    d0, d1 = (date.today() - timedelta(days=1)).isoformat(), (date.today() + timedelta(days=1)).isoformat()
    today = date.today().isoformat()
    res = await client.get(f"/api/worktime/stats/summary?from={d0}&to={d1}", headers=adm)
    assert res.status_code == 200, res.text
    stats = res.json()
    assert stats["worksheets"] == 1 and stats["written_hours"] == 2.0
    assert stats["by_employee"][0]["name"] == "Kiss Péter" and stats["by_employee"][0]["worksheets"] == 1
    assert stats["rows"][0]["serial"].startswith("ML-") and stats["rows"][0]["pause_count"] == 1
    assert len(stats["rows"][0]["segments"]) == 3
    res = await client.get(f"/api/worktime/stats/summary?from={d0}&to={d1}&employee_id={emp.id}", headers=adm)
    assert res.json()["worksheets"] == 1
    other_emp_id = (await client.get("/api/employees", headers=adm)).json()
    other_id = next(e["id"] for e in other_emp_id if e["id"] != str(emp.id))
    assert (await client.get(f"/api/worktime/stats/summary?from={d0}&to={d1}&employee_id={other_id}", headers=adm)).json()["worksheets"] == 0
    assert (await client.get(f"/api/worktime/stats/summary?from={today}&to=2020-01-01", headers=adm)).status_code == 422
    # a dolgozó a statisztikát nem éri el
    assert (await client.get(f"/api/worktime/stats/summary?from={d0}&to={d1}", headers=emp_h)).status_code == 403

    # modul kikapcsolva → 403 a timerre is, a munkalap-képen nincs timer
    await _enable_modules(["parts"])
    assert (await client.post(f"/api/worktime/{task['id']}/start", headers=emp_h)).status_code == 403
    assert (await client.get(f"/api/tasks/{task['id']}/worksheet", headers=adm)).json()["timer"] is None
