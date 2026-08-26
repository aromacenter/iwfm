"""Automatizálási motor (Flow-szerű): trigger → feltételek → akciók.

A végpontok a sikeres művelet UTÁN hívják a `fire_event(event, ctx)`-et —
az esemény háttérfeladatban, SAJÁT DB-sessionnel fut, így a fő művelet
sosem lassul vagy bukik el az automatizálás miatt.

ctx: lapos dict (str/szám értékek) — a feltételek erre hivatkoznak, a
sablonok {{kulcs}} formában helyettesítenek belőle.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Trigger-katalógus: esemény-kulcs → elérhető ctx-mezők (a UI súgójához).
TRIGGERS: dict[str, list[str]] = {
    "settlement.created": [
        "partner_nev", "partner_kod", "partner_email", "vegosszeg_brutto",
        "vegosszeg_netto", "fizetes_mod", "elszamolo", "ceg", "afa_nelkul",
    ],
    "settlement.signed": [
        "partner_nev", "partner_kod", "partner_email", "vegosszeg_brutto", "elszamolo",
    ],
    "ticket.created": [
        "cim", "prioritas", "fajta", "gep_vonalkod", "gep_nev", "partner_nev", "bejelento",
    ],
    "ticket.done": [
        "cim", "gep_vonalkod", "gep_nev", "partner_nev", "koltseg_osszesen",
    ],
    "order.created": [
        "rendeles_szam", "partner_nev", "tetel_lista",
    ],
    "partner.created": [
        "partner_nev", "partner_kod", "varos", "ceg",
    ],
    "counter.reported": [
        "gep_vonalkod", "gep_nev", "partner_nev", "szamlalo", "elozo_szamlalo", "keszlet_kg",
    ],
    "stock.low": [
        "partner_nev", "termek_nev", "keszlet_kg", "kuszob_kg",
    ],
    "task.assigned": [
        "cim", "dolgozo", "hatarido", "gep_nev", "gep_vonalkod", "ugyfel", "sorszam",
    ],
    "worksheet.signed": [
        "sorszam", "cim", "dolgozo", "ugyfel", "gep_nev",
    ],
    "worksheet.quote_accepted": [
        "sorszam", "cim", "ugyfel", "opcio", "elfogado",
    ],
    "worksheet.picked_up": [
        "sorszam", "ugyfel", "gep_nev",
    ],
    "worksheet.handed_over": [
        "sorszam", "ugyfel", "osszeg_netto", "fizetes_mod", "kedvezmeny",
    ],
    "intake.created": [
        "sorszam", "ugyfel", "gep_nev", "gep_vonalkod", "hibak", "atvette",
    ],
    "warehouse.transfer_pending": [
        "termek_nev", "mennyiseg", "egyseg", "honnan", "hova", "inditotta",
    ],
    "warehouse.transfer_accepted": [
        "termek_nev", "mennyiseg", "egyseg", "honnan", "hova", "dontott",
    ],
    "warehouse.transfer_rejected": [
        "termek_nev", "mennyiseg", "egyseg", "honnan", "hova", "dontott", "indok",
    ],
}

OPS = ("eq", "ne", "gte", "lte", "contains")
ACTION_TYPES = (
    "send_email", "add_partner_note", "create_task", "send_whatsapp", "send_telegram",
)


def render(template: str, ctx: dict) -> str:
    """{{kulcs}} helyettesítés — ismeretlen kulcs üres string lesz."""
    def _sub(m: re.Match) -> str:
        val = ctx.get(m.group(1).strip())
        return "" if val is None else str(val)

    return re.sub(r"\{\{([^}]+)\}\}", _sub, template or "")


def _check(cond: dict, ctx: dict) -> bool:
    field = str(cond.get("field") or "")
    op = str(cond.get("op") or "eq")
    expected = cond.get("value")
    actual = ctx.get(field)
    if op in ("gte", "lte"):
        try:
            a, b = float(actual), float(expected)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        return a >= b if op == "gte" else a <= b
    a_s = "" if actual is None else str(actual).strip().lower()
    b_s = "" if expected is None else str(expected).strip().lower()
    if op == "eq":
        return a_s == b_s
    if op == "ne":
        return a_s != b_s
    if op == "contains":
        return b_s in a_s
    return False


def conditions_met(conditions: list, ctx: dict) -> bool:
    return all(_check(c, ctx) for c in (conditions or []))


async def _act_send_email(db: AsyncSession, action: dict, ctx: dict) -> None:
    from app.models import EmailTemplate, NotificationSettings
    from app.services.wfm.email_service import load_smtp_config, send_email

    config = await load_smtp_config(db)
    if config is None:
        raise RuntimeError("SMTP nincs beállítva")
    template = None
    if action.get("template_id"):
        import uuid as _uuid

        template = (
            await db.execute(
                select(EmailTemplate).where(EmailTemplate.id == _uuid.UUID(str(action["template_id"])))
            )
        ).scalar_one_or_none()
    if template is None:
        raise RuntimeError("e-mail sablon nem található")

    to_spec = str(action.get("to") or "recipients").strip()
    recipients: list[str] = []
    if to_spec == "partner":
        if ctx.get("partner_email"):
            recipients = [str(ctx["partner_email"])]
    elif to_spec == "agent":
        # A partner felelős képviselője — távollétekor a kijelölt helyettese.
        from app.models import Partner as _Partner
        from app.services.wfm.agents import resolve_agent_user

        pid = ctx.get("_partner_id")
        partner = (
            await db.execute(select(_Partner).where(_Partner.id == pid))
        ).scalar_one_or_none() if pid else None
        agent = await resolve_agent_user(
            db, partner.agent_user_id if partner else None
        )
        if agent is not None:
            recipients = [agent.email]
    elif to_spec == "recipients":
        row = (
            await db.execute(
                select(NotificationSettings).where(NotificationSettings.id == 1)
            )
        ).scalar_one_or_none()
        raw = (row.recipients if row else "") or ""
        recipients = [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]
    else:  # fix címlista vesszővel
        recipients = [x.strip() for x in to_spec.replace(";", ",").split(",") if x.strip()]
    if not recipients:
        raise RuntimeError("nincs címzett")

    subject = render(template.subject, ctx)
    body = render(template.body, ctx)
    for to in recipients:
        await send_email(config, to, subject, body)


async def _act_send_whatsapp(db: AsyncSession, action: dict, ctx: dict) -> None:
    """WhatsApp-üzenet: action.text a {{változós}} szöveg; action.to =
    'recipients' (a beállított WhatsApp-címzettek), 'agent' (a partner felelős
    képviselőjének — távollétekor helyettesének — telefonszáma) vagy konkrét
    számok vesszővel."""
    from app.services.wfm.whatsapp import load_whatsapp_config, normalize_phone, send_whatsapp

    config = await load_whatsapp_config(db)
    if config is None:
        raise RuntimeError("WhatsApp nincs beállítva")
    text = render(str(action.get("text") or ""), ctx).strip()
    if not text:
        raise RuntimeError("üres üzenet")

    to_spec = str(action.get("to") or "recipients").strip()
    numbers: list[str] = []
    if to_spec == "recipients":
        numbers = config["recipients"]
    elif to_spec == "agent":
        from app.models import Employee, Partner as _Partner
        from app.services.wfm.agents import resolve_agent_user

        pid = ctx.get("_partner_id")
        partner = (
            await db.execute(select(_Partner).where(_Partner.id == pid))
        ).scalar_one_or_none() if pid else None
        agent = await resolve_agent_user(db, partner.agent_user_id if partner else None)
        if agent is not None:
            emp = (
                await db.execute(select(Employee).where(Employee.user_id == agent.id))
            ).scalar_one_or_none()
            phone = normalize_phone(emp.phone or "") if emp and emp.phone else None
            if phone:
                numbers = [phone]
    else:
        numbers = [
            p for p in (normalize_phone(x) for x in to_spec.replace(";", ",").split(","))
            if p
        ]
    if not numbers:
        raise RuntimeError("nincs WhatsApp-címzett")
    for num in numbers:
        if not await send_whatsapp(config, num, text):
            raise RuntimeError(f"küldés sikertelen: {num}")


async def _act_send_telegram(db: AsyncSession, action: dict, ctx: dict) -> None:
    """Telegram-üzenet: action.text a {{változós}} szöveg; action.to =
    'recipients' (a beállított chat-ek) vagy konkrét chat_id-k vesszővel."""
    from app.services.wfm.telegram import load_telegram_config, send_telegram

    config = await load_telegram_config(db)
    if config is None:
        raise RuntimeError("Telegram nincs beállítva")
    text = render(str(action.get("text") or ""), ctx).strip()
    if not text:
        raise RuntimeError("üres üzenet")
    to_spec = str(action.get("to") or "recipients").strip()
    chats = (
        config["chat_ids"]
        if to_spec == "recipients"
        else [x.strip() for x in to_spec.replace(";", ",").split(",") if x.strip()]
    )
    if not chats:
        raise RuntimeError("nincs Telegram-címzett")
    for chat in chats:
        if not await send_telegram(config, chat, text):
            raise RuntimeError(f"küldés sikertelen: {chat}")


async def _act_add_partner_note(db: AsyncSession, action: dict, ctx: dict) -> None:
    from app.models import Partner

    pid = ctx.get("_partner_id")
    if not pid:
        raise RuntimeError("az eseményhez nem tartozik partner")
    partner = (
        await db.execute(select(Partner).where(Partner.id == pid))
    ).scalar_one_or_none()
    if partner is None:
        raise RuntimeError("partner nem található")
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    line = f"[{stamp} · automatizálás] {render(str(action.get('text') or ''), ctx)}".strip()
    partner.notes = f"{partner.notes}\n{line}".strip() if partner.notes else line
    await db.commit()


async def _act_create_task(db: AsyncSession, action: dict, ctx: dict) -> None:
    from datetime import date

    from app.api.tasks import _next_worksheet_serial
    from app.models import Employee, Task, Worksheet

    title = render(str(action.get("title") or ""), ctx).strip()
    if not title:
        raise RuntimeError("üres feladat-cím")
    emps = (
        (await db.execute(select(Employee).where(Employee.status == "active")))
        .scalars()
        .all()
    )
    if not emps:
        raise RuntimeError("nincs aktív dolgozó, akire a feladat kiosztható")
    employee = emps[0]
    emp_q = str(action.get("employee") or "").strip().lower()
    if emp_q:
        for e in emps:
            if emp_q in f"{e.last_name} {e.first_name}".lower():
                employee = e
                break
        else:
            raise RuntimeError(f"dolgozó nem található: {action.get('employee')}")
    task = Task(
        title=title[:256],
        description=render(str(action.get("description") or ""), ctx) or None,
        employee_id=employee.id,
        due_date=date.today(),
    )
    db.add(task)
    await db.flush()
    db.add(Worksheet(
        task_id=task.id,
        serial=await _next_worksheet_serial(db),
        work_description="",
    ))
    await db.commit()


_ACTIONS = {
    "send_email": _act_send_email,
    "add_partner_note": _act_add_partner_note,
    "create_task": _act_create_task,
    "send_whatsapp": _act_send_whatsapp,
    "send_telegram": _act_send_telegram,
}


async def run_event(db: AsyncSession, event: str, ctx: dict) -> int:
    """Az eseményhez tartozó engedélyezett szabályok futtatása. Vissza: hány
    szabály futott le. Akció-hiba a szabály last_error-jébe kerül."""
    from app.models import AutomationRule

    rules = (
        (
            await db.execute(
                select(AutomationRule).where(
                    AutomationRule.trigger == event, AutomationRule.enabled.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    fired = 0
    for rule in rules:
        if not conditions_met(rule.conditions or [], ctx):
            continue
        fired += 1
        rule.run_count += 1
        rule.last_run_at = datetime.now(UTC)
        rule.last_error = None
        for action in rule.actions or []:
            handler = _ACTIONS.get(str(action.get("type") or ""))
            if handler is None:
                rule.last_error = f"ismeretlen akció: {action.get('type')}"
                continue
            try:
                await handler(db, action, ctx)
            except Exception as exc:
                logger.warning("Automation action failed (%s / %s): %s", rule.name, action.get("type"), exc)
                rule.last_error = f"{action.get('type')}: {str(exc)[:200]}"
        await db.commit()

    # Beépített Telegram-értesítések: a Beállításokban pipált eseményekről
    # szabály nélkül is megy üzenet (best-effort).
    try:
        from app.services.wfm.telegram import notify_event

        await notify_event(db, event, ctx)
        await db.commit()
    except Exception:
        logger.warning("Built-in telegram notify failed (%s)", event, exc_info=True)
    return fired


def fire_event(event: str, ctx: dict) -> None:
    """Tűz-és-felejt: háttérfeladat saját sessionnel (a hívó nem lassul).
    Tesztekben a WFM_DISABLE_SCHEDULER=1 kikapcsolja — ott a run_event
    közvetlenül hívható."""
    import os

    if os.getenv("WFM_DISABLE_SCHEDULER"):
        return

    async def _job() -> None:
        from app.db import get_session_factory

        try:
            async with get_session_factory()() as db:
                await run_event(db, event, ctx)
        except Exception:
            logger.exception("Automation event failed: %s", event)

    try:
        asyncio.get_running_loop()
        asyncio.create_task(_job())
    except RuntimeError:
        pass  # nincs futó event loop (pl. szinkron kontextus) — kihagyjuk
