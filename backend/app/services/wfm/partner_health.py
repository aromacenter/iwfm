"""Partner-egészség pontszám (127. kör, ötlet #1): egy 0–100 szám partnerenként,
hogy látszódjon, kihez kell kimenni és ki fog lemorzsolódni, mielőtt lemorzsolódik.

Csak elszámolós (kihelyezett géppel rendelkező) aktív partnerre értelmezett.
Összetevők (súly):
- fogyás-trend 30%: az utolsó 90 nap adagszáma az azt megelőző 90 naphoz képest
- fizetési fegyelem 30%: nyitott tartozás és lejárt tételek
- szerviz 15%: szervizjegyek az utolsó 90 napban, régóta nyitott jegy
- látogatás 25%: napok az utolsó elszámolás óta a szerződéses ciklushoz képest
Szint: ≥70 jó (zöld), 45–69 figyelni (sárga), <45 kockázat (piros).
Az okok magyar szövegként mennek a felületre (chip-tooltip, adatlap, térkép-popup)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Asset, Partner, ServiceTicket, Settlement, SettlementLine

WEIGHTS = {"trend": 0.30, "payment": 0.30, "service": 0.15, "visit": 0.25}


@dataclass
class PartnerHealth:
    partner_id: str
    score: int
    level: str                       # good | watch | risk
    reasons: list[str] = field(default_factory=list)
    components: dict = field(default_factory=dict)
    last_settlement_at: date | None = None
    days_since_visit: int | None = None
    trend_ratio: float | None = None
    outstanding: float = 0.0
    overdue_items: int = 0
    tickets_90d: int = 0

    def as_dict(self) -> dict:
        return {
            "partner_id": self.partner_id, "score": self.score, "level": self.level,
            "reasons": self.reasons, "components": self.components,
            "last_settlement_at": self.last_settlement_at.isoformat() if self.last_settlement_at else None,
            "days_since_visit": self.days_since_visit, "trend_ratio": self.trend_ratio,
            "outstanding": round(self.outstanding), "overdue_items": self.overdue_items,
            "tickets_90d": self.tickets_90d,
        }


def level_of(score: int) -> str:
    return "good" if score >= 70 else "watch" if score >= 45 else "risk"


def _clamp(v: float) -> int:
    return int(max(0, min(100, round(v))))


async def compute_partner_health(db: AsyncSession, today: date | None = None) -> dict[uuid.UUID, PartnerHealth]:
    today = today or datetime.now(UTC).date()
    now = datetime.combine(today, datetime.min.time(), tzinfo=UTC)
    since_90 = now - timedelta(days=90)
    since_180 = now - timedelta(days=180)

    placed = {
        pid for (pid,) in (
            await db.execute(
                select(Asset.partner_id).where(
                    Asset.partner_id.is_not(None), Asset.status == "deployed",
                    Asset.tangible.is_(True), Asset.customer_owned.is_(False),
                ).distinct()
            )
        ).all()
    }
    if not placed:
        return {}
    partners = {
        p.id: p for p in (
            await db.execute(select(Partner).where(Partner.id.in_(list(placed)), Partner.is_active.is_(True)))
        ).scalars().all()
    }
    if not partners:
        return {}
    ids = list(partners)

    # adagok 90 / 180 napra (elszámolás-sorokból)
    portions_recent: dict = {}
    portions_prev: dict = {}
    for pid, created, portions in (
        await db.execute(
            select(Settlement.partner_id, Settlement.created_at, func.sum(SettlementLine.portions))
            .join(SettlementLine, SettlementLine.settlement_id == Settlement.id)
            .where(Settlement.partner_id.in_(ids), Settlement.created_at >= since_180)
            .group_by(Settlement.id, Settlement.partner_id, Settlement.created_at)
        )
    ).all():
        created = created if created.tzinfo else created.replace(tzinfo=UTC)
        bucket = portions_recent if created >= since_90 else portions_prev
        bucket[pid] = bucket.get(pid, 0.0) + float(portions or 0)

    # utolsó elszámolás + tartozás
    last_at: dict = {}
    outstanding: dict = {}
    overdue_items: dict = {}
    oldest_overdue_days: dict = {}
    for s in (
        await db.execute(select(Settlement).where(Settlement.partner_id.in_(ids)))
    ).scalars().all():
        c = s.created_at.date() if s.created_at else None
        if c and (s.partner_id not in last_at or c > last_at[s.partner_id]):
            last_at[s.partner_id] = c
        paid = s.paid_amount if s.paid_amount is not None else s.total_gross
        remaining = round(float(s.total_gross) - float(paid), 2)
        if remaining > 0.5:
            outstanding[s.partner_id] = outstanding.get(s.partner_id, 0.0) + remaining
            if s.due_date and s.due_date < today:
                overdue_items[s.partner_id] = overdue_items.get(s.partner_id, 0) + 1
                days = (today - s.due_date).days
                oldest_overdue_days[s.partner_id] = max(oldest_overdue_days.get(s.partner_id, 0), days)

    # szervizjegyek 90 nap + régóta nyitott
    tickets: dict = {}
    stale_open: dict = {}
    for t in (
        await db.execute(
            select(ServiceTicket).where(ServiceTicket.partner_id.in_(ids), ServiceTicket.created_at >= since_90)
        )
    ).scalars().all():
        tickets[t.partner_id] = tickets.get(t.partner_id, 0) + 1
        created = t.created_at if t.created_at.tzinfo else t.created_at.replace(tzinfo=UTC)
        if t.status in ("open", "in_progress") and (now - created).days > 7:
            stale_open[t.partner_id] = stale_open.get(t.partner_id, 0) + 1

    out: dict[uuid.UUID, PartnerHealth] = {}
    for pid, p in partners.items():
        reasons: list[str] = []
        # 1. fogyás-trend
        rec, prev = portions_recent.get(pid, 0.0), portions_prev.get(pid, 0.0)
        ratio = None
        if prev > 0:
            ratio = rec / prev
            trend = _clamp(50 + (ratio - 1) * 100)
            if ratio < 0.7:
                reasons.append(f"Fogyás {round((1 - ratio) * 100)}%-kal esett az előző 90 naphoz képest")
        elif rec > 0:
            trend = 60
        else:
            trend = 50
            if pid in last_at:
                reasons.append("Nincs elszámolt fogyás az utolsó 180 napban")
        # 2. fizetés
        pay = 100
        if outstanding.get(pid, 0) > 0:
            pay -= 15
            n_over = overdue_items.get(pid, 0)
            pay -= min(n_over, 3) * 20
            od = oldest_overdue_days.get(pid, 0)
            if od > 60:
                pay = min(pay, 0)
            elif od > 30:
                pay -= 25
            if n_over:
                reasons.append(f"{n_over} lejárt tétel, {round(outstanding[pid]):,} Ft nyitott".replace(",", " ") + (f", legrégebbi {od} napja" if od else ""))
            else:
                reasons.append(f"{round(outstanding[pid]):,} Ft nyitott (még nem lejárt)".replace(",", " "))
        pay = _clamp(pay)
        # 3. szerviz
        n_t = tickets.get(pid, 0)
        service = {0: 100, 1: 80, 2: 60}.get(n_t, 30)
        if stale_open.get(pid):
            service -= 20
            reasons.append(f"{stale_open[pid]} szervizjegy 7 napnál régebb óta nyitva")
        if n_t >= 3:
            reasons.append(f"{n_t} szervizjegy 90 nap alatt")
        service = _clamp(service)
        # 4. látogatás
        weeks = int(p.contract_settlement_weeks or 4)
        cycle = max(weeks * 7, 7)
        last = last_at.get(pid)
        if last is None:
            visit = 40
            days = None
            reasons.append("Még nem volt elszámolás")
        else:
            days = (today - last).days
            r = days / cycle
            visit = _clamp(100 - max(r - 1, 0) * 100)
            if r >= 1.5:
                reasons.append(f"{days} napja nem volt elszámolás (ciklus: {cycle} nap)")
        score = _clamp(
            trend * WEIGHTS["trend"] + pay * WEIGHTS["payment"] + service * WEIGHTS["service"] + visit * WEIGHTS["visit"]
        )
        out[pid] = PartnerHealth(
            partner_id=str(pid), score=score, level=level_of(score), reasons=reasons,
            components={"trend": trend, "payment": pay, "service": service, "visit": visit},
            last_settlement_at=last, days_since_visit=days,
            trend_ratio=round(ratio, 2) if ratio is not None else None,
            outstanding=outstanding.get(pid, 0.0), overdue_items=overdue_items.get(pid, 0),
            tickets_90d=n_t,
        )
    return out
