"""Számlázz.hu Számla Agent: beállítások, szolgáltató-váltás, számlázás-flow."""

from __future__ import annotations

import httpx


async def test_szamlazz_settings_roundtrip(client, admin):
    """Provider-váltás + agent-kulcs mentése (write-only, '-' = törlés)."""
    _, adm = admin
    res = await client.put(
        "/api/settings/billingo",
        json={"enabled": True, "provider": "szamlazz",
              "szamlazz_agent_key": "agent-titok-123",
              "szamlazz_prefix": "XP"},
        headers=adm,
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["provider"] == "szamlazz"
    assert out["has_szamlazz_key"] is True
    assert out["szamlazz_prefix"] == "XP"
    assert "agent-titok" not in res.text  # a kulcs SOHA nem jön vissza

    # törlés kötőjellel
    res2 = await client.put(
        "/api/settings/billingo",
        json={"enabled": True, "provider": "szamlazz", "szamlazz_agent_key": "-"},
        headers=adm,
    )
    assert res2.json()["has_szamlazz_key"] is False

    # érvénytelen provider
    bad = await client.put(
        "/api/settings/billingo",
        json={"enabled": True, "provider": "kamu"},
        headers=adm,
    )
    assert bad.status_code == 422


async def test_szamlazz_invoice_flow(client, admin, manager, monkeypatch):
    """Elszámolás kiszámlázása Számlázz.hu-val: a diszpécser a Számla Agentet
    hívja, teszt-módban díjbekérőként, a számlaszám visszaíródik."""
    from tests.test_consignment import make_product

    from app.services.wfm import szamlazz_service

    _, adm = admin
    _, mgr = manager

    await client.put(
        "/api/settings/billingo",
        json={"enabled": True, "provider": "szamlazz",
              "szamlazz_agent_key": "agent-key-x", "szamlazz_prefix": "XP",
              "test_mode": True},
        headers=adm,
    )

    partner = (
        await client.post(
            "/api/partners",
            json={"name": "Agent Bisztró", "tax_number": "12345678-2-42"},
            headers=mgr,
        )
    ).json()
    product = await make_product(client, mgr, price_per_portion=50.0, grams_per_portion=7)
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 1.0},
        headers=mgr,
    )
    settlement = (
        await client.post(
            "/api/settlements",
            json={"partner_id": partner["id"], "payment_method": "transfer",
                  "lines": [{"product_id": product["id"], "physical_qty": 0.3}]},
            headers=mgr,
        )
    ).json()

    sent: dict = {}

    async def fake_agent_call(field_name: str, xml: str) -> httpx.Headers:
        sent["field"] = field_name
        sent["xml"] = xml
        return httpx.Headers({"szlahu_szamlaszam": "XP-2026-00001"})

    monkeypatch.setattr(szamlazz_service, "_agent_call", fake_agent_call)

    res = await client.post(f"/api/settlements/{settlement['id']}/invoice", headers=mgr)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["invoiced"] is True
    assert out["billingo_document_id"] == "XP-2026-00001"
    assert out["billingo_status"] == "proforma"  # teszt-mód → díjbekérő

    assert sent["field"] == "action-xmlagentxmlfile"
    assert "<szamlaagentkulcs>agent-key-x</szamlaagentkulcs>" in sent["xml"]
    assert "<dijbekero>true</dijbekero>" in sent["xml"]
    assert "<szamlaszamElotag>XP</szamlaszamElotag>" in sent["xml"]
    assert "Agent Bisztró" in sent["xml"]
    assert "<adoszam>12345678-2-42</adoszam>" in sent["xml"]


async def test_szamlazz_not_configured(client, admin, manager):
    """Provider számlázz, de nincs agent-kulcs → 422, érthető hibakóddal."""
    from tests.test_consignment import make_product

    _, adm = admin
    _, mgr = manager
    await client.put(
        "/api/settings/billingo",
        json={"enabled": True, "provider": "szamlazz"},
        headers=adm,
    )
    partner = (
        await client.post("/api/partners", json={"name": "Kulcstalan Kávézó"}, headers=mgr)
    ).json()
    product = await make_product(client, mgr, price_per_portion=50.0, grams_per_portion=7)
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 1.0},
        headers=mgr,
    )
    settlement = (
        await client.post(
            "/api/settlements",
            json={"partner_id": partner["id"], "payment_method": "cash",
                  "lines": [{"product_id": product["id"], "physical_qty": 0.5}]},
            headers=mgr,
        )
    ).json()
    res = await client.post(f"/api/settlements/{settlement['id']}/invoice", headers=mgr)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "settings.billingo_not_configured"


async def test_billingo_szamlazz_separate_modules(client, admin, monkeypatch):
    """A Billingo (billing) es a Szamlazz.hu (szamlazz) kulon modul: a
    Szamlazas ful barmelyikkel el, de csak a bekapcsolt szolgaltato valaszthato."""
    from tests.test_license import OP, _arm_operator

    _, adm = admin
    _arm_operator(monkeypatch)

    # csak szamlazz modul
    res = await client.put(
        "/api/operator/license",
        json={"plan": "m", "enabled_modules": ["szamlazz"]},
        headers=OP,
    )
    assert res.status_code == 200, res.text
    assert (await client.get("/api/settings/billingo", headers=adm)).status_code == 200
    ok = await client.put(
        "/api/settings/billingo",
        json={"enabled": True, "provider": "szamlazz", "szamlazz_agent_key": "k1"},
        headers=adm,
    )
    assert ok.status_code == 200, ok.text
    denied = await client.put(
        "/api/settings/billingo",
        json={"enabled": True, "provider": "billingo"},
        headers=adm,
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["module"] == "billing"

    # csak billing modul → szamlazz provider tiltva
    await client.put(
        "/api/operator/license",
        json={"plan": "m", "enabled_modules": ["billing"]},
        headers=OP,
    )
    denied2 = await client.put(
        "/api/settings/billingo",
        json={"enabled": True, "provider": "szamlazz"},
        headers=adm,
    )
    assert denied2.status_code == 403
    assert denied2.json()["detail"]["module"] == "szamlazz"

    # egyik sem → a ful (GET) is zarva
    await client.put(
        "/api/operator/license",
        json={"plan": "m", "enabled_modules": ["gls"]},
        headers=OP,
    )
    assert (await client.get("/api/settings/billingo", headers=adm)).status_code == 403
