"""178–180. kör: fogyás-csökkenés felismerése, reggeli brief tartalma és
Telegram-kiküldése, üzemeltetői jelenlét sávja az ügyfél-adminnak,
munkaóra-önköltség a gép-jövedelmezőségben."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.conftest import make_user


def test_drop_from_series_detects_thirty_percent():
    from app.services.wfm.consumption_watch import drop_from_series

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    # 14 naponta elszámolás, 7 kg/ciklus (0,5 kg/nap), majd két ciklus 3,5 kg (0,25 kg/nap) → −50%
    series = [(t0 + timedelta(days=14 * i), 7.0) for i in range(5)] + [(t0 + timedelta(days=14 * 5), 3.5), (t0 + timedelta(days=14 * 6), 3.5)]
    d = drop_from_series(series)
    assert d is not None and d["pct"] == 50 and d["recent"] == 0.25 and d["base"] == 0.5
    # kis ingadozás (−10%) nem riaszt
    series = [(t0 + timedelta(days=14 * i), 7.0) for i in range(5)] + [(t0 + timedelta(days=14 * 5), 6.3), (t0 + timedelta(days=14 * 6), 6.3)]
    assert drop_from_series(series) is None
    # kevés ciklus → nincs mihez mérni
    assert drop_from_series([(t0, 7.0), (t0 + timedelta(days=14), 1.0), (t0 + timedelta(days=28), 1.0)]) is None
    # egyetlen gyenge ciklus után egy normál → nem riaszt (két ciklus kell)
    series = [(t0 + timedelta(days=14 * i), 7.0) for i in range(5)] + [(t0 + timedelta(days=14 * 5), 2.0), (t0 + timedelta(days=14 * 6), 7.0)]
    assert drop_from_series(series) is None


async def test_watch_runs_and_brief_mentions_sections(client, admin, monkeypatch):
    import app.db as app_db
    from app.services.wfm import notifier
    from app.services.wfm.consumption_watch import run_consumption_watch

    _, mgr = admin
    async with app_db.get_session_factory()() as db:
        assert await run_consumption_watch(db, force=True) == 0  # üres rendszer: nincs riasztás, nem hal meg
        body = await notifier.build_daily_digest(db)
    assert "reggeli brief" in body and "Kintlévő számla" in body
    # a brief Telegramra: a kiküldés a beállított óra után, csoport-chatre, e-mail nélkül is
    sent: list = []

    async def fake_send(cfg, chat, text):
        sent.append((chat, text[:40]))
        return True

    async def fake_cfg(db):
        return {"token": "x", "chat_ids": ["-100123"]}

    monkeypatch.setattr("app.services.wfm.telegram.send_telegram", fake_send)
    monkeypatch.setattr("app.services.wfm.telegram.load_telegram_config", fake_cfg)
    res = await client.put("/api/settings/notifications", json={"daily_enabled": False, "daily_telegram": True, "send_hour": 0, "tg_events": []}, headers=mgr)
    assert res.status_code == 200, res.text
    assert (await client.get("/api/settings/notifications", headers=mgr)).json()["daily_telegram"] is True
    async with app_db.get_session_factory()() as db:
        result = await notifier.run_pending_notifications(db)
    assert result["daily"] is True and sent and sent[0][0] == "-100123" and "reggeli brief" in sent[0][1]
    async with app_db.get_session_factory()() as db:
        result = await notifier.run_pending_notifications(db)
    assert result["daily"] is False  # ma már ment


async def test_operator_presence_visible_to_customer_admin(client, manager, admin):
    import app.db as app_db
    from sqlalchemy import select

    from app.models import User

    _, mgr = manager
    _, adm = admin
    op_user, op = await make_user(email="uzemelteto@x-systems.hu", role="admin", display_name="X-systems üzemeltető")
    async with app_db.get_session_factory()() as db:
        u = (await db.execute(select(User).where(User.id == op_user.id))).scalar_one()
        u.is_operator = True
        await db.commit()
    # amíg az üzemeltető nem járt bent: nincs sáv
    me = (await client.get("/api/auth/me", headers=adm)).json()
    assert me["operator_active"] is None
    # az üzemeltető bejön → időbélyeg → az admin és a manager látja
    r = await client.get("/api/auth/me", headers=op)
    assert r.status_code == 200 and r.json()["is_operator"] is True and r.json()["operator_active"] is None
    me = (await client.get("/api/auth/me", headers=adm)).json()
    assert me["operator_active"] and me["operator_active"]["name"] == "X-systems üzemeltető" and me["operator_active"]["minutes_ago"] == 0
    me = (await client.get("/api/auth/me", headers=mgr)).json()
    assert me["operator_active"] is not None
    # 20 perce járt bent → már nem aktív
    async with app_db.get_session_factory()() as db:
        u = (await db.execute(select(User).where(User.id == op_user.id))).scalar_one()
        u.last_seen_at = datetime.now(UTC) - timedelta(minutes=20)
        await db.commit()
    assert (await client.get("/api/auth/me", headers=adm)).json()["operator_active"] is None


async def test_labor_cost_setting_in_profitability(client, admin):
    _, mgr = admin
    cur = (await client.get("/api/settings/worksheet", headers=mgr)).json()
    body = {k: v for k, v in cur.items() if k in ("company_name", "accent_color", "show_materials", "show_hours", "show_client_signature", "show_comments", "photo_policy", "photo_min")}
    body["labor_cost_per_hour"] = 8500
    res = await client.put("/api/settings/worksheet", json=body, headers=mgr)
    assert res.status_code == 200, res.text
    assert (await client.get("/api/settings/worksheet", headers=mgr)).json()["labor_cost_per_hour"] == 8500
    res = await client.get("/api/stats/machine-profitability?months=12", headers=mgr)
    assert res.status_code == 200, res.text
    assert res.json()["totals"]["hour_rate"] == 8500 and "labor_cost" in res.json()["totals"] and "watch" in res.json()["totals"]
