"""Mega-kör 2: szerződéses tételek, gépcsere, fotók, szerviz-költség,
havi riport, értesítések, lefedettség."""

from __future__ import annotations

PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="


async def test_contract_lines_on_settlement(client, manager):
    """Bérleti díjas + minimum-adagos gép → az elszámolás automatikusan
    hozzáteszi a bérleti díjat és a minimum-különbözetet."""
    from tests.test_consignment import make_product

    _, mgr = manager
    partner = (
        await client.post("/api/partners", json={"name": "Szerződéses Kávézó"}, headers=mgr)
    ).json()
    asset = (
        await client.post(
            "/api/assets",
            json={"barcode": "SZERZ-1", "name": "Jura X8",
                  "contract_min_portions": 600, "contract_below_min_price": 40,
                  "rent_fee": 6000},
            headers=mgr,
        )
    ).json()
    assert asset["rent_fee"] == 6000
    await client.post(
        f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr
    )

    product = await make_product(client, mgr)
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 1.0},
        headers=mgr,
    )
    res = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash",
              "lines": [{"product_id": product["id"], "physical_qty": 0.3}]},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    names = [ln["product_name"] for ln in body["lines"]]
    # a válaszban a termék-sor van; a mentett sorok között ott a bérleti díj +
    # különbözet — kérjük le a részletes nézetet
    detail = (await client.get(f"/api/settlements?partner_id={partner['id']}", headers=mgr)).json()
    assert detail[0]["total_gross"] > 0
    # fogyás 0.7 kg = 100 adag; első elszámolás → 30 nap → minimum 600 adag
    # → 500 adag különbözet × 40 Ft = 20 000 + bérleti díj 6 000 = 26 000 nettó
    # + kávé 100 × 50 = 5 000 → összesen 31 000 nettó
    assert abs(detail[0]["total_net"] - 31000) < 1, (names, detail[0])


async def test_swap_inherits_contract(client, manager):
    _, mgr = manager
    partner = (
        await client.post("/api/partners", json={"name": "Cserés Büfé"}, headers=mgr)
    ).json()
    old = (
        await client.post(
            "/api/assets",
            json={"barcode": "CSERE-REGI", "name": "Saeco Royal", "rent_fee": 5000,
                  "contract_min_portions": 300, "contract_below_min_price": 35},
            headers=mgr,
        )
    ).json()
    await client.post(
        f"/api/assets/{old['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr
    )
    new = (
        await client.post(
            "/api/assets", json={"barcode": "CSERE-UJ", "name": "Saeco Royal"}, headers=mgr
        )
    ).json()

    res = await client.post(
        f"/api/assets/{old['id']}/swap",
        json={"replacement_asset_id": new["id"], "note": "hibás daráló"},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["status"] == "deployed" and out["rent_fee"] == 5000
    assert out["contract_min_portions"] == 300

    old_detail = (await client.get(f"/api/assets/{old['id']}", headers=mgr)).json()
    assert old_detail["status"] == "maintenance"
    assert old_detail["partner_id"] is None


async def test_asset_photos(client, manager):
    from tests.test_service import make_asset

    _, mgr = manager
    asset = await make_asset(client, mgr, barcode="FOTO-1")
    up = await client.post(
        f"/api/assets/{asset['id']}/photos", json={"photos": [PNG]}, headers=mgr
    )
    assert up.status_code == 201, up.text
    photos = (await client.get(f"/api/assets/{asset['id']}/photos", headers=mgr)).json()
    assert len(photos) == 1
    img = await client.get(f"/api/assets/photos/{photos[0]['id']}", headers=mgr)
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    dele = await client.delete(f"/api/assets/photos/{photos[0]['id']}", headers=mgr)
    assert dele.status_code == 200
    assert (await client.get(f"/api/assets/{asset['id']}/photos", headers=mgr)).json() == []


async def test_ticket_parts_and_cost(client, manager):
    from tests.test_service import make_asset

    _, mgr = manager
    asset = await make_asset(client, mgr, barcode="KTSG-1")
    tk = (
        await client.post(
            "/api/service", json={"title": "Szivattyú csere", "asset_id": asset["id"]},
            headers=mgr,
        )
    ).json()
    res = await client.patch(
        f"/api/service/{tk['id']}",
        json={"parts": [{"name": "Ulka szivattyú", "qty": 1, "unit_price": 4500},
                        {"name": "Tömítés", "qty": 2, "unit_price": 250}],
              "labor_fee": 8000},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["total_cost"] == 13000


async def test_monthly_report_pdf(client, manager):
    from datetime import datetime

    from tests.test_consignment import _setup_settlement

    _, mgr = manager
    await _setup_settlement(client, mgr)
    now = datetime.now()
    res = await client.get(
        f"/api/settlements/monthly-report?year={now.year}&month={now.month}", headers=mgr
    )
    assert res.status_code == 200, res.text
    assert res.content[:5] == b"%PDF-"


async def test_notification_settings_and_digest(client, admin, manager, monkeypatch):
    from tests.test_consignment import _setup_settlement

    _, adm = admin
    _, mgr = manager
    await _setup_settlement(client, mgr)

    put = await client.put(
        "/api/settings/notifications",
        json={"daily_enabled": True, "recipients": "vezeto@ceg.hu", "send_hour": 0,
              "weekly_backup": False, "auto_receipt": False},
        headers=adm,
    )
    assert put.status_code == 200
    got = (await client.get("/api/settings/notifications", headers=adm)).json()
    assert got["daily_enabled"] is True and got["recipients"] == "vezeto@ceg.hu"

    # a digest szövege összeáll (SMTP nélkül is tesztelhető közvetlenül)
    import app.db as app_db
    from app.services.wfm.notifier import build_daily_digest

    async with app_db.get_session_factory()() as session:
        text = await build_daily_digest(session)
    assert "Iwfm napi összefoglaló" in text
    assert "Esedékes elszámolás" in text

    # a run_pending: SMTP hiányában nem küld, nem is hasal el
    from app.services.wfm.notifier import run_pending_notifications

    async with app_db.get_session_factory()() as session:
        result = await run_pending_notifications(session)
    assert result == {"daily": False, "backup": False, "auto_clockout": 0, "telegram_link": 0}


async def test_coverage_endpoint(client, manager):
    from tests.test_consignment import _setup_settlement

    _, mgr = manager
    await _setup_settlement(client, mgr)
    rows = (await client.get("/api/dashboard/coverage", headers=mgr)).json()
    assert isinstance(rows, list) and len(rows) >= 1
    assert {"city", "partners", "machines", "gross"} <= set(rows[0].keys())