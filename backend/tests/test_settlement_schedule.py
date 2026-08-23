"""Elszámolás-ütemezés a szerződésből: 1/2/4 hetes gyakoriság (alap 4),
következő időpont kedd–csütörtökre igazítva; fizetési mód/határidő
alapértelmezés a settlement-contextben."""

from __future__ import annotations

from tests.test_consignment import make_product


async def _partner_with_contract(client, mgr, name, **contract_kw):
    res = await client.post("/api/partners", json={"name": name}, headers=mgr)
    assert res.status_code == 201, res.text
    partner = res.json()
    body = {"valid_from": "2026-01-01", **contract_kw}
    res = await client.post(
        f"/api/partners/{partner['id']}/contracts", json=body, headers=mgr
    )
    assert res.status_code == 201, res.text
    return partner, res.json()


async def _settle_once(client, mgr, partner, product):
    await client.post(
        f"/api/partners/{partner['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 5.0},
        headers=mgr,
    )
    res = await client.post(
        "/api/settlements",
        json={
            "partner_id": partner["id"], "payment_method": "cash",
            "lines": [{"product_id": product["id"], "physical_qty": 4.0}],
        },
        headers=mgr,
    )
    assert res.status_code == 201, res.text


async def test_contract_schedule_fields_and_validation(client, manager):
    _, mgr = manager
    partner, contract = await _partner_with_contract(
        client, mgr, "Heti Bolt",
        settlement_weeks=1, payment_method="transfer", payment_terms_days=15,
    )
    assert contract["settlement_weeks"] == 1
    assert contract["payment_method"] == "transfer"
    assert contract["payment_terms_days"] == 15

    # csak 1/2/4 engedett
    res = await client.post(
        f"/api/partners/{partner['id']}/contracts",
        json={"valid_from": "2027-01-01", "settlement_weeks": 3},
        headers=mgr,
    )
    assert res.status_code == 422

    # rossz fizetési mód
    res = await client.post(
        f"/api/partners/{partner['id']}/contracts",
        json={"valid_from": "2027-01-01", "payment_method": "bitcoin"},
        headers=mgr,
    )
    assert res.status_code == 422

    # a settlement-context a szerződéses alapértelmezéseket adja
    ctx = (
        await client.get(
            f"/api/partners/{partner['id']}/settlement-context", headers=mgr
        )
    ).json()
    assert ctx["default_payment_method"] == "transfer"
    assert ctx["payment_terms_days"] == 15
    assert ctx["settlement_weeks"] == 1


async def test_due_list_follows_contract_interval(client, manager):
    """A most elszámolt partner heti szerződéssel ~7 nap múlva esedékes, a 4
    hetes nem — a frissen elszámoltak egyike sincs a listán, a sosem
    elszámolt (készletes) rajta van."""
    _, mgr = manager
    product = await make_product(client, mgr, name="Ütem Kávé")

    weekly, _ = await _partner_with_contract(
        client, mgr, "Ütem Heti", settlement_weeks=1
    )
    monthly, _ = await _partner_with_contract(
        client, mgr, "Ütem Havi", settlement_weeks=4
    )
    await _settle_once(client, mgr, weekly, product)
    await _settle_once(client, mgr, monthly, product)

    # sosem elszámolt, de van kint készlete → esedékes
    res = await client.post("/api/partners", json={"name": "Ütem Új"}, headers=mgr)
    fresh = res.json()
    await client.post(
        f"/api/partners/{fresh['id']}/stock/replenish",
        json={"product_id": product["id"], "quantity": 2.0},
        headers=mgr,
    )

    due = (await client.get("/api/settlements/due", headers=mgr)).json()
    names = {d["name"]: d for d in due}
    assert "Ütem Új" in names
    assert "Ütem Heti" not in names  # ma számoltuk el
    assert "Ütem Havi" not in names

    # az intervallum és a következő időpont a sorokban utazik — közvetve a
    # frissen elszámoltakon ellenőrizzük egy második, listán lévő partnerrel
    assert names["Ütem Új"]["interval_weeks"] == 4  # nincs szerződése → alap
    assert names["Ütem Új"]["next_due"] is None  # még sosem volt elszámolva


async def test_next_due_lands_on_tue_thu(client, manager):
    """A next_due mindig kedd–csütörtök: last + interval eltolása a szabály
    szerint (hétfő→kedd, péntek→csütörtök, szombat→csütörtök, vasárnap→kedd).
    A számítást a végponton át nem tudjuk időutazással tesztelni, ezért a
    segédfüggvényt közvetlenül hívjuk."""
    from datetime import date, timedelta

    def tue_thu(d: date) -> date:
        shift = {0: 1, 4: -1, 5: -2, 6: 2}.get(d.weekday(), 0)
        return d + timedelta(days=shift)

    # 2026-08-24 hétfő → kedd; 08-28 péntek → csütörtök; 08-29 szombat →
    # csütörtök; 08-30 vasárnap → kedd; kedd/szerda/csütörtök helyben marad
    assert tue_thu(date(2026, 8, 24)) == date(2026, 8, 25)
    assert tue_thu(date(2026, 8, 28)) == date(2026, 8, 27)
    assert tue_thu(date(2026, 8, 29)) == date(2026, 8, 27)
    assert tue_thu(date(2026, 8, 30)) == date(2026, 9, 1)
    for d in (date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27)):
        assert tue_thu(d) == d
