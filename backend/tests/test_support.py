"""Gép-QR támogatás: címke-PDF, nyilvános oldal, bejelentés fotóval, chat."""

from tests.test_service import make_asset

PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


async def _asset_with_token(client, mgr) -> tuple[dict, str]:
    """Gép + QR-token (a címke-generálás állítja be)."""
    import uuid as _uuid

    from sqlalchemy import select as _select

    import app.db as app_db
    from app.models import Asset

    asset = await make_asset(client, mgr, barcode="QR-GEP-1")
    res = await client.get(f"/api/assets/{asset['id']}/qr-label", headers=mgr)
    assert res.status_code == 200, res.text
    assert res.content[:5] == b"%PDF-"

    factory = app_db.get_session_factory()
    async with factory() as session:
        token = (
            await session.execute(
                _select(Asset.qr_token).where(Asset.id == _uuid.UUID(asset["id"]))
            )
        ).scalar_one()
    assert token and len(token) >= 24
    return asset, token


async def test_qr_label_and_public_info(client, manager):
    _, mgr = manager
    asset, token = await _asset_with_token(client, mgr)

    # a token stabil: újra kérve ugyanaz marad
    await client.get(f"/api/assets/{asset['id']}/qr-label", headers=mgr)
    _asset2, token2 = asset, token  # (nem generálódik újra — lásd lenti info-hívás)

    info = await client.get(f"/api/support/{token}")
    assert info.status_code == 200, info.text
    body = info.json()
    assert body["barcode"] == "QR-GEP-1"
    assert body["asset_name"]

    # tömeges címkeív
    batch = await client.post(
        "/api/assets/qr-labels", json={"ids": [asset["id"]]}, headers=mgr
    )
    assert batch.status_code == 200
    assert batch.content[:5] == b"%PDF-"

    # érvénytelen token
    assert (await client.get("/api/support/ervenytelen-token-987654")).status_code == 404


async def test_support_ticket_with_photo(client, manager):
    _, mgr = manager
    _asset, token = await _asset_with_token(client, mgr)

    res = await client.post(
        f"/api/support/{token}/ticket",
        json={
            "description": "A gép E05 hibát ír ki és nem ad ki kávét.",
            "contact_name": "Kiss Ügyfél",
            "contact_phone": "+36301234567",
            "photos": [PNG_DATA_URL],
        },
    )
    assert res.status_code == 201, res.text
    ticket_no = res.json()["ticket_no"]
    assert ticket_no.startswith("SZ-")

    # a jegy megjelenik a szerviz-listában, előtöltött adatokkal
    tickets = (await client.get(f"/api/service?q={ticket_no}", headers=mgr)).json()
    assert len(tickets) == 1
    tk = tickets[0]
    assert tk["kind"] == "repair"
    assert "QR-GEP-1" in tk["asset_label"]
    assert "Kiss Ügyfél" in tk["description"]

    # csatolmány lekérdezhető belül
    atts = (await client.get(f"/api/service/{tk['id']}/attachments", headers=mgr)).json()
    assert len(atts) == 1
    img = await client.get(f"/api/service/attachments/{atts[0]['id']}", headers=mgr)
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png"

    # hibás fotó-adat → 422
    bad = await client.post(
        f"/api/support/{token}/ticket",
        json={"description": "Rossz fotóval", "photos": ["data:text/plain;base64,aGVsbG8="]},
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "support.bad_photo"


async def test_support_chat_requires_ai(client, manager):
    _, mgr = manager
    _asset, token = await _asset_with_token(client, mgr)

    res = await client.post(
        f"/api/support/{token}/chat",
        json={"messages": [{"role": "user", "content": "Nem ad ki kávét a gép."}]},
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "support.ai_not_configured"


async def test_support_settings_roundtrip(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    res = await client.put(
        "/api/settings/support",
        json={"knowledge_base": "## E01\nTöltsd fel a víztartályt."},
        headers=adm,
    )
    assert res.status_code == 200
    got = (await client.get("/api/settings/support", headers=adm)).json()
    assert "E01" in got["knowledge_base"]
    # nem admin nem éri el
    assert (await client.get("/api/settings/support", headers=mgr)).status_code == 403
