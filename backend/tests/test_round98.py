"""98. kor: statisztika-epoch (30ea42b2 reopened), alvallalkozo ceg
szerelo-nevsora (d8bde9e6), igenyles auto-atvetel a test_round94-ben."""

from __future__ import annotations

from datetime import date


async def _add_expense(user_id, expense_date, amount, entry_type="expense"):
    import app.db as app_db
    from app.models import AgentExpense

    factory = app_db.get_session_factory()
    async with factory() as session:
        session.add(AgentExpense(
            user_id=user_id, expense_date=expense_date,
            amount_gross=amount, entry_type=entry_type, note="teszt",
        ))
        await session.commit()


async def test_business_stats_epoch_and_archive(client, admin, manager):
    """A statisztika is az ujrainditastol (2026-10-12) szamol; a regi vilag
    csak admin archivumban."""
    _, adm = admin
    mgr_user, mgr = manager

    await _add_expense(mgr_user.id, date(2026, 10, 1), 9999.0)  # epoch elotti
    await _add_expense(mgr_user.id, date.today(), 500.0)        # elo

    stats = (await client.get("/api/stats/business", headers=adm)).json()
    assert stats["expenses_total"] == 500

    # archivum: csak admin
    res = await client.get("/api/stats/business?archive=true", headers=mgr)
    assert res.status_code == 403
    arch = (await client.get("/api/stats/business?archive=true", headers=adm)).json()
    assert arch["expenses_total"] == 9999

    # a drill is epoch-tol szamol
    rows = (
        await client.get(
            f"/api/stats/business/drill?kind=expense&name={mgr_user.display_name}",
            headers=adm,
        )
    ).json()
    assert len(rows) == 1 and rows[0]["amount_gross"] == 500


async def test_company_technicians_roundtrip(client, admin):
    """d8bde9e6: a ceg szerelo-nevsora menedzselheto; a sajat nevsor
    lekerheto a munkalap-sugohoz; takaritas (dedup, ures sorok ki)."""
    from tests.conftest import make_employee_record, make_user

    _, adm = admin
    cég_user, cég_hdr = await make_user(email="szervizkft@example.com", role="szervizes")
    emp = await make_employee_record(
        cég_user, is_contractor=True, is_company=True, company_name="GépDoktor Kft.",
    )

    res = await client.patch(
        f"/api/employees/{emp.id}",
        json={"company_technicians": [" Kiss Péter ", "Nagy Gábor", "Kiss Péter", ""]},
        headers=adm,
    )
    assert res.status_code == 200, res.text
    assert res.json()["company_technicians"] == ["Kiss Péter", "Nagy Gábor"]

    # a ceg sajat nevsora a munkalap-sugohoz
    mine = (await client.get("/api/tasks/my-technicians", headers=cég_hdr)).json()
    assert mine == ["Kiss Péter", "Nagy Gábor"]

    # nem ceges user: ures lista
    mine = (await client.get("/api/tasks/my-technicians", headers=adm)).json()
    assert mine == []
