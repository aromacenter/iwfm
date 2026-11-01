"""119. kör: ottfelejtett munkalap-timerek — 12 órás plafon a nyitott munkamenetre,
automatikus zárás az őrben, zárás a feladat lezárásakor (x-presso jegy: 102 óra / hét)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

import app.db as app_db
from app.models import WorksheetTimer
from app.services.wfm import worktime_service as wt
from tests.conftest import make_employee_record, make_user
from tests.test_round116 import _set_modules
from tests.test_license import _arm_operator


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def test_summarize_caps_open_session_and_auto_close():
    now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    start = now - timedelta(days=3)
    sessions = [{"start": _iso(start), "end": None, "mode": "work", "pauses": [], "modes": []}]
    s = wt.summarize(sessions, now)
    assert s.active_seconds == 12 * 3600 and s.auto_closed is True and s.state == "running"
    closed = wt.auto_close_stale(sessions, now)
    assert wt.state_of(closed) == "stopped" and closed[-1]["auto"] is True
    assert wt._parse(closed[-1]["end"]) == start + timedelta(hours=12)
    assert wt.summarize(closed, now).active_seconds == 12 * 3600
    # friss nyitott munkamenet: nincs vágás, nincs auto
    fresh = [{"start": _iso(now - timedelta(hours=1)), "end": None, "mode": "work", "pauses": [], "modes": []}]
    assert wt.auto_close_stale(fresh, now) is fresh
    f = wt.summarize(fresh, now)
    assert f.active_seconds == 3600 and f.auto_closed is False
    # szünetben felejtett timer: a szünet is a plafonnál zárul, az aktív idő a szünet előtti rész
    paused = [{"start": _iso(start), "end": None, "mode": "work",
               "pauses": [{"p": _iso(start + timedelta(hours=1)), "r": None}], "modes": []}]
    p = wt.summarize(paused, now)
    assert p.active_seconds == 3600 and p.auto_closed is True
    pc = wt.auto_close_stale(paused, now)
    assert wt.state_of(pc) == "stopped" and pc[-1]["pauses"][0]["r"] == _iso(start + timedelta(hours=12))
    # rendesen zárt munkamenet érintetlen
    done = [{"start": _iso(start), "end": _iso(start + timedelta(hours=5)), "mode": "work", "pauses": [], "modes": []}]
    assert wt.summarize(done, now).active_seconds == 5 * 3600 and wt.summarize(done, now).auto_closed is False


async def test_task_done_stops_timer_and_stale_guard(client, admin, monkeypatch):
    _, adm = admin
    _arm_operator(monkeypatch)
    await _set_modules(client, ["worktime", "parts"])
    user, emp_h = await make_user(email="timer119@example.com", role="employee")
    emp = await make_employee_record(user, last_name="Varga", first_name="Csaba")
    task = (await client.post("/api/tasks", json={
        "title": "Jura szerviz", "employee_id": str(emp.id), "due_date": date.today().isoformat(),
    }, headers=adm)).json()
    assert (await client.post(f"/api/worktime/{task['id']}/start", headers=emp_h)).json()["state"] == "running"

    # a feladat lezárása (admin) zárja a nyitott timert is
    res = await client.patch(f"/api/tasks/{task['id']}", json={"status": "done"}, headers=adm)
    assert res.status_code == 200, res.text
    assert (await client.get(f"/api/worktime/{task['id']}", headers=emp_h)).json()["state"] == "stopped"

    # második feladat: a timer 20 órája nyitva (ottfelejtve) → az őr a 12 órás plafonnál zárja
    task2 = (await client.post("/api/tasks", json={
        "title": "Saeco szerviz", "employee_id": str(emp.id), "due_date": date.today().isoformat(),
    }, headers=adm)).json()
    assert (await client.post(f"/api/worktime/{task2['id']}/start", headers=emp_h)).json()["state"] == "running"
    now = datetime.now(UTC)
    start = now - timedelta(hours=20)
    factory = app_db.get_session_factory()
    async with factory() as session:
        tm = (await session.execute(select(WorksheetTimer).where(WorksheetTimer.user_id == user.id).order_by(WorksheetTimer.first_started_at.desc()))).scalars().first()
        tm.sessions = [{"start": _iso(start), "end": None, "mode": "work", "pauses": [], "modes": []}]
        tm.first_started_at = start
        tm.last_ended_at = None
        await session.commit()

    # a statisztika már az őr ELŐTT is plafonoz: 12 óra, ⚠ jelöléssel (nem 20)
    d0 = (date.today() - timedelta(days=2)).isoformat()
    d1 = date.today().isoformat()
    stats = (await client.get(f"/api/worktime/stats/summary?from={d0}&to={d1}&employee_id={emp.id}", headers=adm)).json()
    row2 = next(r for r in stats["rows"] if r["task_id"] == task2["id"])
    assert row2["active_seconds"] == 12 * 3600 and row2["auto_closed"] is True

    from app.api.worktime import close_stale_timers

    async with factory() as session:
        assert await close_stale_timers(session, now) == 1
        assert await close_stale_timers(session, now) == 0  # idempotens
    st = (await client.get(f"/api/worktime/{task2['id']}", headers=emp_h)).json()
    assert st["state"] == "stopped"
    stats = (await client.get(f"/api/worktime/stats/summary?from={d0}&to={d1}&employee_id={emp.id}", headers=adm)).json()
    row2 = next(r for r in stats["rows"] if r["task_id"] == task2["id"])
    assert row2["active_seconds"] == 12 * 3600 and row2["auto_closed"] is True and row2["state"] == "stopped"
    # a dolgozónkénti összeg sem szalad el: 2 munkalap, az első rövid, a második 12 óra
    by = next(e for e in stats["by_employee"] if e["employee_id"] == str(emp.id))
    assert 12 * 3600 <= by["active_seconds"] < 12 * 3600 + 600
