"""Értesítések: napi e-mail összefoglaló + heti automatikus mentés.

A lifespan indít egy háttérhurkot (5 percenként ellenőriz); a beállított óra
után egyszer küld naponta (last_daily_sent védi az ismétlést), hétfőnként
pedig a heti mentést. SMTP nélkül csendben kihagy.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
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


async def run_pending_notifications(db: AsyncSession) -> dict:
    """Egy ellenőrzési kör: ha esedékes, kiküldi a napit és/vagy a heti
    mentést. Visszaadja, mi ment ki (teszthez is)."""
    row = await get_or_create_settings(db)
    result = {"daily": False, "backup": False}
    recipients = _recipients(row)
    if not recipients:
        return result
    now = datetime.now(UTC)
    today = f"{now:%Y-%m-%d}"

    smtp = await load_smtp_config(db)
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
