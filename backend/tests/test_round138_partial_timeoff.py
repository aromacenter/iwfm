"""138. kör: később jövök / korábban megyek kérelem — egy nap + időpont, önkiszolgáló
és vezetői felvétel, nem ütközik egymással, teljes napos távolléttel igen, nem
számít távollétnek a bérszámfejtésben és a beosztás-ütközésnél."""

from __future__ import annotations

from datetime import date, timedelta

from tests.test_tasks import make_emp


async def test_partial_timeoff_requests(client, manager):
    _, mgr = manager
    emp, emp_h = await make_emp(email="resz-tavollet@example.com")
    day = (date.today() + timedelta(days=3)).isoformat()

    # időpont nélkül 422; két napra 422; rossz időpont 422
    res = await client.post("/api/me/time-off", json={"type": "late_arrival", "start_date": day, "end_date": day}, headers=emp_h)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "timeoff.time_required"
    res = await client.post("/api/me/time-off", json={"type": "early_leave", "start_date": day, "end_date": (date.today() + timedelta(days=4)).isoformat(), "at_time": "15:00"}, headers=emp_h)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "timeoff.partial_one_day"
    res = await client.post("/api/me/time-off", json={"type": "early_leave", "start_date": day, "end_date": day, "at_time": "25:00"}, headers=emp_h)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "timeoff.bad_time"

    # később jövök 9:30 + ugyanazon a napon korábban megyek 15:00 → mindkettő mehet
    res = await client.post("/api/me/time-off", json={"type": "late_arrival", "start_date": day, "end_date": day, "at_time": "9:30", "minutes": 90, "reason": "orvos"}, headers=emp_h)
    assert res.status_code == 201, res.text
    late = res.json()
    assert late["at_time"] == "09:30" and late["minutes"] == 90 and late["status"] == "pending"
    res = await client.post("/api/me/time-off", json={"type": "early_leave", "start_date": day, "end_date": day, "at_time": "15:00"}, headers=emp_h)
    assert res.status_code == 201, res.text
    early = res.json()
    # teljes napos szabadság ugyanarra a napra → ütközik
    res = await client.post("/api/me/time-off", json={"type": "annual", "start_date": day, "end_date": day}, headers=emp_h)
    assert res.status_code == 409

    # vezetői döntés ugyanúgy megy; a lista mutatja az időpontot
    res = await client.patch(f"/api/time-off/{late['id']}/decide", json={"status": "approved"}, headers=mgr)
    assert res.status_code == 200 and res.json()["status"] == "approved"
    rows = {r["id"]: r for r in (await client.get("/api/time-off", headers=mgr)).json()}
    assert rows[late["id"]]["at_time"] == "09:30" and rows[early["id"]]["type"] == "early_leave"

    # vezetői felvétel azonnal jóváhagyva, másik napra
    other = (date.today() + timedelta(days=5)).isoformat()
    res = await client.post("/api/time-off", json={
        "employee_id": str(emp.id), "type": "early_leave", "start_date": other, "end_date": other,
        "at_time": "14:00", "auto_approve": True,
    }, headers=mgr)
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "approved" and res.json()["at_time"] == "14:00"
