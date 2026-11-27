"""191. kör: helyadat a blokkolásnál — kioszk és telefon, távolság a telephelytől,
kötelező mód, telefonos blokkolás kapcsoló, napló."""

from __future__ import annotations

from tests.conftest import make_employee_record, make_user

SITE = {"name": "Központ", "address": "Budapest, Teszt utca 1.", "lat": 47.4979, "lng": 19.0402}
NEAR = {"lat": 47.4981, "lng": 19.0405, "accuracy_m": 12}      # ~30 m
FAR = {"lat": 47.5300, "lng": 19.0900, "accuracy_m": 20}       # ~5 km


async def _rules(client, adm, **extra):
    body = {"break_paid": False, "sunday_bonus_default": True, "group_by_site": True, **extra}
    res = await client.put("/api/settings/work-rules", json=body, headers=adm)
    assert res.status_code == 200, res.text
    return res.json()


async def test_clock_location_off_by_default(client, admin):
    _, adm = admin
    user, hdr = await make_user(email="geo191a@example.com", role="employee")
    await make_employee_record(user, last_name="Geo", first_name="Alap", employee_code="191001")
    out = await _rules(client, adm)
    assert out["clock_location"] == "off" and out["phone_clock"] is False and out["clock_radius_m"] == 300
    res = await client.post("/api/kiosk/preview", json={"code": "191001"})
    assert res.status_code == 200 and res.json()["location_mode"] == "off"
    # telefonos blokkolás alapból tilos
    res = await client.post("/api/me/clock", json={"geo": NEAR}, headers=hdr)
    assert res.status_code == 403 and res.json()["detail"]["code"] == "me.phone_clock_disabled"
    st = (await client.get("/api/me/clock", headers=hdr)).json()
    assert st["open"] is None and st["phone_clock"] is False and st["location_mode"] == "off"
    # érvénytelen mód → 422
    res = await client.put("/api/settings/work-rules", json={"clock_location": "gps"}, headers=adm)
    assert res.status_code == 422


async def test_kiosk_and_phone_clock_with_location(client, admin):
    _, adm = admin
    out = await _rules(client, adm, clock_location="required", clock_radius_m=300, phone_clock=True,
                       clock_sites=[SITE, {"name": "", "address": ""}])
    assert out["clock_location"] == "required" and out["phone_clock"] is True
    assert len(out["clock_sites"]) == 1 and out["clock_sites"][0]["lat"] == SITE["lat"]

    user, hdr = await make_user(email="geo191b@example.com", role="employee")
    emp = await make_employee_record(user, last_name="Geo", first_name="Gábor", employee_code="191002")

    # kioszk: a preview jelzi a kötelező helyadatot; nélküle 422
    res = await client.post("/api/kiosk/preview", json={"code": "191002"})
    assert res.status_code == 200 and res.json()["location_mode"] == "required"
    res = await client.post("/api/kiosk/clock", json={"code": "191002"})
    assert res.status_code == 422 and res.json()["detail"]["code"] == "kiosk.location_required"
    # beblokkolás a telephely mellett
    res = await client.post("/api/kiosk/clock", json={"code": "191002", "geo": NEAR})
    assert res.status_code == 200, res.text
    r = res.json()
    assert r["action"] == "in" and r["site"] == "Központ" and r["far"] is False and 0 <= r["dist_m"] <= 100

    # telefon: helyadat nélkül 422; messziről kiblokkolás → far
    res = await client.post("/api/me/clock", json={}, headers=hdr)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "me.location_required"
    res = await client.post("/api/me/clock", json={"geo": FAR}, headers=hdr)
    assert res.status_code == 200, res.text
    r = res.json()
    assert r["action"] == "out" and r["geo"]["far"] is True and r["geo"]["dist_m"] > 300 and r["geo"]["src"] == "phone"
    assert r["worked_minutes"] is not None

    # a vezetői lista mindkét helyadatot mutatja
    from datetime import date

    today = date.today().isoformat()
    rows = (await client.get(f"/api/time-entries?date_from={today}&date_to={today}", headers=adm)).json()
    mine = [e for e in rows if e["employee_id"] == str(emp.id)]
    assert len(mine) == 1
    assert mine[0]["in_geo"]["site"] == "Központ" and mine[0]["in_geo"]["src"] == "kiosk" and mine[0]["in_geo"]["far"] is False
    assert mine[0]["out_geo"]["far"] is True and mine[0]["out_geo"]["lat"] == FAR["lat"]

    # napló: telefonos kiblokkolás, távolsággal
    log = (await client.get("/api/audit?action=timeclock.phone_out", headers=adm)).json()
    items = log if isinstance(log, list) else log.get("items", [])
    assert items and items[0]["detail"]["far"] is True and items[0]["detail"]["site"] == "Központ"

    # opcionális mód: helyadat nélkül is megy, geo nélkül
    await _rules(client, adm, clock_location="optional", phone_clock=True, clock_sites=[SITE])
    res = await client.post("/api/me/clock", json={}, headers=hdr)
    assert res.status_code == 200 and res.json()["action"] == "in" and res.json()["geo"] is None


def test_haversine_and_far_tolerance():
    from app.services.wfm.clock_geo import haversine_m, normalize_sites

    assert abs(haversine_m(47.4979, 19.0402, 47.4979, 19.0402)) < 0.01
    d = haversine_m(47.4979, 19.0402, 47.5069, 19.0402)  # ~1 km északra
    assert 950 < d < 1050
    sites = normalize_sites([{"name": "A", "lat": "47.5", "lng": "19.0"}, {"name": "B", "lat": 999, "lng": 1}, {"address": "  "}])
    assert sites == [{"name": "A", "address": "", "lat": 47.5, "lng": 19.0}, {"name": "B", "address": "", "lat": None, "lng": None}]
