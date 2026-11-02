"""127. kör (ötlet #1): partner-egészség pontszám — fogyás-trend, fizetési
fegyelem, szerviz, látogatás; szint és okok; a lista-végpont, a térkép és az
adatlap is hozza."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import app.db as app_db
from app.models import Settlement, SettlementLine
from tests.test_inventory import asset_payload, make_partner


async def _partner_with_machine(client, mgr, name):
    partner = await make_partner(client, mgr, name=name)
    a = (await client.post("/api/assets", json=asset_payload(
        barcode=f"R127-{abs(hash(name)) % 10**6}", serial_number=f"SN-{name}", name="Royal", tangible=True,
    ), headers=mgr)).json()
    await client.post(f"/api/assets/{a['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr)
    return partner


async def _settlement(partner_id: str, days_ago: int, portions: float, gross: float = 10000.0,
                      paid: float | None = None, due_days_ago: int | None = None):
    factory = app_db.get_session_factory()
    async with factory() as session:
        created = datetime.now(UTC) - timedelta(days=days_ago)
        s = Settlement(
            id=uuid.uuid4(), partner_id=uuid.UUID(partner_id), settled_by_name="Teszt",
            payment_method="transfer", total_net=gross / 1.27, total_gross=gross, invoiced=True,
            payment_status="outstanding" if paid is not None and paid < gross else "paid",
            paid_amount=paid, created_at=created,
            due_date=(date.today() - timedelta(days=due_days_ago)) if due_days_ago is not None else None,
        )
        session.add(s)
        await session.flush()
        session.add(SettlementLine(
            settlement_id=s.id, product_id=None, product_name="Kávé", previous_qty=0.0, physical_qty=0.0,
            consumed_qty=portions / 143.0, portions=portions, price_per_portion=70.0, vat_percent=27,
            amount_net=portions * 70.0,
        ))
        await session.commit()


async def test_health_levels_and_reasons(client, manager):
    _, mgr = manager
    good = await _partner_with_machine(client, mgr, "Jó Partner")
    risk = await _partner_with_machine(client, mgr, "Bajos Partner")
    nomach = await make_partner(client, mgr, name="Csak Ügyfél")

    # jó: stabil fogyás, friss elszámolás, minden fizetve
    await _settlement(good["id"], 120, 1000, paid=None)
    await _settlement(good["id"], 10, 1050, paid=None)
    # bajos: fogyás felére esett, 2 lejárt tétel (egyik 70 napja), régen volt elszámolás
    await _settlement(risk["id"], 150, 1200, gross=20000, paid=0, due_days_ago=70)
    await _settlement(risk["id"], 100, 1100, gross=20000, paid=5000, due_days_ago=40)
    await _settlement(risk["id"], 75, 400, gross=8000, paid=None)

    rows = (await client.get("/api/partners/health", headers=mgr)).json()
    by = {r["partner_id"]: r for r in rows}
    assert nomach["id"] not in by  # gép nélküli ügyfélre nincs pontszám
    g, r = by[good["id"]], by[risk["id"]]
    assert g["level"] == "good" and g["score"] >= 70 and g["reasons"] == []
    assert g["components"]["payment"] == 100 and g["components"]["visit"] == 100
    assert r["level"] == "risk" and r["score"] < 45
    assert r["overdue_items"] == 2 and r["outstanding"] == 35000
    assert any("lejárt" in x for x in r["reasons"]) and any("napja nem volt elszámolás" in x for x in r["reasons"])
    assert any("Fogyás" in x and "esett" in x for x in r["reasons"])
    assert r["components"]["payment"] == 0  # 60 napnál régebbi lejárt → 0
    assert rows[0]["partner_id"] == risk["id"]  # a legrosszabb elöl

    # térkép és adatlap is hozza
    mrows = (await client.get("/api/stats/map", headers=mgr)).json()
    m = next(x for x in mrows if x["id"] == risk["id"])
    assert m["health_level"] == "risk" and m["health_score"] == r["score"] and m["health_reasons"]
    assert next(x for x in mrows if x["id"] == nomach["id"])["health_level"] is None
    ov = (await client.get(f"/api/partners/{good['id']}/overview", headers=mgr)).json()
    assert ov["health"]["level"] == "good"
    assert (await client.get(f"/api/partners/{nomach['id']}/overview", headers=mgr)).json()["health"] is None


async def test_health_without_settlements_is_watch(client, manager):
    _, mgr = manager
    p = await _partner_with_machine(client, mgr, "Új Partner")
    rows = (await client.get("/api/partners/health", headers=mgr)).json()
    h = next(r for r in rows if r["partner_id"] == p["id"])
    assert h["components"]["visit"] == 40 and "Még nem volt elszámolás" in h["reasons"]
    assert h["level"] in ("watch", "good")
