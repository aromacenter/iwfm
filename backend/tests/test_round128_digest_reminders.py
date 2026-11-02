"""128. kör: heti vezetői digest (szöveg + beállítás) és számla-emlékeztető lépcső
(3 / 7 / 14 nap, tételenként egyszer, e-mail a partnernek + Telegram a képviselőnek)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import app.db as app_db
from app.models import Settlement
from tests.conftest import make_employee_record, make_user
from tests.test_inventory import make_partner


async def _overdue_settlement(partner_id: str, due_days_ago: int, gross: float = 12700.0, paid: float = 0.0, number="XP-2026-0001"):
    factory = app_db.get_session_factory()
    async with factory() as session:
        s = Settlement(
            id=uuid.uuid4(), partner_id=uuid.UUID(partner_id), settled_by_name="Teszt", payment_method="transfer",
            total_net=gross / 1.27, total_gross=gross, invoiced=True, payment_status="outstanding", paid_amount=paid,
            billingo_document_id="doc-1", billingo_document_number=number,
            due_date=date.today() - timedelta(days=due_days_ago), created_at=datetime.now(UTC) - timedelta(days=due_days_ago + 8),
        )
        session.add(s)
        await session.commit()
        return str(s.id)


async def test_reminder_stages_email_and_telegram(client, admin, manager, monkeypatch):
    _, adm = admin
    _, mgr = manager
    # képviselő Telegrammal
    agent_user, _h = await make_user(email="kepviselo@example.com", role="uzletkoto")
    await make_employee_record(agent_user, last_name="Kép", first_name="Viselő", telegram_chat_id="555")
    partner = await make_partner(client, mgr, name="Késős Kft.")
    await client.patch(f"/api/partners/{partner['id']}", json={"name": "Késős Kft.", "contact_email": "penzugy@keso.hu; ugyvezeto@keso.hu", "agent_user_id": str(agent_user.id)}, headers=mgr)
    res = await client.put("/api/settings/notifications", json={
        "daily_enabled": False, "send_hour": 0, "weekly_backup": False, "auto_receipt": False, "wa_enabled": False,
        "tg_enabled": True, "tg_token": "123:abc", "tg_chat_ids": "-100555",
        "reminders_enabled": True, "reminder_days": "3,7,14",
    }, headers=adm)
    assert res.status_code == 200, res.text
    got = (await client.get("/api/settings/notifications", headers=adm)).json()
    assert got["reminders_enabled"] is True and got["reminder_days"] == "3,7,14"

    mails: list = []
    tg: list = []

    async def fake_mail(config, to, subject, body, attachments=None):
        mails.append((to, subject, body))
        return True

    async def fake_tg(config, chat_id, text):
        tg.append((chat_id, text))
        return True

    import app.services.wfm.email_service as es
    import app.services.wfm.telegram as tg_mod

    monkeypatch.setattr(es, "send_email", fake_mail)
    monkeypatch.setattr(tg_mod, "send_telegram", fake_tg)

    sid = await _overdue_settlement(partner["id"], due_days_ago=8)  # 8 napja lejárt → 2. lépcső (3 és 7 is elmúlt)
    from app.services.wfm.notifier import run_payment_reminders

    factory = app_db.get_session_factory()
    async with factory() as db:
        assert await run_payment_reminders(db, smtp=object(), today=date.today()) == 1
    assert len(mails) == 2 and {m[0] for m in mails} == {"penzugy@keso.hu", "ugyvezeto@keso.hu"}
    assert mails[0][1].startswith("2. fizetési emlékeztető") and "XP-2026-0001" in mails[0][2] and "12 700 Ft" in mails[0][2]
    assert len(tg) == 1 and tg[0][0] == "555" and "2. emlékeztető" in tg[0][1]
    # ugyanaz a lépcső másodszor nem megy
    async with factory() as db:
        assert await run_payment_reminders(db, smtp=object(), today=date.today()) == 0
    # 14 nap után a 3. lépcső igen
    async with factory() as db:
        assert await run_payment_reminders(db, smtp=object(), today=date.today() + timedelta(days=7)) == 1
    assert mails[-1][1].startswith("3. fizetési emlékeztető")
    row = (await client.get(f"/api/settlements/{sid}", headers=mgr)).json()
    assert row["total_gross"] == 12700
    # rendezett tételre nem megy
    paid_id = await _overdue_settlement(partner["id"], due_days_ago=20, paid=12700.0, number="XP-2026-0002")
    async with factory() as db:
        assert await run_payment_reminders(db, smtp=object(), today=date.today() + timedelta(days=7)) == 0
    assert paid_id


async def test_weekly_digest_text_and_settings(client, admin, manager):
    _, adm = admin
    res = await client.put("/api/settings/notifications", json={
        "daily_enabled": False, "send_hour": 6, "weekly_backup": False, "auto_receipt": False, "wa_enabled": False,
        "weekly_digest": True, "weekly_day": 0,
    }, headers=adm)
    assert res.status_code == 200, res.text
    got = (await client.get("/api/settings/notifications", headers=adm)).json()
    assert got["weekly_digest"] is True and got["weekly_day"] == 0
    from app.services.wfm.notifier import build_weekly_digest

    factory = app_db.get_session_factory()
    async with factory() as db:
        text = await build_weekly_digest(db)
    assert "heti összefoglaló" in text and "Bevétel" in text and "Lejárt kintlévőség" in text and "szervizjegy" in text
    # rossz nap / rossz formátum: 422
    assert (await client.put("/api/settings/notifications", json={"daily_enabled": False, "send_hour": 6, "weekly_backup": False, "auto_receipt": False, "wa_enabled": False, "weekly_day": 9}, headers=adm)).status_code == 422
    assert (await client.put("/api/settings/notifications", json={"daily_enabled": False, "send_hour": 6, "weekly_backup": False, "auto_receipt": False, "wa_enabled": False, "reminder_days": "3,x"}, headers=adm)).status_code == 422
