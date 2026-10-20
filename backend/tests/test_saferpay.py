"""Saferpay QR-fizetés modul: beállítások (titkos jelszó), rendelés-folyamat
(Initialize → notify → Assert + Capture → paid), QR, modul-kapcsoló."""

from __future__ import annotations

import pytest


async def _enable_modules(modules):
    import app.db as app_db
    from app.models import LicenseSettings
    from app.services.wfm import license as license_service

    factory = app_db.get_session_factory()
    async with factory() as session:
        row = LicenseSettings(id=1, plan="xl", enabled_modules=modules)
        session.add(row)
        await session.commit()
    license_service.invalidate_cache()


@pytest.fixture()
def fake_saferpay(monkeypatch):
    """A _post cseréje — éles hálózati hívás tesztben nincs."""
    calls: list[tuple[str, dict]] = []

    async def _fake_post(row, path, payload):
        calls.append((path, payload))
        if path.endswith("/PaymentPage/Initialize"):
            return {"Token": "tok-abc123", "RedirectUrl": "https://test.saferpay.com/vt2/pay/tok-abc123"}
        if path.endswith("/PaymentPage/Assert"):
            return {
                "Transaction": {"Status": "AUTHORIZED", "Id": "TR-777"},
                "PaymentMeans": {"Brand": {"Name": "VISA"}},
            }
        if path.endswith("/Transaction/Capture"):
            return {"Status": "CAPTURED"}
        raise AssertionError(f"váratlan Saferpay-hívás: {path}")

    from app.services.wfm import saferpay_service

    monkeypatch.setattr(saferpay_service, "_post", _fake_post)
    return calls


async def _configure(client, adm):
    res = await client.put(
        "/api/settings/saferpay",
        json={"customer_id": "123456", "terminal_id": "17900001",
              "api_username": "API_123456_7890", "api_password": "titkos-jelszo",
              "test_mode": True},
        headers=adm,
    )
    assert res.status_code == 200, res.text
    return res.json()


async def test_settings_roundtrip_and_module_gate(client, admin):
    _, adm = admin
    out = await _configure(client, adm)
    assert out["has_password"] is True
    assert "titkos" not in str(out)  # a jelszó sosem megy vissza

    # modul kikapcsolva → 403 (a Flotta-pult kapcsolja távolról)
    await _enable_modules(["cashbox", "stats"])
    res = await client.get("/api/settings/saferpay", headers=adm)
    assert res.status_code == 403
    res = await client.post(
        "/api/saferpay/orders",
        json={"product_name": "Cappuccino", "amount_huf": 600},
        headers=adm,
    )
    assert res.status_code == 403


async def test_order_flow_paid(client, admin, fake_saferpay):
    """QR-rendelés: Initialize → QR → notify → Assert+Capture → paid, és a
    gép a status végpontról tudja meg, hogy adhatja az italt."""
    _, adm = admin
    await _configure(client, adm)

    res = await client.post(
        "/api/saferpay/orders",
        json={"product_name": "Cappuccino", "amount_huf": 600, "machine_code": "174226"},
        headers=adm,
    )
    assert res.status_code == 201, res.text
    order = res.json()
    assert order["status"] == "created"
    assert order["order_no"].startswith("QR-")
    assert order["redirect_url"].startswith("https://test.saferpay.com/")
    # az Initialize a HUF-ot minor unitban (×100) küldi
    init_payload = fake_saferpay[0][1]
    assert init_payload["Payment"]["Amount"] == {"Value": "60000", "CurrencyCode": "HUF"}
    assert init_payload["TerminalId"] == "17900001"

    # QR SVG a vevőnek
    res = await client.get(order["qr_path"])
    assert res.status_code == 200
    assert b"<svg" in res.content

    # fizetés után a Saferpay meghívja a notify-t → Assert + Capture → paid
    res = await client.get(f"/api/pay/{order['id']}/notify/ok")
    assert res.status_code == 200
    paths = [p for p, _ in fake_saferpay]
    assert any(p.endswith("/PaymentPage/Assert") for p in paths)
    assert any(p.endswith("/Transaction/Capture") for p in paths)

    res = await client.get(f"/api/pay/{order['id']}/status")
    st = res.json()
    assert st["status"] == "paid"
    assert st["payment_means"] == "VISA"

    # ismételt notify idempotens (nincs második Capture)
    captures_before = sum(1 for p, _ in fake_saferpay if p.endswith("/Transaction/Capture"))
    await client.get(f"/api/pay/{order['id']}/notify/ok")
    captures_after = sum(1 for p, _ in fake_saferpay if p.endswith("/Transaction/Capture"))
    assert captures_before == captures_after

    # a visszatérő oldal emberbarát és sikeres állapotot mutat
    res = await client.get(f"/api/pay/{order['id']}/return")
    assert res.status_code == 200
    assert "Sikeres fizetés" in res.text

    # admin rendelés-lista
    rows = (await client.get("/api/saferpay/orders", headers=adm)).json()
    assert rows[0]["status"] == "paid"


