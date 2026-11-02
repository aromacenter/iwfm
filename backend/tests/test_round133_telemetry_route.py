"""133. kör: dinamikus útvonal telemetriából — gép ↔ partner kapcsolat, az esedékes
lista telemetria-jelei (italok, kifogyás-becslés, nyitott hiba, offline) és okai."""

from __future__ import annotations

from datetime import date, timedelta

from tests.test_consignment import make_product
from tests.test_inventory import make_partner


async def _settle(client, mgr, partner, product, physical: float):
    res = await client.post("/api/settlements", json={
        "partner_id": partner["id"], "payment_method": "cash",
        "lines": [{"product_id": product["id"], "physical_qty": physical}],
    }, headers=mgr)
    assert res.status_code == 201, res.text
    return res.json()


async def test_machine_partner_link_and_due_telemetry(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    partner = await make_partner(client, mgr, name="Telemetriás Kávézó")
    product = await make_product(client, mgr)  # 7 g/adag
    # 10 kg kint → kb. 1428 adag; friss elszámolás (ma) → ütemezés szerint NEM esedékes
    await client.post(f"/api/partners/{partner['id']}/stock/replenish", json={"product_id": product["id"], "quantity": 10.0}, headers=mgr)
    await _settle(client, mgr, partner, product, physical=10.0)
    due = (await client.get("/api/settlements/due", headers=mgr)).json()
    assert partner["id"] not in [d["partner_id"] for d in due]

    # gép partnerhez kötve (rossz partner → 422)
    res = await client.post("/api/saferpay/machines", json={"name": "Pult gép", "machine_code": "174300", "partner_id": "nem-uuid"}, headers=adm)
    assert res.status_code == 422
    res = await client.post("/api/saferpay/machines", json={"name": "Pult gép", "machine_code": "174300", "partner_id": partner["id"]}, headers=adm)
    assert res.status_code == 201, res.text
    m = res.json()
    assert m["partner_id"] == partner["id"]
    auth = {"machine_code": "174300", "key": m["api_key"]}

    # még nincs telemetria-jel (offline, nincs ital) → bekerül "tel_offline" okkal
    due = {d["partner_id"]: d for d in (await client.get("/api/settlements/due", headers=mgr)).json()}
    row = due[partner["id"]]
    assert row["tel_machines"] == 1 and row["tel_offline"] is True and row["due_reasons"] == ["tel_offline"]

    # az ügynök jelent: 14 napon át napi 120 ital → ~1428 adag / 120 ≈ 11 nap → nem "kifogy", de online
    today = date.today()
    days = [{"day": (today - timedelta(days=i)).isoformat(), "orders": 125, "ok": 120} for i in range(14)]
    res = await client.post("/api/pay/telemetry", json={**auth, "data": {"agent": "3.6", "sales_days": days, "counter_total": 50000}})
    assert res.status_code == 200, res.text
    due = {d["partner_id"]: d for d in (await client.get("/api/settlements/due", headers=mgr)).json()}
    row = due.get(partner["id"])
    assert row is None or "tel_offline" not in row["due_reasons"]
    if row is not None:
        assert row["tel_daily_drinks"] == 120.0

    # 2 kg-ra csökkent a készlet (elszámolás) → ~285 adag / 120 ≈ 2 nap → tel_runs_out
    await _settle(client, mgr, partner, product, physical=2.0)
    due = {d["partner_id"]: d for d in (await client.get("/api/settlements/due", headers=mgr)).json()}
    row = due[partner["id"]]
    assert "tel_runs_out" in row["due_reasons"] and row["tel_days_left"] == 2 and row["days_left"] == 2
    assert row["tel_drinks_since"] == 120  # a mai nap italai az elszámolás napjától

    # nyitott gép-hiba → tel_error, és a lista elejére kerül
    res = await client.post("/api/pay/errors", json={**auth, "errors": [{"source": "exception", "message": "Boiler sensor timeout", "count": 2}]})
    assert res.status_code == 200, res.text
    rows = (await client.get("/api/settlements/due", headers=mgr)).json()
    assert rows[0]["partner_id"] == partner["id"] and rows[0]["tel_open_errors"] == 1 and "tel_error" in rows[0]["due_reasons"]

    # a gép-lista is mutatja a partnert; levétel partnerről → nincs telemetria-jel
    lst = next(x for x in (await client.get("/api/saferpay/machines", headers=adm)).json() if x["id"] == m["id"])
    assert lst["partner_name"] == "Telemetriás Kávézó"
    res = await client.put(f"/api/saferpay/machines/{m['id']}", json={"name": "Pult gép", "machine_code": "174300", "partner_id": None}, headers=adm)
    assert res.status_code == 200, res.text
    due = {d["partner_id"]: d for d in (await client.get("/api/settlements/due", headers=mgr)).json()}
    assert partner["id"] not in due or due[partner["id"]]["tel_machines"] == 0
