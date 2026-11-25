"""Fogyás-csökkenés figyelése partnerenként (178. kör).

Minden elszámolás egy „ciklus”: a benne elszámolt fogyás (kg/db) osztva az
előző elszámolás óta eltelt napokkal = napi fogyás. Ha az utolsó két ciklus
átlagos napi fogyása legalább DROP_PCT százalékkal alacsonyabb az azt
megelőző (legfeljebb négy) ciklus átlagánál, az a lemorzsolódás korai jele:
esemény megy (Telegram a képviselőnek személyesen + a csoportnak, ha be van
pipálva; automatizálás), partnerenként legfeljebb 30 naponta egyszer.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Employee, Partner, Settlement, SettlementLine

logger = logging.getLogger(__name__)

DROP_PCT = 30
MIN_CYCLES = 4  # ennyi elszámolás kell, hogy legyen mihez mérni
MIN_BASE_PER_DAY = 0.05  # kg/nap — ez alatt zaj
ALERT_COOLDOWN = timedelta(days=30)
LOOKBACK = timedelta(days=400)


def drop_from_series(series: list[tuple[datetime, float]]) -> dict | None:
    """``series``: (elszámolás ideje, elszámolt mennyiség) időrendben.
    Visszaad {recent, base, pct} ha van legalább DROP_PCT% csökkenés, különben None."""
    pts = sorted(series, key=lambda x: x[0])
    if len(pts) < MIN_CYCLES:
        return None
    rates: list[float] = []
    for prev, cur in zip(pts, pts[1:], strict=False):
        days = max(1.0, (cur[0] - prev[0]).total_seconds() / 86400)
        rates.append(max(0.0, float(cur[1])) / days)
    if len(rates) < MIN_CYCLES - 1:
        return None
    recent = rates[-2:]
    base = rates[-6:-2]
    if not base:
        return None
    recent_avg = sum(recent) / len(recent)
    base_avg = sum(base) / len(base)
    if base_avg < MIN_BASE_PER_DAY:
        return None
    # MINDKÉT utolsó ciklusnak a küszöb alatt kell lennie — egy gyenge ciklus még nem trend
    if any(r > base_avg * (1 - DROP_PCT / 100) for r in recent):
        return None
    pct = round((1 - recent_avg / base_avg) * 100)
    if pct < DROP_PCT:
        return None
    return {"recent": round(recent_avg, 3), "base": round(base_avg, 3), "pct": pct}


async def consumption_drops(db: AsyncSession, now: datetime | None = None) -> list[dict]:
    """Partnerek, ahol most csökkenés látszik (mellékhatás nélkül)."""
    now = now or datetime.now(UTC)
    rows = (
        await db.execute(
            select(Settlement.partner_id, Settlement.created_at, SettlementLine.consumed_qty)
            .join(SettlementLine, SettlementLine.settlement_id == Settlement.id)
            .where(Settlement.created_at >= now - LOOKBACK, Settlement.partner_id.is_not(None))
        )
    ).all()
    per_partner: dict = {}
    for pid, at, qty in rows:
        bucket = per_partner.setdefault(pid, {})
        bucket[at] = bucket.get(at, 0.0) + float(qty or 0)
    out: list[dict] = []
    for pid, by_time in per_partner.items():
        d = drop_from_series(list(by_time.items()))
        if d is None:
            continue
        partner = await db.get(Partner, pid)
        if partner is None or not getattr(partner, "is_active", True):
            continue
        out.append({"partner": partner, **d})
    out.sort(key=lambda x: -x["pct"])
    return out


async def run_consumption_watch(db: AsyncSession, now: datetime | None = None, *, force: bool = False) -> int:
    """Esemény + Telegram a csökkenő partnerekre, 30 napos partnerenkénti őrrel."""
    from app.services.wfm.automation import fire_event
    from app.services.wfm.telegram import notify_event, send_personal

    now = now or datetime.now(UTC)
    sent = 0
    for d in await consumption_drops(db, now):
        p: Partner = d["partner"]
        last = p.consumption_alert_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if not force and last is not None and now - last < ALERT_COOLDOWN:
            continue
        p.consumption_alert_at = now
        agent_name = ""
        emp = None
        if p.agent_user_id:
            emp = (await db.execute(select(Employee).where(Employee.user_id == p.agent_user_id))).scalar_one_or_none()
            if emp is not None:
                agent_name = f"{emp.last_name} {emp.first_name}".strip()
        ctx = {
            "partner_nev": p.name, "partner_kod": p.partner_code or "", "varos": getattr(p, "city", "") or "",
            "csokkenes_pct": d["pct"], "mostani_kg_nap": d["recent"], "korabbi_kg_nap": d["base"], "kepviselo": agent_name,
        }
        try:
            fire_event("partner.consumption_drop", ctx)
        except Exception:  # noqa: BLE001
            logger.warning("consumption_drop event failed", exc_info=True)
        try:
            await notify_event(db, "partner.consumption_drop", ctx)
            if emp is not None:
                await send_personal(
                    db, emp,
                    f"📉 Fogyás-csökkenés: {p.name} — az utolsó két ciklusban {d['pct']}%-kal kevesebb "
                    f"({d['recent']:g} → korábban {d['base']:g} kg/nap). Érdemes ránézni: gép, konkurencia, zárva?",
                )
        except Exception:  # noqa: BLE001
            logger.warning("consumption_drop telegram failed", exc_info=True)
        sent += 1
    if sent:
        await db.commit()
    return sent
