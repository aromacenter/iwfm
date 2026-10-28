"""114. kör: párbeszéd-szál a hibajegyen — az üzemeltető/admin kérdez
(needs_info, felugró értesítés a bejelentőnél), a bejelentő válaszol (answered),
olvasottsági jelzések mindkét oldalon."""

from __future__ import annotations

from tests.conftest import make_user
from tests.test_license import OP, _arm_operator


async def _report(client, hdr, text="A raktár oldalon eltűnt a mentés gomb."):
    res = await client.post(
        "/api/bugs",
        json={"description": text, "severity": "major", "page_url": "https://x/raktar"},
        headers=hdr,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_operator_question_and_reporter_answer(client, admin, manager, monkeypatch):
    _, mgr = manager
    _arm_operator(monkeypatch)
    bug = await _report(client, mgr)

    # üres hozzászólás nem megy
    res = await client.post(f"/api/operator/bugs/{bug['id']}/comments", json={"text": "  "}, headers=OP)
    assert res.status_code == 422

    # az üzemeltető kérdez → needs_info, a bejelentőnél olvasatlan
    res = await client.post(
        f"/api/operator/bugs/{bug['id']}/comments",
        json={"text": "Melyik böngészőben? Mobilon is?", "author_name": "Üzemeltető"},
        headers=OP,
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["status"] == "needs_info"
    assert out["comments"][0]["author_kind"] == "operator"
    assert out["unread_for_reporter"] is True
    assert out["unread_for_operator"] is False

    mine = (await client.get("/api/bugs/mine", headers=mgr)).json()
    me = next(b for b in mine if b["id"] == bug["id"])
    assert me["unread_for_reporter"] is True
    assert me["comments"][0]["text"].startswith("Melyik")

    # a bejelentő megnézte → a jelzés törlődik
    assert (await client.post(f"/api/bugs/{bug['id']}/seen", headers=mgr)).status_code == 200
    me = next(b for b in (await client.get("/api/bugs/mine", headers=mgr)).json() if b["id"] == bug["id"])
    assert me["unread_for_reporter"] is False

    # a bejelentő válaszol → answered, az üzemeltetőnél olvasatlan
    res = await client.post(
        f"/api/bugs/{bug['id']}/comments", json={"text": "Chrome, asztali gépen.", "status": "closed"},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["status"] == "answered"  # a bejelentő státuszt nem állíthat
    assert [c["author_kind"] for c in out["comments"]] == ["operator", "reporter"]
    assert out["unread_for_reporter"] is False
    assert out["unread_for_operator"] is True

    rows = (await client.get("/api/operator/bugs?status=answered", headers=OP)).json()
    assert rows and rows[0]["unread_for_operator"] is True
    assert (await client.post(f"/api/operator/bugs/{bug['id']}/seen", headers=OP)).status_code == 200
    rows = (await client.get("/api/operator/bugs?status=answered", headers=OP)).json()
    assert rows[0]["unread_for_operator"] is False

    # az üzemeltető lezárja megjegyzéssel + explicit státusszal
    res = await client.post(
        f"/api/operator/bugs/{bug['id']}/comments",
        json={"text": "Köszi, javítva, teszteld!", "status": "resolved"},
        headers=OP,
    )
    assert res.json()["status"] == "resolved"
    assert res.json()["unread_for_reporter"] is True


async def test_admin_bounce_and_foreign_user_forbidden(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    bug = await _report(client, mgr, "A naptár üres marad frissítés után.")

    # a példány adminja kérdez: needs_info
    res = await client.post(
        f"/api/bugs/{bug['id']}/comments", json={"text": "Melyik hónapban?"}, headers=adm,
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "needs_info"
    assert res.json()["comments"][0]["author_kind"] == "admin"

    # admin lista tartalmazza a szálat
    rows = (await client.get("/api/bugs?status=needs_info", headers=adm)).json()
    assert rows[0]["comments"][0]["text"] == "Melyik hónapban?"

    # idegen (nem bejelentő, nem admin) nem szólhat hozzá és nem jelölhet olvasottnak
    _, other = await make_user(email="r114-other@example.com", role="szervizes")
    assert (await client.post(f"/api/bugs/{bug['id']}/comments", json={"text": "én is"}, headers=other)).status_code == 403
    assert (await client.post(f"/api/bugs/{bug['id']}/seen", headers=other)).status_code == 403

    # rossz státusz az admintól
    res = await client.post(
        f"/api/bugs/{bug['id']}/comments", json={"text": "x", "status": "nemletezik"}, headers=adm,
    )
    assert res.status_code == 422

    # nem létező jegy
    assert (await client.post("/api/bugs/00000000-0000-0000-0000-000000000000/comments", json={"text": "?"}, headers=adm)).status_code == 404


async def test_resolved_bug_stays_resolved_on_operator_note(client, manager, monkeypatch):
    """Lezárás után a megjegyzés nem dobja vissza needs_info-ra."""
    _, mgr = manager
    _arm_operator(monkeypatch)
    bug = await _report(client, mgr, "Kis elírás a láblécben.")
    await client.patch(f"/api/operator/bugs/{bug['id']}", json={"status": "resolved"}, headers=OP)
    res = await client.post(f"/api/operator/bugs/{bug['id']}/comments", json={"text": "Javítva."}, headers=OP)
    assert res.json()["status"] == "resolved"
