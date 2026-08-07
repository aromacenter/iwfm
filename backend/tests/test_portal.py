"""Partner-portál: link-generálás, nyilvános nézet, visszavonás."""

from tests.test_consignment import _setup_settlement


async def test_portal_flow(client, manager):
    _, mgr = manager
    partner, _product, _settlement = await _setup_settlement(client, mgr)

    # link generálása (idempotens: másodszor ugyanazt adja)
    res = await client.post(f"/api/partners/{partner['id']}/portal-link", headers=mgr)
    assert res.status_code == 200, res.text
    token = res.json()["token"]
    assert len(token) >= 32
    again = await client.post(f"/api/partners/{partner['id']}/portal-link", headers=mgr)
    assert again.json()["token"] == token

    # nyilvános nézet — bejelentkezés nélkül
    view = await client.get(f"/api/portal/{token}")
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["partner_name"] == "Kávézó Bt."
    assert len(body["stock"]) == 1
    assert abs(body["stock"][0]["quantity"] - 0.3) < 1e-9
    assert len(body["settlements"]) == 1
    assert body["settlements"][0]["total_gross"] == 6350.0
    assert body["outstanding_total"] == 0

    # érvénytelen token → 404
    assert (await client.get("/api/portal/ervenytelen-token-123456")).status_code == 404

    # visszavonás után a link halott
    revoke = await client.delete(f"/api/partners/{partner['id']}/portal-link", headers=mgr)
    assert revoke.status_code == 200
    assert (await client.get(f"/api/portal/{token}")).status_code == 404


async def test_portal_link_requires_partner_perm(client, employee_user):
    _, emp_headers, _ = employee_user
    res = await client.post(
        "/api/partners/00000000-0000-0000-0000-000000000000/portal-link",
        headers=emp_headers,
    )
    assert res.status_code == 403
