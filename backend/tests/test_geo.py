"""Irányítószám → város feloldás (beépített GeoNames-kivonat)."""


async def test_zip_resolves_city(client, manager):
    _, mgr = manager
    assert (await client.get("/api/geo/zip/1011", headers=mgr)).json()["city"] == "Budapest"
    assert (await client.get("/api/geo/zip/6720", headers=mgr)).json()["city"] == "Szeged"
    assert (await client.get("/api/geo/zip/9700", headers=mgr)).json()["city"] == "Szombathely"


async def test_unknown_zip_404(client, manager):
    _, mgr = manager
    res = await client.get("/api/geo/zip/0000", headers=mgr)
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "geo.zip_not_found"


async def test_zip_requires_auth(client):
    assert (await client.get("/api/geo/zip/1011")).status_code == 401
