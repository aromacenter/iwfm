"""126. kör: a fix díjas automata elszámolás ciklusa (hetente / kéthetente / négyhetente)
és pontos időpontja (helyi idő) a szerződésen."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from tests.test_inventory import asset_payload, make_partner
from tests.test_round102 import no_invoice  # noqa: F401 — számla-mock

TZ = ZoneInfo("Europe/Budapest")


def _at(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=TZ).astimezone(UTC)


async def _rental_partner(client, mgr, name):
    partner = await make_partner(client, mgr, name=name)
    a = (await client.post("/api/assets", json=asset_payload(
        barcode=f"R126-{abs(hash(name)) % 10**6}", serial_number=f"SN-{name}", name="Royal", tangible=True,
    ), headers=mgr)).json()
    await client.post(f"/api/assets/{a['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr)
    return partner


async def test_weekly_cycle_and_billing_time(client, manager, no_invoice):
    import app.db as app_db
    from app.services.wfm.rental_billing import run_fixed_rental_billing

    _, mgr = manager
    partner = await _rental_partner(client, mgr, "Heti Bérlő")
    res = await client.post(f"/api/partners/{partner['id']}/contracts", json={
        "valid_from": "2026-01-01", "no_minimum": True, "fixed_rental": True,
        "rent_fee": 9000, "billing_cycle": "weekly", "billing_weekday": 0, "billing_time": "07:30",
        "payment_method": "transfer",
    }, headers=mgr)
    assert res.status_code == 201, res.text
    out = res.json()
    assert out["billing_cycle"] == "weekly" and out["billing_weekday"] == 0 and out["billing_time"] == "07:30"

    monday = date(2026, 6, 1)  # hétfő
    factory = app_db.get_session_factory()
    async with factory() as db:
        # hétfő 07:00 — az időpont előtt: nem készül
        assert await run_fixed_rental_billing(db, now=_at(monday, 7, 0)) == 0
        # hétfő 07:30 — elkészül
        assert await run_fixed_rental_billing(db, now=_at(monday, 7, 30)) == 1
        # kedd: nem (7 napon belül)
        assert await run_fixed_rental_billing(db, now=_at(monday + timedelta(days=1), 9, 0)) == 0
        # következő hétfő 08:00: újra
        assert await run_fixed_rental_billing(db, now=_at(monday + timedelta(days=7), 8, 0)) == 1
        # kimaradt hét (leállás): a 15. napon pótolja, függetlenül a hét napjától
        assert await run_fixed_rental_billing(db, now=_at(monday + timedelta(days=22), 10, 0)) == 1
    rows = (await client.get(f"/api/settlements?partner_id={partner['id']}", headers=mgr)).json()
    assert len(rows) == 3 and all(r["total_net"] == 9000 for r in rows)
    assert "hét" in rows[0]["note"]


async def test_monthly_cycle_respects_time_and_validation(client, manager, no_invoice):
    import app.db as app_db
    from app.services.wfm.rental_billing import run_fixed_rental_billing

    _, mgr = manager
    partner = await _rental_partner(client, mgr, "Havi Bérlő")
    res = await client.post(f"/api/partners/{partner['id']}/contracts", json={
        "valid_from": "2026-01-01", "no_minimum": True, "fixed_rental": True,
        "rent_fee": 42000, "billing_day": 10, "billing_time": "18:00",
    }, headers=mgr)
    assert res.status_code == 201, res.text
    factory = app_db.get_session_factory()
    async with factory() as db:
        assert await run_fixed_rental_billing(db, now=_at(date(2026, 6, 10), 9, 0)) == 0   # még nem 18:00
        assert await run_fixed_rental_billing(db, now=_at(date(2026, 6, 10), 18, 0)) == 1
        assert await run_fixed_rental_billing(db, now=_at(date(2026, 6, 25), 18, 0)) == 0   # a hónapban csak egyszer
        assert await run_fixed_rental_billing(db, now=_at(date(2026, 7, 12), 18, 5)) == 1   # következő hónap, kimaradt nap pótolva
    # hibás időformátum / ciklus: 422
    bad = await client.post(f"/api/partners/{partner['id']}/contracts", json={
        "valid_from": "2027-01-01", "fixed_rental": True, "billing_day": 1, "billing_time": "25:00",
    }, headers=mgr)
    assert bad.status_code == 422
    bad = await client.post(f"/api/partners/{partner['id']}/contracts", json={
        "valid_from": "2027-01-01", "fixed_rental": True, "billing_cycle": "daily",
    }, headers=mgr)
    assert bad.status_code == 422