async def test_products_and_menu(client, admin, fake_saferpay):
    """Termékek és árak az adminból; a gép a /api/pay/menu-t olvassa, és a
    rendelés ára a katalógusból jön (a gép nem árazhat felül)."""
    _, adm = admin
    await _configure(client, adm)

    espresso = (
        await client.post(
            "/api/saferpay/products",
            json={"name": "Espresso", "price_huf": 450, "sort_order": 1},
            headers=adm,
        )
    ).json()
    cappu = (
        await client.post(
            "/api/saferpay/products",
            json={"name": "Cappuccino", "price_huf": 600, "sort_order": 2},
            headers=adm,
        )
    ).json()
    # gép-specifikus ár felülírja az általánosat
    await client.post(
        "/api/saferpay/products",
        json={"name": "Cappuccino", "price_huf": 700, "machine_code": "174226", "sort_order": 2},
        headers=adm,
    )

    menu = (await client.get("/api/pay/menu")).json()
    assert {m["name"]: m["price_huf"] for m in menu} == {"Espresso": 450, "Cappuccino": 600}
    menu = (await client.get("/api/pay/menu?machine=174226")).json()
    assert {m["name"]: m["price_huf"] for m in menu} == {"Espresso": 450, "Cappuccino": 700}

    # rendelés katalógus-termékkel: ár a katalógusból
    res = await client.post(
        "/api/saferpay/orders",
        json={"product_id": espresso["id"], "machine_code": "174226"},
        headers=adm,
    )
    assert res.status_code == 201, res.text
    assert res.json()["amount_huf"] == 450
    init_payload = fake_saferpay[-1][1]
    assert init_payload["Payment"]["Amount"]["Value"] == "45000"

    # ár-módosítás + inaktiválás
    res = await client.put(
        f"/api/saferpay/products/{cappu['id']}",
        json={"name": "Cappuccino", "price_huf": 650, "sort_order": 2, "is_active": False},
        headers=adm,
    )
    assert res.status_code == 200
    menu = (await client.get("/api/pay/menu")).json()
    assert "Cappuccino" not in {m["name"] for m in menu}
    # inaktív termékre rendelés sem nyitható
    res = await client.post(
        "/api/saferpay/orders", json={"product_id": cappu["id"]}, headers=adm,
    )
    assert res.status_code == 404

    # törlés
    res = await client.delete(f"/api/saferpay/products/{espresso['id']}", headers=adm)
    assert res.status_code == 200
    rows = (await client.get("/api/saferpay/products", headers=adm)).json()
    assert all(r["id"] != espresso["id"] for r in rows)


