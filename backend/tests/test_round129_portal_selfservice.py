"""129. kör: ügyfél-portál önkiszolgáló — gépek + számlálók, következő elszámolás
becslése (bizományi és fix bérleti), rendelés és szervizkérés a portálról."""

from __future__ import annotations

from datetime import date

from tests.test_consignment import _setup_settlement
from tests.test_inventory import make_partner
from tests.test_service import make_asset


async def _portal_token(client, mgr, partner_id: str) -> str:
    res = await client.post(f"/api/partners/{partner_id}/portal-link", headers=mgr)
    assert res.status_code == 200, res.text
    return res.json()["token"]


async def test_portal_assets_estimate_order_and_ticket(client, manager):
    _, mgr = manager
    partner, product, settlement = await _setup_settlement(client, mgr)
    asset = await make_asset(client, mgr, barcode="PORTAL-GEP-1", counter=1200)
    res = await client.post(f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr)
    assert res.status_code == 200, res.text
    token = await _portal_token(client, mgr, partner["id"])

    view = (await client.get(f"/api/portal/{token}")).json()
    # gépek + számlálók
    assert [a["barcode"] for a in view["assets"]] == ["PORTAL-GEP-1"]
    assert view["assets"][0]["counter"] == 1200
    # rendelhető termékek név szerint, ár nélkül
    assert any(p["id"] == product["id"] for p in view["products"])
    assert all("price" not in p for p in view["products"])
    # következő elszámolás: bizományi, utolsó elszámolás + 4 hét (kedd–csütörtök), becsült összeg az előzményből
    ns = view["next_settlement"]
    assert ns["kind"] == "consumption" and ns["due_date"] and ns["cycle"] == "4w"
    due = date.fromisoformat(ns["due_date"])
    assert due.weekday() in (1, 2, 3)
    assert ns["estimated_gross"] is not None and ns["estimated_gross"] > 0
    assert view["orders"] == [] and view["tickets"] == []

    # rendelés a portálról
    res = await client.post(f"/api/portal/{token}/order", json={
        "items": [{"product_id": product["id"], "quantity": 2}],
        "note": "Két csomag kérünk", "contact_name": "Portál Pál",
    })
    assert res.status_code == 201, res.text
    order_no = res.json()["order_no"]
    assert order_no.startswith("R-")
    rows = (await client.get("/api/orders", headers=mgr)).json()
    mine = [o for o in rows if o["order_no"] == order_no]
    assert len(mine) == 1 and mine[0]["partner_label"] == "Kávézó Bt."
    # ismeretlen termék → 422
    assert (await client.post(f"/api/portal/{token}/order", json={
        "items": [{"product_id": "00000000-0000-0000-0000-000000000000", "quantity": 1}],
    })).status_code == 422

    # szervizkérés a saját géphez
    res = await client.post(f"/api/portal/{token}/ticket", json={
        "description": "Nem melegít a gép, villog a piros lámpa.",
        "asset_barcode": "PORTAL-GEP-1", "contact_phone": "+36 30 111 2222",
    })
    assert res.status_code == 201, res.text
    ticket_no = res.json()["ticket_no"]
    tickets = (await client.get("/api/service", headers=mgr)).json()
    tk = next(t for t in tickets if t["ticket_no"] == ticket_no)
    assert tk["partner_label"] == "Kávézó Bt." and "PORTAL-GEP-1" in (tk["asset_label"] or "")
    assert "Bejelentő (portál)" in (tk["description"] or "")
    # idegen vonalkód → 422
    assert (await client.post(f"/api/portal/{token}/ticket", json={
        "description": "Valami más gép", "asset_barcode": "NINCS-ILYEN",
    })).status_code == 422

    # a portál a saját kéréseket mutatja
    view = (await client.get(f"/api/portal/{token}")).json()
    assert [o["order_no"] for o in view["orders"]] == [order_no]
    assert [t["ticket_no"] for t in view["tickets"]] == [ticket_no]

    # érvénytelen token → 404 (mindkét végpont)
    assert (await client.post("/api/portal/ervenytelen-token-123456/order", json={
        "items": [{"product_id": product["id"], "quantity": 1}],
    })).status_code == 404
    assert (await client.post("/api/portal/ervenytelen-token-123456/ticket", json={
        "description": "Nem megy semmi sem",
    })).status_code == 404


async def test_portal_fixed_rental_estimate(client, manager):
    _, mgr = manager
    partner = await make_partner(client, mgr, name="Fix Bérlő Kft.")
    res = await client.post(f"/api/partners/{partner['id']}/contracts", json={
        "valid_from": "2026-01-01", "no_minimum": True, "fixed_rental": True,
        "rent_fee": 10000, "billing_cycle": "weekly", "billing_weekday": 2,
        "payment_method": "transfer",
    }, headers=mgr)
    assert res.status_code == 201, res.text
    token = await _portal_token(client, mgr, partner["id"])
    ns = (await client.get(f"/api/portal/{token}")).json()["next_settlement"]
    assert ns["kind"] == "fixed" and ns["cycle"] == "weekly"
    assert ns["estimated_gross"] == 12700.0
    due = date.fromisoformat(ns["due_date"])
    assert due.weekday() == 2 and due >= date.today()
