"""154. kör (Senzortech jegyek):
- aa541fa1: alkatrész opcionális vonalkódja — mentés, keresés, CSV export/import.
- 481ab7f4: feladat „visszatérő / garanciális javítás” jelölése (létrehozáskor és
  utólag, korábbi feladatra hivatkozva), csapat- és egyéni statisztika."""

from __future__ import annotations

from datetime import date, timedelta

from tests.conftest import make_employee_record, make_user
from tests.test_tasks import payload


async def _named_emp(email: str, last: str):
    user, headers = await make_user(email=email, role="employee")
    return await make_employee_record(user, last_name=last, first_name="Szerelő"), headers


async def test_part_barcode_roundtrip_search_and_csv(client, admin, monkeypatch):
    from tests.test_parts import _enable_modules

    _, adm = admin
    await _enable_modules(["parts"])
    res = await client.post("/api/parts", json={"sku": "FUTO-01", "name": "Fűtőbetét 1300W", "barcode": "5999123456789", "min_stock": 2}, headers=adm)
    assert res.status_code == 201, res.text
    pid = res.json()["id"]
    assert res.json()["barcode"] == "5999123456789"
    # vonalkódra keresve megvan (a cikkszám és a gyártói kód mellett)
    rows = (await client.get("/api/parts?q=5999123456", headers=adm)).json()
    assert [r["id"] for r in rows] == [pid]
    # üresre állítás törli
    body = {"sku": "FUTO-01", "name": "Fűtőbetét 1300W", "barcode": "   ", "min_stock": 2}
    res = await client.patch(f"/api/parts/{pid}", json=body, headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()["barcode"] is None
    body["barcode"] = "5999000000001"
    assert (await client.patch(f"/api/parts/{pid}", json=body, headers=adm)).json()["barcode"] == "5999000000001"
    # CSV export oszlop
    csv = (await client.get("/api/parts/export.csv?what=parts", headers=adm))
    assert csv.status_code == 200 and "Vonalkód" in csv.text and "5999000000001" in csv.text
    # import vonalkóddal (új + meglévő frissítése)
    res = await client.post("/api/parts/import", json={"rows": [
        {"sku": "FUTO-01", "name": "Fűtőbetét 1300W", "barcode": "5999000000002"},
        {"sku": "TERMO-01", "name": "Termosztát 90°C", "barcode": "5999000000003"},
    ], "update_existing": True}, headers=adm)
    assert res.status_code == 200, res.text
    by_sku = {r["sku"]: r for r in (await client.get("/api/parts", headers=adm)).json()}
    assert by_sku["FUTO-01"]["barcode"] == "5999000000002" and by_sku["TERMO-01"]["barcode"] == "5999000000003"


async def test_task_rework_flag_and_team_stats(client, manager):
    _, mgr = manager
    a, _ = await _named_emp("csapat-a@example.com", "Alfa")
    b, _ = await _named_emp("csapat-b@example.com", "Béta")
    c, _ = await _named_emp("csapat-c@example.com", "Gamma")
    today = date.today().isoformat()

    first = (await client.post("/api/tasks", json=payload(str(a.id), title="Első kiszállás", helper_employee_ids=[str(b.id)]), headers=mgr)).json()
    assert first["rework"] is False
    # visszatérő javítás az elsőre hivatkozva — ugyanaz a csapat
    back = (await client.post("/api/tasks", json=payload(str(a.id), title="Vissza kellett menni", helper_employee_ids=[str(b.id)],
                                                            rework_of_task_id=first["id"]), headers=mgr)).json()
    assert back["rework"] is True and back["rework_of_task_id"] == first["id"]
    # egyéni munka, jelölés nélkül, majd utólag bejelölve
    solo = (await client.post("/api/tasks", json=payload(str(c.id), title="Egyedül"), headers=mgr)).json()
    res = await client.patch(f"/api/tasks/{solo['id']}", json={"rework": True}, headers=mgr)
    assert res.status_code == 200 and res.json()["rework"] is True
    # hibás hivatkozás
    assert (await client.patch(f"/api/tasks/{solo['id']}", json={"rework_of_task_id": "nem-uuid"}, headers=mgr)).status_code == 422

    stats = (await client.get("/api/dashboard/rework-stats?days=30", headers=mgr)).json()
    assert stats["tasks_total"] == 3 and stats["rework_total"] == 2
    people = {p["name"]: p for p in stats["people"]}
    # a felelős és a segítő is megkapja a munkát és a visszatérést
    assert people[f"{a.last_name} {a.first_name}"]["tasks"] == 2 and people[f"{a.last_name} {a.first_name}"]["rework"] == 1
    assert people[f"{b.last_name} {b.first_name}"]["tasks"] == 2 and people[f"{b.last_name} {b.first_name}"]["rework"] == 1
    assert people[f"{c.last_name} {c.first_name}"] == {**people[f"{c.last_name} {c.first_name}"], "tasks": 1, "rework": 1, "rework_pct": 100}
    # csapat = felelős + segítők; az egyedül végzett munka nem csapat
    assert len(stats["teams"]) == 1
    team = stats["teams"][0]
    assert team["tasks"] == 2 and team["rework"] == 1 and team["rework_pct"] == 50 and len(team["members"]) == 2
    # a lista a legtöbb visszatéréssel kezd
    assert stats["people"][0]["rework"] >= stats["people"][-1]["rework"]
    # időszakon kívüli feladat nem számít
    old = (await client.post("/api/tasks", json=payload(str(c.id), title="Régi", due_date=(date.today() - timedelta(days=60)).isoformat(), rework=True), headers=mgr)).json()
    assert old["rework"] is True
    assert (await client.get("/api/dashboard/rework-stats?days=30", headers=mgr)).json()["tasks_total"] == 3
    assert today  # a mai dátumot használja a payload
