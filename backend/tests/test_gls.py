"""GLS-integráció: beállítások (titkosított jelszó, maszkolt GET), címke-
készítés utánvéttel (mockolt MyGLS), címke-újranyomtatás, státusz-frissítés."""

from __future__ import annotations

GLS_SETTINGS = {
    "username": "teszt@x-presso.hu",
    "password": "titok123",
    "client_number": "100123456",
    "test_mode": True,
    "printer_type": "A4_2x2",
    "sender_name": "X-Presso Coffee Kft.",
    "sender_zip": "1134",
    "sender_city": "Budapest",
    "sender_street": "Lehel utca",
    "sender_house": "12",
    "sender_phone": "+36301234567",
    "sender_email": "hello@x-presso.hu",
}


def _fake_call(calls: list, statuses: list | None = None):
    async def fake(cfg, method, payload):
        calls.append({"cfg": cfg, "method": method, "payload": payload})
        if method == "PrintLabels":
            return {
                "PrintLabelsInfoList": [
                    {"ParcelNumber": 12345678901, "ParcelId": 555}
                ],
                "Labels": list(b"%PDF-1.4 gls-cimke"),
                "PrintLabelsErrorList": [],
            }
        if method == "GetParcelStatuses":
            return {
                "ParcelStatusList": statuses if statuses is not None else [
                    {"StatusDescription": "Csomag kézbesítve", "StatusDate": "2026-08-27",
                     "DepotCity": "Budapest"},
                    {"StatusDescription": "Kiszállítás alatt", "StatusDate": "2026-08-27",
                     "DepotCity": "Budapest"},
                    {"StatusDescription": "A csomag a depóba érkezett",
                     "StatusDate": "2026-08-26", "DepotCity": "Budapest"},
                ],
                "GetParcelStatusErrors": [],
            }
        if method == "DeleteLabels":
            return {"DeleteLabelsErrorList": []}
        raise AssertionError(f"váratlan metódus: {method}")

    return fake


async def test_gls_settings_and_parcel_flow(client, admin, manager, monkeypatch):
    _, adm = admin
    _, mgr = manager

    # beállítás nélkül a címke-készítés 422
    res = await client.post(
        "/api/gls",
        json={"recipient_name": "Vevő Vince", "recipient_zip": "1051",
              "recipient_city": "Budapest", "recipient_street": "Fő utca",
              "recipient_house": "1"},
        headers=mgr,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "gls.not_configured"

    # beállítások: csak admin; a GET maszkolt (jelszó sosem jön vissza)
    res = await client.put("/api/settings/gls", json=GLS_SETTINGS, headers=mgr)
    assert res.status_code in (401, 403)
    res = await client.put("/api/settings/gls", json=GLS_SETTINGS, headers=adm)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["has_password"] is True
    assert "password" not in out
    res = await client.get("/api/settings/gls", headers=adm)
    assert res.json()["username"] == "teszt@x-presso.hu"

    # MyGLS-hívás mockolva — címke utánvéttel
    from app.services.wfm import gls_service

    calls: list = []
    monkeypatch.setattr(gls_service, "_call", _fake_call(calls))

    res = await client.post(
        "/api/gls",
        json={"recipient_name": "Vevő Vince", "recipient_zip": "1051",
              "recipient_city": "Budapest", "recipient_street": "Fő utca",
              "recipient_house": "1", "recipient_email": "vince@example.com",
              "content": "Szemes kávé 2 kg", "count": 1,
              "cod_amount": 19990},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    parcel = res.json()
    assert parcel["parcel_number"] == "12345678901"
    assert parcel["cod_amount"] == 19990
    assert parcel["test_mode"] is True

    sent = calls[0]["payload"]["ParcelList"][0]
    assert sent["CODAmount"] == 19990
    assert sent["CODCurrency"] == "HUF"
    assert sent["ClientNumber"] == 100123456
    assert sent["PickupAddress"]["City"] == "Budapest"
    assert sent["DeliveryAddress"]["Name"] == "Vevő Vince"
    # e-mail értesítés szolgáltatás a címzett címére
    assert any(s.get("Code") == "FDS" for s in sent["ServiceList"])

    # a címke-PDF visszakérhető (újranyomtatás)
    res = await client.get(f"/api/gls/{parcel['id']}/label", headers=mgr)
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF-")

    # lista
    rows = (await client.get("/api/gls", headers=mgr)).json()
    assert any(r["parcel_number"] == "12345678901" for r in rows)

    # frissen létrehozva: created státusz, törölhető
    assert parcel["status_key"] == "created"
    assert parcel["can_delete"] is True

    # nyomkövetés: teljes idővonal + normalizált státusz (kézbesítve)
    res = await client.post(f"/api/gls/{parcel['id']}/refresh-status", headers=mgr)
    assert res.status_code == 200, res.text
    tracked = res.json()
    assert tracked["last_status"] == "Csomag kézbesítve"
    assert tracked["status_key"] == "delivered"
    assert len(tracked["history"]) == 3
    assert tracked["history"][2]["description"] == "A csomag a depóba érkezett"
    # kézbesítve már NEM törölhető
    assert tracked["can_delete"] is False
    res = await client.delete(f"/api/gls/{parcel['id']}", headers=mgr)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "gls.already_handed_over"


async def test_gls_delete_before_pickup(client, admin, manager, monkeypatch):
    """A feladás törölhető, amíg a futár nem vette át — a MyGLS-nél is
    érvénytelenítjük (DeleteLabels a ParcelId-val)."""
    _, adm = admin
    _, mgr = manager
    await client.put("/api/settings/gls", json=GLS_SETTINGS, headers=adm)

    from app.services.wfm import gls_service

    calls: list = []
    monkeypatch.setattr(gls_service, "_call", _fake_call(calls))
    res = await client.post(
        "/api/gls",
        json={"recipient_name": "Törlős Tóni", "recipient_zip": "1051",
              "recipient_city": "Budapest", "recipient_street": "Fő utca",
              "recipient_house": "2"},
        headers=mgr,
    )
    parcel = res.json()
    res = await client.delete(f"/api/gls/{parcel['id']}", headers=mgr)
    assert res.status_code == 200, res.text
    delete_call = next(c for c in calls if c["method"] == "DeleteLabels")
    assert delete_call["payload"]["ParcelIdList"] == [555]
    rows = (await client.get("/api/gls", headers=mgr)).json()
    assert all(r["id"] != parcel["id"] for r in rows)


async def test_gls_status_normalization():
    """Az 5 fix státusz leképezése az idővonalból."""
    from app.services.wfm.gls_service import normalize_status

    assert normalize_status([]) == "created"
    assert normalize_status([{"description": "Adatok beérkeztek"}]) == "handed_over"
    assert normalize_status(
        [{"description": "Úton"}, {"description": "Depó"}]
    ) == "in_transit"
    assert normalize_status(
        [{"description": "Csomag kézbesítve"}, {"description": "Úton"}]
    ) == "delivered"
    assert normalize_status(
        [{"description": "Visszaszállítás a feladónak"}, {"description": "Úton"}]
    ) == "returned"


async def test_gls_password_hash_format():
    """A MyGLS a jelszó SHA512-hash-ét int-listaként várja."""
    import hashlib

    from app.services.wfm.gls_service import _password_bytes

    out = _password_bytes("titok123")
    assert isinstance(out, list) and len(out) == 64
    assert bytes(out) == hashlib.sha512(b"titok123").digest()
