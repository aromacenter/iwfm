"""Jetinno hibanapló-tudástár: magyar diagnózis a gép kínai/angol soraiból,
kínai karakter nélkül; ismeretlen minta → általános; az errors végpont adja."""
from __future__ import annotations

import re

from app.services.wfm.vending_errors_kb import diagnose, strip_cjk

CJK = re.compile(r"[一-鿿]")


def _no_cjk(d: dict) -> bool:
    return not CJK.search(d["title"] + d["cause"] + " ".join(d["steps"]))


def test_kb_known_patterns():
    d = diagnose("operation", "GeneralDialog: 显示除垢弹框")
    assert d["code"] == "descale.required" and d["severity"] == "warn" and d["known"] and d["component"] == "GeneralDialog" and _no_cjk(d)
    assert any("Vízkőtelenítés" in s for s in d["steps"])
    d = diagnose("operation", "WaterFilterHelper: 更新除垢水量,,累计用水量:0,本次总用水量:225上次用水量171")
    assert d["code"] == "descale.update" and "225" in d["cause"] and "171" in d["cause"] and d["severity"] == "info"
    d = diagnose("operation", "WaterFilterHelper: 获取除垢累计: 28588")
    assert d["code"] == "descale.counter" and "28588" in d["cause"]
    d = diagnose("exception", "2026:10:20 09:40:51.002 [main] GrinderCtrl exception 咖啡豆缺料 beans empty canister=170")
    assert d["code"] == "beans.empty" and d["severity"] == "critical" and "170" in d["cause"] and _no_cjk(d)
    assert diagnose("exception", "BoilerCtrl: 加热超时")["code"] == "boiler.temperature"
    assert diagnose("exception", "java.net.UnknownHostException: backend")["code"] == "network.fault"
    assert diagnose("operation", "DoorSensor: 门未关")["code"] == "door.open"


def test_kb_unknown_and_strip():
    d = diagnose("exception", "FooCtrl: 某种未知错误 code=42")
    assert d["known"] is False and d["code"] == "unknown" and d["severity"] == "warn" and d["component"] == "FooCtrl" and _no_cjk(d)
    assert strip_cjk("WaterFilterHelper: 获取除垢累计: 28588") == "WaterFilterHelper: 28588"
    assert not CJK.search(strip_cjk("GeneralDialog: 显示除垢弹框"))


async def test_errors_endpoint_has_diagnosis(client, admin):
    _, adm = admin
    res = await client.post("/api/saferpay/machines", json={"name": "KB gép", "machine_code": "174299"}, headers=adm)
    assert res.status_code == 201, res.text
    m = res.json()
    key = m["api_key"]
    res = await client.post("/api/pay/errors", json={"machine_code": "174299", "key": key, "errors": [
        {"source": "operation", "message": "GeneralDialog: 显示除垢弹框", "count": 1},
    ]})
    assert res.status_code == 200, res.text
    rows = (await client.get(f"/api/saferpay/machines/{m['id']}/errors", headers=adm)).json()
    assert rows[0]["diagnosis"]["code"] == "descale.required" and "technical" in rows[0]
    assert not CJK.search(rows[0]["technical"])
    # AI-magyarázat: nincs AI beállítva → 422 ai.not_configured
    res = await client.post(f"/api/saferpay/machines/{m['id']}/errors/{rows[0]['id']}/explain", headers=adm)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "ai.not_configured"
