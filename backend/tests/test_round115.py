"""115. kör: termék-roadmap — a Flotta-pult lenyomja (operator), a
felhasználók olvassák."""

from __future__ import annotations

from tests.test_license import OP, _arm_operator


async def test_roadmap_push_and_read(client, manager, monkeypatch):
    _, mgr = manager
    _arm_operator(monkeypatch)

    # token nélkül zárva
    assert (await client.put("/api/operator/roadmap", json={"items": []})).status_code == 403

    res = await client.put(
        "/api/operator/roadmap",
        json={"items": [
            {"title": "Kártyás fizetés", "description": "SimplePay", "status": "planned", "eta": "2026 Q4", "sort": 20},
            {"title": "Hibajegy-párbeszéd", "description": "", "status": "done", "eta": "2026 Q4", "sort": 5},
            {"title": "", "status": "planned"},  # üres cím kimarad
            {"title": "Rossz státusz", "status": "whatever", "sort": 30},
        ]},
        headers=OP,
    )
    assert res.status_code == 200, res.text
    assert res.json()["stored"] == 3

    rows = (await client.get("/api/roadmap", headers=mgr)).json()
    assert [r["title"] for r in rows] == ["Hibajegy-párbeszéd", "Kártyás fizetés", "Rossz státusz"]
    assert rows[0]["status"] == "done"
    assert rows[2]["status"] == "planned"
    assert "sort" not in rows[0]

    # teljes csere: üres lista törli
    await client.put("/api/operator/roadmap", json={"items": []}, headers=OP)
    assert (await client.get("/api/roadmap", headers=mgr)).json() == []

    # bejelentkezés nélkül nem olvasható
    assert (await client.get("/api/roadmap")).status_code == 401
