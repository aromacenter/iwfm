"""Tudásbázis: kézi bejegyzések CRUD + auto-KB generálás új gépeknél."""

import pytest

from tests.test_service import make_asset


async def test_kb_entry_crud_and_chat_integration(client, manager, admin):
    _, mgr = manager
    _, adm = admin

    res = await client.post(
        "/api/knowledge",
        json={
            "machine_label": "Jura XJ6",
            "title": "E8 gyakori oka nálunk",
            "content": "A zacctartó mögé szorult őrlemény — porszívózd ki, zsírozd az O-gyűrűket.",
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    entry = res.json()
    assert entry["source"] == "manual"
    assert entry["created_by_name"]

    # lista + szűrés
    listed = (await client.get("/api/knowledge?machine=jura", headers=mgr)).json()
    assert len(listed) == 1
    assert (await client.get("/api/knowledge?q=porszívózd", headers=mgr)).json()
    assert (await client.get("/api/knowledge?machine=saeco", headers=mgr)).json() == []

    # szerkesztés
    upd = await client.patch(
        f"/api/knowledge/{entry['id']}",
        json={"machine_label": "Jura XJ6", "title": "E8 — bevált megoldás",
              "content": entry["content"]},
        headers=mgr,
    )
    assert upd.status_code == 200
    assert upd.json()["title"] == "E8 — bevált megoldás"

    # a chat-tudásba bekerül, gépre szűrve
    import app.db as app_db
    from app.api.support import _load_kb, _relevant_kb

    async with app_db.get_session_factory()() as session:
        kb = await _load_kb(session)
    assert "E8 — bevált megoldás" in kb and "bevált házi megoldás" in kb
    assert "E8 — bevált megoldás" in _relevant_kb(kb, ["Jura", "XJ6"])
    assert "E8 — bevált megoldás" not in _relevant_kb(
        kb + "\n## Saeco Royal — E01\nx\n", ["Saeco", "Royal"]
    )

    # törlés csak delete-joggal
    denied = await client.post(
        "/api/knowledge/bulk-delete", json={"ids": [entry["id"]]}, headers=mgr
    )
    assert denied.status_code == 403
    ok = await client.post(
        "/api/knowledge/bulk-delete", json={"ids": [entry["id"]]}, headers=adm
    )
    assert ok.status_code == 200 and ok.json()["deleted"] == 1


async def test_kb_machines_suggestions(client, manager):
    _, mgr = manager
    await make_asset(client, mgr, barcode="KB-GEP-1", name="Jura X8 Professional",
                     manufacturer="Jura")
    machines = (await client.get("/api/knowledge/machines", headers=mgr)).json()
    assert "Jura X8 Professional" in machines


async def test_kb_rbac(client, employee_user):
    _, emp_headers, _ = employee_user
    assert (await client.get("/api/knowledge", headers=emp_headers)).status_code == 403


async def test_auto_kb_on_asset_create(client, manager, admin, monkeypatch):
    """Új gép rögzítésekor az AI strukturált tudásbázis-bejegyzéseket generál
    (knowledge_entries); ugyanahhoz a típushoz nem generálunk kétszer, de
    ugyanazon gyártó MÁSIK típusához igen."""
    from app.services.wfm import kb_auto

    calls: list[str] = []

    async def fake_generate(db, prompt, **kw):
        calls.append(prompt)
        return ('[{"code": "Error 5", "title": "Hőmérséklet-hiba", '
                '"body": "Hagyd hűlni 30 percet, utána indítsd újra."},'
                '{"code": "Víztartály", "title": "Víztartály üres", '
                '"body": "Töltsd fel a tartályt friss vízzel."}]')

    monkeypatch.setattr(kb_auto.ai_service, "generate", fake_generate)

    _, mgr = manager
    _, adm = admin
    await make_asset(client, mgr, barcode="KB-AUTO-1", name="CafeRomatica 960",
                     manufacturer="Nivona")

    entries = (
        await client.get("/api/knowledge?machine=Nivona CafeRomatica 960", headers=mgr)
    ).json()
    assert len(entries) == 2
    assert any(e["title"].startswith("Error 5") for e in entries)
    assert all(e["source"] == "ai" for e in entries)
    assert len(calls) == 1

    # ugyanaz a típus még egyszer → nincs újabb generálás
    await make_asset(client, mgr, barcode="KB-AUTO-2", name="CafeRomatica 960",
                     manufacturer="Nivona")
    assert len(calls) == 1

    # ugyanazon gyártó MÁSIK típusa → külön tudásbázis készül hozzá
    await make_asset(client, mgr, barcode="KB-AUTO-3", name="CafeRomatica 8",
                     manufacturer="Nivona")
    assert len(calls) == 2


async def test_auto_kb_skips_without_ai(client, manager, admin):
    """AI-konfiguráció nélkül a gép-rögzítés zavartalan, a KB változatlan."""
    _, mgr = manager
    _, adm = admin
    res = await client.post(
        "/api/assets",
        json={"barcode": "KB-NOAI-1", "name": "Coffee Art Plus", "manufacturer": "Schaerer"},
        headers=mgr,
    )
    assert res.status_code == 201
    kb = (await client.get("/api/settings/support", headers=adm)).json()["knowledge_base"]
    assert not kb or "Coffee Art Plus" not in kb


async def test_auto_kb_toggle_off(client, manager, admin, monkeypatch):
    """Kikapcsolt auto_kb mellett akkor sem generálunk, ha az AI menne."""
    from app.services.wfm import kb_auto

    called = []

    async def fake_generate(db, prompt, **kw):
        called.append(1)
        return "## X — Y\nz"

    monkeypatch.setattr(kb_auto.ai_service, "generate", fake_generate)

    _, mgr = manager
    _, adm = admin
    await client.put(
        "/api/settings/support", json={"knowledge_base": None, "auto_kb": False}, headers=adm
    )
    await make_asset(client, mgr, barcode="KB-OFF-1", name="Melitta Cafina XT6",
                     manufacturer="Melitta")
    assert called == []
