"""156. kör: régi rendszerből átemelt elszámolások (visszadátumozva, mellékhatás nélkül)."""

from __future__ import annotations

from tests.test_consignment import make_product


async def test_backfill_creates_backdated_settlements_idempotently(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    partner = (await client.post("/api/partners", json={"name": "Szondi Dohánybolt", "company_name": "Zália Dohány Bt.", "contact_name": "Kis Pál"}, headers=adm)).json()
    product = await make_product(client, adm, name="Tenebre")
    asset = (await client.post("/api/assets", json={"barcode": "95322576634", "name": "Jura X8", "counter": 41000, "default_product_id": product["id"]}, headers=adm)).json()

    items = [
        {"legacy_id": "23408", "created_at": "2026-10-13T10:32:00+01:00", "partner_name": "szondi dohanybolt", "company_name": "Zália Dohány Bt.",
         "payment_method": "cash", "total_gross": 85795, "settled_by": "Régi Rendszer Rezső",
         "machines": [{"barcode": "95322576634", "new_counter": 41324, "portions": 559, "price_per_portion": 115, "amount_net": 64285}],
         "lines": [{"product_name": "Tenebre", "qty": 2, "price": 3000}, {"product_name": "Ismeretlen cucc", "qty": 1, "price": 100}]},
        {"legacy_id": "99999", "created_at": "2026-10-20T09:00:00+01:00", "partner_name": "Nincs Ilyen Partner", "total_gross": 1000},
    ]
    # csak admin
    assert (await client.post("/api/settlements-backfill", json={"items": items}, headers=mgr)).status_code == 403
    # próbafutás nem ír
    dry = (await client.post("/api/settlements-backfill", json={"items": items, "dry_run": True}, headers=adm)).json()
    assert dry["dry_run"] and dry["created"] == 0 and dry["results"][0]["status"] == "would_create" and dry["results"][1]["status"] == "partner_not_found"
    assert (await client.get("/api/settlements", headers=adm)).json() == []

    res = await client.post("/api/settlements-backfill", json={"items": items}, headers=adm)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["created"] == 1 and out["results"][0]["status"] == "created" and out["results"][1]["status"] == "partner_not_found"
    rows = (await client.get("/api/settlements?date_from=2026-10-01&date_to=2026-10-31", headers=adm)).json()
    assert len(rows) == 1
    s = rows[0]
    assert s["partner_name"] == partner["name"] and s["created_at"].startswith("2026-10-13") and s["total_gross"] == 85795
    assert s["payment_status"] == "paid" and s["invoiced"] is False and s["note"].startswith("Régi rendszer #23408")
    assert s["settled_by_name"] == "Régi Rendszer Rezső"
    # a gép számlálója felment az átemelt állásra, készlet-levonás és kassza nélkül
    a = (await client.get(f"/api/assets/{asset['id']}", headers=adm)).json()
    assert a["counter"] == 41324
    stock = (await client.get(f"/api/partners/{partner['id']}/stock", headers=adm)).json()
    assert all(float(x.get("quantity", 0) or 0) == 0 for x in stock)
    # ismételt futás: nem duplikál
    again = (await client.post("/api/settlements-backfill", json={"items": items}, headers=adm)).json()
    assert again["created"] == 0 and again["skipped"] == 1
    assert len((await client.get("/api/settlements?date_from=2026-10-01&date_to=2026-10-31", headers=adm)).json()) == 1
