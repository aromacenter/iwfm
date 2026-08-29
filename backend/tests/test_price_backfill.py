"""Régi árak betöltése a termék-megjegyzésekből + ár-történet napló."""

from __future__ import annotations


async def test_backfill_and_price_history(client, manager):
    _, mgr = manager

    # importált termék: 0 Ft-os ár, a régi ár a megjegyzésben
    res = await client.post(
        "/api/products",
        json={"name": "0,1 L tejkiöntő", "unit": "db", "price_per_portion": 0,
              "notes": "Xpresso kód: 413\nRégi eladási ár (nettó/egység): 2362.21 Ft"},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    p1 = res.json()

    # kézzel árazott termék: a backfill NEM nyúlhat hozzá
    res = await client.post(
        "/api/products",
        json={"name": "Szemes kávé", "price_per_portion": 55,
              "notes": "Régi eladási ár (nettó/egység): 40 Ft\nBeszerzési ár (Ft/kg, nettó): 4 500,50 Ft"},
        headers=mgr,
    )
    p2 = res.json()

    res = await client.post("/api/products/backfill-import-prices", json={}, headers=mgr)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["updated_price"] == 1  # csak a nullás termék
    assert out["updated_purchase"] == 1  # a kávé beszerzési ára üres volt

    rows = {r["id"]: r for r in (await client.get("/api/products", headers=mgr)).json()}
    assert abs(rows[p1["id"]]["price_per_portion"] - 2362.21) < 0.01
    assert rows[p2["id"]]["price_per_portion"] == 55  # nem írta felül
    assert abs(rows[p2["id"]]["purchase_price"] - 4500.5) < 0.01

    # ár-történet: létrehozás + import = 2 bejegyzés a kávénál
    hist = (
        await client.get(f"/api/products/{p2['id']}/price-history", headers=mgr)
    ).json()
    assert len(hist) == 2
    assert hist[0]["source"] == "import"

    # árváltoztatás új bejegyzést ír
    body = {**{k: rows[p1["id"]][k] for k in (
        "name", "category", "unit", "grams_per_portion", "vat_percent",
        "is_consignment", "low_stock_threshold", "purchase_price", "is_active",
        "notes",
    )}, "price_per_portion": 2500}
    res = await client.patch(f"/api/products/{p1['id']}", json=body, headers=mgr)
    assert res.status_code == 200, res.text
    hist = (
        await client.get(f"/api/products/{p1['id']}/price-history", headers=mgr)
    ).json()
    assert len(hist) == 3  # létrehozás + import + módosítás
    assert hist[0]["price_per_portion"] == 2500
