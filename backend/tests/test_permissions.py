"""Szerepkör-jogosultságok: alapértelmezett mátrix, törlés admin-only, mátrix-szerkesztés."""

from tests.conftest import make_user


async def test_uzletkoto_defaults(client, manager):
    """Üzletkötő: partnerek/elszámolás ok; dolgozók/törlés tiltva."""
    _, mgr = manager
    _, uz = await make_user(email="uzletkoto@example.com", role="uzletkoto")

    assert (await client.get("/api/partners", headers=uz)).status_code == 200
    assert (await client.get("/api/products", headers=uz)).status_code == 200
    assert (await client.get("/api/settlements", headers=uz)).status_code == 200
    assert (await client.get("/api/employees", headers=uz)).status_code == 403
    assert (await client.get("/api/shifts?week_start=2026-01-05", headers=uz)).status_code == 403
    assert (
        await client.post("/api/partners/bulk-delete", json={"ids": ["x"]}, headers=uz)
    ).status_code == 403


async def test_szervizes_defaults(client):
    """Szervizes: gépek ok; partnerek/elszámolás tiltva."""
    _, sz = await make_user(email="szervizes@example.com", role="szervizes")
    assert (await client.get("/api/assets", headers=sz)).status_code == 200
    assert (await client.get("/api/partners", headers=sz)).status_code == 403
    assert (await client.get("/api/settlements", headers=sz)).status_code == 403


async def test_delete_admin_only_by_default(client, admin, manager):
    """Törlés: admin igen, manager alapból nem."""
    _, adm = admin
    _, mgr = manager
    assert (
        await client.post("/api/assets/bulk-delete", json={"ids": ["x"]}, headers=mgr)
    ).status_code == 403
    res = await client.post(
        "/api/assets/bulk-delete",
        json={"ids": ["00000000-0000-0000-0000-000000000000"]},
        headers=adm,
    )
    assert res.status_code == 200  # admin mehet (0 törlés, nincs ilyen id)


async def test_matrix_editable(client, admin):
    """A mátrix szerkesztésével a szervizes megkaphatja a partnerek jogot."""
    _, adm = admin
    _, sz = await make_user(email="sz2@example.com", role="szervizes")
    assert (await client.get("/api/partners", headers=sz)).status_code == 403

    current = (await client.get("/api/settings/permissions", headers=adm)).json()
    assert "delete" in current["features"]
    matrix = current["matrix"]
    matrix["szervizes"] = matrix["szervizes"] + ["partners"]
    upd = await client.put(
        "/api/settings/permissions", json={"matrix": matrix}, headers=adm
    )
    assert upd.status_code == 200, upd.text
    assert "partners" in upd.json()["matrix"]["szervizes"]

    assert (await client.get("/api/partners", headers=sz)).status_code == 200


async def test_me_returns_permissions(client, admin):
    _, adm = admin
    me = (await client.get("/api/auth/me", headers=adm)).json()
    assert "delete" in me["permissions"]  # admin mindent kap

    _, uz = await make_user(email="uz2@example.com", role="uzletkoto")
    me2 = (await client.get("/api/auth/me", headers=uz)).json()
    assert "partners" in me2["permissions"]
    assert "delete" not in me2["permissions"]


async def test_employee_create_with_role(client, admin):
    """Dolgozó felvétele üzletkötő login-szerepkörrel."""
    from tests.test_employees import employee_payload

    _, adm = admin
    res = await client.post(
        "/api/employees",
        json=employee_payload(email="uzkoto.uj@example.com", role="uzletkoto"),
        headers=adm,
    )
    assert res.status_code == 201, res.text
    assert res.json()["role"] == "uzletkoto"

    bad = await client.post(
        "/api/employees",
        json=employee_payload(email="rossz@example.com", role="fonok"),
        headers=adm,
    )
    assert bad.status_code == 422
