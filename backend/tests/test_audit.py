"""Audit napló: admin-only nézet, szűrés, lapozás."""

from tests.test_inventory import make_partner


async def test_audit_admin_only(client, manager):
    _, mgr = manager
    assert (await client.get("/api/audit", headers=mgr)).status_code == 403


async def test_audit_list_and_filters(client, manager, admin):
    _, mgr = manager
    _, adm = admin
    await make_partner(client, mgr, name="Naplózott Kft.")  # partner.create esemény

    body = (await client.get("/api/audit", headers=adm)).json()
    assert body["total"] >= 1
    actions = {item["action"] for item in body["items"]}
    assert "partner.create" in actions
    created = next(i for i in body["items"] if i["action"] == "partner.create")
    assert created["actor_name"]  # a manager display_name-je feloldva

    # prefix-szűrés
    filtered = (await client.get("/api/audit?action=partner.", headers=adm)).json()
    assert all(i["action"].startswith("partner.") for i in filtered["items"])
    assert filtered["total"] >= 1

    # lapozás: limit=1 → 1 elem, a total változatlan
    paged = (await client.get("/api/audit?limit=1", headers=adm)).json()
    assert len(paged["items"]) == 1
    assert paged["total"] == body["total"]

    # segéd-végpontok
    assert "partner.create" in (await client.get("/api/audit/actions", headers=adm)).json()
    actors = (await client.get("/api/audit/actors", headers=adm)).json()
    assert len(actors) >= 1 and all("name" in a for a in actors)
