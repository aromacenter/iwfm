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

    # a token stabil: újra kérve ugyanaz marad (lásd lenti info-hívás)
    await client.get(f"/api/assets/{asset['id']}/qr-label", headers=mgr)

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


def test_relevant_kb_filters_by_machine():
    """A tudásbázisból a gép gyártójához illő + általános szekciók maradnak."""
    from app.api.support import _relevant_kb

    kb = (
        "# Cím\n\n"
        "## Jura XJ6 — Error 8\nJura teendő.\n\n"
        "## Saeco — E01\nSaeco teendő.\n\n"
        "## Schaerer — 163\nSchaerer teendő.\n\n"
        "## Általános — gyenge kávé\nÁltalános teendő.\n"
    )
    picked = _relevant_kb(kb, ["Jura", "XJ6", "Impressa"])
    assert "Jura XJ6" in picked and "Általános" in picked
    assert "Saeco" not in picked and "Schaerer" not in picked

    # ismeretlen gyártó → nincs specifikus találat → teljes tudásbázis marad
    assert _relevant_kb(kb, ["Nivona"]) == kb
    # szekcionálatlan szöveg → változatlan
    assert _relevant_kb("csak sima szoveg", ["Jura"]) == "csak sima szoveg"


async def test_counter_report_updates_asset_and_history(client, manager):
    """Számláló-bejelentés: a gép adatlapján frissül, az előzményekbe
    régi → új + bejelentő kerül."""
    _, mgr = manager
    asset, token = await _asset_with_token(client, mgr)  # counter nélkül jött létre?

    res = await client.post(
        f"/api/support/{token}/counter",
        json={"counter": 12500, "reporter_name": "Kis Júlia"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["new_counter"] == 12500

    detail = (await client.get(f"/api/assets/{asset['id']}", headers=mgr)).json()
    assert detail["counter"] == 12500
    moves = detail.get("movements") or []
    counter_moves = [m for m in moves if m["action"] == "counter"]
    assert counter_moves, moves
    assert "12500" in counter_moves[0]["detail"]
    assert "Kis Júlia" in counter_moves[0]["detail"]

    # az info végpont a friss állást adja
    info = (await client.get(f"/api/support/{token}")).json()
    assert info["counter"] == 12500


async def test_order_from_qr_page(client, manager):
    """Termékrendelés a QR-oldalról: R-sorszám, a belső listában megjelenik,
    teljesítettre zárható."""
    from tests.test_consignment import make_product

    _, mgr = manager
    _asset, token = await _asset_with_token(client, mgr)
    product = await make_product(client, mgr, name="Rendelhető kávé")

    info = (await client.get(f"/api/support/{token}")).json()
    assert any(p["name"] == "Rendelhető kávé" for p in info["products"])

    res = await client.post(
        f"/api/support/{token}/order",
        json={
            "items": [{"product_id": product["id"], "quantity": 3}],
            "note": "Hétvégére elfogy",
            "contact_name": "Kis Júlia",
        },
    )
    assert res.status_code == 201, res.text
    order_no = res.json()["order_no"]
    assert order_no == "R-0001"

    listed = (await client.get("/api/orders?status=open", headers=mgr)).json()
    assert len(listed) == 1
    order = listed[0]
    assert order["items"][0]["name"] == "Rendelhető kávé"
    assert order["items"][0]["quantity"] == 3
    assert order["note"] == "Hétvégére elfogy"

    done = await client.patch(
        f"/api/orders/{order['id']}", json={"status": "done"}, headers=mgr
    )
    assert done.status_code == 200
    assert done.json()["status"] == "done"
    assert (await client.get("/api/orders?status=open", headers=mgr)).json() == []

    # érvénytelen termék → 422
    bad = await client.post(
        f"/api/support/{token}/order",
        json={"items": [{"product_id": "00000000-0000-0000-0000-000000000000", "quantity": 1}]},
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "support.bad_product"


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
