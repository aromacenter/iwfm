"""183. kör (x-presso fce3e291): kattintható link minden Telegram-üzenetben —
esemény → céloldal, a beépített üzenet végén 🔗, a sablonban {{link}}."""

from __future__ import annotations


def test_event_links_point_to_the_right_page(monkeypatch):
    from app.core import config as cfg_mod
    from app.services.wfm.links import event_link, with_link

    monkeypatch.setattr(cfg_mod.get_settings(), "frontend_origin", "https://program.example.hu/")
    assert event_link("settlement.created", {"_settlement_id": "abc", "_partner_id": "p1"}) == "https://program.example.hu/elszamolas?settlement=abc"
    assert event_link("settlement.paid", {"_partner_id": "p1"}) == "https://program.example.hu/elszamolas?partner=p1"
    assert event_link("ticket.created", {"_ticket_id": "t1"}) == "https://program.example.hu/szerviz?ticket=t1"
    assert event_link("counter.reported", {"gep_vonalkod": "K 42"}) == "https://program.example.hu/gepek?q=K%2042"
    assert event_link("task.assigned", {"_task_id": "x"}) == "https://program.example.hu/feladatok?task=x"
    assert event_link("worksheet.quote_ready", {"link": "https://x/y"}) == "https://x/y"
    assert event_link("intake.created", {"_intake_id": "i"}) == "https://program.example.hu/atvetel?intake=i"
    assert event_link("stock.expiring", {"_warehouse_id": "w"}) == "https://program.example.hu/raktar?wh=w"
    assert event_link("parts.low", {"cikkszam": "P-17"}) == "https://program.example.hu/alkatreszek?q=P-17"
    assert event_link("partner.created", {"_partner_id": "p"}) == "https://program.example.hu/partnerek?edit=p"
    assert event_link("ismeretlen.esemeny", {}) is None
    ctx = with_link({"_task_id": "t"}, "task.reschedule_requested")
    assert ctx["link"].endswith("/feladatok?task=t")


def test_every_trigger_offers_link():
    from app.services.wfm.automation import TRIGGERS

    assert all("link" in fields for fields in TRIGGERS.values())


async def test_builtin_message_gets_link_and_rules_can_use_it(client, admin, monkeypatch):
    import app.db as app_db
    import app.services.wfm.telegram as tg_mod
    from app.services.wfm.automation import render, run_event

    _, adm = admin
    res = await client.put("/api/settings/notifications", json={
        "daily_enabled": False, "send_hour": 6, "weekly_backup": False, "auto_receipt": False,
        "tg_enabled": True, "tg_token": "123:abc", "tg_chat_ids": "-100555", "tg_events": ["settlement.created"],
    }, headers=adm)
    assert res.status_code == 200, res.text
    sent: list[tuple[str, str]] = []

    async def fake_send(config, chat_id, text):
        sent.append((str(chat_id), text))
        return True

    monkeypatch.setattr(tg_mod, "send_telegram", fake_send)
    ctx = {"_settlement_id": "s-1", "_partner_id": "p-1", "partner_nev": "Pláza Kávézó", "vegosszeg_brutto": 48200, "elszamolo": "Kovács P."}
    async with app_db.get_session_factory()() as db:
        assert await tg_mod.notify_event(db, "settlement.created", ctx) is True
    assert sent and sent[0][0] == "-100555"
    assert "Pláza Kávézó" in sent[0][1] and "\n🔗 " in sent[0][1] and "/elszamolas?settlement=s-1" in sent[0][1]
    # egyedi sablon: {{link}} helyettesítődik
    async with app_db.get_session_factory()() as db:
        ctx2 = {"_ticket_id": "t-9", "cim": "Nem fűt"}
        await run_event(db, "ticket.created", ctx2)
    assert "/szerviz?ticket=t-9" in render("Jegy: {{cim}} — {{link}}", ctx2)
