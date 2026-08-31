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


# --- Szervizjegy → dedikált feladat a felelősnek ---------------------------


async def _make_assignee(email: str, role: str = "szervizes"):
    from tests.conftest import make_employee_record, make_user

    user, headers = await make_user(email=email, role=role)
    emp = await make_employee_record(user)
    return user, headers, emp


async def _add_attachment(ticket_id: str) -> str:
    """Csatolt kép közvetlen beszúrása (élesben az ügyfélportálról érkezik)."""
    import uuid as _uuid

    import app.db as app_db
    from app.models import TicketAttachment

    factory = app_db.get_session_factory()
    async with factory() as session:
        att = TicketAttachment(
            ticket_id=_uuid.UUID(ticket_id), filename="hiba.png",
            mime="image/png", data=b"\x89PNG-teszt",
        )
        session.add(att)
        await session.commit()
        return str(att.id)


async def test_assignment_creates_dedicated_task(client, manager):
    """Felelős kijelölésekor a kolléga Feladataim nézetébe kerül a jegy —
    kitöltetlen KSZ-munkalappal (szervizes szerepkör) és a jegy képeivel."""
    _, mgr = manager
    szerv_user, szerv_headers, _ = await _make_assignee("szaki@example.com")
    asset = await make_asset(client, mgr, barcode="GEP-TASK", counter=10)

    tk = (
        await client.post(
            "/api/service",
            json={"title": "Nem fűt a bojler", "kind": "repair", "priority": "high",
                  "description": "Reggel óta hideg a víz.",
                  "asset_id": asset["id"],
                  "assigned_to_user_id": str(szerv_user.id)},
            headers=mgr,
        )
    ).json()
    att_id = await _add_attachment(tk["id"])

    my = (await client.get("/api/me/tasks", headers=szerv_headers)).json()
    assert len(my) == 1
    task = my[0]
    assert tk["ticket_no"] in task["title"]
    assert "Reggel óta hideg a víz." in task["description"]
    assert "SÜRGŐS" in task["description"]
    assert task["worksheet_serial"].startswith("KSZ-")
    assert task["worksheet_completed"] is False
    assert task["asset"]["barcode"] == "GEP-TASK"
    assert task["ticket_images"] == [att_id]

    # a képet a feladat gazdája szerviz-jogosultság nélkül is eléri
    img = await client.get(
        f"/api/me/tasks/{task['id']}/ticket-image/{att_id}", headers=szerv_headers
    )
    assert img.status_code == 200
    assert img.headers["content-type"].startswith("image/png")
    assert img.content == b"\x89PNG-teszt"

    # idegen dolgozó nem éri el sem a feladatot, sem a képet
    _, other_headers, _ = await _make_assignee("masik@example.com", role="employee")
    assert (await client.get("/api/me/tasks", headers=other_headers)).json() == []
    denied = await client.get(
        f"/api/me/tasks/{task['id']}/ticket-image/{att_id}", headers=other_headers
    )
    assert denied.status_code == 404


async def test_reassignment_moves_task(client, manager):
    """Átosztáskor nem duplikálódik a feladat — az új felelőshöz vándorol."""
    _, mgr = manager
    first_user, first_headers, _ = await _make_assignee("elso@example.com")
    second_user, second_headers, _ = await _make_assignee(
        "masodik@example.com", role="manager"
    )

    tk = (
        await client.post(
            "/api/service",
            json={"title": "Szivárgó szelep",
                  "assigned_to_user_id": str(first_user.id)},
            headers=mgr,
        )
    ).json()
    assert len((await client.get("/api/me/tasks", headers=first_headers)).json()) == 1

    res = await client.patch(
        f"/api/service/{tk['id']}",
        json={"assigned_to_user_id": str(second_user.id)},
        headers=mgr,
    )
    assert res.status_code == 200
    assert (await client.get("/api/me/tasks", headers=first_headers)).json() == []
    moved = (await client.get("/api/me/tasks", headers=second_headers)).json()
    assert len(moved) == 1 and tk["ticket_no"] in moved[0]["title"]


async def test_assignee_without_employee_record(client, manager, admin):
    """Ha a felelősnek nincs dolgozói törzsadata (pl. tiszta admin-fiók),
    a jegy létrejön, feladat viszont nem — és nincs hiba sem."""
    _, mgr = manager
    adm_user, _adm = admin

    res = await client.post(
        "/api/service",
        json={"title": "Adminra osztott jegy",
              "assigned_to_user_id": str(adm_user.id)},
        headers=mgr,
    )
    assert res.status_code == 201
    listed = (await client.get("/api/tasks", headers=mgr)).json()
    assert all("Adminra osztott" not in t["title"] for t in listed)
