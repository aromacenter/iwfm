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
