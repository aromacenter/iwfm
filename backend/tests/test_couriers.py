"""Több-futáros réteg: beállítások (write-only), diszpécser, modul-kapuk,
adapter-mezőleképezések (mockolt futár-API-kkal)."""

from __future__ import annotations

import base64

from app.services.wfm import couriers

RECIPIENT = {
    "recipient_name": "Vevő Vince", "recipient_zip": "1051",
    "recipient_city": "Budapest", "recipient_street": "Fő utca",
    "recipient_house": "3",
}


async def test_courier_settings_write_only(client, admin):
    _, adm = admin
    res = await client.put(
        "/api/settings/courier/foxpost",
        json={"username": "fox@example.com", "password": "titok",
              "api_key": "kulcs123", "test_mode": True},
        headers=adm,
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["username"] == "fox@example.com"
    assert out["has_password"] is True and out["has_api_key"] is True
    assert "password" not in out and "api_key" not in out

    # üres titkos mező = marad; "-" = törlés
    await client.put(
        "/api/settings/courier/foxpost",
        json={"username": "fox@example.com", "password": "", "api_key": "-",
              "test_mode": True},
        headers=adm,
    )
    out = (await client.get("/api/settings/courier/foxpost", headers=adm)).json()
    assert out["has_password"] is True
    assert out["has_api_key"] is False

    res = await client.get("/api/settings/courier/ufo", headers=adm)
    assert res.status_code == 404


async def test_foxpost_parcel_flow(client, admin, manager, monkeypatch):
    _, adm = admin
    _, mgr = manager
    await client.put(
        "/api/settings/courier/foxpost",
        json={"username": "u", "password": "p", "api_key": "k", "test_mode": True},
        headers=adm,
    )

    calls: list = []

    async def fake_create(db, cfg, **kw):
        calls.append(kw)
        assert kw["recipient"]["apm_id"] == "HU123"
        return {"tracking_number": "CLFOX0001", "carrier_ref": "555001",
                "label_pdf": b"%PDF-1.4 fox", "test_mode": True}

    async def fake_statuses(db, cfg, tracking, ref):
        assert ref == "555001"
        return [{"date": "2026-08-29", "description": "Csomag kézbesítve",
                 "depot": "", "code": "OK"}]

    deleted: list = []

    async def fake_delete(db, cfg, tracking, ref):
        deleted.append(ref)

    monkeypatch.setitem(
        couriers._ADAPTERS, "foxpost", (fake_create, fake_statuses, fake_delete)
    )

    res = await client.post(
        "/api/gls",
        json={**RECIPIENT, "carrier": "foxpost", "apm_id": "HU123",
              "cod_amount": 4990},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    parcel = res.json()
    assert parcel["carrier"] == "foxpost"
    assert parcel["parcel_number"] == "CLFOX0001"
    assert parcel["test_mode"] is True
    assert parcel["can_delete"] is True

    # címke-PDF visszakérhető
    res = await client.get(f"/api/gls/{parcel['id']}/label", headers=mgr)
    assert res.status_code == 200 and res.content.startswith(b"%PDF")

    # nyomkövetés a FoxPost-adapterrel
    res = await client.post(f"/api/gls/{parcel['id']}/refresh-status", headers=mgr)
    assert res.json()["status_key"] == "delivered"

    # kézbesítve már nem törölhető
    res = await client.delete(f"/api/gls/{parcel['id']}", headers=mgr)
    assert res.status_code == 422

    # új (created) csomag törlése a futárnál is töröl
    res = await client.post(
        "/api/gls", json={**RECIPIENT, "carrier": "foxpost", "apm_id": "HU123"},
        headers=mgr,
    )
    pid = res.json()["id"]
    res = await client.delete(f"/api/gls/{pid}", headers=mgr)
    assert res.status_code == 200, res.text
    assert deleted == ["555001"]


async def test_carrier_module_gate(client, admin, manager, monkeypatch):
    _, adm = admin
    _, mgr = manager
    await client.put(
        "/api/settings/license",
        json={"plan": "m", "valid_until": None, "enabled_modules": ["gls"]},
        headers=adm,
    )
    res = await client.post(
        "/api/gls", json={**RECIPIENT, "carrier": "foxpost"}, headers=mgr
    )
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "license.module_disabled"
    # a lista él, mert a gls modul be van kapcsolva
    assert (await client.get("/api/gls", headers=mgr)).status_code == 200
    # futár-beállítás is zárva kikapcsolt modulnál
    res = await client.get("/api/settings/courier/foxpost", headers=adm)
    assert res.status_code == 403
    await client.put(
        "/api/settings/license",
        json={"plan": "xl", "valid_until": None, "enabled_modules": None},
        headers=adm,
    )


async def test_dpd_field_mapping(client, admin, manager, monkeypatch):
    _, adm = admin
    _, mgr = manager
    await client.put(
        "/api/settings/courier/dpd",
        json={"username": "u", "password": "p", "api_key": "k", "test_mode": False},
        headers=adm,
    )
    posts: list = []

    async def fake_post(cfg, path, data):
        posts.append({"path": path, "data": data})
        if path == "parcel_import.php":
            return {"status": "ok", "pl_number": ["16300000000001"]}
        if path == "parcel_print.php":
            return {"status": "ok", "pdf": base64.b64encode(b"%PDF-1.4 dpd").decode()}
        return {"status": "ok"}

    monkeypatch.setattr(couriers, "_dpd_post", fake_post)
    res = await client.post(
        "/api/gls",
        json={**RECIPIENT, "carrier": "dpd", "cod_amount": 12990,
              "weight_kg": 2.5, "content": "Kávégép alkatrész"},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["parcel_number"] == "16300000000001"
    imp = next(p for p in posts if p["path"] == "parcel_import.php")["data"]
    assert imp["parcel_type"] == "D-COD"
    assert imp["cod_amount"] == 12990
    assert imp["weight"] == 2.5
    assert imp["pcode"] == "1051"


async def test_mpl_field_mapping(client, admin, manager, monkeypatch):
    _, adm = admin
    _, mgr = manager
    await client.put(
        "/api/settings/courier/mpl",
        json={"client_id": "cid", "client_secret": "cs", "accounting_code": "123",
              "agreement": "999", "test_mode": False},
        headers=adm,
    )
    calls: list = []

    class FakeRes:
        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    async def fake_call(cfg, method, path, json_body=None):
        calls.append({"method": method, "path": path, "body": json_body})
        if method == "POST" and path == "/v2/mplapi/shipments":
            return FakeRes([
                {"trackingNumber": "PNVF001",
                 "label": base64.b64encode(b"%PDF-1.4 mpl").decode(),
                 "errors": None}
            ])
        return FakeRes([])

    monkeypatch.setattr(couriers, "_mpl_call", fake_call)
    res = await client.post(
        "/api/gls",
        json={**RECIPIENT, "carrier": "mpl", "cod_amount": 8000, "weight_kg": 1.2},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["parcel_number"] == "PNVF001"
    body = calls[0]["body"][0]
    assert body["item"][0]["weight"] == {"value": 1200, "unit": "G"}
    assert "K_UVT" in body["item"][0]["services"]["extra"]
    assert body["item"][0]["services"]["cod"] == 8000
    assert body["recipient"]["address"]["postCode"] == "1051"
