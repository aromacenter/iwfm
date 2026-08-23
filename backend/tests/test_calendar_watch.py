"""Éves munkarend-frissítés (AI + kézi) és havi Mt.-ellenőrzés."""

from __future__ import annotations

import json
from datetime import date


async def test_calendar_manual_roundtrip_and_generation(client, admin):
    """Kézi munkarend-rögzítés: validálás + a beosztás-generálás használja."""
    _, adm = admin
    year = date.today().year + 1

    # rossz dátum / rossz év / nem-szombat ledolgozó: 422
    res = await client.put(f"/api/settings/calendar/{year}",
                           json={"rest_days": ["nem-datum"]}, headers=adm)
    assert res.status_code == 422
    res = await client.put(f"/api/settings/calendar/{year}",
                           json={"rest_days": [f"{year - 1}-01-02"]}, headers=adm)
    assert res.status_code == 422

    # találjunk egy jövő évi szombatot + egy hétköznapot
    d = date(year, 6, 1)
    while d.weekday() != 5:
        d = d.replace(day=d.day + 1)
    saturday = d
    weekday = date(year, 6, 2) if date(year, 6, 2).weekday() < 5 else date(year, 6, 4)
    while weekday.weekday() >= 5:
        weekday = weekday.replace(day=weekday.day + 1)

    res = await client.put(
        f"/api/settings/calendar/{year}",
        json={"rest_days": [str(weekday)], "worked_saturdays": [str(saturday)]},
        headers=adm,
    )
    assert res.status_code == 200, res.text
    got = (await client.get(f"/api/settings/calendar/{year}", headers=adm)).json()
    assert got["rest_days"] == [str(weekday)]
    assert got["source"] == "manual"
    assert len(got["holidays"]) >= 10  # a piros betűs ünnepek számítottan

    # a holidays-cache betöltés után a nap nem-munkanapnak számít
    from app import db as app_db
    from app.services.wfm.holidays import is_non_working_day, load_overrides

    factory = app_db.get_session_factory()
    async with factory() as session:
        await load_overrides(session)
    assert is_non_working_day(weekday) is True


async def test_calendar_ai_fetch_and_guard(client, admin, monkeypatch):
    """Az AI-frissítés lementi az évet (source=ai), validálja a dátumokat, és
    a havi őr megakadályozza az ismételt hívást."""
    _, adm = admin
    year = date.today().year + 1

    # következő évi szombat + hétköznap
    d = date(year, 3, 1)
    while d.weekday() != 5:
        d = d.replace(day=d.day + 1)
    saturday = d
    calls = []

    async def fake_generate(db, prompt, max_tokens=1000, **kw):
        calls.append(prompt)
        return json.dumps({
            "ismert": True,
            "pihenonapok": [f"{year}-05-02", f"{year - 1}-01-01"],  # rossz évi kiszűrve
            "ledolgozo_szombatok": [str(saturday), f"{year}-05-04"],  # nem-szombat kiszűrve? 05-04 lehet nem szombat
        })

    import app.services.wfm.ai_service as ai_mod

    monkeypatch.setattr(ai_mod, "generate", fake_generate)

    from app import db as app_db
    from app.services.wfm.calendar_watch import ensure_next_year_calendar

    factory = app_db.get_session_factory()
    async with factory() as session:
        added = await ensure_next_year_calendar(session)
    assert added is True
    assert len(calls) >= 1

    got = (await client.get(f"/api/settings/calendar/{year}", headers=adm)).json()
    assert got["source"] == "ai"
    assert f"{year}-05-02" in got["rest_days"]
    assert f"{year - 1}-01-01" not in got["rest_days"]  # rossz év kiszűrve
    assert str(saturday) in got["worked_saturdays"]
    for x in got["worked_saturdays"]:
        assert date.fromisoformat(x).weekday() == 5  # csak valódi szombat

    # havi őr: újra hívva nem kérdezi az AI-t (év már megvan + hónap-pecsét)
    calls.clear()
    async with factory() as session:
        added = await ensure_next_year_calendar(session)
    assert added is False and calls == []


async def test_monthly_mt_check(client, admin, monkeypatch):
    """Mt.-ellenőrzés: eredmény tárolva, havi őr, kézi futtatás végpontja."""
    _, adm = admin

    async def fake_generate(db, prompt, max_tokens=1000, **kw):
        return json.dumps({"valtozott": True, "osszefoglalo": "Teszt-módosítás a pihenőidőben."})

    import app.services.wfm.ai_service as ai_mod

    monkeypatch.setattr(ai_mod, "generate", fake_generate)

    res = await client.post("/api/settings/mt-check", json={}, headers=adm)
    assert res.status_code == 200, res.text
    assert "Teszt-módosítás" in res.json()["result"]

    from app import db as app_db
    from app.services.wfm.calendar_watch import monthly_mt_check

    factory = app_db.get_session_factory()
    async with factory() as session:
        ran = await monthly_mt_check(session)
    assert ran is False  # ebben a hónapban már futott
