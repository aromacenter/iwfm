"""Függő raktár-átadások: autót érintő mozgás csak a fogadó fél
jóváhagyása után könyvelődik; elutasítás/visszavonás visszaadja a
forrásnak; telephely↔telephely marad azonnali."""

from __future__ import annotations

from tests.conftest import make_user


async def _setup(client, mgr):
    """Telephely + két autó (két üzletkötővel) + termék, 100 egység a telephelyen."""
    site = (await client.post(
        "/api/warehouses", json={"name": "Központ", "kind": "site"}, headers=mgr,
    )).json()
    rep_user, rep_headers = await make_user(email="rep1@example.com", role="uzletkoto")
    rep2_user, rep2_headers = await make_user(email="rep2@example.com", role="uzletkoto")
    van = (await client.post(
        "/api/warehouses",
        json={"name": "Autó-1", "kind": "van", "user_id": str(rep_user.id)},
        headers=mgr,
    )).json()
    res = await client.post(
        "/api/products",
        json={"name": "Szemes kávé", "sku": "WT-K1", "unit": "kg", "price_per_unit": 8000},
        headers=mgr,
    )
    assert res.status_code in (200, 201), res.text
    product = res.json()
    await client.post(
        f"/api/warehouses/{site['id']}/stock/add", json={"product_id": product["id"]},
        headers=mgr,
    )
    res = await client.post(
        f"/api/warehouses/{site['id']}/receive",
        json={"product_id": product["id"], "quantity": 100},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    return site, van, product, rep_headers, rep2_headers


async def _qty(client, mgr, wh_id, product_id):
    rows = (await client.get(f"/api/warehouses/{wh_id}/stock", headers=mgr)).json()
    row = next((r for r in rows if r["product_id"] == product_id), None)
    return row["quantity"] if row else 0


async def test_transfer_to_van_needs_rep_approval(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    site, van, product, rep_headers, rep2_headers = await _setup(client, mgr)

    # raktáros autóra mozgat → függő, a forrásról lekerül, az autóra MÉG nem
    res = await client.post(
        "/api/warehouses/transfer",
        json={"from_warehouse_id": site["id"], "to_warehouse_id": van["id"],
              "product_id": product["id"], "quantity": 30},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["pending"] is True
    assert await _qty(client, mgr, site["id"], product["id"]) == 70
    assert await _qty(client, mgr, van["id"], product["id"]) == 0

    rows = (await client.get("/api/warehouses/transfers?status=pending", headers=mgr)).json()
    tr = next(r for r in rows if r["to_warehouse"] == "Autó-1")
    # a küldő raktáros NEM hagyhatja jóvá; a MÁSIK üzletkötő sem
    assert tr["can_decide"] is False
    res = await client.post(f"/api/warehouses/transfers/{tr['id']}/accept", headers=mgr)
    assert res.status_code == 403
    res = await client.post(f"/api/warehouses/transfers/{tr['id']}/accept", headers=rep2_headers)
    assert res.status_code == 403
    # meg az admin SEM fogadhatja el mas neveben
    res = await client.post(f"/api/warehouses/transfers/{tr['id']}/accept", headers=adm)
    assert res.status_code == 403
    # a nem erintett uzletkoto a listaban SEM latja
    rows = (await client.get("/api/warehouses/transfers?status=pending", headers=rep2_headers)).json()
    assert all(r["id"] != tr["id"] for r in rows)

    # az autóhoz rendelt üzletkötő igazolja az átvételt → rákerül az autóra
    rows = (await client.get("/api/warehouses/transfers?status=pending", headers=rep_headers)).json()
    assert next(r for r in rows if r["id"] == tr["id"])["can_decide"] is True
    res = await client.post(f"/api/warehouses/transfers/{tr['id']}/accept", headers=rep_headers)
    assert res.status_code == 200, res.text
    assert await _qty(client, mgr, van["id"], product["id"]) == 30
    # másodszor már nem bírálható el
    res = await client.post(f"/api/warehouses/transfers/{tr['id']}/accept", headers=rep_headers)
    assert res.status_code == 422


async def test_transfer_back_to_site_needs_warehouse_approval(client, admin, manager):
    _, mgr = manager
    site, van, product, rep_headers, _ = await _setup(client, mgr)
    # előbb kerüljön készlet az autóra (elfogadott átadással)
    res = await client.post(
        "/api/warehouses/transfer",
        json={"from_warehouse_id": site["id"], "to_warehouse_id": van["id"],
              "product_id": product["id"], "quantity": 40},
        headers=mgr,
    )
    tid = res.json()["transfer_id"]
    await client.post(f"/api/warehouses/transfers/{tid}/accept", headers=rep_headers)

    # az üzletkötő visszaad a telephelyre → függő; ő maga NEM fogadhatja el
    res = await client.post(
        "/api/warehouses/transfer",
        json={"from_warehouse_id": van["id"], "to_warehouse_id": site["id"],
              "product_id": product["id"], "quantity": 10},
        headers=rep_headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["pending"] is True
    back_id = res.json()["transfer_id"]
    assert await _qty(client, mgr, van["id"], product["id"]) == 30
    assert await _qty(client, mgr, site["id"], product["id"]) == 60
    res = await client.post(f"/api/warehouses/transfers/{back_id}/accept", headers=rep_headers)
    assert res.status_code == 403
    # a raktáros igazolja → a telephelyre kerül
    res = await client.post(f"/api/warehouses/transfers/{back_id}/accept", headers=mgr)
    assert res.status_code == 200, res.text
    assert await _qty(client, mgr, site["id"], product["id"]) == 70


async def test_transfer_reject_and_cancel_return_stock(client, admin, manager):
    _, mgr = manager
    site, van, product, rep_headers, _ = await _setup(client, mgr)

    # elutasítás: visszakerül a telephelyre
    res = await client.post(
        "/api/warehouses/transfer",
        json={"from_warehouse_id": site["id"], "to_warehouse_id": van["id"],
              "product_id": product["id"], "quantity": 25},
        headers=mgr,
    )
    tid = res.json()["transfer_id"]
    res = await client.post(
        f"/api/warehouses/transfers/{tid}/reject",
        json={"note": "nem fért be az autóba"}, headers=rep_headers,
    )
    assert res.status_code == 200, res.text
    assert await _qty(client, mgr, site["id"], product["id"]) == 100
    assert await _qty(client, mgr, van["id"], product["id"]) == 0

    # visszavonás: a küldő vonja vissza, mielőtt döntenének
    res = await client.post(
        "/api/warehouses/transfer",
        json={"from_warehouse_id": site["id"], "to_warehouse_id": van["id"],
              "product_id": product["id"], "quantity": 15},
        headers=mgr,
    )
    tid = res.json()["transfer_id"]
    assert await _qty(client, mgr, site["id"], product["id"]) == 85
    res = await client.post(f"/api/warehouses/transfers/{tid}/cancel", headers=rep_headers)
    assert res.status_code == 403  # nem a küldő
    res = await client.post(f"/api/warehouses/transfers/{tid}/cancel", headers=mgr)
    assert res.status_code == 200, res.text
    assert await _qty(client, mgr, site["id"], product["id"]) == 100


async def test_site_to_site_transfer_stays_immediate(client, admin, manager):
    _, mgr = manager
    site, _van, product, _rep, _ = await _setup(client, mgr)
    site2 = (await client.post(
        "/api/warehouses", json={"name": "Telephely-2", "kind": "site"}, headers=mgr,
    )).json()
    res = await client.post(
        "/api/warehouses/transfer",
        json={"from_warehouse_id": site["id"], "to_warehouse_id": site2["id"],
              "product_id": product["id"], "quantity": 20},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["pending"] is False
    assert await _qty(client, mgr, site2["id"], product["id"]) == 20


async def test_van_to_van_follows_same_flow(client, admin, manager):
    """Autóról autóra: ugyanaz a szisztéma — a CÉL-autó üzletkötője igazol,
    a küldő üzletkötő (és a raktáros) nem."""
    _, mgr = manager
    site, van, product, rep_headers, _rep2 = await _setup(client, mgr)
    rep3_user, rep3_headers = await make_user(email="rep3@example.com", role="uzletkoto")
    van2 = (await client.post(
        "/api/warehouses",
        json={"name": "Autó-2", "kind": "van", "user_id": str(rep3_user.id)},
        headers=mgr,
    )).json()

    # készlet az 1-es autóra (elfogadott átadással)
    res = await client.post(
        "/api/warehouses/transfer",
        json={"from_warehouse_id": site["id"], "to_warehouse_id": van["id"],
              "product_id": product["id"], "quantity": 20},
        headers=mgr,
    )
    await client.post(
        f"/api/warehouses/transfers/{res.json()['transfer_id']}/accept",
        headers=rep_headers,
    )

    # rep1 átad a rep3 autójára → függő; csak rep3 igazolhatja
    res = await client.post(
        "/api/warehouses/transfer",
        json={"from_warehouse_id": van["id"], "to_warehouse_id": van2["id"],
              "product_id": product["id"], "quantity": 8},
        headers=rep_headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["pending"] is True
    tid = res.json()["transfer_id"]
    assert await _qty(client, mgr, van["id"], product["id"]) == 12
    assert await _qty(client, mgr, van2["id"], product["id"]) == 0

    res = await client.post(f"/api/warehouses/transfers/{tid}/accept", headers=rep_headers)
    assert res.status_code == 403  # a küldő nem
    res = await client.post(f"/api/warehouses/transfers/{tid}/accept", headers=mgr)
    assert res.status_code == 403  # a raktáros sem
    res = await client.post(f"/api/warehouses/transfers/{tid}/accept", headers=rep3_headers)
    assert res.status_code == 200, res.text
    assert await _qty(client, mgr, van2["id"], product["id"]) == 8


async def test_van_outward_only_by_assigned_rep(client, admin, manager):
    """Autóból kifelé mozgást (áthelyezés, partner-feltöltés) csak a
    hozzárendelt üzletkötő indíthat — raktáros, admin, másik üzletkötő nem."""
    _, adm = admin
    _, mgr = manager
    site, van, product, rep_headers, rep2_headers = await _setup(client, mgr)

    res = await client.post(
        "/api/warehouses/transfer",
        json={"from_warehouse_id": site["id"], "to_warehouse_id": van["id"],
              "product_id": product["id"], "quantity": 30},
        headers=mgr,
    )
    await client.post(
        f"/api/warehouses/transfers/{res.json()['transfer_id']}/accept",
        headers=rep_headers,
    )

    body = {"from_warehouse_id": van["id"], "to_warehouse_id": site["id"],
            "product_id": product["id"], "quantity": 5}
    for headers in (mgr, adm, rep2_headers):
        res = await client.post("/api/warehouses/transfer", json=body, headers=headers)
        assert res.status_code == 403, res.text
        assert res.json()["detail"]["code"] == "warehouse.not_your_van"
    res = await client.post("/api/warehouses/transfer", json=body, headers=rep_headers)
    assert res.status_code == 200, res.text
    assert res.json()["pending"] is True

    # partner-feltöltés az autóból forrás-raktárként: ugyanez a szabály
    partner = (await client.post(
        "/api/partners", json={"name": "Kifelé Bolt"}, headers=mgr,
    )).json()
    rep_body = {"product_id": product["id"], "quantity": 2,
                "source_warehouse_id": van["id"]}
    res = await client.post(
        f"/api/partners/{partner['id']}/stock/replenish", json=rep_body, headers=mgr,
    )
    assert res.status_code == 403, res.text
    res = await client.post(
        f"/api/partners/{partner['id']}/stock/replenish", json=rep_body,
        headers=rep_headers,
    )
    assert res.status_code == 200, res.text


async def test_movement_event_fired(client, manager, monkeypatch):
    """Minden készletmozgás warehouse.movement eseményt tüzel (Telegram a fő
    csoportba) — az esemény és a sablon regisztrálva van."""
    from app.services.wfm import automation, telegram

    assert "warehouse.movement" in automation.TRIGGERS
    assert "warehouse.movement" in telegram.EVENT_TEMPLATES

    import app.api.warehouse as wh_mod

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(wh_mod, "fire_event", lambda ev, ctx: captured.append((ev, ctx)))

    _, mgr = manager
    site, van, product, rep_headers, _ = await _setup(client, mgr)  # bevételez 100-at
    assert any(
        ev == "warehouse.movement" and ctx["muvelet"] == "bevételezés"
        and ctx["termek_nev"] == "Szemes kávé" and ctx["mennyiseg"] == 100
        for ev, ctx in captured
    ), captured
