"""SLA a szervizjegyeken (185. kör).

Szerződésenként vállalt reakció- és megoldási idő (óra, munkanapokon
számolva: hétvége és ünnepnap nem fogy). Partner-szerződés nélkül a céges
alapértelmezés (Beállítások → munkalap kártya) él; sürgős jegynél a fele.
Az óránkénti kör a lejárat előtt (a vállalt idő 20%-ával, legalább 1 órával)
figyelmeztet, lejáratkor jelez: esemény (Telegram-csoport, automatizálás) +
privát üzenet a felelősnek. Havi riport: hány jegy csúszott.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Partner, ServiceTicket, WorksheetSettings
from app.services.wfm.holidays import is_working_day

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/Budapest")
OPEN_STATUSES = ("open", "in_progress")


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def add_working_hours(start: datetime, hours: float) -> datetime:
    """``start`` + ``hours``, a nem munkanapokat (hétvége, ünnep, áthelyezett
    pihenőnap) átugorva — a nap 0–24 óráig számít."""
    cur = _aware(start).astimezone(TZ)
    remaining = float(hours)
    guard = 0
    while remaining > 1e-9 and guard < 2000:
        guard += 1
        midnight = (cur + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        if not is_working_day(cur.date()):
            cur = midnight
            continue
        step = (midnight - cur).total_seconds() / 3600
        if step >= remaining:
            cur = cur + timedelta(hours=remaining)
            remaining = 0
        else:
            cur = midnight
            remaining -= step
    return cur.astimezone(UTC)


async def sla_hours(db: AsyncSession, partner_id, priority: str) -> tuple[int | None, int | None]:
    """(reakció óra, megoldás óra) — szerződés, különben céges alap; sürgősnél a fele."""
    resp = res = None
    if partner_id is not None:
        p = await db.get(Partner, partner_id)
        if p is not None:
            resp, res = p.contract_sla_response_hours, p.contract_sla_resolve_hours
    if resp is None and res is None:
        ws = (await db.execute(select(WorksheetSettings).where(WorksheetSettings.id == 1))).scalar_one_or_none()
        if ws is not None:
            resp, res = ws.sla_response_hours, ws.sla_resolve_hours
    if priority == "high":
        resp = math.ceil(resp / 2) if resp else resp
        res = math.ceil(res / 2) if res else res
    return resp, res


async def set_due_dates(db: AsyncSession, tk: ServiceTicket) -> None:
    """A jegy határidői a létrehozás idejéből (csak ha van SLA)."""
    resp, res = await sla_hours(db, tk.partner_id, tk.priority)
    start = _aware(tk.created_at) or datetime.now(UTC)
    tk.sla_response_due = add_working_hours(start, resp) if resp else None
    tk.sla_resolve_due = add_working_hours(start, res) if res else None


def _state(due: datetime | None, done_at: datetime | None, now: datetime, warn_h: float) -> str | None:
    if due is None:
        return None
    due = _aware(due)
    if done_at is not None:
        return "met" if _aware(done_at) <= due else "late"
    if now > due:
        return "breached"
    if now >= due - timedelta(hours=warn_h):
        return "warning"
    return "ok"


def warn_window_hours(created_at: datetime | None, due: datetime | None) -> float:
    if created_at is None or due is None:
        return 1.0
    total = (_aware(due) - _aware(created_at)).total_seconds() / 3600
    return max(1.0, total * 0.2)


def sla_state(tk: ServiceTicket, now: datetime | None = None) -> dict:
    """{response, resolve} állapotok: ok | warning | breached | met | late | None."""
    now = now or datetime.now(UTC)
    closed = tk.status in ("done", "cancelled")
    return {
        "response": _state(tk.sla_response_due, tk.first_response_at or (tk.resolved_at if closed else None), now,
                           warn_window_hours(tk.created_at, tk.sla_response_due)),
        "resolve": _state(tk.sla_resolve_due, tk.resolved_at if tk.status == "done" else None, now,
                          warn_window_hours(tk.created_at, tk.sla_resolve_due)) if tk.status != "cancelled" else None,
    }


def _ctx(tk: ServiceTicket, phase: str, due: datetime) -> dict:
    return {
        "_ticket_id": str(tk.id), "_partner_id": tk.partner_id, "jegy": tk.ticket_no, "cim": tk.title,
        "partner_nev": tk.partner_label or "", "gep_nev": tk.asset_label or "", "felelos": tk.assigned_to_name or "",
        "fazis": "reakció" if phase == "response" else "megoldás",
        "hatarido": _aware(due).astimezone(TZ).strftime("%m.%d. %H:%M"), "prioritas": tk.priority,
    }


async def run_sla_watch(db: AsyncSession, now: datetime | None = None) -> int:
    """Óránkénti kör: figyelmeztetés a lejárat előtt, jelzés lejáratkor — jegyenként
    és fázisonként egyszer (sla_flags). Visszaad: kiküldött jelzések száma."""
    from app.services.wfm.automation import fire_event
    from app.services.wfm.telegram import notify_event, send_personal

    now = now or datetime.now(UTC)
    rows = (
        await db.execute(
            select(ServiceTicket).where(
                ServiceTicket.status.in_(OPEN_STATUSES),
                (ServiceTicket.sla_response_due.is_not(None)) | (ServiceTicket.sla_resolve_due.is_not(None)),
            )
        )
    ).scalars().all()
    sent = 0
    for tk in rows:
        flags = dict(tk.sla_flags or {})
        changed = False
        for phase, due, done in (
            ("response", tk.sla_response_due, tk.first_response_at),
            ("resolve", tk.sla_resolve_due, None),
        ):
            if due is None or done is not None:
                continue
            due = _aware(due)
            warn_at = due - timedelta(hours=warn_window_hours(tk.created_at, due))
            event = None
            if now > due and not flags.get(f"{phase}_breached"):
                flags[f"{phase}_breached"] = True
                event = "ticket.sla_breached"
            elif now >= warn_at and not flags.get(f"{phase}_warned") and not flags.get(f"{phase}_breached"):
                flags[f"{phase}_warned"] = True
                event = "ticket.sla_warning"
            if event is None:
                continue
            changed = True
            ctx = _ctx(tk, phase, due)
            try:
                fire_event(event, ctx)
            except Exception:  # noqa: BLE001
                logger.warning("sla event failed", exc_info=True)
            try:
                await notify_event(db, event, ctx)
                if tk.assigned_to_user_id is not None:
                    from app.models import Employee
                    from app.services.wfm.links import frontend_url

                    emp = (await db.execute(select(Employee).where(Employee.user_id == tk.assigned_to_user_id))).scalar_one_or_none()
                    if emp is not None:
                        icon = "⏰" if event == "ticket.sla_warning" else "🚨"
                        what = "hamarosan lejár" if event == "ticket.sla_warning" else "LEJÁRT"
                        await send_personal(
                            db, emp,
                            f"{icon} SLA {what}: {tk.ticket_no} — {tk.title} ({ctx['fazis']}, határidő {ctx['hatarido']})"
                            + "\n🔗 " + frontend_url(f"/szerviz?ticket={tk.id}"),
                        )
            except Exception:  # noqa: BLE001
                logger.warning("sla telegram failed", exc_info=True)
            sent += 1
        if changed:
            tk.sla_flags = flags
    if sent:
        await db.commit()
    return sent


async def sla_report(db: AsyncSession, month: str, now: datetime | None = None) -> dict:
    """Havi SLA-riport a hónapban nyitott jegyekre: partnerenként és összesen —
    jegyek, határidőre válaszolt / megoldott arány, átlagos órák, csúszott jegyek."""
    now = now or datetime.now(UTC)
    y, m = (int(x) for x in month.split("-")[:2])
    start = datetime(y, m, 1, tzinfo=TZ)
    end = datetime(y + (m // 12), (m % 12) + 1, 1, tzinfo=TZ)
    rows = (
        await db.execute(
            select(ServiceTicket).where(ServiceTicket.created_at >= start, ServiceTicket.created_at < end, ServiceTicket.status != "cancelled")
            .order_by(ServiceTicket.created_at)
        )
    ).scalars().all()
    per: dict[str, dict] = {}
    late: list[dict] = []

    def bucket(key: str, label: str) -> dict:
        return per.setdefault(key, {"partner_id": key or None, "partner": label, "tickets": 0, "with_sla": 0,
                                    "response_met": 0, "response_total": 0, "resolve_met": 0, "resolve_total": 0,
                                    "response_hours": [], "resolve_hours": []})

    for tk in rows:
        key = str(tk.partner_id) if tk.partner_id else ""
        b = bucket(key, tk.partner_label or "—")
        tot = bucket("*", "Összesen")
        for x in (b, tot):
            x["tickets"] += 1
        st = sla_state(tk, now)
        if st["response"] is None and st["resolve"] is None:
            continue
        created = _aware(tk.created_at)
        for x in (b, tot):
            x["with_sla"] += 1
        for phase, done_at in (("response", tk.first_response_at or tk.resolved_at), ("resolve", tk.resolved_at if tk.status == "done" else None)):
            s = st[phase]
            if s is None or s in ("ok", "warning"):
                continue  # még futó határidő: nem dőlt el, nem számít az arányba
            for x in (b, tot):
                x[f"{phase}_total"] += 1
                if s == "met":
                    x[f"{phase}_met"] += 1
                if done_at is not None:
                    x[f"{phase}_hours"].append((_aware(done_at) - created).total_seconds() / 3600)
            if s in ("late", "breached") and not any(entry["id"] == str(tk.id) for entry in late):
                late.append({"id": str(tk.id), "ticket_no": tk.ticket_no, "title": tk.title, "partner": tk.partner_label or "",
                             "status": tk.status, "assigned_to_name": tk.assigned_to_name or "",
                             "response": st["response"], "resolve": st["resolve"]})

    def finish(x: dict) -> dict:
        out = {k: v for k, v in x.items() if not k.endswith("_hours")}
        out["avg_response_hours"] = round(sum(x["response_hours"]) / len(x["response_hours"]), 1) if x["response_hours"] else None
        out["avg_resolve_hours"] = round(sum(x["resolve_hours"]) / len(x["resolve_hours"]), 1) if x["resolve_hours"] else None
        out["response_pct"] = round(100 * x["response_met"] / x["response_total"]) if x["response_total"] else None
        out["resolve_pct"] = round(100 * x["resolve_met"] / x["resolve_total"]) if x["resolve_total"] else None
        return out

    total = finish(per.pop("*")) if "*" in per else finish(bucket("*", "Összesen"))
    partners = sorted((finish(v) for v in per.values()), key=lambda r: (-(r["tickets"]), r["partner"]))
    return {"month": month, "total": total, "partners": partners, "late": late}


def month_key(d: date | None = None) -> str:
    d = d or date.today()
    return f"{d.year:04d}-{d.month:02d}"
