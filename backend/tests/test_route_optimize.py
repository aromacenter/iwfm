"""Körút-optimalizálás: legrövidebb megálló-sorrend offline koordinátákból."""

from __future__ import annotations


def test_shortest_open_path_unit():
    """Egyenes mentén fekvő pontok összekevert sorrendből is sorba rendeződnek."""
    from app.api.geo import _haversine_matrix, _path_km, _shortest_open_path

    # Budapest → Kecskemét → Szeged kb. egy vonalon fekszik észak-délen;
    # Győr nyugatra van. A legrövidebb nyílt út: Győr–Bp–Kecskemét–Szeged.
    gyor = (47.68333, 17.63512)
    bud = (47.498, 19.0399)
    kecskemet = (46.90618, 19.69128)
    szeged = (46.253, 20.14824)
    coords = [szeged, gyor, kecskemet, bud]  # összekevert bemenet
    dist = _haversine_matrix(coords)
    order = _shortest_open_path(dist)
    path = [coords[i] for i in order]
    assert path in ([gyor, bud, kecskemet, szeged], [szeged, kecskemet, bud, gyor])
    assert _path_km(dist, order) < _path_km(dist, [0, 1, 2, 3])


def test_heuristic_matches_exact_on_small_input():
    """A >10 megállós heurisztika kis bemeneten hozza az egzakt eredményt."""
    from app.api.geo import (
        _haversine_matrix,
        _nearest_neighbor_2opt,
        _path_km,
        _shortest_open_path,
    )

    coords = [
        (47.68333, 17.63512), (47.498, 19.0399), (46.90618, 19.69128),
        (46.253, 20.14824), (47.95539, 21.71671), (46.07308, 18.23265),
    ]
    dist = _haversine_matrix(coords)
    exact = _path_km(dist, _shortest_open_path(dist))
    heur = _path_km(dist, _nearest_neighbor_2opt(dist))
    assert abs(heur - exact) < 1.0


async def test_optimize_route_endpoint(client, manager):
    """A végpont irányítószámból rendez; koordináta nélküli partner a végére."""
    _, mgr = manager

    async def partner(name, **kw):
        res = await client.post("/api/partners", json={"name": name, **kw}, headers=mgr)
        assert res.status_code in (200, 201), res.text
        return res.json()

    szeged = await partner("Szegedi Bolt", address_zip="6720", address_city="Szeged",
                           address_street="Kárász u.", address_number="1")
    gyor = await partner("Győri Bolt", address_zip="9021", address_city="Győr")
    kecskemet = await partner("Kecskeméti Bolt", address_city="Kecskemét")  # város-fallback
    bud = await partner("Pesti Bolt", address_zip="1051", address_city="Budapest")
    nowhere = await partner("Címtelen Bolt")  # se irányítószám, se város

    res = await client.post(
        "/api/geo/optimize-route",
        json={"partner_ids": [szeged["id"], gyor["id"], kecskemet["id"],
                              bud["id"], nowhere["id"]]},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    data = res.json()
    names = [s["name"] for s in data["stops"]]
    # koordinátás megállók a legrövidebb sorrendben (bármelyik irányban)
    assert names[:4] in (
        ["Győri Bolt", "Pesti Bolt", "Kecskeméti Bolt", "Szegedi Bolt"],
        ["Szegedi Bolt", "Kecskeméti Bolt", "Pesti Bolt", "Győri Bolt"],
    )
    # a koordináta nélküli a végén, jelölve
    assert names[4] == "Címtelen Bolt"
    assert data["stops"][4]["located"] is False
    assert data["total_km"] is not None
    assert data["total_km"] <= data["original_km"]
    # a cím a strukturált mezőkből is összeáll
    szeged_stop = next(s for s in data["stops"] if s["name"] == "Szegedi Bolt")
    assert "6720" in szeged_stop["address"]


async def test_optimize_route_validation(client, manager):
    _, mgr = manager
    res = await client.post(
        "/api/geo/optimize-route", json={"partner_ids": ["nem-uuid", "szinten-nem"]},
        headers=mgr,
    )
    assert res.status_code == 404


async def test_geocoded_latlng_takes_precedence(client, manager):
    """Ha a partneren van geokódolt lat/lng, az erősebb az irányítószámnál."""
    import uuid as uuid_mod

    import app.db as app_db
    from app.api.geo import _partner_coords
    from app.models import Partner

    _, mgr = manager
    res = await client.post(
        "/api/partners",
        json={"name": "Geo Bolt", "address_zip": "6720", "address_city": "Szeged"},
        headers=mgr,
    )
    assert res.status_code in (200, 201)
    pid = uuid_mod.UUID(res.json()["id"])
    factory = app_db.get_session_factory()
    async with factory() as db:
        p = await db.get(Partner, pid)
        assert _partner_coords(p) is not None  # irányítószámból
        p.lat, p.lng = 47.5, 19.05  # geokódolt (pontos) koordináta
        assert _partner_coords(p) == (47.5, 19.05)


async def test_plan_week_endpoint(client, manager):
    """A heti tervező napokra osztja a megállókat; a címtelen a végére kerül."""
    _, mgr = manager

    async def partner(name, **kw):
        res = await client.post("/api/partners", json={"name": name, **kw}, headers=mgr)
        assert res.status_code in (200, 201), res.text
        return res.json()

    # Két, földrajzilag jól elváló csoport: kelet (Szeged/Kecskemét/Debrecen)
    # és nyugat (Győr/Sopron/Pécs) — a sweep-nek nem szabad összekevernie
    # nagyon: minden nap kb. 3 megálló.
    names = []
    for name, zip_code in [
        ("Szegedi", "6720"), ("Kecskeméti", "6000"), ("Debreceni", "4024"),
        ("Győri", "9021"), ("Soproni", "9400"), ("Pécsi", "7621"),
    ]:
        names.append(await partner(f"{name} Bolt", address_zip=zip_code))
    nowhere = await partner("Címtelen Bolt")

    res = await client.post(
        "/api/geo/plan-week",
        json={"partner_ids": [p["id"] for p in names] + [nowhere["id"]], "days": 2},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert len(data["days"]) == 2
    sizes = [len(d["stops"]) for d in data["days"]]
    assert sum(sizes) == 7
    # kiegyenlített osztás: 3 + 3 koordinátás, +1 címtelen az utolsó napon
    assert data["days"][-1]["stops"][-1]["name"] == "Címtelen Bolt"
    assert data["days"][-1]["stops"][-1]["located"] is False
    assert data["total_km"] is not None and data["total_km"] > 0
