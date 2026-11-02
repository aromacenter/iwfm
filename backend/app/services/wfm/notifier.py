"""Értesítések: napi e-mail összefoglaló + heti automatikus mentés.

A lifespan indít egy háttérhurkot (5 percenként ellenőriz); a beállított óra
után egyszer küld naponta (last_daily_sent védi az ismétlést), hétfőnként
pedig a heti mentést. SMTP nélkül csendben kihagy.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Employee,
    NotificationSettings,
    Partner,
    ProductOrder,
    ServiceTicket,
    Settlement,
    SettlementLine,
)
from app.services.wfm.email_service import load_smtp_config, send_email

logger = logging.getLogger(__name__)

CHECK_SECONDS = 300
NL = chr(10)  # sortörés a digest- és emlékeztető-szövegekhez


async def get_or_create_settings(db: AsyncSession) -> NotificationSettings:
    row = (
        await db.execute(select(NotificationSettings).where(NotificationSettings.id == 1))
    ).scalar_one_or_none()
    if row is None:
        row = NotificationSettings(id=1)
        db.add(row)
        await db.flush()
    return row


def _recipients(row: NotificationSettings) -> list[str]:
    return [e.strip() for e in (row.recipients or "").replace(";", ",").split(",") if e.strip()]


async def _anomalies(db: AsyncSession) -> list[str]:
    """Elmúlt 24 óra elszámolásai, ahol a fogyás a partner korábbi átlagának
    felére (vagy az alá) esett — érdemes ránézni (gép hibás? konkurencia?)."""
    since = datetime.now(UTC) - timedelta(days=1)
    recent = (
        await db.execute(
            select(Settlement, Partner.name)
            .join(Partner, Partner.id == Settlement.partner_id)
            .where(Settlement.created_at >= since)
        )
    ).all()
    out: list[str] = []
    for s, pname in recent:
        consumed_now = (
            await db.execute(
                select(SettlementLine.consumed_qty).where(
                    SettlementLine.settlement_id == s.id
                )
            )
        ).scalars().all()
        now_kg = sum(consumed_now)
        prev = (
            await db.execute(
                select(SettlementLine.consumed_qty)
                .join(Settlement, Settlement.id == SettlementLine.settlement_id)
                .where(
                    Settlement.partner_id == s.partner_id,
                    Settlement.id != s.id,
                    Settlement.created_at >= datetime.now(UTC) - timedelta(days=365),
                )
            )
        ).scalars().all()
        prev_settlement_count = (
            await db.execute(
                select(Settlement.id).where(
                    Settlement.partner_id == s.partner_id, Settlement.id != s.id,
                    Settlement.created_at >= datetime.now(UTC) - timedelta(days=365),
                )
            )
        ).all()
        if len(prev_settlement_count) >= 3:
            avg_kg = sum(prev) / len(prev_settlement_count)
            if avg_kg > 0.2 and now_kg <= avg_kg * 0.5:
                out.append(
                    f"- {pname}: mostani fogyás {now_kg:.1f} kg, korábbi átlag "
                    f"{avg_kg:.1f} kg/elszámolás — érdemes rákérdezni"
                )
    return out[:10]


async def build_daily_digest(db: AsyncSession) -> str:
    """A napi összefoglaló szövege (esedékesek, kifogyók, rendelések,
    kintlévőség, anomáliák)."""
    from app.api.consignment import due_settlements

    due = await due_settlements(days=30, db=db, _=None)  # type: ignore[arg-type]
    runout = [d for d in due if d.days_left is not None and d.days_left <= 7]
    orders = (
        await db.execute(select(ProductOrder).where(ProductOrder.status == "open"))
    ).scalars().all()
    open_tickets = (
        await db.execute(
            select(ServiceTicket).where(ServiceTicket.status.in_(("open", "in_progress")))
        )
    ).scalars().all()
    outstanding = (
        await db.execute(
            select(Settlement).where(Settlement.payment_status == "outstanding")
        )
    ).scalars().all()

    lines = [f"Iwfm napi összefoglaló — {datetime.now(UTC):%Y-%m-%d}", ""]
    if runout:
        lines.append(f"⚠ HAMAROSAN KIFOGYNAK ({len(runout)}):")
        for d in runout[:10]:
            lines.append(
                f"- {d.name}"
                + (f" ({d.city})" if d.city else "")
                + f": ~{d.days_left} nap múlva, vinni ~{d.suggested_kg:g} kg"
                if d.suggested_kg
                else f"- {d.name}: ~{d.days_left} nap múlva"
            )
        lines.append("")
    lines.append(f"Esedékes elszámolás: {len(due)} partner")
    for d in [x for x in due if x not in runout][:10]:
        ago = f"{d.days_since} napja" if d.days_since is not None else "még sosem"
        lines.append(f"- {d.name}" + (f" ({d.city})" if d.city else "") + f" — {ago}")
    lines.append("")
    if orders:
        lines.append(f"Nyitott termékrendelés: {len(orders)} db")
        for o in orders[:8]:
            items = ", ".join(f"{i['name']} ×{i['quantity']}" for i in (o.items or [])[:4])
            lines.append(f"- {o.order_no} · {o.partner_label or '—'}: {items}")
        lines.append("")
    lines.append(f"Nyitott szervizjegy: {len(open_tickets)} db")
    lines.append(f"Kintlévő számla: {len(outstanding)} db "
                 f"({sum(s.total_gross for s in outstanding):,.0f} Ft)".replace(",", " "))
    anomalies = await _anomalies(db)
    if anomalies:
        lines.append("")
        lines.append("❗ ANOMÁLIÁK (tegnapi elszámolások, feleződő fogyás):")
        lines.extend(anomalies)
    return "\n".join(lines)


async def build_weekly_digest(db: AsyncSession) -> str:
    """Heti vezetői összefoglaló (128. kör): bevétel és elszámolás-szám az
    elmúlt 7 napban az előző 7-hez képest, kockázatos partnerek (egészség-
    pontszám), lejárt kintlévőség, nyitott szervizjegyek, a következő 7 nap
    esedékességei — hétfő reggelre a tulajdonosnak."""
    import uuid as _uuid

    from sqlalchemy import func as sa_func

    from app.api.consignment import due_settlements
    from app.services.wfm.partner_health import compute_partner_health

    now = datetime.now(UTC)
    wk = now - timedelta(days=7)
    wk2 = now - timedelta(days=14)
    today = now.date()

    async def _sum(since, until):
        return (
            await db.execute(
                select(sa_func.count(), sa_func.coalesce(sa_func.sum(Settlement.total_gross), 0.0)).where(
                    Settlement.created_at >= since, Settlement.created_at < until
                )
            )
        ).one()

    cnt, gross = await _sum(wk, now + timedelta(days=1))
    pcnt, pgross = await _sum(wk2, wk)

    def pct(a, b):
        return f"{((a - b) / b * 100):+.0f}%" if b else "—"

    def ft(v):
        return f"{float(v):,.0f}".replace(",", " ")

    lines = [f"Iwfm heti összefoglaló — {today:%Y-%m-%d} (elmúlt 7 nap)", ""]
    lines.append(f"💰 Bevétel: {ft(gross)} Ft ({pct(float(gross), float(pgross))} az előző héthez) · {int(cnt)} elszámolás ({int(pcnt)} volt)")
    if hasattr(Partner, "created_at"):
        new_p = (await db.execute(select(sa_func.count()).where(Partner.created_at >= wk))).scalar_one()
        lines.append(f"🤝 Új partner: {int(new_p)}")
    overdue_total = 0.0
    overdue_n = 0
    for s_ in (await db.execute(select(Settlement).where(Settlement.invoiced.is_(True)))).scalars().all():
        paid = s_.paid_amount if s_.paid_amount is not None else s_.total_gross
        rem = float(s_.total_gross) - float(paid)
        if rem > 0.5 and s_.due_date and s_.due_date < today:
            overdue_total += rem
            overdue_n += 1
    lines.append(f"⏰ Lejárt kintlévőség: {overdue_n} tétel, {ft(overdue_total)} Ft")
    open_tickets = (
        await db.execute(select(ServiceTicket).where(ServiceTicket.status.in_(("open", "in_progress"))))
    ).scalars().all()
    stale = 0
    for t in open_tickets:
        c = t.created_at if t.created_at.tzinfo else t.created_at.replace(tzinfo=UTC)
        if (now - c).days > 7:
            stale += 1
    lines.append(f"🔧 Nyitott szervizjegy: {len(open_tickets)}" + (f" (ebből {stale} egy hétnél régebbi)" if stale else ""))
    try:
        health = await compute_partner_health(db, today)
    except Exception:
        health = {}
    risky = sorted((h for h in health.values() if h.level == "risk"), key=lambda h: h.score)
    watch = [h for h in health.values() if h.level == "watch"]
    if health:
        lines.append("")
        lines.append(f"❤️ Partner-egészség: {sum(1 for h in health.values() if h.level == 'good')} jó · {len(watch)} figyelni · {len(risky)} kockázat")
        if risky:
            ids = [_uuid.UUID(h.partner_id) for h in risky[:8]]
            names = {p.id: p.name for p in (await db.execute(select(Partner).where(Partner.id.in_(ids)))).scalars().all()}
            for h in risky[:8]:
                lines.append(f"- 🔴 {names.get(_uuid.UUID(h.partner_id), '?')} ({h.score}/100): " + "; ".join(h.reasons[:2]))
    try:
        due = await due_settlements(days=7, db=db, _=None)  # type: ignore[arg-type]
        soon = [d for d in due if d.days_left is not None and d.days_left <= 7]
        lines.append("")
        lines.append(f"📅 Esedékes elszámolás a héten: {len(due)} partner" + (f", ebből {len(soon)} kifogyás előtt" if soon else ""))
    except Exception:
        pass
    return NL.join(lines)


def _reminder_thresholds(raw: str | None) -> list[int]:
    out: list[int] = []
    for x in (raw or "3,7,14").replace(";", ",").split(","):
        x = x.strip()
        if x.isdigit() and int(x) > 0:
            out.append(int(x))
    return sorted(set(out)) or [3, 7, 14]


async def run_payment_reminders(db: AsyncSession, smtp, today: date | None = None) -> int:
    """Számla-emlékeztető lépcső (128. kör, ötlet #3): lejárt, rendezetlen
    számlákra a beállított napoknál (alap 3 / 7 / 14) e-mail a partner címeire,
    és Telegram a felelős képviselőnek. Tételenként egy lépcső csak egyszer.
    Visszaadja a kiküldött emlékeztetők számát."""
    from app.services.wfm.email_service import send_email as _send, split_addresses
    from app.services.wfm.telegram import send_personal

    row = await get_or_create_settings(db)
    if not row.reminders_enabled:
        return 0
    today = today or datetime.now(UTC).date()
    thresholds = _reminder_thresholds(row.reminder_days)
    sent = 0
    rows = (
        await db.execute(
            select(Settlement, Partner)
            .join(Partner, Partner.id == Settlement.partner_id)
            .where(Settlement.invoiced.is_(True), Settlement.due_date.is_not(None), Settlement.due_date < today)
        )
    ).all()
    for s_, partner in rows:
        paid = s_.paid_amount if s_.paid_amount is not None else s_.total_gross
        remaining = round(float(s_.total_gross) - float(paid))
        if remaining <= 0:
            continue
        days = (today - s_.due_date).days
        stage = sum(1 for t in thresholds if days >= t)
        if stage <= int(s_.reminder_stage or 0):
            continue
        number = s_.billingo_document_number or f"ELSZ-{str(s_.id)[:8].upper()}"
        emails = [e for e in split_addresses(partner.contact_email) + split_addresses(s_.invoice_emails) if "@" in e]
        rem_txt = f"{remaining:,}".replace(",", " ")
        body = NL.join([
            "Tisztelt Partnerünk!", "",
            f"A(z) {number} számú számla fizetési határideje {s_.due_date.isoformat()} volt, {days} napja lejárt. Nyitott összeg: {rem_txt} Ft.",
            "Kérjük, a rendezésről mielőbb gondoskodjon; ha időközben utalták, kérjük, tekintse tárgytalannak.", "",
            "Üdvözlettel,", "X-admin",
        ])
        ok_any = False
        if smtp is not None:
            for to in dict.fromkeys(emails):
                if await _send(smtp, to, f"{stage}. fizetési emlékeztető — {number}", body):
                    ok_any = True
        if partner.agent_user_id:
            emp = (await db.execute(select(Employee).where(Employee.user_id == partner.agent_user_id))).scalar_one_or_none()
            if emp is not None:
                await send_personal(
                    db, emp,
                    f"⏰ {stage}. emlékeztető ment: {partner.name} — {number}, {days} napja lejárt, {rem_txt} Ft nyitott"
                    + ("" if ok_any else " (e-mail cím nélkül, csak neked)"),
                )
        if ok_any or not emails:
            s_.reminder_stage = stage
            s_.reminder_sent_at = datetime.now(UTC)
            sent += 1
    if sent:
        await db.commit()
    return sent


async def run_pending_notifications(db: AsyncSession) -> dict:
    """Egy ellenőrzési kör: ha esedékes, kiküldi a napit és/vagy a heti
    mentést. Visszaadja, mi ment ki (teszthez is)."""
    # Nyitva felejtett jelenlétek automatikus zárása a munkaidő végén —
    # SMTP-beállítástól függetlenül fut minden körben.
    from app.services.wfm.timeclock_auto import auto_clockout

    closed = await auto_clockout(db)
    if closed:
        await db.commit()

    # Telegram: dolgozói összekapcsolások feldolgozása (best-effort).
    from app.services.wfm.telegram import process_updates

    try:
        tg_linked = await process_updates(db)
    except Exception:
        import logging

        logging.getLogger(__name__).warning("telegram link poll failed", exc_info=True)
        tg_linked = 0

    # Fix bérleti díjas automata számlázás (1b2a4153/8e843400) — best effort,
    # havi őr (rental_last_billed) védi a duplázástól.
    from app.services.wfm.rental_billing import run_fixed_rental_billing

    try:
        await run_fixed_rental_billing(db)
    except Exception:
        import logging

        logging.getLogger(__name__).warning("fixed rental billing failed", exc_info=True)

    # Naptár- és Mt.-figyelés (AI, havi őrökkel, best-effort).
    from app.services.wfm.calendar_watch import ensure_next_year_calendar, monthly_mt_check

    try:
        cal_added = await ensure_next_year_calendar(db)
    except Exception:
        import logging

        logging.getLogger(__name__).warning("calendar watch failed", exc_info=True)
        cal_added = False
    try:
        mt_ran = await monthly_mt_check(db)
    except Exception:
        import logging

        logging.getLogger(__name__).warning("mt watch failed", exc_info=True)
        mt_ran = False

    # Ottfelejtett munkalap-timerek zárása a 12 órás plafonnál (119. kör).
    try:
        from app.api.worktime import close_stale_timers

        stale_closed = await close_stale_timers(db)
    except Exception:
        import logging

        logging.getLogger(__name__).warning("stale timer close failed", exc_info=True)
        stale_closed = 0

    row = await get_or_create_settings(db)
    result = {"daily": False, "backup": False, "auto_clockout": closed,
              "telegram_link": tg_linked, "calendar": cal_added, "mt_check": mt_ran,
              "stale_timers": stale_closed, "weekly": False, "reminders": 0}
    recipients = _recipients(row)
    now = datetime.now(UTC)
    today = f"{now:%Y-%m-%d}"
    smtp = await load_smtp_config(db)

    # Számla-emlékeztetők naponta egyszer (e-mail nélkül is: Telegram a képviselőnek)
    if row.reminders_enabled and row.last_reminder_run != today and now.hour >= row.send_hour:
        try:
            result["reminders"] = await run_payment_reminders(db, smtp, now.date())
        except Exception:
            import logging

            logging.getLogger(__name__).warning("payment reminders failed", exc_info=True)
        row.last_reminder_run = today
        await db.commit()

    # Heti vezetői digest: e-mail a címzetteknek + Telegram-csoport (ha be van kötve)
    if row.weekly_digest and now.weekday() == int(row.weekly_day or 0) and now.hour >= row.send_hour and row.last_weekly_sent != today:
        body = await build_weekly_digest(db)
        ok = False
        if smtp is not None:
            for to in recipients:
                ok = await send_email(smtp, to, f"Iwfm heti összefoglaló — {today}", body) or ok
        try:
            from app.services.wfm.telegram import load_telegram_config, send_telegram

            cfg = await load_telegram_config(db)
            if cfg and cfg.get("chat_ids"):
                for chat in cfg["chat_ids"]:
                    ok = await send_telegram(cfg, chat, body) or ok
        except Exception:
            pass
        if ok:
            row.last_weekly_sent = today
            await db.commit()
            result["weekly"] = True

    if not recipients:
        return result
    if smtp is None:
        return result

    if row.daily_enabled and now.hour >= row.send_hour and row.last_daily_sent != today:
        body = await build_daily_digest(db)
        ok = True
        for to in recipients:
            ok = await send_email(smtp, to, f"Iwfm napi összefoglaló — {today}", body) and ok
        if ok:
            row.last_daily_sent = today
            await db.commit()
            result["daily"] = True

    if (
        row.weekly_backup
        and now.weekday() == 0  # hétfő
        and now.hour >= row.send_hour
        and row.last_backup_sent != today
    ):
        from app.api.admin_tools import build_backup_gzip

        content = await build_backup_gzip(db)
        if len(content) <= 15 * 1024 * 1024:
            attachments = [(f"iwfm-mentes-{today}.json.gz", content, "application", "gzip")]
            body = "Heti automatikus adatbázis-mentés csatolva."
        else:
            attachments = None
            body = (
                f"A heti mentés túl nagy e-mailhez ({len(content) / 1024 / 1024:.0f} MB) — "
                "töltsd le a Beállítások → Adatbázis-mentés gombbal."
            )
        ok = True
        for to in recipients:
            ok = await send_email(
                smtp, to, f"Iwfm heti mentés — {today}", body, attachments
            ) and ok
        if ok:
            row.last_backup_sent = today
            await db.commit()
            result["backup"] = True
    return result


async def notification_loop() -> None:
    """Háttérhurok — a lifespan indítja."""
    from app.db import get_session_factory

    while True:
        try:
            async with get_session_factory()() as db:
                await run_pending_notifications(db)
        except Exception:  # noqa: BLE001 — a hurok nem halhat meg
            logger.exception("notification loop iteration failed")
        await asyncio.sleep(CHECK_SECONDS)
