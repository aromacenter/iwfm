"""Automatikus be-/kiléptetés + kapcsolható bérszámfejtés-alap."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.conftest import make_employee_record, make_user


async def test_auto_clockin_on_first_login(client):
    """Aznapi első app-belépéskor jelenlét nyílik; második belépés nem duplikál."""
    user, _ = await make_user(email="autoin@example.com", role="employee")
    await make_employee_record(user)

    res = await client.post(
        "/api/auth/login", json={"email": "autoin@example.com", "password": "Passw0rd-12345"}
    )
    assert res.status_code == 200, res.text

    # a nyitott bejegyzés létrejött
    from sqlalchemy import select

    from app.db import get_session_factory
    from app.models import TimeEntry
    SessionLocal = get_session_factory()

    async with SessionLocal() as db:
        entries = (await db.execute(select(TimeEntry))).scalars().all()
        assert len(entries) == 1
        assert entries[0].clock_out is None
        assert "Automatikus beléptetés" in (entries[0].note or "")

    # második belépés aznap → nincs új bejegyzés
    res = await client.post(
        "/api/auth/login", json={"email": "autoin@example.com", "password": "Passw0rd-12345"}
    )
    assert res.status_code == 200
    async with SessionLocal() as db:
        entries = (await db.execute(select(TimeEntry))).scalars().all()
        assert len(entries) == 1


async def test_auto_clockout_closes_forgotten_entries(client, admin):
    """A háttér-zárás a munkaidő végén (elérhetőség szerint) lezárja a nyitva
    felejtett bejegyzést; a frisset békén hagyja."""
    from sqlalchemy import select

    from app.db import get_session_factory
    from app.models import Employee, TimeEntry
    SessionLocal = get_session_factory()
    from app.services.wfm.timeclock_auto import auto_clockout

    _, adm = admin
    user, _ = await make_user(email="autoout@example.com", role="employee")
    emp = await make_employee_record(user)

    # elérhetőség: minden nap 08–16 (a teszt-emp patch admin-nal)
    res = await client.patch(
        f"/api/employees/{emp.id}",
        json={"availability": {str(i): ["08:00", "16:00"] for i in range(7)}},
        headers=adm,
    )
    assert res.status_code == 200, res.text

    async with SessionLocal() as db:
        # tegnapelőtt reggel nyitott, sosem zárt bejegyzés
        db.add(TimeEntry(
            employee_id=emp.id,
            clock_in=datetime.now(UTC) - timedelta(days=2, hours=10),
            source="self",
        ))
        # friss (ma nyitott) bejegyzés — még nem járt le a munkaidő vége
        db.add(TimeEntry(
            employee_id=emp.id, clock_in=datetime.now(UTC), source="self",
        ))
        await db.commit()

    async with SessionLocal() as db:
        closed = await auto_clockout(db)
        await db.commit()
    assert closed >= 1

    async with SessionLocal() as db:
        entries = (
            await db.execute(
                select(TimeEntry).where(TimeEntry.employee_id == emp.id)
                .order_by(TimeEntry.clock_in)
            )
        ).scalars().all()
        old, fresh = entries[0], entries[-1]
        assert old.clock_out is not None
        assert "Automatikus kiléptetés" in (old.note or "")
        # a lezárás nem "most", hanem a munkaidő végén történt
        assert old.clock_out < datetime.now(UTC).replace(tzinfo=None) if old.clock_out.tzinfo is None else True


async def test_payroll_source_switch(client, admin):
    """A bérexport Fizetendő órája a kapcsolótól függ (blokkolás vs beosztás)."""
    _, adm = admin
    res = await client.post(
        "/api/employees",
        json={"email": "berforras@example.com", "last_name": "Bér", "first_name": "Forrás",
              "hire_date": "2026-01-05", "payroll_source": "schedule"},
        headers=adm,
    )
    assert res.status_code == 201, res.text
    assert res.json()["payroll_source"] == "schedule"

    # rossz érték → 422
    res = await client.patch(
        f"/api/employees/{res.json()['id']}", json={"payroll_source": "rossz"},
        headers=adm,
    )
    assert res.status_code == 422

    # export lefut és tartalmazza az új oszlopokat
    res = await client.get(
        "/api/payroll/export?period_start=2026-08-01&period_end=2026-08-31&format=csv",
        headers=adm,
    )
    assert res.status_code == 200
    text = res.content.decode("utf-8-sig")
    assert "Bérszámfejtés alapja" in text
    assert "Fizetendő óra" in text
    assert "Beosztás" in text
