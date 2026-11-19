"""162. kör: beépített tananyag-könyvtár (raktár-oktatóanyagok) telepítése +
csempés nézet borítója (cover_id = az első képmelléklet)."""

from __future__ import annotations


async def test_library_install_and_cover(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    lib = (await client.get("/api/learning/library", headers=mgr)).json()
    keys = {p["key"]: p for p in lib}
    assert {"raktar_keszlet", "raktar_tarhely"} <= set(keys)
    assert keys["raktar_tarhely"]["questions"] == 15 and keys["raktar_tarhely"]["images"] == 18  # + külön borító and keys["raktar_tarhely"]["installed"] is False
    # telepítés
    res = await client.post("/api/learning/library/raktar_tarhely/install", json={}, headers=mgr)
    assert res.status_code == 201, res.text
    m = res.json()
    assert m["title"].startswith("Raktárkezelés 2.") and m["question_count"] == 15
    body = (await client.get("/api/learning/materials", headers=mgr)).json()
    mat = next(x for x in body if x["id"] == m["id"])
    assert len(mat["attachments"]) == 18 and all(a["mime"] == "image/png" for a in mat["attachments"])
    # a szövegben minden kép att:-hivatkozás, jelölő nem maradt
    detail_body = mat.get("body") or ""
    assert "{{img:" not in detail_body and detail_body.count("](att:") == 18
    qs = (await client.get(f"/api/learning/materials/{m['id']}/questions", headers=mgr)).json()
    assert len(qs) == 15 and all(any(o["correct"] for o in q["options"]) for q in qs)
    # másodszor 409, replace-szel újratelepül
    assert (await client.post("/api/learning/library/raktar_tarhely/install", json={}, headers=mgr)).status_code == 409
    res = await client.post("/api/learning/library/raktar_tarhely/install", json={"replace": True}, headers=mgr)
    assert res.status_code == 201
    assert (await client.get("/api/learning/library", headers=mgr)).json()[1]["installed"] is True
    assert (await client.post("/api/learning/library/nincs_ilyen/install", json={}, headers=mgr)).status_code == 404
    # a dolgozó kartonján a borító az első (cover) kép
    emp = (await client.post("/api/employees", json={"email": "tanulo@example.com", "initial_password": "Titkos-jelszo-123", "role": "employee",
                                                     "last_name": "Tanuló", "first_name": "Teszt", "hire_date": "2025-01-01"}, headers=adm)).json()
    mid = res.json()["id"]
    assert (await client.post(f"/api/learning/materials/{mid}/assign", json={"employee_ids": [emp["id"]]}, headers=mgr)).status_code == 200
    login = await client.post("/api/auth/login", json={"email": "tanulo@example.com", "password": "Titkos-jelszo-123"})
    assert login.status_code == 200
    my = (await client.get("/api/learning/my")).json()
    item = my["items"][0]
    assert item["cover_id"] and item["has_test"] is True
    assert item["cover_svg"].startswith("<svg")  # 164. kör: rajzolt borító, nem képernyőkép
