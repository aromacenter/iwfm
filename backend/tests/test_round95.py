"""95. kor: kassza-epoch + archivum (30ea42b2, b5a36526), jelszo-beallito
link (b3640159), fizikai gepszam (0e8c6908), fuggo szamlalo-bejelentesek
(0df8e8ea)."""

from __future__ import annotations

from datetime import date, timedelta


async def _add_expense(user_id, expense_date, amount, entry_type="deposit"):
    import app.db as app_db
    from app.models import AgentExpense

    factory = app_db.get_session_factory()
    async with factory() as session:
        session.add(AgentExpense(
            user_id=user_id, expense_date=expense_date,
            amount_gross=amount, entry_type=entry_type, note="teszt",
        ))
        await session.commit()


async def test_cash_epoch_and_archive(client, admin, manager):
    """A kassza 2026-10-12-en ujraindult: az egyenleg csak az azota rogzitett
    tetelekbol szamol, a regiek az admin-archivumban; a gyors-szuro a
    reszosszegeket szukiti, az egyenleget NEM irja at."""
    _, adm = admin
    mgr_user, mgr = manager

    await _add_expense(mgr_user.id, date(2026, 10, 1), 1000.0)  # epoch elotti
    await _add_expense(mgr_user.id, date.today(), 500.0)        # elo kassza

    reg = (await client.get("/api/stats/cash/me", headers=mgr)).json()
    assert reg["deposits"] == 500
    assert reg["balance"] == 500

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    reg = (
        await client.get(f"/api/stats/cash/me?date_from={tomorrow}", headers=mgr)
    ).json()
    assert reg["deposits"] == 0
    assert reg["balance"] == 500  # gongyolt egyenleg szurotol fuggetlenul

    # archivum: csak admin lathatja, es a regi vilagot mutatja
    res = await client.get("/api/stats/cash/me?archive=true", headers=mgr)
    assert res.status_code == 403
    res = await client.get(
        f"/api/stats/cash/overview?archive=true&user_id={mgr_user.id}", headers=adm
    )
    assert res.status_code == 200, res.text
    arch = res.json()["registers"][0]
    assert arch["deposits"] == 1000
    assert arch["balance"] == 1000
    assert any(e["amount_gross"] == 1000 for e in arch["entries"])


async def test_password_reset_link_flow(client, admin, manager):
    """b3640159: az admin linket general, a dolgozo MAGA ad uj jelszot; a
    link egyszer hasznalatos, nem admin nem generalhat."""
    _, adm = admin
    mgr_user, mgr = manager

    res = await client.post(f"/api/auth/users/{mgr_user.id}/reset-link", headers=mgr)
    assert res.status_code == 403
    res = await client.post(f"/api/auth/users/{mgr_user.id}/reset-link", headers=adm)
    assert res.status_code == 200, res.text
    out = res.json()
    assert "/jelszo?token=" in out["url"]
    token = out["url"].split("token=")[1]

    res = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "Uj-Jelszo-9876"},
    )
    assert res.status_code == 200, res.text
    # az uj jelszoval be lehet lepni
    res = await client.post(
        "/api/auth/login",
        json={"email": mgr_user.email, "password": "Uj-Jelszo-9876"},
    )
    assert res.status_code == 200, res.text
    # a link masodszor mar nem el (token_version leptetve)
    res = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "Megegy-Jelszo-123"},
    )
    assert res.status_code == 422


async def test_physical_machine_count(client, manager):
    """0e8c6908: a szamlalonkent kulon felvett (-A/-B utotagos) sorok EGY
    fizikai gepnek szamitanak a partner-listan es a terkepen."""
    from tests.test_inventory import asset_payload, make_partner

    _, mgr = manager
    partner = await make_partner(client, mgr, name="Gépszám Kft.")
    await client.post(
        f"/api/partners/{partner['id']}/contracts",
        json={"valid_from": "2020-01-01", "min_portions": 100, "below_min_price": 40},
        headers=mgr,
    )
    for bc in ("55500011122", "55500011122-A", "55500011122-B"):
        asset = (
            await client.post(
                "/api/assets",
                json=asset_payload(barcode=bc, serial_number=bc, tangible=True),
                headers=mgr,
            )
        ).json()
        res = await client.post(
            f"/api/assets/{asset['id']}/deploy",
            json={"partner_id": partner["id"]},
            headers=mgr,
        )
        assert res.status_code == 200, res.text

    rows = (await client.get("/api/partners", headers=mgr)).json()
    row = next(r for r in rows if r["id"] == partner["id"])
    assert row["placed_machine_count"] == 1
    assert row["counter_count"] == 3

    m = (await client.get("/api/stats/map", headers=mgr)).json()
    mrow = next(r for r in m if r["id"] == partner["id"])
    assert mrow["machines"] == 1
    assert mrow["counters"] == 3


async def test_pending_counter_reports(client, manager):
    """0df8e8ea: a QR-s bejelentes megjelenik a feldolgozatlan listaban —
    amig ott van, elszamolas NEM keszult belole."""
    from tests.test_support import _asset_with_token

    _, mgr = manager
    from tests.test_inventory import make_partner

    partner = await make_partner(client, mgr, name="Bejelentős Kft.")
    asset, token = await _asset_with_token(client, mgr)
    res = await client.post(
        f"/api/assets/{asset['id']}/deploy",
        json={"partner_id": partner["id"]},
        headers=mgr,
    )
    assert res.status_code == 200, res.text

    res = await client.post(
        f"/api/support/{token}/counter",
        json={"counter": 4321, "reporter_name": "Ügyfél Ubul"},
    )
    assert res.status_code == 200, res.text

    rows = (await client.get("/api/settlements/pending-reports", headers=mgr)).json()
    mine = [r for r in rows if r["partner_id"] == partner["id"]]
    assert len(mine) == 1
    assert mine[0]["total"] == 4321
    assert mine[0]["reporter_name"] == "Ügyfél Ubul"
    assert mine[0]["partner_name"] == "Bejelentős Kft."
