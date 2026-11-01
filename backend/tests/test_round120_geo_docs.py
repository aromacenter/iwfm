"""120. kör (x-presso jegyek): térkép — a hiányzó partnereknél látszik, hogy a
geokódolás még nem futott / nem találta a címet; partner-bizonylatok — alapból
csak a rendszerben (CASH_EPOCH után) készült elszámolások, a régiek `legacy`
jelöléssel kérésre."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

import app.db as app_db
from app.models import Partner, Settlement
from tests.test_inventory import make_partner


async def test_geocode_attempt_recorded_and_shown_on_map(client, manager, monkeypatch):
    _, mgr = manager
    partner = await make_partner(client, mgr, name="Nem Található Kft.")
    rows = (await client.get("/api/stats/map", headers=mgr)).json()
    row = next(r for r in rows if r["id"] == partner["id"])
    assert row["lat"] is None and row["geocode_error"] is None and row["geocode_attempted_at"] is None

    # a háttér-geokódoló nem találja a címet → not_found + időbélyeg
    from app.services.wfm import geocode as geo

    async def _none(*a, **k):
        return None

    monkeypatch.setattr(geo, "geocode_address", _none)
    await geo.geocode_partner_background(uuid.UUID(partner["id"]))
    rows = (await client.get("/api/stats/map", headers=mgr)).json()
    row = next(r for r in rows if r["id"] == partner["id"])
    assert row["geocode_error"] == "not_found" and row["geocode_attempted_at"] is not None and row["lat"] is None

    # sikeres kísérlet törli a hibát és koordinátát ad
    async def _ok(*a, **k):
        return (47.5, 19.05)

    monkeypatch.setattr(geo, "geocode_address", _ok)
    await geo.geocode_partner_background(uuid.UUID(partner["id"]))
    rows = (await client.get("/api/stats/map", headers=mgr)).json()
    row = next(r for r in rows if r["id"] == partner["id"])
    assert row["geocode_error"] is None and row["lat"] == 47.5

    # hálózati hiba → error, a partner mentése nem borul
    async def _boom(*a, **k):
        raise RuntimeError("nominatim down")

    monkeypatch.setattr(geo, "geocode_address", _boom)
    await geo.geocode_partner_background(uuid.UUID(partner["id"]))
    factory = app_db.get_session_factory()
    async with factory() as session:
        p = (await session.execute(select(Partner).where(Partner.id == uuid.UUID(partner["id"])))).scalar_one()
        assert p.geocode_error == "error" and p.lat == 47.5


async def test_partner_documents_scope_system_hides_legacy(client, manager):
    _, mgr = manager
    partner = await make_partner(client, mgr, name="Bizonylat Kft.")
    from app.api.stats import CASH_EPOCH

    epoch = datetime.combine(CASH_EPOCH, datetime.min.time(), tzinfo=UTC)
    factory = app_db.get_session_factory()
    async with factory() as session:
        for created, invoiced, doc in (
            (epoch - timedelta(days=200), True, "OLD-1"),   # migrált, Billingóban létező számla
            (epoch - timedelta(days=10), False, None),       # migrált, számla nélkül
            (epoch + timedelta(days=3), True, "NEW-1"),      # a rendszerben készült
        ):
            session.add(Settlement(
                id=uuid.uuid4(), partner_id=uuid.UUID(partner["id"]), settled_by_name="Teszt",
                payment_method="transfer", total_net=1000.0, total_gross=1270.0, invoiced=invoiced,
                billingo_document_id=("doc-" + doc) if doc else None, billingo_document_number=doc,
                payment_status="none", paid_amount=0.0, created_at=created,
            ))
        await session.commit()

    sys_rows = (await client.get(f"/api/settlements?partner_id={partner['id']}&scope=system", headers=mgr)).json()
    assert len(sys_rows) == 1 and sys_rows[0]["billingo_document_number"] == "NEW-1" and sys_rows[0]["legacy"] is False
    all_rows = (await client.get(f"/api/settlements?partner_id={partner['id']}&scope=all", headers=mgr)).json()
    assert len(all_rows) == 3
    legacy = [r for r in all_rows if r["legacy"]]
    assert len(legacy) == 2 and all(r["created_at"] < epoch.isoformat() for r in legacy)
    # alapértelmezés (scope nélkül) változatlan: minden sor
    assert len((await client.get(f"/api/settlements?partner_id={partner['id']}", headers=mgr)).json()) == 3
    assert (await client.get(f"/api/settlements?partner_id={partner['id']}&scope=x", headers=mgr)).status_code == 422