async def test_vending_telemetry(client, admin):
    """Gép-nyilvántartás + telemetria: a gép a saját kulcsával jelent, rossz
    kulccsal nem; a pillanatkép és a napló is megvan."""
    _, adm = admin
    m = (
        await client.post(
            "/api/saferpay/machines",
            json={"machine_code": "174226", "name": "Jetinno JL30 — iroda"},
            headers=adm,
        )
    ).json()
    assert m["api_key"] and len(m["api_key"]) == 32

    # duplikált gép-kód: 409
    res = await client.post(
        "/api/saferpay/machines",
        json={"machine_code": "174226", "name": "Másik"},
        headers=adm,
    )
    assert res.status_code == 409

    # rossz kulcs → 403, jó kulcs → pillanatkép + last_seen
    res = await client.post(
        "/api/pay/telemetry",
        json={"machine_code": "174226", "key": "x" * 32, "data": {"viz": "OK"}},
    )
    assert res.status_code == 403
    res = await client.post(
        "/api/pay/telemetry",
        json={"machine_code": "174226", "key": m["api_key"],
              "data": {"viz": "OK", "bab_g": 480, "hiba": None, "verzio": "1.2"}},
    )
    assert res.status_code == 200, res.text

    rows = (await client.get("/api/saferpay/machines", headers=adm)).json()
    row = next(r for r in rows if r["machine_code"] == "174226")
    assert row["last_seen_at"] is not None
    assert row["telemetry"]["bab_g"] == 480

    hist = (
        await client.get(f"/api/saferpay/machines/{m['id']}/telemetry", headers=adm)
    ).json()
    assert len(hist) == 1 and hist[0]["data"]["viz"] == "OK"

    # kulcs-csere után a régi kulcs érvénytelen
    m2 = (
        await client.post(f"/api/saferpay/machines/{m['id']}/regen-key", headers=adm)
    ).json()
    assert m2["api_key"] != m["api_key"]
    res = await client.post(
        "/api/pay/telemetry",
        json={"machine_code": "174226", "key": m["api_key"], "data": {}},
    )
    assert res.status_code == 403


