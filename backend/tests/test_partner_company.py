"""Hivatalos cégnév + ügyfél-gép megkülönböztetés + VIES-lekérés."""

from __future__ import annotations

import httpx


async def test_company_name_roundtrip(client, manager):
    _, mgr = manager
    res = await client.post(
        "/api/partners",
        json={"name": "Erzsébet körút Dohánybolt", "company_name": "Zália Bt.",
              "tax_number": "24336541-2-43"},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    assert res.json()["company_name"] == "Zália Bt."

    pid = res.json()["id"]
    patched = await client.patch(
        f"/api/partners/{pid}",
        json={"name": "Erzsébet körút Dohánybolt", "company_name": "Zália Kft."},
        headers=mgr,
    )
    assert patched.json()["company_name"] == "Zália Kft."


async def test_customer_owned_flag_and_filter(client, manager):
    from tests.test_service import make_asset

    _, mgr = manager
    partner = (
        await client.post("/api/partners", json={"name": "Gépes Ügyfél"}, headers=mgr)
    ).json()

    own = await make_asset(client, mgr, barcode="SAJAT-1")
    cust = (
        await client.post(
            "/api/assets",
            json={"barcode": "UGYFEL-1", "name": "Saeco Royal", "customer_owned": True},
            headers=mgr,
        )
    ).json()
    assert cust["customer_owned"] is True

    for aid in (own["id"], cust["id"]):
        dep = await client.post(
            f"/api/assets/{aid}/deploy", json={"partner_id": partner["id"]}, headers=mgr
        )
        assert dep.status_code == 200

    deployed = (await client.get("/api/assets?status=deployed", headers=mgr)).json()
    assert [a["barcode"] for a in deployed] == ["SAJAT-1"]

    customer = (await client.get("/api/assets?status=customer", headers=mgr)).json()
    assert [a["barcode"] for a in customer] == ["UGYFEL-1"]


async def test_invoicing_company_and_settlement_split(client, manager):
    """A partner szerződött cége (xp/pc) pillanatképként az elszámolásra
    kerül, a lista és az összesítés cégenként bontható."""
    from tests.test_consignment import make_product

    _, mgr = manager
    xp = (
        await client.post(
            "/api/partners", json={"name": "XP Büfé", "invoicing_company": "xp"}, headers=mgr
        )
    ).json()
    pc = (
        await client.post(
            "/api/partners", json={"name": "PC Kávézó", "invoicing_company": "pc"}, headers=mgr
        )
    ).json()
    assert xp["invoicing_company"] == "xp" and pc["invoicing_company"] == "pc"

    # érvénytelen cégkód → 422
    bad = await client.post(
        "/api/partners", json={"name": "Rossz", "invoicing_company": "zz"}, headers=mgr
    )
    assert bad.status_code == 422

    product = await make_product(client, mgr)
    for p in (xp, pc):
        await client.post(
            f"/api/partners/{p['id']}/stock/replenish",
            json={"product_id": product["id"], "quantity": 1.0},
            headers=mgr,
        )
        res = await client.post(
            "/api/settlements",
            json={
                "partner_id": p["id"],
                "payment_method": "cash",
                "lines": [{"product_id": product["id"], "physical_qty": 0.3}],
            },
            headers=mgr,
        )
        assert res.status_code == 201, res.text
        assert res.json()["invoicing_company"] == p["invoicing_company"]

    only_pc = (await client.get("/api/settlements?company=pc", headers=mgr)).json()
    assert len(only_pc) == 1 and only_pc[0]["invoicing_company"] == "pc"

    summary = (await client.get("/api/settlements/summary", headers=mgr)).json()
    assert summary["by_company"]["xp"]["count"] == 1
    assert summary["by_company"]["pc"]["count"] == 1
    assert summary["by_company"]["none"]["count"] == 0

    filtered = (await client.get("/api/settlements/summary?company=xp", headers=mgr)).json()
    assert filtered["count"] == 1


async def test_tax_lookup_validation_and_success(client, manager, monkeypatch):
    _, mgr = manager

    short = await client.get("/api/partners/tax-lookup?tax_number=123", headers=mgr)
    assert short.status_code == 422

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "isValid": True,
                "name": "ZÁLIA BETÉTI TÁRSASÁG",
                "address": "ERZSÉBET KÖRÚT 1 1073 BUDAPEST",
            }

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            assert "/HU/vat/24336541" in url
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    res = await client.get(
        "/api/partners/tax-lookup?tax_number=24336541-2-43", headers=mgr
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["found"] is True
    assert "Zália" in body["company_name"] or "ZÁLIA" in body["company_name"]
    assert body["address_zip"] == "1073"
    assert body["address_city"] == "Budapest"
