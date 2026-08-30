"""Hibabejelentő: bejelentés képpel, saját lista, admin-triázs, retest-hurok,
modul-kapu."""

from __future__ import annotations

import base64

from tests.test_license import OP, _arm_operator

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake-image").decode()


async def test_bug_lifecycle(client, admin, manager):
    _, adm = admin
    _, mgr = manager

    # bejelentés (tesztelő): 3 mező + auto URL/böngésző + kép
    res = await client.post(
        "/api/bugs",
        json={"description": "A mentés gomb nem csinál semmit a raktár oldalon.",
              "severity": "major", "page_url": "https://x/raktar",
              "user_agent": "Mozilla/5.0 Teszt", "screenshot": PNG},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    bug = res.json()
    assert bug["status"] == "new"
    assert bug["has_screenshot"] is True

    # rossz súlyosság / rossz kép: érthető hiba
    res = await client.post(
        "/api/bugs",
        json={"description": "valami elromlott nagyon", "severity": "katasztrofa",
              "page_url": "https://x/a"},
        headers=mgr,
    )
    assert res.status_code == 422
    res = await client.post(
        "/api/bugs",
        json={"description": "valami elromlott nagyon", "severity": "minor",
              "page_url": "https://x/a", "screenshot": "data:text/html;base64,PGI+"},
        headers=mgr,
    )
    assert res.status_code == 422

    # saját lista a bejelentőnek
    mine = (await client.get("/api/bugs/mine", headers=mgr)).json()
    assert len(mine) == 1

    # admin-sor: szűrés + képernyőkép
    rows = (await client.get("/api/bugs?severity=major", headers=adm)).json()
    assert len(rows) == 1
    res = await client.get(f"/api/bugs/{bug['id']}/screenshot", headers=adm)
    assert res.status_code == 200
    assert res.content.startswith(b"\x89PNG")
    # nem admin nem listáz és nem lát képet
    assert (await client.get("/api/bugs", headers=mgr)).status_code in (401, 403)

    # triázs: megerősítés + kötegelés, majd javítás után resolved
    res = await client.patch(
        f"/api/bugs/{bug['id']}",
        json={"status": "confirmed", "fix_group": "raktar-mentes"},
        headers=adm,
    )
    assert res.json()["status"] == "confirmed"
    assert res.json()["fix_group"] == "raktar-mentes"
    await client.patch(
        f"/api/bugs/{bug['id']}",
        json={"status": "resolved", "resolution_note": "Javítva a v64-ben."},
        headers=adm,
    )

    # a kör zárása: a bejelentő újranyit, majd (újra resolved után) lezár
    res = await client.post(f"/api/bugs/{bug['id']}/reopen", headers=mgr)
    assert res.json()["status"] == "reopened"
    await client.patch(f"/api/bugs/{bug['id']}", json={"status": "resolved"}, headers=adm)
    res = await client.post(f"/api/bugs/{bug['id']}/retest-ok", headers=mgr)
    assert res.json()["status"] == "closed"

    # más bejelentését nem zárhatja le
    res2 = await client.post(
        "/api/bugs",
        json={"description": "Admin saját tesztje ehhez a hibához.",
              "severity": "cosmetic", "page_url": "https://x/b"},
        headers=adm,
    )
    await client.patch(
        f"/api/bugs/{res2.json()['id']}", json={"status": "resolved"}, headers=adm
    )
    res = await client.post(f"/api/bugs/{res2.json()['id']}/retest-ok", headers=mgr)
    assert res.status_code == 403


async def test_bug_module_gate(client, admin, manager, monkeypatch):
    _, adm = admin
    _, mgr = manager
    _arm_operator(monkeypatch)
    await client.put(
        "/api/operator/license",
        json={"plan": "m", "valid_until": None, "enabled_modules": []},
        headers=OP,
    )
    res = await client.post(
        "/api/bugs",
        json={"description": "kikapcsolt modulnál nem megy", "severity": "minor",
              "page_url": "https://x"},
        headers=mgr,
    )
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "license.module_disabled"
    await client.put(
        "/api/operator/license",
        json={"plan": "xl", "valid_until": None, "enabled_modules": None},
        headers=OP,
    )
