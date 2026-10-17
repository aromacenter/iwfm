"""102. kor: fix berleti dijas automata szamlazas (1b2a4153/8e843400),
gepcsere utani auto-munkalap (b3149cb7), tárgyi-eszköz munkalap belső
flow (ab1c33d7)."""

from __future__ import annotations

from datetime import date

import pytest

from tests.test_inventory import asset_payload, make_partner


@pytest.fixture()
def no_invoice(monkeypatch):
    """A számla-kiállítás mockja — éles szolgáltatót tesztben nem hívunk."""
    calls: list = []

    async def _fake(db, settlement, partner):
        calls.append((settlement.id, partner.name))
        return "INV-TEST-1", "created", date.today()

    from app.services.wfm import invoicing

    monkeypatch.setattr(invoicing, "create_invoice_for_settlement", _fake)
    return calls


async def _mk_rental_partner(client, mgr, name, tax="11111111-1-11", machines=1, fee=15000):
    partner = await make_partner(client, mgr, name=name)
    res = await client.patch(
        f"/api/partners/{partner['id']}",
        json={"name": name, "tax_number": tax},
        headers=mgr,
    )
    assert res.status_code == 200, res.text
    for i in range(machines):
        asset = (
            await client.post(
                "/api/assets",
                json=asset_payload(
                    barcode=f"{abs(hash(name)) % 10**9}{i:02d}",
                    serial_number=f"SN-{name}-{i}",
                    name="Royal Professional",
                    manufacturer="Saeco",
                    tangible=True,
                    rent_fee=fee,
                ),
                headers=mgr,
            )
        ).json()
        res = await client.post(
            f"/api/assets/{asset['id']}/deploy",
            json={"partner_id": partner["id"]},
            headers=mgr,
        )
        assert res.status_code == 200, res.text
    return partner


async def test_fixed_rental_billing(client, manager, no_invoice):
    """A billing_day napján elszámolás + számla készül gépenkénti tétellel,
    havonta csak egyszer; összevonásnál a társ-cím gépei is rákerülnek."""
    import app.db as app_db
    from app.services.wfm.rental_billing import run_fixed_rental_billing

    _, mgr = manager
    today = date.today()

    main_p = await _mk_rental_partner(client, mgr, "Bérlő Központ", machines=2)
    site_p = await _mk_rental_partner(client, mgr, "Bérlő Telephely", machines=1)

    res = await client.post(
        f"/api/partners/{main_p['id']}/contracts",
        json={
            "valid_from": "2026-01-01", "no_minimum": True,
            "fixed_rental": True, "billing_day": min(today.day, 28),
            "rental_merge_company": True,
            "payment_method": "transfer", "payment_terms_days": 8,
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text

    factory = app_db.get_session_factory()
    async with factory() as db:
        created = await run_fixed_rental_billing(db)
    assert created == 1
    assert len(no_invoice) == 1  # számla azonnal

    rows = (
        await client.get(f"/api/settlements?partner_id={main_p['id']}", headers=mgr)
    ).json()
    s = rows[0] if isinstance(rows, list) else rows["items"][0]
    assert s["total_net"] == 45000  # 2×15000 + társ-cím 1×15000
    assert s["payment_method"] == "transfer"

    # havi őr: még egyszer NEM fut le
    async with factory() as db:
        created = await run_fixed_rental_billing(db)
    assert created == 0
    void = site_p  # a társ-cím gépe az összevont számlán van


async def test_swap_creates_worksheet_task(client, manager):
    """b3149cb7: gépcsere után automatikusan nyílik munkalap-feladat a hibás
    gépre, X-Presso tulajdonosi adatokkal (ab1c33d7)."""
    from tests.conftest import make_employee_record

    mgr_user, mgr = manager
    await make_employee_record(mgr_user, last_name="Csere", first_name="Béla")
    partner = await make_partner(client, mgr, name="Cserés Kft.")
    old = (
        await client.post(
            "/api/assets",
            json=asset_payload(barcode="88800011122", serial_number="SN-OLD",
                               tangible=True, counter=1000),
            headers=mgr,
        )
    ).json()
    await client.post(
        f"/api/assets/{old['id']}/deploy", json={"partner_id": partner["id"]}, headers=mgr,
    )
    new = (
        await client.post(
            "/api/assets",
            json=asset_payload(barcode="88800033344", serial_number="SN-NEW", tangible=True),
            headers=mgr,
        )
    ).json()
    res = await client.post(
        f"/api/assets/{old['id']}/swap",
        json={"replacement_asset_id": new["id"], "old_counter": 1200, "new_counter": 0},
        headers=mgr,
    )
    assert res.status_code == 200, res.text

    tasks = (await client.get("/api/me/tasks", headers=mgr)).json()
    swap_task = next((x for x in tasks if "Gépcsere" in x["title"]), None)
    assert swap_task is not None, tasks
    ws = (
        await client.get(f"/api/me/tasks/{swap_task['id']}/worksheet", headers=mgr)
    ).json()
    assert ws["client_name"] == "X-Presso Coffee Kft."
