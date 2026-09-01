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


async def test_total_loss_quote_two_options(client, manager, admin):
    """Gazdasagi totalkar: a publikus ajanlaton csak ket opcio — bevizsgalasi
    dij VAGY tulajdonjog-lemondas (dijmentes)."""
    from tests.conftest import make_employee_record, make_user

    _, mgr = manager
    emp_user, emp_hdr = await make_user(email="totalos@example.com", role="szervizes")
    emp = await make_employee_record(emp_user)
    task = (
        await client.post(
            "/api/tasks",
            json={"title": "Totalkaros gep", "employee_id": str(emp.id),
                  "due_date": "2026-09-05", "external_service": True},
            headers=mgr,
        )
    ).json()
    # a szervizes megjeloli: totalkar
    ws = await client.put(
        f"/api/me/tasks/{task['id']}/worksheet",
        json={"work_description": "Bevizsgalva: gazdasagi totalkar.",
              "total_loss": True},
        headers=emp_hdr,
    )
    assert ws.status_code == 200, ws.text
    assert ws.json()["total_loss"] is True

    # ajanlat kikuldese (token-generalas) — a kikuldo vegpont? kozvetlen token:
    import app.db as app_db
    from app.models import Worksheet
    import uuid as _uuid

    factory = app_db.get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                __import__("sqlalchemy").select(Worksheet).where(
                    Worksheet.task_id == _uuid.UUID(task["id"])
                )
            )
        ).scalar_one()
        row.quote_token = "totalkar-token-1"
        row.quote_status = "sent"
        await session.commit()

    info = (await client.get("/api/public/worksheet-quote/totalkar-token-1")).json()
    assert info["total_loss"] is True

    # lemondas: dijmentes, a jegy lezarul, 0 Ft-os sor kerul a munkalapra
    res = await client.post(
        "/api/public/worksheet-quote/totalkar-token-1/accept",
        json={"renounce": True, "accepted_by": "Kovacs Anna"},
    )
    assert res.status_code == 200, res.text
    info2 = (await client.get("/api/public/worksheet-quote/totalkar-token-1")).json()
    assert info2["status"] == "declined"
    assert "tulajdonjog" in (info2["selected_name"] or "")


async def test_renounce_requires_total_loss(client, manager):
    """Tulajdonjog-lemondas csak totalkaros gepnel valaszthato."""
    from tests.conftest import make_employee_record, make_user

    _, mgr = manager
    emp_user, emp_hdr = await make_user(email="nemtotal@example.com", role="szervizes")
    emp = await make_employee_record(emp_user)
    task = (
        await client.post(
            "/api/tasks",
            json={"title": "Sima javitas", "employee_id": str(emp.id),
                  "due_date": "2026-09-05", "external_service": True},
            headers=mgr,
        )
    ).json()
    await client.put(
        f"/api/me/tasks/{task['id']}/worksheet",
        json={"work_description": "Javithato."},
        headers=emp_hdr,
    )
    import app.db as app_db
    from app.models import Worksheet
    import uuid as _uuid

    factory = app_db.get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                __import__("sqlalchemy").select(Worksheet).where(
                    Worksheet.task_id == _uuid.UUID(task["id"])
                )
            )
        ).scalar_one()
        row.quote_token = "nem-total-token-1234"
        row.quote_status = "sent"
        await session.commit()

    res = await client.post(
        "/api/public/worksheet-quote/nem-total-token-1234/accept",
        json={"renounce": True, "accepted_by": "Kovacs Anna"},
    )
    assert res.status_code == 422
