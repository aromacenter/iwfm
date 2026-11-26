"""188. kör: szerelői teljesítmény-riport — lezárt jegyek + átfutás, visszatérő
hiba (30 napon belül ugyanarra a gépre), munkalapok/órák/érték, értékelés,
előző időszak Δ, kilógó jelölés."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from tests.conftest import make_employee_record, make_user
from tests.test_maintenance_fee import PNG_SIG


async def _tech(email: str, last: str):
    user, hdr = await make_user(email=email, role="szervizes")
    emp = await make_employee_record(user, last_name=last, first_name="Szerelő")
    return user, hdr, emp


async def test_technician_report(client, admin, manager, monkeypatch):
    import app.db as app_db
    from sqlalchemy import select

    from app.api.tech_stats import technician_report
    from app.models import ServiceTicket

    _, adm = admin
    _, mgr = manager
    u1, _, e1 = await _tech("gyors188@example.com", "Gyors")
    u2, _, e2 = await _tech("lassu188@example.com", "Lassú")
    res = await client.post("/api/assets", json={"barcode": "T188-1", "name": "Jura", "serial_number": "SN-188-1"}, headers=mgr)
    asset1 = res.json()
    assets = {}
    for i in range(2, 6):
        res = await client.post("/api/assets", json={"barcode": f"T188-{i}", "name": "Saeco", "serial_number": f"SN-188-{i}"}, headers=mgr)
        assets[i] = res.json()
    asset2, asset3, asset4, asset5 = assets[2], assets[3], assets[4], assets[5]

    async def ticket(title, asset, user_id, created, resolved):
        r = await client.post("/api/service", json={"title": title, "asset_id": asset["id"], "assigned_to_user_id": str(user_id)}, headers=mgr)
        assert r.status_code == 201, r.text
        tid = r.json()["id"]
        r = await client.patch(f"/api/service/{tid}", json={"status": "done"}, headers=mgr)
        assert r.status_code == 200
        async with app_db.get_session_factory()() as db:
            import uuid

            tk = await db.get(ServiceTicket, uuid.UUID(tid))
            tk.created_at, tk.resolved_at = created, resolved
            await db.commit()
        return tid

    now = datetime.now(UTC).replace(microsecond=0)
    d = lambda days, hours=0: now - timedelta(days=days, hours=hours)  # noqa: E731
    # Gyors: 3 jegy, 2 óra átfutás; az egyik gépre 10 nap múlva új jegy → visszatérő
    # (külön gépek, hogy csak a szándékolt legyen „visszatérő”)
    await ticket("Nem fűt", asset1, u1.id, d(20, 2), d(20))
    await ticket("Csöpög", asset2, u1.id, d(15, 2), d(15))
    await ticket("Zajos", asset3, u1.id, d(12, 2), d(12))
    repeat = await ticket("Megint nem fűt", asset1, u2.id, d(8, 40), d(8))  # Lassú: 40 óra átfutás; asset1-re 10 napon belül → Gyors visszatérője
    assert repeat
    await ticket("Régi jegy", asset4, u2.id, d(49, 10), d(49))  # előző időszak
    await ticket("Lassú 2", asset4, u2.id, d(4, 40), d(4))
    await ticket("Lassú 3", asset5, u2.id, d(3, 40), d(3))

    # munkalap Gyorsnak, lezárt feladattal, értékeléssel
    res = await client.post("/api/tasks", json={"title": "Munkalapos", "employee_id": str(e1.id), "due_date": date.today().isoformat(), "asset_id": asset1["id"]}, headers=mgr)
    assert res.status_code == 201, res.text
    task_id = res.json()["id"]
    res = await client.put(f"/api/tasks/{task_id}/worksheet", json={
        "work_description": "Szivattyú", "materials": [{"name": "Szivattyú", "qty": "1", "unit": "db", "cost_net": 8000, "price_net": 12000}],
        "works": [{"name": "Beszerelés", "cost_net": None, "price_net": 9000}], "hours_spent": 1.5,
        "employee_signature": PNG_SIG, "client_signature": PNG_SIG, "client_signer_name": "Vevő", "client_name": "Vevő Kft.",
    }, headers=mgr)
    assert res.status_code == 200, res.text
    token = res.json()["rating_token"]
    assert (await client.patch(f"/api/tasks/{task_id}", json={"status": "done"}, headers=mgr)).status_code == 200
    assert (await client.post(f"/api/public/rating/{token}", json={"rating": 5})).status_code == 200

    rep = (await client.get(f"/api/stats/technicians?date_from={(now - timedelta(days=30)).date()}&date_to={now.date()}", headers=mgr)).json()
    rows = {r["name"]: r for r in rep["rows"]}
    g, l_ = rows["Gyors Szerelő"], rows["Lassú Szerelő"]
    assert g["tickets_closed"] == 3 and g["avg_resolve_hours"] == 2.0 and g["repeat_count"] == 1 and g["repeat_pct"] == 33.3
    assert g["worksheets"] == 1 and g["hours"] == 1.5 and g["invoiced_net"] == 21000 and g["rating_avg"] == 5 and g["rating_count"] == 1
    assert l_["tickets_closed"] == 3 and l_["tickets_closed_prev"] == 1 and l_["avg_resolve_hours"] == 40.0 and l_["repeat_count"] == 0
    assert "slow" in l_["flags"] and "slow" not in g["flags"]
    assert rep["team_avg_resolve_hours"] == 21.0 and rep["team_repeat_pct"] == 16.7
    # időszak nélkül: minden
    async with app_db.get_session_factory()() as db:
        allrep = await technician_report(db, None, None)
    assert sum(r.tickets_closed for r in allrep.rows) == 7
    assert all(r.tickets_closed_prev == 0 for r in allrep.rows)
    # jog: dolgozó nem látja
    _, emp_hdr = await make_user(email="dolgozo188@example.com", role="szervizes")
    assert (await client.get("/api/stats/technicians", headers=emp_hdr)).status_code == 403
    async with app_db.get_session_factory()() as db:
        assert (await db.execute(select(ServiceTicket))).scalars().all()
