"""137. kör: szerszám-nyilvántartás — felvétel, saját lista, átadás + napló,
ellenőrzés (intervallumból), állapot, gyári szám ütközés, modulkapcsoló, jogok."""

from __future__ import annotations

from datetime import date, timedelta

from tests.test_parts import _enable_modules
from tests.test_tasks import make_emp


async def test_tools_flow(client, manager):
    _, mgr = manager
    a, a_h = await make_emp(email="szerszamos-a@example.com")
    b, b_h = await make_emp(email="szerszamos-b@example.com")

    # felvétel A-nak, ellenőrzési ciklussal; a gyári szám egyedi
    res = await client.post("/api/tools", json={
        "name": "Digitális multiméter", "serial_no": "MM-001", "category": "mérőműszer",
        "holder_employee_id": str(a.id), "check_interval_months": 12,
        "next_check": (date.today() + timedelta(days=10)).isoformat(),
    }, headers=mgr)
    assert res.status_code == 201, res.text
    tool = res.json()
    assert tool["holder_employee_id"] == str(a.id) and tool["holder_name"] and tool["check_state"] == "soon"
    assert (await client.post("/api/tools", json={"name": "Másik", "serial_no": "MM-001"}, headers=mgr)).status_code == 409
    assert (await client.post("/api/tools", json={"name": "Rossz", "holder_employee_id": "nem-uuid"}, headers=mgr)).status_code == 422

    # A látja a sajátját, B nem; a dolgozó a teljes listát nem
    assert [x["id"] for x in (await client.get("/api/tools/my", headers=a_h)).json()] == [tool["id"]]
    assert (await client.get("/api/tools/my", headers=b_h)).json() == []
    assert (await client.get("/api/tools", headers=a_h)).status_code == 403

    # esedékes szűrő
    due = (await client.get("/api/tools?due=true", headers=mgr)).json()
    assert [x["id"] for x in due] == [tool["id"]]

    # átadás B-nek → napló
    res = await client.post(f"/api/tools/{tool['id']}/handover", json={"employee_id": str(b.id), "note": "autóval együtt"}, headers=mgr)
    assert res.status_code == 200 and res.json()["holder_employee_id"] == str(b.id)
    assert (await client.get("/api/tools/my", headers=a_h)).json() == []
    assert len((await client.get("/api/tools/my", headers=b_h)).json()) == 1
    events = (await client.get(f"/api/tools/{tool['id']}/events", headers=mgr)).json()
    assert events[0]["kind"] == "handover" and events[0]["to_name"] and events[0]["note"] == "autóval együtt"

    # ellenőrzés: next_check a 12 hónapos ciklusból, last_check = ma
    res = await client.post(f"/api/tools/{tool['id']}/check", json={"note": "kalibrálva"}, headers=mgr)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["last_check"] == date.today().isoformat()
    assert out["next_check"][:4] == str(date.today().year + 1) and out["check_state"] == "ok"
    assert (await client.get("/api/tools?due=true", headers=mgr)).json() == []

    # állapot módosítás → napló; visszavétel a raktárba
    res = await client.patch(f"/api/tools/{tool['id']}", json={"status": "repair"}, headers=mgr)
    assert res.status_code == 200 and res.json()["status"] == "repair"
    res = await client.post(f"/api/tools/{tool['id']}/handover", json={"employee_id": None}, headers=mgr)
    assert res.json()["holder_employee_id"] is None
    kinds = [e["kind"] for e in (await client.get(f"/api/tools/{tool['id']}/events", headers=mgr)).json()]
    assert kinds == ["handover", "status", "check", "handover", "handover"]
    assert (await client.get("/api/tools?holder=none", headers=mgr)).json()[0]["id"] == tool["id"]

    # modulkapcsoló: tools nélkül 403
    await _enable_modules(["employees", "tasks", "service"])
    assert (await client.get("/api/tools", headers=mgr)).status_code == 403
    assert (await client.get("/api/tools/my", headers=b_h)).status_code == 403
