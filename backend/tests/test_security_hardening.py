"""Biztonsági keményítés (2026-11-03 audit): SVG-feltöltés tiltása (tárolt
XSS), szigorú fejlécek a visszaszolgált képeken, a nyilvános QR-oldal nem ad
ki kapcsolattartói személyes adatot, névtelen bejelentésből nem lesz
partner-kapcsolattartó e-mail."""

from __future__ import annotations

import base64

from tests.conftest import make_user
from tests.test_support import _asset_with_token

SVG = "data:image/svg+xml;base64," + base64.b64encode(
    b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
).decode()
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake-image").decode()


async def test_intake_photo_rejects_svg(client, admin, manager):
    _, mgr = manager
    res = await client.post(
        "/api/assets", json={"barcode": "SEC-INT-1", "name": "Gép", "customer_owned": True}, headers=mgr
    )
    assert res.status_code == 201, res.text
    asset = res.json()
    _, sz = await make_user(email="sec-atvevo@example.com", role="szervizes")
    res = await client.post(
        "/api/intakes",
        json={"asset_id": asset["id"], "client_name": "X", "client_phone": "+3630",
              "client_email": "x@example.com", "photos": [SVG]},
        headers=sz,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "intake.bad_photo"


async def test_bug_screenshot_rejects_svg_and_image_gets_sandbox_csp(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    res = await client.post(
        "/api/bugs",
        json={"description": "svg-vel probalkozom itt", "severity": "minor",
              "page_url": "https://x/a", "screenshot": SVG},
        headers=mgr,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "bugs.bad_screenshot"

    res = await client.post(
        "/api/bugs",
        json={"description": "png-vel rendben van ez", "severity": "minor",
              "page_url": "https://x/a", "screenshot": PNG},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    img = await client.get(f"/api/bugs/{res.json()['id']}/screenshot", headers=adm)
    assert img.status_code == 200
    assert img.headers["content-type"].startswith("image/png")
    # képválaszon szigorú CSP + sandbox: még egy becsúszó SVG se futtathatna scriptet
    assert "sandbox" in img.headers.get("content-security-policy", "")
    assert img.headers.get("x-content-type-options") == "nosniff"


async def test_public_qr_page_has_no_contact_pii_and_does_not_set_partner_email(client, manager):
    _, mgr = manager
    asset, token = await _asset_with_token(client, mgr)

    # partner kapcsolattartóval, a gép hozzá kihelyezve
    res = await client.post(
        "/api/partners",
        json={"name": "QR Partner Kft.", "contact_name": "Titkos Tamás",
              "contact_phone": "+36301112222"},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    partner = res.json()
    res = await client.post(
        f"/api/assets/{asset['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr
    )
    assert res.status_code in (200, 201), res.text

    info = (await client.get(f"/api/support/{token}")).json()
    assert "contact_name" not in info and "contact_phone" not in info
    assert "Titkos" not in str(info)

    # névtelen bejelentés e-maillel: a jegyre kerül, a partnerre NEM
    res = await client.post(
        f"/api/support/{token}/ticket",
        json={"description": "Nem ad ki kávét, kérem hívjanak.",
              "contact_email": "tamado@example.com"},
    )
    assert res.status_code == 201, res.text
    p = (await client.get(f"/api/partners/{partner['id']}", headers=mgr)).json()
    assert (p.get("contact_email") or "") == ""
