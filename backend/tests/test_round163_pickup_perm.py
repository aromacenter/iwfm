"""163. kör: a Szerelő-átadás saját jogosultság („pickup”): a Feladatok-jog
nélkül is adható, a Feladatok-joggal rendelkezők automatikusan megkapják."""

from __future__ import annotations

from app.api.deps import DEFAULT_MATRIX, FEATURES
from tests.conftest import make_user


def test_pickup_is_a_feature():
    assert "pickup" in FEATURES
    assert "pickup" in DEFAULT_MATRIX["manager"] and "pickup" in DEFAULT_MATRIX["employee"]
    assert "pickup" not in DEFAULT_MATRIX["szervizes"]  # a külsős szerelő nem látja a saját díjait innen


async def test_pickup_permission_gate(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    # manager: alapból megy
    assert (await client.get("/api/tasks/service-handover/list", headers=mgr)).status_code == 200
    # a jogosultság-mátrixban külön sorként szerepel
    perms = (await client.get("/api/settings/permissions", headers=adm)).json()
    assert "pickup" in perms["features"]
    # mentett mátrix: a managernek csak "tasks" → a pickup automatikusan hozzáadódik
    matrix = perms["matrix"]
    matrix["manager"] = ["dashboard", "tasks"]
    matrix["employee"] = ["my_schedule", "my_tasks"]
    res = await client.put("/api/settings/permissions", json={"matrix": matrix}, headers=adm)
    assert res.status_code == 200, res.text
    assert "pickup" in (await client.get("/api/settings/permissions", headers=adm)).json()["matrix"]["manager"]
    assert (await client.get("/api/tasks/service-handover/list", headers=mgr)).status_code == 200
    # dolgozó pickup nélkül: 403; pickup-pal (Feladatok-jog nélkül): 200
    _, emp = await make_user(email="szerelo@example.com", role="employee")
    assert (await client.get("/api/tasks/service-handover/list", headers=emp)).status_code == 403
    matrix["employee"] = ["my_schedule", "my_tasks", "pickup"]
    assert (await client.put("/api/settings/permissions", json={"matrix": matrix}, headers=adm)).status_code == 200
    assert (await client.get("/api/tasks/service-handover/list", headers=emp)).status_code == 200
    assert (await client.get("/api/tasks", headers=emp)).status_code == 403  # a Feladatok továbbra sem az övé
