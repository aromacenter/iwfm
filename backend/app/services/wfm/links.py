"""Kattintható linkek a Telegram-üzenetekhez (183. kör, x-presso fce3e291).

Minden eseményhez van egy céloldal a programban; a ``event_link`` a ctx
azonosítóiból (``_settlement_id``, ``_task_id`` …) rakja össze az URL-t.
A beépített Telegram-üzenet végére a `notify_event` teszi oda (🔗), az
automatizálási sablonokban `{{link}}` néven érhető el.
"""

from __future__ import annotations

from urllib.parse import quote

from app.core.config import get_settings


def frontend_url(path: str) -> str:
    return f"{get_settings().frontend_origin.rstrip('/')}{path}"


def _q(v) -> str:
    return quote(str(v), safe="")


def event_link(event: str, ctx: dict) -> str | None:
    """Az eseményhez tartozó oldal linkje, vagy None, ha nincs mihez kötni."""
    g = ctx.get
    if event in ("settlement.created", "settlement.signed", "settlement.paid", "rental.billed"):
        if g("_settlement_id"):
            return frontend_url(f"/elszamolas?settlement={_q(g('_settlement_id'))}")
        if g("_partner_id"):
            return frontend_url(f"/elszamolas?partner={_q(g('_partner_id'))}")
        return frontend_url("/elszamolas")
    if event in ("order.created", "stock.low", "partner.consumption_drop"):
        return frontend_url(f"/elszamolas?partner={_q(g('_partner_id'))}") if g("_partner_id") else frontend_url("/elszamolas")
    if event == "partner.created":
        return frontend_url(f"/partnerek?edit={_q(g('_partner_id'))}") if g("_partner_id") else frontend_url("/partnerek")
    if event in ("ticket.created", "ticket.done", "ticket.sla_warning", "ticket.sla_breached"):
        return frontend_url(f"/szerviz?ticket={_q(g('_ticket_id'))}") if g("_ticket_id") else frontend_url("/szerviz")
    if event in ("counter.reported", "vending.error"):
        code = g("gep_vonalkod") or g("gep_kod")
        return frontend_url(f"/gepek?q={_q(code)}") if code else frontend_url("/gepek")
    if event in ("task.assigned", "task.reschedule_requested", "worksheet.signed", "worksheet.quote_ready",
                 "worksheet.quote_accepted", "worksheet.picked_up", "worksheet.handed_over"):
        if g("link"):
            return str(g("link"))
        return frontend_url(f"/feladatok?task={_q(g('_task_id'))}") if g("_task_id") else frontend_url("/feladatok")
    if event == "intake.created":
        return frontend_url(f"/atvetel?intake={_q(g('_intake_id'))}") if g("_intake_id") else frontend_url("/atvetel")
    if event in ("warehouse.movement", "warehouse.transfer_pending", "warehouse.transfer_accepted",
                 "warehouse.transfer_rejected", "stock.expiring"):
        return frontend_url(f"/raktar?wh={_q(g('_warehouse_id'))}") if g("_warehouse_id") else frontend_url("/raktar")
    if event == "price.indexation_applied":
        return frontend_url("/aremeles")
    if event in ("parts.low", "parts.request"):
        return frontend_url(f"/alkatreszek?q={_q(g('cikkszam'))}") if g("cikkszam") else frontend_url("/alkatreszek")
    return None


def with_link(ctx: dict, event: str) -> dict:
    """A ctx kiegészítve a ``link`` kulccsal (ha még nincs)."""
    if not ctx.get("link"):
        link = event_link(event, ctx)
        if link:
            ctx["link"] = link
    return ctx
