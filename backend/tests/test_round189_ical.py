"""189. kör: iCal naptár-előfizetés — saját link (token), publikus .ics
feladatokkal, publikált műszakkal és jóváhagyott távolléttel; link-csere."""

from __future__ import annotations

from datetime import date, timedelta

from tests.conftest import make_employee_record, make_user


async def test_ical_feed_and_rotate(client, admin, manager):
    import app.db as app_db
    from app.models import Shift, TimeOffRequest

    _, adm = admin
    _, mgr = manager
    user, hdr = await make_user(email="naptar189@example.com", role="employee")
    emp = await make_employee_record(user, last_name="Naptáros", first_name="Nóra")

    res = await client.get("/api/me/ical", headers=hdr)
    assert res.status_code == 200, res.text
    url = res.json()["url"]
    assert "/api/public/ical/" in url and url.endswith(".ics")
    token = url.rsplit("/", 1)[1][:-4]
    assert (await client.get("/api/me/ical", headers=hdr)).json()["url"] == url  # stabil

    today = date.today()
    res = await client.post("/api/tasks", json={"title": "Kávégép karbantartás, Pláza", "employee_id": str(emp.id), "due_date": (today + timedelta(days=2)).isoformat(), "description": "Vízkőtelenítés; szűrőcsere"}, headers=mgr)
    assert res.status_code == 201, res.text
    async with app_db.get_session_factory()() as db:
        from datetime import time as dtime

        db.add(Shift(employee_id=emp.id, work_date=today + timedelta(days=1), start_time=dtime(8, 0), end_time=dtime(16, 30), status="published", role_label="Szerviz", location="Bp. telephely"))
        db.add(Shift(employee_id=emp.id, work_date=today + timedelta(days=3), start_time=dtime(8, 0), end_time=dtime(16, 0), status="draft"))
        db.add(TimeOffRequest(employee_id=emp.id, type="annual", start_date=today + timedelta(days=10), end_date=today + timedelta(days=12), status="approved"))
        db.add(TimeOffRequest(employee_id=emp.id, type="annual", start_date=today + timedelta(days=20), end_date=today + timedelta(days=21), status="pending"))
        await db.commit()

    res = await client.get(f"/api/public/ical/{token}.ics")  # belépés nélkül
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/calendar")
    ics = res.text
    assert ics.startswith("BEGIN:VCALENDAR") and "END:VCALENDAR" in ics
    assert "X-WR-CALNAME:X-admin – " in ics
    assert ics.count("BEGIN:VEVENT") == 3  # feladat + publikált műszak + jóváhagyott távollét
    assert "SUMMARY:🗂️ Kávégép karbantartás\\, Pláza" in ics
    assert f"DTSTART;VALUE=DATE:{(today + timedelta(days=2)).strftime('%Y%m%d')}" in ics
    assert "SUMMARY:🗓️ Műszak · Szerviz" in ics and "LOCATION:Bp. telephely" in ics
    assert "SUMMARY:🏖️ Szabadság" in ics
    assert f"DTEND;VALUE=DATE:{(today + timedelta(days=13)).strftime('%Y%m%d')}" in ics  # a távollét vége + 1 nap
    assert "Vízkőtelenítés\\; szűrőcsere" in ics
    # sorok legfeljebb 75 oktett
    assert all(len(line.encode("utf-8")) <= 75 for line in ics.split("\r\n"))

    # új link: a régi 404
    res = await client.post("/api/me/ical/rotate", headers=hdr)
    assert res.status_code == 200 and res.json()["url"] != url
    assert (await client.get(f"/api/public/ical/{token}.ics")).status_code == 404
    new_token = res.json()["url"].rsplit("/", 1)[1][:-4]
    assert (await client.get(f"/api/public/ical/{new_token}.ics")).status_code == 200
    assert (await client.get("/api/public/ical/nincs.ics")).status_code == 404
    # dolgozói karton nélküli fiók: üres naptár, nem hiba
    assert (await client.get("/api/me/ical", headers=adm)).status_code == 200
