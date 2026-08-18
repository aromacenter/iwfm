"""Árajánlat-kérés → szerződés → online aláírás → automatikus partner."""

from __future__ import annotations

QUOTE_BODY = {
    "company_name": "Próba Pékség Kft.",
    "contact_name": "Kovács Anna",
    "contact_email": "anna@probapekseg.hu",
    "contact_phone": "+36301234567",
    "tax_number": "12345678-2-42",
    "address_zip": "1181",
    "address_city": "Budapest",
    "address_street": "Kenyér utca",
    "address_number": "12",
    "bank_account": "11711000-12345678",
    "message": "Két gépre is kérnénk ajánlatot.",
}

SIG = "data:image/png;base64," + "A" * 200


async def _machine_type(client, mgr, name="Jura X8 automata"):
    res = await client.post(
        "/api/quotes/machine-types",
        json={
            "name": name,
            "description": "Egykaros automata, napi 100 adagig.",
            "ideal_for": "Irodák, kisebb üzletek.",
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_public_quote_flow_to_partner(client, manager):
    """Teljes út: publikus ajánlatkérés → feltételek → küldés → aláírás →
    partner + szerződés automatikusan, minden adattal."""
    _, mgr = manager
    mtype = await _machine_type(client, mgr)

    # publikus katalógus
    res = await client.get("/api/public/quotes/machine-types")
    assert res.status_code == 200
    assert any(m["name"] == mtype["name"] for m in res.json())

    # publikus ajánlatkérés (auth nélkül)
    res = await client.post(
        "/api/public/quotes", json={**QUOTE_BODY, "machine_type_id": mtype["id"]}
    )
    assert res.status_code == 201, res.text
    serial = res.json()["serial"]
    assert serial.startswith("AJ-")

    # belső lista — új státusz
    rows = (await client.get("/api/quotes", headers=mgr)).json()
    quote = next(q for q in rows if q["serial"] == serial)
    assert quote["status"] == "new"
    assert quote["machine_type_name"] == mtype["name"]

    # küldés feltételek nélkül → 422
    res = await client.post(f"/api/quotes/{quote['id']}/send", headers=mgr)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] in ("quote.no_contract", "quote.missing_terms")

    # feltételek kitöltése + szerződés-generálás
    res = await client.patch(
        f"/api/quotes/{quote['id']}",
        json={
            "price_per_portion": 55, "min_portions": 300, "below_min_price": 40,
            "valid_from": "2026-08-01",
        },
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    res = await client.post(f"/api/quotes/{quote['id']}/prepare", headers=mgr)
    assert res.status_code == 200, res.text
    text = res.json()["contract_text"]
    assert "Próba Pékség Kft." in text
    assert "Jura X8 automata" in text
    assert "55" in text

    # küldés (SMTP nincs → email_sent False, de a link megvan)
    res = await client.post(f"/api/quotes/{quote['id']}/send", headers=mgr)
    assert res.status_code == 200, res.text
    sign_url = res.json()["sign_url"]
    assert res.json()["email_sent"] is False
    token = sign_url.rsplit("/", 1)[-1]

    # publikus szerződés-nézet
    res = await client.get(f"/api/public/contract/{token}")
    assert res.status_code == 200
    assert res.json()["status"] == "sent"
    assert "Próba Pékség Kft." in res.json()["contract_text"]

    # aláírás
    res = await client.post(
        f"/api/public/contract/{token}/sign",
        json={"signature": SIG, "signed_name": "Kovács Anna"},
    )
    assert res.status_code == 200, res.text
    partner_code = res.json()["partner_code"]
    assert partner_code.startswith("PT-")

    # partner létrejött MINDEN adattal + aktív szerződés-tükörrel
    partners = (await client.get("/api/partners", headers=mgr)).json()
    p = next(x for x in partners if x["partner_code"] == partner_code)
    assert p["name"] == "Próba Pékség Kft."
    assert p["tax_number"] == "12345678-2-42"
    assert p["contact_email"] == "anna@probapekseg.hu"
    assert p["address_zip"] == "1181"
    assert p["billing_city"] == "Budapest"  # székhely átvéve számlázásinak
    assert p["bank_account"] == "11711000-12345678"
    assert p["contract_min_portions"] == 300
    contracts = (
        await client.get(f"/api/partners/{p['id']}/contracts", headers=mgr)
    ).json()
    assert len(contracts) == 1
    assert contracts[0]["min_portions"] == 300

    # az ajánlat lezárult; újra aláírni nem lehet
    res = await client.post(
        f"/api/public/contract/{token}/sign",
        json={"signature": SIG, "signed_name": "Kovács Anna"},
    )
    assert res.status_code == 409
    quote = (await client.get(f"/api/quotes/{quote['id']}", headers=mgr)).json()
    assert quote["status"] == "signed"
    assert quote["partner_id"] == p["id"]
    # aláírás után a feltételek már nem módosíthatók
    res = await client.patch(
        f"/api/quotes/{quote['id']}", json={"price_per_portion": 99}, headers=mgr
    )
    assert res.status_code == 409


async def test_sign_requires_sent_status(client, manager):
    """Kiküldetlen (new) ajánlat linkje nem létezik; visszavont link 404."""
    _, mgr = manager
    res = await client.post("/api/public/quotes", json=QUOTE_BODY)
    assert res.status_code == 201
    rows = (await client.get("/api/quotes?status=new", headers=mgr)).json()
    quote = rows[0]

    # nincs token → nincs mit aláírni
    assert quote["sign_url"] is None

    # kitöltés + küldés, majd visszavonás → a link érvénytelen
    await client.patch(
        f"/api/quotes/{quote['id']}",
        json={"price_per_portion": 60, "valid_from": "2026-09-01"},
        headers=mgr,
    )
    await client.post(f"/api/quotes/{quote['id']}/prepare", headers=mgr)
    sign_url = (
        await client.post(f"/api/quotes/{quote['id']}/send", headers=mgr)
    ).json()["sign_url"]
    token = sign_url.rsplit("/", 1)[-1]
    res = await client.post(f"/api/quotes/{quote['id']}/cancel", headers=mgr)
    assert res.status_code == 200
    res = await client.get(f"/api/public/contract/{token}")
    assert res.status_code == 404

    # rossz aláírás-formátum
    res = await client.post(
        f"/api/public/contract/{token}/sign",
        json={"signature": "data:image/jpeg;base64," + "A" * 200, "signed_name": "X Y"},
    )
    assert res.status_code in (404, 422)


async def test_template_editing_and_machine_type_crud(client, manager):
    """Sablon mentése/érvényesítése + katalógus CRUD; a publikus lista csak
    az aktív típusokat adja."""
    _, mgr = manager
    # sablon
    res = await client.put(
        "/api/quotes/template",
        json={"contract_template": "Egyedi szerződés: {{ceg_nev}} — {{adagar}} Ft"},
        headers=mgr,
    )
    assert res.status_code == 200
    tpl = (await client.get("/api/quotes/template", headers=mgr)).json()
    assert tpl["effective"].startswith("Egyedi szerződés")

    # a generálás az egyedi sablont használja
    await client.post("/api/public/quotes", json=QUOTE_BODY)
    quote = (await client.get("/api/quotes?status=new", headers=mgr)).json()[0]
    await client.patch(
        f"/api/quotes/{quote['id']}",
        json={"price_per_portion": 70, "valid_from": "2026-09-01"},
        headers=mgr,
    )
    res = await client.post(f"/api/quotes/{quote['id']}/prepare", headers=mgr)
    assert res.json()["contract_text"] == "Egyedi szerződés: Próba Pékség Kft. — 70 Ft"

    # katalógus: inaktív típus nem látszik publikusan, és nem is választható
    m = await _machine_type(client, mgr, name="Kiöregedett gép")
    res = await client.patch(
        f"/api/quotes/machine-types/{m['id']}",
        json={"name": m["name"], "active": False},
        headers=mgr,
    )
    assert res.status_code == 200
    public = (await client.get("/api/public/quotes/machine-types")).json()
    assert all(x["name"] != "Kiöregedett gép" for x in public)
    res = await client.post(
        "/api/public/quotes", json={**QUOTE_BODY, "machine_type_id": m["id"]}
    )
    assert res.status_code == 422
    # törlés
    res = await client.delete(f"/api/quotes/machine-types/{m['id']}", headers=mgr)
    assert res.status_code == 200


async def test_machine_type_image(client, manager):
    """Gép-fotó: feltöltés data URL-lel, publikus kiszolgálás, törlés."""
    import base64

    _, mgr = manager
    png_hex = (
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c626001000000ffff03000006000557bfabd4"
        "0000000049454e44ae426082"
    )
    data_url = "data:image/png;base64," + base64.b64encode(bytes.fromhex(png_hex)).decode()

    m = await _machine_type(client, mgr, name="Fotós gép")
    assert m["has_image"] is False

    res = await client.patch(
        f"/api/quotes/machine-types/{m['id']}",
        json={"name": "Fotós gép", "image": data_url},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    assert res.json()["has_image"] is True

    # publikus kiszolgálás (auth nélkül)
    res = await client.get(f"/api/public/quotes/machine-types/{m['id']}/image")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/png")

    # rossz formátum
    res = await client.patch(
        f"/api/quotes/machine-types/{m['id']}",
        json={"name": "Fotós gép", "image": "data:image/gif;base64,AAAA"},
        headers=mgr,
    )
    assert res.status_code == 422

    # törlés
    res = await client.patch(
        f"/api/quotes/machine-types/{m['id']}",
        json={"name": "Fotós gép", "remove_image": True},
        headers=mgr,
    )
    assert res.status_code == 200
    assert res.json()["has_image"] is False
    res = await client.get(f"/api/public/quotes/machine-types/{m['id']}/image")
    assert res.status_code == 404
