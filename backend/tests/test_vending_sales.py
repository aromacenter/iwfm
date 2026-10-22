"""Kávégép napi fogyás (ügynök 3.5): a telemetria `sales_days` listája
nap-összesítőbe + italonkénti táblába kerül (felülírva), a pillanatképbe nem;
a fogyás-mátrix végpont a katalógusból nevez; CSV export; 32 KB payload."""
from __future__ import annotations

import json
from datetime import date, timedelta


async def _machine(client, adm, code="174231"):
    res = await client.post("/api/saferpay/machines", json={"name": "Fogyás gép", "machine_code": code}, headers=adm)
    assert res.status_code == 201, res.text
    return res.json()


async def test_sales_days_upsert_and_matrix(client, admin):
    _, adm = admin
    m = await _machine(client, adm)
    auth = {"machine_code": m["machine_code"], "key": m["api_key"]}
    today, yday = date.today(), date.today() - timedelta(days=1)
    # katalógus a nevekhez (id 811 = Hot Milk, 801 = Espresso)
    cat = {"products": [{"id": 811, "product_id": 811, "name": "Hot Milk", "price": 0}, {"id": 801, "product_id": 801, "name": "Espresso", "price": 450}], "recipes": [], "files": [], "canisters": []}
    res = await client.post("/api/pay/upload", json={**auth, "kind": "catalog", "name": "JL30.product", "text": json.dumps(cat)})
    assert res.status_code == 200, res.text
    data = {
        "agent": "3.5", "orders_today": 5, "drinks_ok_today": 4, "counter_total": 1234, "counter_days": 9,
        "products_today": [{"id": 801, "key": "espresso", "count": 3}, {"id": 811, "key": "hot_milk", "count": 1}],
        "pay_today": {"free": 1, "card": 3},
        "sales_days": [
            {"day": yday.isoformat(), "orders": 7, "ok": 6, "products": [{"id": 801, "key": "espresso", "count": 6}], "pay": {"card": 6}},
            {"day": today.isoformat(), "orders": 5, "ok": 4, "products": [{"id": 801, "key": "espresso", "count": 3}, {"id": 811, "key": "hot_milk", "count": 1}], "pay": {"free": 1, "card": 3}},
        ],
    }
    res = await client.post("/api/pay/telemetry", json={**auth, "data": data})
    assert res.status_code == 200, res.text
    # a pillanatképben a mai termékek és a számláló igen, a sales_days nem
    mc = next(x for x in (await client.get("/api/saferpay/machines", headers=adm)).json() if x["id"] == m["id"])
    assert mc["drinks_today"] == 4 and mc["counter_total"] == 1234 and "sales_days" not in (mc["telemetry"] or {})
    assert mc["telemetry"]["products_today"][0]["count"] == 3
    # mátrix
    s = (await client.get(f"/api/saferpay/machines/{m['id']}/sales?days=7", headers=adm)).json()
    assert [d["drinks_ok"] for d in s["days"]] == [6, 4] and s["total_drinks"] == 10 and s["counter_total"] == 1234
    assert s["products"][0]["name"] == "Espresso" and s["products"][0]["total"] == 9
    assert s["products"][1]["name"] == "Hot Milk"
    assert s["matrix"][today.isoformat()]["811"] == 1 and s["pay_totals"] == {"card": 9, "free": 1}
    # felülírás: a mai nap újraküldve más számokkal → nem duplázódik
    data["sales_days"] = [{"day": today.isoformat(), "orders": 6, "ok": 5, "products": [{"id": 801, "key": "espresso", "count": 4}, {"id": 811, "key": "hot_milk", "count": 1}], "pay": {"card": 5}}]
    assert (await client.post("/api/pay/telemetry", json={**auth, "data": data})).status_code == 200
    s = (await client.get(f"/api/saferpay/machines/{m['id']}/sales?days=7", headers=adm)).json()
    assert [d["drinks_ok"] for d in s["days"]] == [6, 5] and s["matrix"][today.isoformat()]["801"] == 4
    # név a nameKey-ből, ha a katalógus nem ismeri
    data["sales_days"] = [{"day": today.isoformat(), "orders": 1, "ok": 1, "products": [{"id": 999, "key": "iced_latte", "count": 1}], "pay": {}}]
    await client.post("/api/pay/telemetry", json={**auth, "data": data})
    s = (await client.get(f"/api/saferpay/machines/{m['id']}/sales?days=7", headers=adm)).json()
    assert any(p["name"] == "Iced latte" for p in s["products"])
    # CSV
    res = await client.get(f"/api/saferpay/machines/{m['id']}/sales.csv?days=7", headers=adm)
    assert res.status_code == 200 and res.content.decode("utf-8").startswith("﻿Nap;") and "Espresso" in res.content.decode("utf-8")
    # 32 KB alatt elfér egy nagyobb lista is; felette 422
    big = {**data, "sales_days": [{"day": today.isoformat(), "orders": 1, "ok": 1, "products": [{"id": i, "key": f"k{i}", "count": 1} for i in range(300)], "pay": {}}]}
    assert (await client.post("/api/pay/telemetry", json={**auth, "data": big})).status_code == 200
    huge = {**data, "blob": "x" * 33000}
    assert (await client.post("/api/pay/telemetry", json={**auth, "data": huge})).status_code == 422
