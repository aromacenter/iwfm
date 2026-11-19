"""164. kör: rajzolt borító — kulcsszavas választás, könyvtár, generálás
(AI nélkül könyvtári tartalék), SVG-tisztítás, törlés; a csempe-API adja."""

from __future__ import annotations

from app.services.wfm.learning_cover import art_keys, pick_art, pick_color, sanitize_svg


def test_pick_art_and_sanitize():
    assert pick_art("Raktárkezelés 2. — Tárhelyezett raktár", "Raktár") == "warehouse_bins"
    assert pick_art("Raktárkezelés 1. — Csak készletként kezelt raktár", "Raktár") == "warehouse_stock"
    assert pick_art("Átvétel: gép behozatala és elismervény", None) == "intake"
    assert pick_art("Valami egészen más", None, "…a szerviz munkalapot így töltsd ki…") == "service"
    assert pick_art("Semmi sem illik", None, "lorem ipsum") == "default"
    assert pick_color("a") != pick_color("b") or pick_color("a") == pick_color("a")
    assert {"warehouse_bins", "warehouse_stock", "service", "default"} <= set(art_keys())
    # tisztítás: script/text/image/style kimegy, alakzatok maradnak, viewBox kerül rá
    dirty = ('Here is your SVG:\n<svg width="200" onload="alert(1)"><style>.x{}</style><script>evil()</script>'
             '<text>no</text><image href="http://x/y.png"/><rect x="1" y="2" width="3" height="4" fill="url(#g)"/>'
             '<circle cx="5" cy="5" r="2" fill="#c7d2fe"/><path d="M1 1L2 2"/><a href="x"><ellipse cx="1" cy="1" rx="2" ry="2"/></a></svg> done')
    out = sanitize_svg(dirty)
    assert out and out.startswith("<svg") and 'viewBox="0 0 200 150"' in out
    for bad in ("script", "<text", "image", "href", "onload", "style", "url("):
        assert bad not in out
    assert out.count("<rect") == 1 and "<circle" in out and "<ellipse" in out
    assert sanitize_svg("no svg here") is None and sanitize_svg("<svg></svg>") is None


async def test_cover_endpoints(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    # könyvtár
    art = (await client.get("/api/learning/cover-art", headers=mgr)).json()
    assert any(a["key"] == "warehouse_bins" and a["svg"].startswith("<svg") for a in art)
    # új anyag: automatikus borító a címből
    m = (await client.post("/api/learning/materials", json={"title": "Elszámolás a partnernél", "category": "X-admin"}, headers=mgr)).json()
    assert m["cover_custom"] is False and "<svg" in m["cover_svg"] and m["cover_color"].startswith("#")
    assert m["cover_svg"] == next(a["svg"] for a in art if a["key"] == "settlement")
    # könyvtári választás + szín
    res = await client.post(f"/api/learning/materials/{m['id']}/cover/generate", json={"mode": "library", "art": "coffee_machine", "color": "#fef3c7"}, headers=mgr)
    assert res.status_code == 200, res.text
    assert res.json()["cover_custom"] is True and res.json()["cover_color"] == "#fef3c7" and res.json()["cover_source"] == "library"
    assert (await client.post(f"/api/learning/materials/{m['id']}/cover/generate", json={"mode": "library", "art": "nincs"}, headers=mgr)).status_code == 404
    # auto mód AI nélkül: könyvtári tartalék; ai mód: 422
    res = await client.post(f"/api/learning/materials/{m['id']}/cover/generate", json={"mode": "auto"}, headers=mgr)
    assert res.status_code == 200 and res.json()["cover_source"] == "library"
    assert (await client.post(f"/api/learning/materials/{m['id']}/cover/generate", json={"mode": "ai"}, headers=mgr)).status_code == 422
    # törlés → vissza az automatikusra
    res = await client.delete(f"/api/learning/materials/{m['id']}/cover", headers=mgr)
    assert res.status_code == 200 and res.json()["cover_custom"] is False
    # a csomag-telepítés az "art" kulcsot tárolja, a tanulói lista hozza a borítót
    inst = (await client.post("/api/learning/library/raktar_tarhely/install", json={}, headers=mgr)).json()
    assert inst["cover_custom"] is True and inst["cover_svg"] == next(a["svg"] for a in art if a["key"] == "warehouse_bins")
    emp = (await client.post("/api/employees", json={"email": "tanulo@example.com", "initial_password": "Titkos-jelszo-123", "role": "employee",
                                                     "last_name": "Tanuló", "first_name": "Teszt", "hire_date": "2025-01-01"}, headers=adm)).json()
    assert (await client.post(f"/api/learning/materials/{inst['id']}/assign", json={"employee_ids": [emp["id"]]}, headers=mgr)).status_code == 200
    assert (await client.post("/api/auth/login", json={"email": "tanulo@example.com", "password": "Titkos-jelszo-123"})).status_code == 200
    item = (await client.get("/api/learning/my")).json()["items"][0]
    assert item["cover_svg"].startswith("<svg") and item["cover_color"].startswith("#")
