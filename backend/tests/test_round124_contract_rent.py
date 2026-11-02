"""124. kör (x-presso jegy 72cfa180): a fix bérleti díj a SZERZŐDÉSEN van —
ez kerül a havi számlára egy tételben, gépcsere nem változtatja; az
elszámoláskori bérleti díj is a szerződés díját használja."""

from __future__ import annotations

from datetime import date

from tests.test_inventory import asset_payload, make_partner
from tests.test_round102 import no_invoice  # noqa: F401 — a számla-mock fixture


async def _partner_with_machines(client, mgr, name, machines=2, fee=None):
    partner = await make_partner(client, mgr, name=name)
    ids = []
    for i in range(machines):
        a = (await client.post("/api/assets", json=asset_payload(
            barcode=f"R124-{abs(hash(name)) % 10**6}-{i}", serial_number=f"SN-{name}-{i}",
            name="Royal Professional", manufacturer="Saeco", tangible=True,
            **({"rent_fee": fee} if fee is not None else {}),
        ), headers=mgr)).json()
        await client.post(f"/api/assets/{a['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr)
        ids.append(a["id"])
    return partner, ids


async def test_contract_rent_fee_billed_once_regardless_of_machines(client, manager, no_invoice):
    import app.db as app_db
    from app.services.wfm.rental_billing import run_fixed_rental_billing

    _, mgr = manager
    today = date.today()
    partner, ids = await _partner_with_machines(client, mgr, "Szerződéses Bérlő", machines=2)  # gépeken NINCS díj
    res = await client.post(f"/api/partners/{partner['id']}/contracts", json={
        "valid_from": "2026-01-01", "no_minimum": True,
        "fixed_rental": True, "billing_day": min(today.day, 28), "rent_fee": 42000,
        "payment_method": "transfer", "payment_terms_days": 8,
    }, headers=mgr)
    assert res.status_code == 201, res.text
    assert res.json()["rent_fee"] == 42000
    # a szerződés-lista is mutatja
    rows = (await client.get("/api/contracts", headers=mgr)).json()
    row = next(r for r in (rows if isinstance(rows, list) else rows["contracts"]) if r["partner_id"] == partner["id"])
    assert row["rent_fee"] == 42000

    factory = app_db.get_session_factory()
    async with factory() as db:
        assert await run_fixed_rental_billing(db) == 1
    s = (await client.get(f"/api/settlements?partner_id={partner['id']}", headers=mgr)).json()[0]
    assert s["total_net"] == 42000 and s["total_gross"] == 53340
    det = (await client.get(f"/api/settlements/{s['id']}", headers=mgr)).json()
    lines = det.get("lines") or []
    assert len(lines) == 1 and lines[0]["amount_net"] == 42000 and "bérleti díj" in lines[0]["product_name"].lower()

    # gépcsere: a díj nem változik — következő hónapban is 42 000 (a havi őrt visszaállítjuk)
    repl = (await client.post("/api/assets", json=asset_payload(barcode="R124-REPL", serial_number="SN-R", name="Cseregép", tangible=True), headers=mgr)).json()
    sw = await client.post(f"/api/assets/{ids[0]}/swap", json={"replacement_asset_id": repl["id"], "old_counter": 100, "new_counter": 0}, headers=mgr)
    assert sw.status_code == 200, sw.text
    from sqlalchemy import select

    from app.models import PartnerContract

    async with factory() as db:
        c = (await db.execute(select(PartnerContract).where(PartnerContract.partner_id == __import__("uuid").UUID(partner["id"])))).scalar_one()
        c.rental_last_billed = None
        await db.commit()
        assert await run_fixed_rental_billing(db) == 1
    rows2 = (await client.get(f"/api/settlements?partner_id={partner['id']}", headers=mgr)).json()
    assert len(rows2) == 2 and all(r["total_net"] == 42000 for r in rows2)


async def test_legacy_machine_fee_still_works_without_contract_fee(client, manager, no_invoice):
    import app.db as app_db
    from app.services.wfm.rental_billing import run_fixed_rental_billing

    _, mgr = manager
    today = date.today()
    partner, _ids = await _partner_with_machines(client, mgr, "Régi Bérlő", machines=2, fee=15000)
    res = await client.post(f"/api/partners/{partner['id']}/contracts", json={
        "valid_from": "2026-01-01", "no_minimum": True,
        "fixed_rental": True, "billing_day": min(today.day, 28),
    }, headers=mgr)
    assert res.status_code == 201, res.text
    factory = app_db.get_session_factory()
    async with factory() as db:
        assert await run_fixed_rental_billing(db) == 1
    s = (await client.get(f"/api/settlements?partner_id={partner['id']}", headers=mgr)).json()[0]
    assert s["total_net"] == 30000  # 2 × 15 000 gépenként (régi mód)


async def test_contract_rent_fee_validation(client, manager):
    _, mgr = manager
    partner = await make_partner(client, mgr, name="Validáló Kft.")
    res = await client.post(f"/api/partners/{partner['id']}/contracts", json={
        "valid_from": "2026-01-01", "fixed_rental": True, "billing_day": 5, "rent_fee": -1,
    }, headers=mgr)
    assert res.status_code == 422