async def test_order_fail_and_not_configured(client, admin, fake_saferpay):
    _, adm = admin
    # beállítás nélkül: 422
    res = await client.post(
        "/api/saferpay/orders",
        json={"product_name": "Espresso", "amount_huf": 450},
        headers=adm,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "saferpay.not_configured"

    await _configure(client, adm)
    order = (
        await client.post(
            "/api/saferpay/orders",
            json={"product_name": "Espresso", "amount_huf": 450},
            headers=adm,
        )
    ).json()
    res = await client.get(f"/api/pay/{order['id']}/notify/fail")
    assert res.status_code == 200
    st = (await client.get(f"/api/pay/{order['id']}/status")).json()
    assert st["status"] == "failed"


async def test_vending_files_and_catalog(client, admin):
    """A gép fájlt tölt fel (katalógus = italok + adagok, napló-minta) a saját
    kulcsával; az admin listázza, olvassa, a legutóbbi katalógus JSON-ként jön."""
    import json

    _, adm = admin
    m = (
        await client.post(
            "/api/saferpay/machines",
            json={"machine_code": "174226", "name": "Jetinno JL30"},
            headers=adm,
        )
    ).json()

    # rossz kulcs / rossz fajta
    res = await client.post(
        "/api/pay/upload",
        json={"machine_code": "174226", "key": "x" * 32, "kind": "catalog", "name": "c.json", "text": "{}"},
    )
    assert res.status_code == 403
    res = await client.post(
        "/api/pay/upload",
        json={"machine_code": "174226", "key": m["api_key"], "kind": "virus", "name": "x", "text": "{}"},
    )
    assert res.status_code == 422

    catalog = {
        "products": [{"id": 29, "name": "Eszpresszó", "price": 450, "recipe": "意式浓缩"}],
        "recipes": [{"name": "意式浓缩", "steps": [{"canister": 170, "water_ml": 60, "coffee_g": 10}]}],
    }
    res = await client.post(
        "/api/pay/upload",
        json={"machine_code": "174226", "key": m["api_key"], "kind": "catalog",
              "name": "Product_2026_01_21_G14.product", "text": json.dumps(catalog, ensure_ascii=False)},
    )
    assert res.status_code == 200, res.text
    res = await client.post(
        "/api/pay/upload",
        json={"machine_code": "174226", "key": m["api_key"], "kind": "order_sample",
              "name": "order_20261010.txt", "text": "2026:10:10 08:01:02.000 [main] X order 订单开始\n"},
    )
    assert res.status_code == 200

    files = (await client.get(f"/api/saferpay/machines/{m['id']}/files", headers=adm)).json()
    assert {f["kind"] for f in files} == {"catalog", "order_sample"}
    sample = next(f for f in files if f["kind"] == "order_sample")
    one = (await client.get(f"/api/saferpay/machines/{m['id']}/files/{sample['id']}", headers=adm)).json()
    assert "订单开始" in one["text"] and one["size"] > 0

    cat = (await client.get(f"/api/saferpay/machines/{m['id']}/catalog", headers=adm)).json()
    assert cat["updated_at"] is not None
    assert cat["catalog"]["products"][0]["name"] == "Eszpresszó"
    assert cat["catalog"]["recipes"][0]["steps"][0]["coffee_g"] == 10

    # retenció: fajtánként legfeljebb 10 marad
    for i in range(12):
        await client.post(
            "/api/pay/upload",
            json={"machine_code": "174226", "key": m["api_key"], "kind": "order_sample",
                  "name": f"s{i}.txt", "text": "x"},
        )
    files = (await client.get(f"/api/saferpay/machines/{m['id']}/files", headers=adm)).json()
    assert sum(1 for f in files if f["kind"] == "order_sample") == 10

    # a feltöltés "életjel" is: last_seen beáll
    rows = (await client.get("/api/saferpay/machines", headers=adm)).json()
    assert next(r for r in rows if r["machine_code"] == "174226")["last_seen_at"] is not None


async def test_vending_promos(client, admin):
    """Promóció: kép feltöltése géphez → a gép manifestből látja, letölti,
    visszajelez (delivered); törlés → remove_pending → ügynök törli → sor megy."""
    import base64

    _, adm = admin
    m = (
        await client.post(
            "/api/saferpay/machines",
            json={"machine_code": "174226", "name": "Jetinno JL30"},
            headers=adm,
        )
    ).json()
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100 + b"\xff\xd9"
    b64 = base64.b64encode(jpeg).decode()

    # rossz slot / nem JPEG
    res = await client.post(
        f"/api/saferpay/machines/{m['id']}/promos", headers=adm,
        json={"slot": "falra", "title": "x.jpg", "data_b64": b64},
    )
    assert res.status_code == 422
    res = await client.post(
        f"/api/saferpay/machines/{m['id']}/promos", headers=adm,
        json={"slot": "making", "title": "x.jpg", "data_b64": base64.b64encode(b"nem kep" * 5).decode()},
    )
    assert res.status_code == 422

    res = await client.post(
        f"/api/saferpay/machines/{m['id']}/promos", headers=adm,
        json={"slot": "making", "title": "Őszi akció.png", "data_b64": b64,
              "thumb": "data:image/jpeg;base64,AAAA", "width": 1280, "height": 800},
    )
    assert res.status_code == 200, res.text
    promo = res.json()
    assert promo["filename"].startswith("xadmin_making_") and promo["filename"].endswith("_1280x800.jpg")
    assert promo["status"] == "pending" and promo["size"] == len(jpeg)

    # a gép: manifest (rossz kulcs 403), fájl letöltés, visszajelzés
    auth = {"machine_code": "174226", "key": m["api_key"]}
    res = await client.post("/api/pay/promos/manifest", json={"machine_code": "174226", "key": "x" * 32})
    assert res.status_code == 403
    man = (await client.post("/api/pay/promos/manifest", json=auth)).json()
    assert man["exclusive"] == {"making": False, "screensaver": False}
    assert len(man["items"]) == 1 and man["items"][0]["action"] == "put"
    res = await client.post(f"/api/pay/promos/{promo['id']}/file", json=auth)
    assert res.status_code == 200 and res.content == jpeg
    assert res.headers["x-sha256"] == man["items"][0]["sha256"]

    res = await client.post("/api/pay/promos/report", json={**auth, "results": [{"id": promo["id"], "ok": True}]})
    assert res.status_code == 200
    lst = (await client.get(f"/api/saferpay/machines/{m['id']}/promos", headers=adm)).json()
    assert lst["items"][0]["status"] == "delivered" and lst["items"][0]["delivered_at"]

    # kizárólagos kapcsoló
    res = await client.put(
        f"/api/saferpay/machines/{m['id']}/promo-settings", headers=adm,
        json={"exclusive_making": True, "exclusive_screensaver": False},
    )
    assert res.status_code == 200
    man = (await client.post("/api/pay/promos/manifest", json=auth)).json()
    assert man["exclusive"]["making"] is True

    # törlés kézbesített képnél: remove_pending → a gép törli → sor eltűnik
    res = await client.delete(f"/api/saferpay/machines/{m['id']}/promos/{promo['id']}", headers=adm)
    assert res.status_code == 200
    man = (await client.post("/api/pay/promos/manifest", json=auth)).json()
    assert man["items"][0]["action"] == "delete"
    await client.post("/api/pay/promos/report", json={**auth, "results": [{"id": promo["id"], "ok": True}]})
    lst = (await client.get(f"/api/saferpay/machines/{m['id']}/promos", headers=adm)).json()
    assert lst["items"] == []

    # hibás kézbesítés → failed + hibaüzenet; még nem kézbesített kép törlése azonnali
    p2 = (
        await client.post(
            f"/api/saferpay/machines/{m['id']}/promos", headers=adm,
            json={"slot": "screensaver", "title": "b.jpg", "data_b64": b64},
        )
    ).json()
    await client.post("/api/pay/promos/report", json={**auth, "results": [{"id": p2["id"], "ok": False, "error": "nincs írási engedély"}]})
    lst = (await client.get(f"/api/saferpay/machines/{m['id']}/promos", headers=adm)).json()
    assert lst["items"][0]["status"] == "failed" and "írási" in lst["items"][0]["error"]
    await client.delete(f"/api/saferpay/machines/{m['id']}/promos/{p2['id']}", headers=adm)
    lst = (await client.get(f"/api/saferpay/machines/{m['id']}/promos", headers=adm)).json()
    assert lst["items"] == []


async def test_vending_errors(client, admin):
    """Hibajelentés: a gép összevont hibákat küld → új ujjlenyomat felvétel,
    ismert → darabszám/utolsó frissül; lezárás; újra előjövő hiba újranyílik;
    a géplistán a nyitott hibák száma."""
    _, adm = admin
    m = (
        await client.post(
            "/api/saferpay/machines",
            json={"machine_code": "174226", "name": "Jetinno JL30"},
            headers=adm,
        )
    ).json()
    auth = {"machine_code": "174226", "key": m["api_key"]}
    res = await client.post("/api/pay/errors", json={"machine_code": "174226", "key": "x" * 32, "errors": []})
    assert res.status_code == 403

    res = await client.post("/api/pay/errors", json={**auth, "errors": [
        {"source": "exception", "message": "NdkWrapper: 锅炉超时 boiler timeout", "count": 3,
         "first_seen": "2026:10:20 08:01:02", "last_seen": "2026:10:20 09:15:00"},
        {"source": "operation", "message": "GeneralDialog: 显示除垢弹框", "count": 1,
         "first_seen": "2026:10:20 03:01:56", "last_seen": "2026:10:20 03:01:56"},
    ]})
    assert res.status_code == 200 and res.json()["new"] == 2

    rows = (await client.get(f"/api/saferpay/machines/{m['id']}/errors", headers=adm)).json()
    assert len(rows) == 2 and rows[0]["message"].startswith("NdkWrapper") and rows[0]["count"] == 3
    machines = (await client.get("/api/saferpay/machines", headers=adm)).json()
    assert next(x for x in machines if x["machine_code"] == "174226")["open_errors"] == 2

    # ugyanaz a hiba újra: nem új, darabszám nő, utolsó időpont frissül
    res = await client.post("/api/pay/errors", json={**auth, "errors": [
        {"source": "exception", "message": "NdkWrapper: 锅炉超时 boiler timeout", "count": 5,
         "first_seen": "2026:10:20 08:01:02", "last_seen": "2026:10:20 11:00:00"},
    ]})
    assert res.json()["new"] == 0
    rows = (await client.get(f"/api/saferpay/machines/{m['id']}/errors", headers=adm)).json()
    boiler = next(r for r in rows if r["message"].startswith("NdkWrapper"))
    assert boiler["count"] == 5 and boiler["last_seen"].startswith("2026-10-20T11:00")

    # lezárás → nem nyitott; a listában include_resolved-dal látszik
    res = await client.post(f"/api/saferpay/machines/{m['id']}/errors/{boiler['id']}/resolve", headers=adm)
    assert res.status_code == 200 and res.json()["resolved_at"]
    rows = (await client.get(f"/api/saferpay/machines/{m['id']}/errors", headers=adm)).json()
    assert len(rows) == 1
    rows = (await client.get(f"/api/saferpay/machines/{m['id']}/errors?include_resolved=true", headers=adm)).json()
    assert len(rows) == 2

    # a lezárás után újra előjövő hiba (későbbi last_seen) újranyílik és "új"-nak számít
    res = await client.post("/api/pay/errors", json={**auth, "errors": [
        {"source": "exception", "message": "NdkWrapper: 锅炉超时 boiler timeout", "count": 1,
         "first_seen": "2036:01:01 10:00:00", "last_seen": "2036:01:01 10:00:00"},
    ]})
    assert res.json()["new"] == 1
    rows = (await client.get(f"/api/saferpay/machines/{m['id']}/errors", headers=adm)).json()
    assert len(rows) == 2


async def test_agent_releases(client, admin):
    """Ügynök-APK kiadások: feltöltés (csak ZIP/APK), duplikált verziókód 409,
    a gép a kulcsával a legújabbat látja és tölti le; nincs kiadás → 404."""
    import base64

    _, adm = admin
    m = (
        await client.post(
            "/api/saferpay/machines",
            json={"machine_code": "174226", "name": "Jetinno JL30"},
            headers=adm,
        )
    ).json()
    auth = {"machine_code": "174226", "key": m["api_key"]}
    res = await client.post("/api/pay/agent/latest", json=auth)
    assert res.status_code == 404

    apk = b"PK\x03\x04" + b"\x00" * 200
    res = await client.post(
        "/api/saferpay/agent/releases", headers=adm,
        json={"version_code": 9, "version_name": "2.5", "filename": "XadminConfig.apk",
              "data_b64": base64.b64encode(b"nem apk" * 10).decode()},
    )
    assert res.status_code == 422
    res = await client.post(
        "/api/saferpay/agent/releases", headers=adm,
        json={"version_code": 9, "version_name": "2.5", "filename": "XadminConfig.apk",
              "note": "promóció + frissítés", "data_b64": base64.b64encode(apk).decode()},
    )
    assert res.status_code == 200, res.text
    rel = res.json()
    assert rel["size"] == len(apk) and rel["version_code"] == 9
    res = await client.post(
        "/api/saferpay/agent/releases", headers=adm,
        json={"version_code": 9, "version_name": "2.5b", "filename": "x.apk",
              "data_b64": base64.b64encode(apk).decode()},
    )
    assert res.status_code == 409

    latest = (await client.post("/api/pay/agent/latest", json=auth)).json()
    assert latest["version_code"] == 9 and latest["sha256"] == rel["sha256"] and "id" not in latest
    res = await client.post("/api/pay/agent/download", json=auth)
    assert res.status_code == 200 and res.content == apk
    assert res.headers["x-sha256"] == rel["sha256"]
    res = await client.post("/api/pay/agent/download", json={"machine_code": "174226", "key": "x" * 32})
    assert res.status_code == 403

    rows = (await client.get("/api/saferpay/agent/releases", headers=adm)).json()
    assert len(rows) == 1
    res = await client.delete(f"/api/saferpay/agent/releases/{rel['id']}", headers=adm)
    assert res.status_code == 200
    assert (await client.post("/api/pay/agent/latest", json=auth)).status_code == 404
