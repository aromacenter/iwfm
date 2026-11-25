"""175. kör: automatikus fizetés-szinkron a Billingóból — a kintlévő számla
fizetettre áll a számlázó szerint, napló, esemény; óránkénti őr; hiba-fék."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from tests.test_round102 import _mk_rental_partner, no_invoice  # noqa: F401
from tests.test_round171_storno_merge import _contract

# ruff: noqa: F811 — a no_invoice fixture paraméterként is szerepel


@pytest.fixture()
def paid_in_billingo(monkeypatch):
    """A számlázó fizetési lekérdezésének mockja: doc_id → válasz."""
    answers: dict[str, dict | None | Exception] = {}
    calls: list[str] = []

    async def _fake(db, document_id, company=None):
        calls.append(document_id)
        a = answers.get(document_id, {"status": "unpaid", "paid_date": None, "cancelled": False})
        if isinstance(a, Exception):
            raise a
        return a

    from app.services.wfm import invoicing

    monkeypatch.setattr(invoicing, "fetch_payment_info", _fake)
    return answers, calls


async def _invoiced_settlement(client, mgr, name, tax):
    import app.db as app_db
    from app.services.wfm.rental_billing import run_fixed_rental_billing

    p = await _mk_rental_partner(client, mgr, name, tax=tax, machines=1, fee=12000)
    await _contract(client, mgr, p["id"], 20000, min(date.today().day, 28), valid_from="2020-01-01")
    async with app_db.get_session_factory()() as db:
        assert await run_fixed_rental_billing(db) == 1
    rows = (await client.get(f"/api/settlements?partner_id={p['id']}", headers=mgr)).json()
    rows = rows if isinstance(rows, list) else rows["items"]
    assert len(rows) == 1 and rows[0]["invoiced"] and rows[0]["payment_status"] == "outstanding"
    return p, rows[0]


async def test_sync_marks_paid_with_billingo_date(client, manager, no_invoice, paid_in_billingo):
    import app.db as app_db
    from app.models import AuditEvent, Settlement
    from app.services.wfm import payment_sync

    _, mgr = manager
    answers, calls = paid_in_billingo
    _p1, s1 = await _invoiced_settlement(client, mgr, "Fizető Kft", "33333333-3-33")
    _p2, s2 = await _invoiced_settlement(client, mgr, "Adós Bt", "44444444-4-44")
    answers["INV-TEST-1"] = {"status": "paid", "paid_date": "2026-11-20", "cancelled": False}
    # mindkét számla ugyanazt a mock-azonosítót kapta (INV-TEST-1) → mindkettő fizetve lesz
    async with app_db.get_session_factory()() as db:
        res = await payment_sync.sync_payments(db, force=True)
    assert res["paid"] == 2 and res["checked"] == 2 and res["errors"] == 0
    for s in (s1, s2):
        det = (await client.get(f"/api/settlements/{s['id']}", headers=mgr)).json()
        assert det["payment_status"] == "paid"
        assert det["paid_at"].startswith("2026-11-20")
    async with app_db.get_session_factory()() as db:
        audits = (await db.execute(select(AuditEvent).where(AuditEvent.action == "settlement.payment_synced"))).scalars().all()
        assert len(audits) == 2 and all(a.detail.get("auto") and a.actor_user_id is None for a in audits)
        # második futás: már nincs kintlévő → nem kérdez
        calls.clear()
        res = await payment_sync.sync_payments(db, force=True)
        assert res["checked"] == 0 and calls == []
        # óránkénti őr: force nélkül nem fut újra
        res = await payment_sync.sync_payments(db)
        assert res["skipped"] == "interval"
        rows = (await db.execute(select(Settlement).where(Settlement.payment_status == "outstanding"))).scalars().all()
        assert rows == []


async def test_sync_leaves_unpaid_and_stops_on_errors(client, manager, no_invoice, paid_in_billingo):
    import app.db as app_db
    from app.services.wfm import payment_sync

    _, mgr = manager
    answers, calls = paid_in_billingo
    _p, s = await _invoiced_settlement(client, mgr, "Nem fizető Zrt", "55555555-5-55")
    answers["INV-TEST-1"] = {"status": "partially_paid", "paid_date": None, "cancelled": False}
    async with app_db.get_session_factory()() as db:
        res = await payment_sync.sync_payments(db, force=True)
    assert res["paid"] == 0 and res["checked"] == 1
    det = (await client.get(f"/api/settlements/{s['id']}", headers=mgr)).json()
    assert det["payment_status"] == "outstanding"
    # hálózati hiba: számolt, a kör nem hal meg
    answers["INV-TEST-1"] = RuntimeError("billingo down")
    async with app_db.get_session_factory()() as db:
        res = await payment_sync.sync_payments(db, force=True)
    assert res["errors"] == 1 and res["paid"] == 0
    # nincs beállítva a számlázó → kihagyva, nem hiba
    answers["INV-TEST-1"] = ValueError("billingo_not_configured")
    async with app_db.get_session_factory()() as db:
        res = await payment_sync.sync_payments(db, force=True)
    assert res["skipped"] == "not_configured" and res["errors"] == 0
    # kézi „fizetve” gomb: esemény megy, állapot fizetett (a dátum most)
    res = await client.post(f"/api/settlements/{s['id']}/mark-paid", headers=mgr)
    assert res.status_code == 200 and res.json()["payment_status"] == "paid"
    assert res.json()["paid_at"][:10] == datetime.now(UTC).date().isoformat()
