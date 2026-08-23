"""Gép-űrlap kör: számlálónkénti norma, származtatott hely-típus,
cikkszám-előtöltés (type-defaults) és a norma-súlyozott kg-ellenőrzés."""

from __future__ import annotations


async def _partner(client, mgr, name):
    res = await client.post("/api/partners", json={"name": name}, headers=mgr)
    assert res.status_code in (200, 201), res.text
    return res.json()


async def test_norms_location_and_type_defaults(client, manager):
    _, mgr = manager
    res = await client.post(
        "/api/assets",
        json={
            "barcode": "NORM-1", "name": "Kétszámlálós Automata",
            "manufacturer": "Necta", "article_number": "CIKK-42",
            "counter_count": 2, "counters": [10, 20], "norms": [7, 0],
            "location_type": "kézzel írt — nem számít",
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    asset = res.json()
    assert asset["norms"] == [7, 0]
    assert asset["counter"] == 30  # összeg
    # a hely-típus SZÁRMAZTATOTT: raktáron → warehouse, a kézi érték nem megy át
    assert asset["location_type"] == "warehouse"

    # kihelyezés után partnernél
    p = await _partner(client, mgr, "Helytípus Bolt")
    dep = await client.post(
        f"/api/assets/{asset['id']}/deploy", json={"partner_id": p["id"]}, headers=mgr
    )
    assert dep.status_code == 200
    assert dep.json()["location_type"] == "partner"

    # ügyfél saját gépe → customer
    res = await client.post(
        "/api/assets",
        json={"barcode": "NORM-2", "name": "Ügyfélgép", "customer_owned": True},
        headers=mgr,
    )
    own = res.json()
    dep = await client.post(
        f"/api/assets/{own['id']}/deploy", json={"partner_id": p["id"]}, headers=mgr
    )
    assert dep.json()["location_type"] == "customer"

    # patch-ben küldött location_type figyelmen kívül marad
    res = await client.patch(
        f"/api/assets/{asset['id']}",
        json={"location_type": "akármi"},
        headers=mgr,
    )
    assert res.status_code == 200
    assert res.json()["location_type"] == "partner"

    # type-defaults: azonos nevű gép adatai előtölthetők
    res = await client.get(
        "/api/assets/type-defaults?name=kétszámlálós automata", headers=mgr
    )
    assert res.status_code == 200, res.text
    d = res.json()
    assert d["article_number"] == "CIKK-42"
    assert d["manufacturer"] == "Necta"
    assert d["norms"] == [7, 0]
    assert d["counter_count"] == 2

    # ismeretlen névre null
    res = await client.get("/api/assets/type-defaults?name=nincsilyen", headers=mgr)
    assert res.status_code == 200
    assert res.json() is None


async def test_deploy_with_site_address_overrides_partner(client, manager):
    """Beüzemeléskor a telepítési cím felülírja a partner címét (a számlázási
    cím marad) — a gépek gyakran nem a székhelyen üzemelnek."""
    _, mgr = manager
    res = await client.post(
        "/api/partners",
        json={"name": "Székhelyes Kft.", "address_zip": "1051",
              "address_city": "Budapest", "address_street": "Székhely utca",
              "address_number": "1", "billing_zip": "1051",
              "billing_city": "Budapest", "billing_street": "Székhely utca",
              "billing_number": "1"},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    partner = res.json()

    asset = (
        await client.post(
            "/api/assets", json={"barcode": "SITE-1", "name": "Telephelyes gép"},
            headers=mgr,
        )
    ).json()
    res = await client.post(
        f"/api/assets/{asset['id']}/deploy",
        json={"partner_id": partner["id"], "site_zip": "9700",
              "site_city": "Szombathely", "site_street": "Bolt utca",
              "site_number": "12"},
        headers=mgr,
    )
    assert res.status_code == 200, res.text

    rows = (await client.get("/api/partners", headers=mgr)).json()
    p = next(x for x in rows if x["id"] == partner["id"])
    assert p["address_city"] == "Szombathely"
    assert p["address_zip"] == "9700"
    assert "Bolt utca 12" in p["address"]
    # a számlázási cím érintetlen
    assert p["billing_city"] == "Budapest"

    # cím nélküli kihelyezés nem nyúl a partnerhez
    asset2 = (
        await client.post(
            "/api/assets", json={"barcode": "SITE-2", "name": "Sima gép"},
            headers=mgr,
        )
    ).json()
    res = await client.post(
        f"/api/assets/{asset2['id']}/deploy", json={"partner_id": partner["id"]},
        headers=mgr,
    )
    assert res.status_code == 200
    p = next(
        x for x in (await client.get("/api/partners", headers=mgr)).json()
        if x["id"] == partner["id"]
    )
    assert p["address_city"] == "Szombathely"


async def test_settlement_norms_weighted_kg_check(client, manager):
    """A 0 normájú (forró csokis) számláló nem fogyaszt kávét: a kg-ellenőrzés
    a normákkal súlyoz — a hiányzó kávé így kiderül, a csoki-adag nem téveszt."""
    from tests.test_consignment import make_product

    _, mgr = manager

    async def settle(tag, norms):
        partner = await _partner(client, mgr, f"Normás Bolt {tag}")
        product = await make_product(
            client, mgr, name=f"Norma Kávé {tag}", grams_per_portion=7,
            price_per_portion=50.0,
        )
        # norma = ADAG/KG: 143 adag/kg ≈ 7 g/adag; 0 = nem használ kávét
        body = {"barcode": f"NRM-{tag}", "name": f"Normás Gép {tag}",
                "counter_count": 2, "counters": [0, 0],
                "default_product_id": product["id"]}
        if norms is not None:
            body["norms"] = norms
        res = await client.post("/api/assets", json=body, headers=mgr)
        assert res.status_code == 201, res.text
        asset = res.json()
        await client.post(
            f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]},
            headers=mgr,
        )
        await client.post(
            f"/api/partners/{partner['id']}/stock/replenish",
            json={"product_id": product["id"], "quantity": 2.0},
            headers=mgr,
        )
        # 100 kávé-adag + 100 csoki-adag; mérve 0,6 kg maradt
        res = await client.post(
            "/api/settlements",
            json={
                "partner_id": partner["id"], "payment_method": "cash",
                "machines": [{"asset_id": asset["id"], "new_counters": [100, 100]}],
                "lines": [{"product_id": product["id"], "physical_qty": 0.6}],
            },
            headers=mgr,
        )
        assert res.status_code == 201, res.text
        detail = (
            await client.get(f"/api/settlements?partner_id={partner['id']}", headers=mgr)
        ).json()[0]
        full = (
            await client.get(f"/api/settlements/{detail['id']}", headers=mgr)
        ).json()
        return full

    # Normákkal: csak 100 adag fogyasztott kávét (0,7 kg) → 2,0 − 0,7 = 1,3 kg
    # kellene legyen; 0,6 a mért → 0,7 kg KÉSZLETHIÁNY sor.
    with_norms = await settle("A", [143, 0])
    shortage = [
        ln for ln in with_norms["lines"] if "Készlethiány" in ln["product_name"]
    ]
    assert len(shortage) == 1, with_norms["lines"]
    assert "0.70 kg" in shortage[0]["product_name"]

    # Normák nélkül: mind a 200 adag kávénak számít (1,4 kg) → 0,6 a várt
    # maradék, pont ennyi a mért → nincs hiány-sor (a csoki elfedné a hiányt).
    without = await settle("B", None)
    assert not [
        ln for ln in without["lines"] if "Készlethiány" in ln["product_name"]
    ]
