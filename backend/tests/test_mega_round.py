"""Mega-kör: előrejelzés, backup, alapvonal, Billingó-szinkron, diktált
elszámolás, e-mail-gyűjtés."""

from __future__ import annotations

import gzip
import json

import app.db as app_db
from app.core.crypto import encrypt_pii


async def _partner_with_settlement(client, mgr, *, physical: float = 0.1) -> dict:
    from tests.test_consignment import make_product

    partner = (
        await client.post(
            "/api/partners",
            json={"name": "Előrejelzés Kávézó", "address_city": "Szentendre",
                  "address_street": "Fő tér 1.", "address_zip": "2000"},
            headers=mgr,
        )
    ).json()
    product = await make_product(client, mgr)
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 1.0},
        headers=mgr,
    )
    res = await client.post(
        "/api/settlements",
        json={"partner_id": partner["id"], "payment_method": "cash",
              "lines": [{"product_id": product["id"], "physical_qty": physical}]},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    return {"partner": partner, "product": product}


async def test_due_forecast_fields(client, manager):
    """Nagy fogyás + kevés maradék → 'hamarosan kifogy' a friss elszámolás
    ellenére is listáz, előrejelzés-mezőkkel és címmel."""
    _, mgr = manager
    await _partner_with_settlement(client, mgr, physical=0.1)

    due = (await client.get("/api/settlements/due?days=30", headers=mgr)).json()
    assert len(due) == 1
    d = due[0]
    assert d["city"] == "Szentendre"
    assert d["address"] and "Fő tér" in d["address"]
    assert d["avg_daily_kg"] > 0
    assert d["days_left"] is not None and d["days_left"] <= 7
    assert d["suggested_kg"] > 0


async def test_backup_download(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    await _partner_with_settlement(client, mgr)

    assert (await client.get("/api/admin/backup", headers=mgr)).status_code == 403
    res = await client.get("/api/admin/backup", headers=adm)
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/gzip"
    payload = json.loads(gzip.decompress(res.content))
    assert payload["format"] == "iwfm-backup-v1"
    assert len(payload["tables"]["partners"]) == 1
    assert len(payload["tables"]["settlements"]) == 1


async def test_maintenance_baseline(client, manager):
    _, mgr = manager
    created = await client.post(
        "/api/assets",
        json={"barcode": "BASE-1", "name": "Jura X8", "counter": 5000, "norm": 100},
        headers=mgr,
    )
    assert created.status_code == 201

    due = (await client.get("/api/service/maintenance-due", headers=mgr)).json()
    assert len(due) == 1  # nincs előzmény → 5000 >= 100 → esedékes

    res = await client.post("/api/service/maintenance-baseline", headers=mgr)
    assert res.status_code == 200 and res.json()["created"] == 1

    assert (await client.get("/api/service/maintenance-due", headers=mgr)).json() == []
    # idempotens: másodszor nincs mit tenni
    again = await client.post("/api/service/maintenance-baseline", headers=mgr)
    assert again.json()["created"] == 0


async def test_billingo_partner_sync(client, admin, manager, monkeypatch):
    import httpx

    _, adm = admin
    _, mgr = manager
    partner = (
        await client.post(
            "/api/partners",
            json={"name": "Sync Büfé", "tax_number": "12345678-2-41",
                  "invoicing_company": "xp"},
            headers=mgr,
        )
    ).json()

    # Billingó-kulcs közvetlenül a DB-be (xp fiók)
    from app.models import BillingoSettings

    factory = app_db.get_session_factory()
    async with factory() as session:
        session.add(BillingoSettings(id=1, enabled=True,
                                     api_key_encrypted=encrypt_pii("xp-key")))
        await session.commit()

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{
                "name": "Sync Büfé Kereskedelmi Kft.",
                "taxcode": "12345678-2-41",
                "address": {"post_code": "1051", "city": "Budapest", "address": "Fő u. 2."},
                "emails": ["sync@bufe.hu"],
            }]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            assert "page=1" in url
            return FakeResponse() if "page=1" in url else None

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    dry = await client.post("/api/admin/billingo-sync", json={"dry_run": True}, headers=adm)
    assert dry.status_code == 200, dry.text
    assert dry.json()["report"]["xp"]["would_update"] == 1

    run = await client.post("/api/admin/billingo-sync", json={"dry_run": False}, headers=adm)
    assert run.status_code == 200
    assert run.json()["report"]["xp"]["updated"] == 1

    got = next(
        p for p in (await client.get("/api/partners", headers=mgr)).json()
        if p["id"] == partner["id"]
    )
    assert got["company_name"] == "Sync Büfé Kereskedelmi Kft."
    assert got["contact_email"] == "sync@bufe.hu"
    assert got["billing_zip"] == "1051"


async def test_assistant_create_settlement(client, admin, monkeypatch):
    from tests.test_assistant import _enable_ai, _script
    from tests.test_consignment import make_product

    _, adm = admin
    await _enable_ai()
    partner = (
        await client.post("/api/partners", json={"name": "Diktált Kávézó"}, headers=adm)
    ).json()
    product = await make_product(client, adm, name="Diktált kávé")
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 2.0},
        headers=adm,
    )

    _script(monkeypatch, [
        ("", [{"id": "t1", "name": "create_settlement",
               "args": {"partner": "Diktált Kávézó", "payment_method": "cash",
                        "lines": [{"product": "Diktált kávé", "physical_qty": 0.5}]}}]),
        ("Rögzítettem az elszámolást.", []),
    ])
    res = await client.post(
        "/api/assistant/chat",
        json={"messages": [{"role": "user", "content": "diktált kávézó, leltár fél kiló, készpénz"}]},
        headers=adm,
    )
    assert res.status_code == 200, res.text
    assert any("Elszámolás" in e["label"] for e in res.json()["events"])

    listed = (await client.get("/api/settlements", headers=adm)).json()
    assert len(listed) == 1
    assert listed[0]["total_gross"] > 0
    stock = (await client.get(f"/api/partners/{partner['id']}/stock", headers=adm)).json()
    assert stock[0]["quantity"] == 0.5


async def test_signature_collects_email(client, manager):
    _, mgr = manager
    seeded = await _partner_with_settlement(client, mgr)
    settlement = (await client.get("/api/settlements", headers=mgr)).json()[0]

    png = "data:image/png;base64," + "A" * 40
    res = await client.post(
        f"/api/settlements/{settlement['id']}/signature",
        json={"signature": png, "partner_email": "uj@partner.hu"},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    got = next(
        p for p in (await client.get("/api/partners", headers=mgr)).json()
        if p["id"] == seeded["partner"]["id"]
    )
    assert got["contact_email"] == "uj@partner.hu"


async def test_support_ticket_collects_email(client, manager):
    from tests.test_support import _asset_with_token

    _, mgr = manager
    partner = (
        await client.post("/api/partners", json={"name": "QR Email Partner"}, headers=mgr)
    ).json()
    asset, token = await _asset_with_token(client, mgr)
    await client.post(
        f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr
    )

    res = await client.post(
        f"/api/support/{token}/ticket",
        json={"description": "Nem melegít a gép rendesen.",
              "contact_email": "qr@partner.hu"},
    )
    assert res.status_code == 201, res.text
    got = next(
        p for p in (await client.get("/api/partners", headers=mgr)).json()
        if p["id"] == partner["id"]
    )
    assert got["contact_email"] == "qr@partner.hu"