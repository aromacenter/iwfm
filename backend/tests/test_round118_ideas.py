"""118. kör: fejlesztési ötletek a hibajegy mintájára — kind=idea, prioritás,
saját státusz-életút (new → considering → planned → done | declined), a közös
kérdés/válasz-szál és a visszadobás az ötleteknél is működik."""

from __future__ import annotations

from tests.test_license import OP, _arm_operator


async def _idea(client, hdr, text="Legyen sötét mód a beosztás-naptáron is.", priority=3):
    res = await client.post(
        "/api/bugs",
        json={"description": text, "kind": "idea", "priority": priority, "page_url": "https://x/naptar"},
        headers=hdr,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _bug(client, hdr, text="A raktár oldalon eltűnt a mentés gomb."):
    res = await client.post(
        "/api/bugs", json={"description": text, "severity": "major", "page_url": "https://x/raktar"}, headers=hdr,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_idea_create_defaults_and_filters(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    idea = await _idea(client, mgr)
    assert idea["kind"] == "idea" and idea["priority"] == 3 and idea["status"] == "new"
    bug = await _bug(client, mgr)
    assert bug["kind"] == "bug" and bug["priority"] is None
    # prioritás nélkül: közepes; rossz kind: 422
    mid = await _idea(client, mgr, text="Exportálható legyen a partnerlista.", priority=None)
    assert mid["priority"] == 2
    res = await client.post("/api/bugs", json={"description": "valami hosszú", "kind": "wish", "page_url": "x"}, headers=mgr)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "bugs.bad_kind"

    # admin-lista szűrhető fajtára, a sajátok listája mindkettőt hozza
    ideas = (await client.get("/api/bugs?kind=idea", headers=adm)).json()
    assert {b["kind"] for b in ideas} == {"idea"} and len(ideas) == 2
    bugs = (await client.get("/api/bugs?kind=bug", headers=adm)).json()
    assert len(bugs) == 1
    assert len((await client.get("/api/bugs", headers=adm)).json()) == 3
    mine = (await client.get("/api/bugs/mine", headers=mgr)).json()
    assert sorted(b["kind"] for b in mine) == ["bug", "idea", "idea"]


async def test_idea_status_lifecycle_and_validation(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    idea = await _idea(client, mgr)
    # hiba-státusz ötleten: 422; ötlet-státusz hibán: 422
    res = await client.patch(f"/api/bugs/{idea['id']}", json={"status": "resolved"}, headers=adm)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "bugs.bad_status"
    bug = await _bug(client, mgr)
    res = await client.patch(f"/api/bugs/{bug['id']}", json={"status": "planned"}, headers=adm)
    assert res.status_code == 422
    for st in ("considering", "planned", "done"):
        res = await client.patch(f"/api/bugs/{idea['id']}", json={"status": st}, headers=adm)
        assert res.status_code == 200, res.text
        assert res.json()["status"] == st
    # prioritás állítható ötleten, hibán nem változik
    res = await client.patch(f"/api/bugs/{idea['id']}", json={"priority": 1}, headers=adm)
    assert res.json()["priority"] == 1
    res = await client.patch(f"/api/bugs/{bug['id']}", json={"priority": 1}, headers=adm)
    assert res.json()["priority"] is None
    # elvetés indoklással
    res = await client.patch(f"/api/bugs/{idea['id']}", json={"status": "declined", "resolution_note": "Már van ilyen a Beállításokban."}, headers=adm)
    assert res.json()["status"] == "declined" and "Beállítások" in res.json()["resolution_note"]


async def test_idea_thread_bounce_and_operator(client, admin, manager, monkeypatch):
    _, adm = admin
    _, mgr = manager
    _arm_operator(monkeypatch)
    idea = await _idea(client, mgr)
    # az üzemeltető kérdez: nyitott ötlet → needs_info, a bejelentőnél felugrik
    res = await client.post(f"/api/operator/bugs/{idea['id']}/comments", json={"text": "Melyik nézetben hiányzik leginkább?"}, headers=OP)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "needs_info" and res.json()["unread_for_reporter"] is True
    # a bejelentő válaszol → answered
    res = await client.post(f"/api/bugs/{idea['id']}/comments", json={"text": "A heti nézetben."}, headers=mgr)
    assert res.json()["status"] == "answered" and res.json()["unread_for_operator"] is True
    # operator-lista fajtára szűrve, státusz csak ötlet-készletből
    ideas = (await client.get("/api/operator/bugs?kind=idea", headers=OP)).json()
    assert len(ideas) == 1 and ideas[0]["kind"] == "idea"
    res = await client.patch(f"/api/operator/bugs/{idea['id']}", json={"status": "resolved"}, headers=OP)
    assert res.status_code == 422
    res = await client.patch(f"/api/operator/bugs/{idea['id']}", json={"status": "planned", "priority": 3}, headers=OP)
    assert res.status_code == 200 and res.json()["status"] == "planned" and res.json()["priority"] == 3
    res = await client.patch(f"/api/operator/bugs/{idea['id']}", json={"priority": 9}, headers=OP)
    assert res.status_code == 422
    # tervezett ötletre az üzemeltetői megjegyzés nem dobja vissza (nem bounceable)
    res = await client.post(f"/api/operator/bugs/{idea['id']}/comments", json={"text": "Bekerült a roadmapre."}, headers=OP)
    assert res.json()["status"] == "planned"
    # a hibáknál az ötlet-státusz nem használható az operator felől sem
    bug = await _bug(client, mgr)
    res = await client.patch(f"/api/operator/bugs/{bug['id']}", json={"status": "done"}, headers=OP)
    assert res.status_code == 422
