"""185. kör: SLA a szervizjegyeken — munkanapos óraszámítás, szerződéses és céges
alap SLA → határidők, sürgős felezés, első reakció, figyelő (figyelmeztetés +
lejárat események, jegyenként egyszer), havi riport."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.test_inventory import make_partner
from tests.test_service import make_asset


def _dt(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo is not None else d.replace(tzinfo=UTC)


def test_add_working_hours_skips_weekend_and_holiday():
    from zoneinfo import ZoneInfo

    from app.services.wfm.sla import add_working_hours

    tz = ZoneInfo("Europe/Budapest")
    fri = datetime(2026, 11, 27, 16, 0, tzinfo=tz)  # péntek
    assert add_working_hours(fri, 24).astimezone(tz) == datetime(2026, 11, 30, 16, 0, tzinfo=tz)  # hétfő 16:00
    assert add_working_hours(fri, 4).astimezone(tz) == datetime(2026, 11, 27, 20, 0, tzinfo=tz)
    # 2026-10-23 péntek ünnep: csütörtök 12:00 + 24h → hétfő 12:00
    thu = datetime(2026, 10, 22, 12, 0, tzinfo=tz)
    assert add_working_hours(thu, 24).astimezone(tz) == datetime(2026, 10, 26, 12, 0, tzinfo=tz)


async def test_contract_sla_sets_due_dates_and_high_priority_halves(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    # céges alap: 48 / 120 óra
    s = (await client.get("/api/settings/worksheet", headers=adm)).json()
    s.update({"sla_response_hours": 48, "sla_resolve_hours": 120})
    res = await client.put("/api/settings/worksheet", json=s, headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()["sla_response_hours"] == 48

    partner = await make_partner(client, mgr, name="SLA Partner")
    res = await client.post(f"/api/partners/{partner['id']}/contracts", json={
        "valid_from": "2026-01-01", "no_minimum": True, "sla_response_hours": 8, "sla_resolve_hours": 24,
    }, headers=mgr)
    assert res.status_code == 201, res.text
    assert res.json()["sla_response_hours"] == 8
    asset = await make_asset(client, mgr, barcode="SLA-1", serial_number="SLA-SN-1")
    await client.post(f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr)

    tk = (await client.post("/api/service", json={"title": "Nem fűt", "asset_id": asset["id"]}, headers=mgr)).json()
    assert tk["sla_response_due"] and tk["sla_resolve_due"]
    created = _dt(tk["created_at"])
    resp_due = _dt(tk["sla_response_due"])
    assert timedelta(hours=8) <= resp_due - created <= timedelta(hours=8 + 72)  # hétvége átugorva legfeljebb
    assert tk["sla"]["response"] in ("ok", "warning") and tk["sla"]["resolve"] == "ok"

    urgent = (await client.post("/api/service", json={"title": "Csöpög", "asset_id": asset["id"], "priority": "high"}, headers=mgr)).json()
    u_due = _dt(urgent["sla_response_due"])
    u_created = _dt(urgent["created_at"])
    assert (u_due - u_created) < (resp_due - created)

    # szerződés nélküli partner: céges alap (48h)
    other = (await client.post("/api/service", json={"title": "Általános"}, headers=mgr)).json()
    o_due = _dt(other["sla_response_due"])
    assert o_due - _dt(other["created_at"]) >= timedelta(hours=48)

    # első reakció: open → in_progress
    res = await client.patch(f"/api/service/{tk['id']}", json={"status": "in_progress"}, headers=mgr)
    assert res.status_code == 200, res.text
    assert res.json()["first_response_at"] is not None and res.json()["sla"]["response"] == "met"
    res = await client.patch(f"/api/service/{tk['id']}", json={"status": "done"}, headers=mgr)
    assert res.json()["sla"]["resolve"] == "met"


async def test_sla_watch_fires_once_per_phase_and_report(client, admin, manager, monkeypatch):
    import app.db as app_db
    import app.services.wfm.telegram as tg_mod
    from app.services.wfm.sla import run_sla_watch, sla_report

    _, adm = admin
    _, mgr = manager
    partner = await make_partner(client, mgr, name="Lejáró Kft.")
    res = await client.post(f"/api/partners/{partner['id']}/contracts", json={
        "valid_from": "2026-01-01", "no_minimum": True, "sla_response_hours": 2, "sla_resolve_hours": 10,
    }, headers=mgr)
    assert res.status_code == 201, res.text
    asset = await make_asset(client, mgr, barcode="SLA-2", serial_number="SLA-SN-2")
    await client.post(f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr)
    tk = (await client.post("/api/service", json={"title": "Nem indul", "asset_id": asset["id"]}, headers=mgr)).json()

    events: list[tuple[str, dict]] = []

    async def fake_notify(db, event, ctx):
        events.append((event, ctx))
        return True

    monkeypatch.setattr(tg_mod, "notify_event", fake_notify)
    resp_due = _dt(tk["sla_response_due"]).astimezone(UTC)
    res_due = _dt(tk["sla_resolve_due"]).astimezone(UTC)
    async with app_db.get_session_factory()() as db:
        assert await run_sla_watch(db, resp_due - timedelta(hours=1, minutes=30)) == 0  # még messze (20% = 1 óra ablak)
        assert await run_sla_watch(db, resp_due - timedelta(minutes=30)) == 1  # figyelmeztetés
        assert await run_sla_watch(db, resp_due - timedelta(minutes=10)) == 0  # már szólt
        assert await run_sla_watch(db, resp_due + timedelta(minutes=5)) == 1  # lejárt
        assert await run_sla_watch(db, resp_due + timedelta(minutes=50)) == 0
        assert await run_sla_watch(db, res_due + timedelta(hours=1)) == 1  # megoldás lejárt (a figyelmeztetés átugorva)
    kinds = [e for e, _ in events]
    assert kinds == ["ticket.sla_warning", "ticket.sla_breached", "ticket.sla_breached"]
    assert events[0][1]["fazis"] == "reakció" and events[2][1]["fazis"] == "megoldás" and events[0][1]["jegy"] == tk["ticket_no"]

    # a jelzések jegyenként egyszer: a flag-ek megmaradtak
    from sqlalchemy import select

    from app.models import ServiceTicket

    async with app_db.get_session_factory()() as db:
        row_tk = (await db.execute(select(ServiceTicket).where(ServiceTicket.ticket_no == tk["ticket_no"]))).scalar_one()
        assert row_tk.sla_flags == {"response_warned": True, "response_breached": True, "resolve_breached": True}
    # valós időben még nem járt le (2 óra múlva) → ok; a riport a lejárat utáni pillanatban csúszottnak látja
    one = next(x for x in (await client.get("/api/service", headers=mgr)).json() if x["id"] == tk["id"])
    assert one["sla"]["response"] in ("ok", "warning")
    month = tk["created_at"][:7]
    rep = (await client.get(f"/api/service/sla-report?month={month}", headers=mgr)).json()
    assert rep["total"]["with_sla"] >= 1 and rep["month"] == month
    async with app_db.get_session_factory()() as db:
        direct = await sla_report(db, month, now=res_due + timedelta(hours=1))
    assert any(latee["id"] == tk["id"] for latee in direct["late"])
    row = next(r for r in direct["partners"] if r["partner_id"] == partner["id"])
    assert row["response_pct"] == 0 and row["resolve_pct"] == 0
    assert (await client.get("/api/service/sla-report?month=2026-13", headers=mgr)).status_code == 422
