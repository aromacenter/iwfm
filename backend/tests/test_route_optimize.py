"""Körút-optimalizálás: legrövidebb megálló-sorrend offline koordinátákból."""

from __future__ import annotations


def test_shortest_open_path_unit():
    """Egyenes mentén fekvő pontok összekevert sorrendből is sorba rendeződnek."""
    from app.api.geo import _path_km, _shortest_open_path

    # Budapest → Kecskemét → Szeged kb. egy vonalon fekszik észak-délen;
    # Győr nyugatra van. A legrövidebb nyílt út: Győr–Bp–Kecskemét–Szeged.
    gyor = (47.68333, 17.63512)
    bud = (47.498, 19.0399)
    kecskemet = (46.90618, 19.69128)
    szeged = (46.253, 20.14824)
    coords = [szeged, gyor, kecskemet, bud]  # összekevert bemenet
    order = _shortest_open_path(coords)
    path = [coords[i] for i in order]
    assert path in ([gyor, bud, kecskemet, szeged], [szeged, kecskemet, bud, gyor])
    assert _path_km(coords, order) < _path_km(coords, [0, 1, 2, 3])


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
