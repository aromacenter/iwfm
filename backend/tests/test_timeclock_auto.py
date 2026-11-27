"""192. kör: tényszerű blokkolás — belépés NEM nyit jelenlétet; nyitva felejtett
blokkolásról egyszeri emlékeztető (a bejegyzés nyitva marad); bérszámfejtés-alap kapcsoló."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.conftest import gen_taj, make_employee_record, make_user


async def test_login_does_not_clock_in(client):
    """App-belépés nem blokkolás: nem keletkezik jelenléti bejegyzés."""
    user, _ = await make_user(email="autoin@example.com", role="employee")
    await make_employee_record(user)
    for _ in range(2):
        res = await client.post("/api/auth/login", json={"email": "autoin@example.com", "password": "Passw0rd-12345"})
        assert res.status_code == 200, res.text
    from sqlalchemy import select

    from app.db import get_session_factory
    from app.models import TimeEntry

    async with get_session_factory()() as db:
        assert (await db.execute(select(TimeEntry))).scalars().all() == []


async def test_open_entry_reminder_once_and_stays_open(client, admin, monkeypatch):
    """A munkaidő vége után nyitva maradt bejegyzés: egyszer emlékeztető, nem záródik,
    a friss bejegyzést békén hagyja; a második kör nem ismétel."""
    from sqlalchemy import select

    from app.db import get_session_factory
    from app.models import TimeEntry
    from app.services.wfm import timeclock_auto
    SessionLocal = get_session_factory()

    _, adm = admin
    user, _ = await make_user(email="autoout@example.com", role="employee")
    emp = await make_employee_record(user)
    res = await client.patch(
        f"/api/employees/{emp.id}", json={"availability": {str(i): ["08:00", "16:00"] for i in range(7)}}, headers=adm,
    )
    assert res.status_code == 200, res.text

    personal: list[str] = []

    async def fake_personal(db, employee, text):
        personal.append(text)
        return True

    monkeypatch.setattr("app.services.wfm.telegram.send_personal", fake_personal)

    async with SessionLocal() as db:
        db.add(TimeEntry(employee_id=emp.id, clock_in=datetime.now(UTC) - timedelta(days=2, hours=10), source="self"))
        db.add(TimeEntry(employee_id=emp.id, clock_in=datetime.now(UTC), source="self"))
        await db.commit()

    async with SessionLocal() as db:
        assert await timeclock_auto.remind_open_entries(db) == 1
        await db.commit()
    async with SessionLocal() as db:
        assert await timeclock_auto.remind_open_entries(db) == 0  # nem ismétel
        await db.commit()
    assert len(personal) == 1 and "Elfelejtettél kiblokkolni" in personal[0]

    async with SessionLocal() as db:
        entries = (
            await db.execute(select(TimeEntry).where(TimeEntry.employee_id == emp.id).order_by(TimeEntry.clock_in))
        ).scalars().all()
        old, fresh = entries[0], entries[-1]
        assert old.clock_out is None and timeclock_auto.REMIND_NOTE in (old.note or "")
        assert fresh.clock_out is None and not fresh.note


async def test_payroll_source_switch(client, admin):
    """A bérexport Fizetendő órája a kapcsolótól függ (blokkolás vs beosztás)."""
    _, adm = admin
    res = await client.post(
        "/api/employees",
        json={"email": "berforras@example.com", "last_name": "Bér", "first_name": "Forrás",
              "hire_date": "2026-01-05", "payroll_source": "schedule",
              "taj": gen_taj(7)},  # c6af18d8: TAJ nélkül nem kerül a bérexportba
        headers=adm,
    )
    assert res.status_code == 201, res.text
    assert res.json()["payroll_source"] == "schedule"
    res = await client.patch(f"/api/employees/{res.json()['id']}", json={"payroll_source": "rossz"}, headers=adm)
    assert res.status_code == 422
    res = await client.get("/api/payroll/export?period_start=2026-08-01&period_end=2026-08-31&format=csv", headers=adm)
    assert res.status_code == 200
    text = res.content.decode("utf-8-sig")
    assert "Bérszámfejtés alapja" in text and "Fizetendő óra" in text and "Beosztás" in text
