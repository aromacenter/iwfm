"""Szerviz: hibajegyek, státusz-léptetés, karbantartás-esedékesség, RBAC."""

from tests.test_inventory import asset_payload


async def make_asset(client, headers, **kw) -> dict:
    body = asset_payload(**kw)
    res = await client.post("/api/assets", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


async def test_ticket_crud_and_numbering(client, manager):
    _, mgr = manager
    asset = await make_asset(client, mgr, counter=1000, norm=5000.0)

    res = await client.post(
        "/api/service",
        json={"title": "Nem ad ki kávét", "kind": "repair", "priority": "high",
              "asset_id": asset["id"]},
        headers=mgr,
    )
    assert res.status_code == 201, res.text
    tk = res.json()
    assert tk["ticket_no"] == "SZ-0001"
    assert tk["status"] == "open"
    assert asset["barcode"] in tk["asset_label"]

    second = (
        await client.post("/api/service", json={"title": "Általános jegy"}, headers=mgr)
    ).json()
    assert second["ticket_no"] == "SZ-0002"
    assert second["asset_id"] is None

    listed = (await client.get("/api/service", headers=mgr)).json()
    assert len(listed) == 2

    # szűrés + keresés
    only_open = (await client.get("/api/service?status=open", headers=mgr)).json()
    assert len(only_open) == 2
    found = (await client.get("/api/service?q=SZ-0002", headers=mgr)).json()
    assert len(found) == 1 and found[0]["title"] == "Általános jegy"


async def test_ticket_status_flow_updates_counter(client, manager):
    _, mgr = manager
    asset = await make_asset(client, mgr, barcode="GEP-777", counter=100, norm=1000.0)
    tk = (
        await client.post(
            "/api/service",
            json={"title": "Karbantartás", "kind": "maintenance", "asset_id": asset["id"]},
            headers=mgr,
        )
    ).json()

    upd = await client.patch(
        f"/api/service/{tk['id']}",
        json={"status": "in_progress"},
        headers=mgr,
    )
    assert upd.status_code == 200
    assert upd.json()["status"] == "in_progress"
    assert upd.json()["resolved_at"] is None

    done = (
        await client.patch(
            f"/api/service/{tk['id']}",
            json={"status": "done", "resolution": "Vízkőtelenítve",
                  "counter_at_service": 1500},
            headers=mgr,
        )
    ).json()
    assert done["status"] == "done"
    assert done["resolved_at"] is not None
    assert done["counter_at_service"] == 1500

    # a gép számlálója felfrissült a rögzített állásra
    assets = (await client.get("/api/assets?q=GEP-777", headers=mgr)).json()
    assert assets[0]["counter"] == 1500


async def test_maintenance_due_logic(client, manager):
    """Esedékesség: counter-utolsó szerviz >= norma; nyitott karbantartás-jegy
    elnémítja; kész szerviz után a következő ciklus a rögzített állástól indul."""
    _, mgr = manager
    asset = await make_asset(client, mgr, barcode="GEP-DUE", counter=6000, norm=5000.0)

    due = (await client.get("/api/service/maintenance-due", headers=mgr)).json()
    assert [d["barcode"] for d in due] == ["GEP-DUE"]
    assert due[0]["overdue_by"] == 1000

    # nyitott karbantartás-jegy → nem jelezzük újra
    tk = (
        await client.post(
            "/api/service",
            json={"title": "Esedékes karbantartás", "kind": "maintenance",
                  "asset_id": asset["id"]},
            headers=mgr,
        )
    ).json()
    assert (await client.get("/api/service/maintenance-due", headers=mgr)).json() == []

    # elkészül 6000-es állásnál → legközelebb 11000-nél esedékes
    await client.patch(
        f"/api/service/{tk['id']}",
        json={"status": "done", "counter_at_service": 6000},
        headers=mgr,
    )
    assert (await client.get("/api/service/maintenance-due", headers=mgr)).json() == []

    # számláló 11500-ra nő → megint esedékes
    upd = asset_payload(barcode="GEP-DUE", counter=11500, norm=5000.0)
    res = await client.patch(f"/api/assets/{asset['id']}", json=upd, headers=mgr)
    assert res.status_code == 200, res.text
    due2 = (await client.get("/api/service/maintenance-due", headers=mgr)).json()
    assert [d["barcode"] for d in due2] == ["GEP-DUE"]
    assert due2[0]["since_last_service"] == 5500


async def test_service_rbac_and_delete(client, manager, admin, employee_user):
    _, mgr = manager
    _, adm = admin
    _, emp_headers, _ = employee_user

    assert (await client.get("/api/service", headers=emp_headers)).status_code == 403

    tk = (
        await client.post("/api/service", json={"title": "Törlendő"}, headers=mgr)
    ).json()
    # törlés csak delete-joggal (alapból admin)
    denied = await client.post(
        "/api/service/bulk-delete", json={"ids": [tk["id"]]}, headers=mgr
    )
    assert denied.status_code == 403
    ok = await client.post(
        "/api/service/bulk-delete", json={"ids": [tk["id"]]}, headers=adm
    )
    assert ok.status_code == 200 and ok.json()["deleted"] == 1


async def test_assignees_list(client, manager, admin):
    _, mgr = manager
    _, _adm = admin
    rows = (await client.get("/api/service/assignees", headers=mgr)).json()
    roles = {r["role"] for r in rows}
    assert roles <= {"szervizes", "manager", "admin"}
    assert len(rows) >= 2  # a manager és az admin fixtúra-user
