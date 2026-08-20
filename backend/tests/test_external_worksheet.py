"""Külső szervizes munkalapok: KSZ-sorszám, költség/ár tételek, és a
két PDF-példány (belső költséges vs. ügyfél-áras -1-es)."""

from __future__ import annotations

from datetime import date

from tests.test_tasks import make_emp


async def test_external_worksheet_flow(client, manager):
    _, mgr = manager
    emp_rec, _ = await make_emp(email="kulso-szerviz@example.com")
    emp = {"id": str(emp_rec.id)}
    today = date.today().isoformat()

    # sima feladat → ML-sorszám
    res = await client.post(
        "/api/tasks",
        json={"title": "Sima munka", "employee_id": emp["id"], "due_date": today},
        headers=mgr,
    )
    assert res.status_code in (200, 201), res.text
    normal = res.json()
    assert normal["worksheet_serial"].startswith("ML-")
    assert normal["worksheet_external"] is False

    # külső szervizes feladat → KSZ-sorszám, külön számozás
    res = await client.post(
        "/api/tasks",
        json={"title": "Gép külső szervizben", "employee_id": emp["id"],
              "due_date": today, "external_service": True,
              "client_name": "Javított Bolt Kft."},
        headers=mgr,
    )
    assert res.status_code in (200, 201), res.text
    ext = res.json()
    assert ext["worksheet_serial"].startswith("KSZ-")
    assert ext["worksheet_serial"].endswith("-0001")
    assert ext["worksheet_external"] is True

    # tételek költséggel (belső) + ügyfél-árral
    res = await client.put(
        f"/api/tasks/{ext['id']}/worksheet",
        json={
            "work_description": "Szivattyú-csere a külső szervizben.",
            "materials": [
                {"name": "Szivattyú", "qty": "1", "unit": "db",
                 "cost_net": 8000, "price_net": 15000},
                {"name": "Munkadíj", "qty": "2", "unit": "óra",
                 "cost_net": 5000, "price_net": 9000},
            ],
        },
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    ws = res.json()
    assert ws["external_service"] is True
    assert ws["materials"][0]["cost_net"] == 8000
    assert ws["materials"][0]["price_net"] == 15000

    # belső PDF: KSZ-sorszám a fájlnévben
    res = await client.get(f"/api/tasks/{ext['id']}/worksheet/pdf", headers=mgr)
    assert res.status_code == 200
    assert ext["worksheet_serial"] in res.headers["content-disposition"]

    # ügyfél-példány: -1-es sorszám
    res = await client.get(
        f"/api/tasks/{ext['id']}/worksheet/pdf?variant=customer", headers=mgr
    )
    assert res.status_code == 200
    assert f"{ext['worksheet_serial']}-1" in res.headers["content-disposition"]

    # sima munkalapnál a customer-variant nem változtat semmit
    res = await client.get(
        f"/api/tasks/{normal['id']}/worksheet/pdf?variant=customer", headers=mgr
    )
    assert res.status_code == 404 or (
        res.status_code == 200
        and normal["worksheet_serial"] + "-1" not in res.headers.get("content-disposition", "")
    )

    # a második KSZ-munkalap sorszáma folytatódik, az ML-számozás független
    res = await client.post(
        "/api/tasks",
        json={"title": "Másik külsős", "employee_id": emp["id"],
              "due_date": today, "external_service": True},
        headers=mgr,
    )
    assert res.json()["worksheet_serial"].endswith("-0002")
