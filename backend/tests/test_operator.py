"""Üzemeltetői végpontok: tokenes védelem + licenc táv-állítása."""

from __future__ import annotations

from app.core.config import get_settings


async def test_operator_endpoints(client, admin, monkeypatch):
    _, adm = admin
    settings = get_settings()

    # token nélkül a végpontok TELJESEN zárva vannak
    monkeypatch.setattr(settings, "operator_token", "", raising=False)
    res = await client.get("/api/operator/status")
    assert res.status_code == 403

    monkeypatch.setattr(settings, "operator_token", "flotta-titok")

    # rossz vagy hiányzó token: tiltott
    res = await client.get("/api/operator/status")
    assert res.status_code == 403
    res = await client.get(
        "/api/operator/status", headers={"X-Operator-Token": "rossz"}
    )
    assert res.status_code == 403

    # jó token: állapot licenccel és kihasználtsággal
    ok = {"X-Operator-Token": "flotta-titok"}
    res = await client.get("/api/operator/status", headers=ok)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["app"] == "iwfm"
    assert out["plan"] == "xl"
    assert "usage" in out

    # licenc táv-állítása — az admin felülete ugyanazt látja
    res = await client.put(
        "/api/operator/license",
        json={"plan": "m", "valid_until": None, "customer_name": "Demo Kávé Kft."},
        headers=ok,
    )
    assert res.status_code == 200, res.text
    assert res.json()["plan"] == "m"
    res = await client.get("/api/settings/license/status", headers=adm)
    assert res.json()["plan"] == "m"
    assert res.json()["customer_name"] == "Demo Kávé Kft."

    # csomag-katalógus lenyomása: az ügyfél-oldali kártyák ebből épülnek
    res = await client.put(
        "/api/operator/plan-catalog",
        json={"plans": [
            {"code": "start", "name": "Start", "max_users": 1,
             "max_employees": 2, "price_monthly": 9900},
            {"code": "xl", "name": "XL — Korlátlan", "max_users": None,
             "max_employees": None, "price_monthly": 169900},
        ]},
        headers=ok,
    )
    assert res.status_code == 200, res.text
    assert res.json()["count"] == 2
    status = (await client.get("/api/settings/license/status", headers=adm)).json()
    codes = [p["code"] for p in status["plan_list"]]
    assert codes == ["start", "xl"]
    assert status["plan_list"][0]["price_monthly"] == 9900

    # csomagváltás-kérés: az ügyfél kér, a status mutatja, a jóváhagyás törli
    res = await client.post(
        "/api/settings/license/request", json={"plan": "xl"}, headers=adm
    )
    assert res.status_code == 200, res.text
    assert res.json()["requested_plan"] == "xl"
    op_status = (
        await client.get("/api/operator/status", headers=ok)
    ).json()
    assert op_status["requested_plan"] == "xl"
    # ismeretlen csomagra nem lehet kérni
    res = await client.post(
        "/api/settings/license/request", json={"plan": "nemletezo"}, headers=adm
    )
    assert res.status_code == 422
    # a jóváhagyás (operator licenc-mentés) lezárja a kérést
    await client.put(
        "/api/operator/license", json={"plan": "xl", "valid_until": None}, headers=ok
    )
    status = (await client.get("/api/settings/license/status", headers=adm)).json()
    assert status["requested_plan"] is None

    # a kérés vissza is vonható (a "start" a lenyomott katalógusban szerepel)
    await client.post(
        "/api/settings/license/request", json={"plan": "start"}, headers=adm
    )
    res = await client.post(
        "/api/settings/license/request", json={"plan": None}, headers=adm
    )
    assert res.json()["requested_plan"] is None

    # vissza korlátlanra, hogy más tesztet ne zavarjon
    await client.put(
        "/api/operator/license", json={"plan": "xl", "valid_until": None}, headers=ok
    )
