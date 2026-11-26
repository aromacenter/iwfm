"""Fizetés-szinkron a számlázóból (175. kör).

Óránként lekérdezzük a kintlévő (kiszámlázott, rendezetlen) elszámolások
számláinak fizetési státuszát a Billingóból. Ami ott fizetve, az itt is
fizetettre áll (paid_at = a Billingó szerinti dátum), bekerül a naplóba,
feladódik a CashBooknak, és megy a `settlement.paid` esemény (Telegram-csoport,
automatizálás). Számlázz.hu-nál nincs olcsó státusz-lekérdezés — ott marad a
kézi „fizetve” gomb.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent, Partner, Settlement

logger = logging.getLogger(__name__)

SYNC_INTERVAL = timedelta(hours=1)
MAX_PER_RUN = 300
MAX_CONSECUTIVE_ERRORS = 5
_last_run: datetime | None = None


def _paid_datetime(raw: str | None, now: datetime) -> datetime:
    """A Billingó `paid_date` (YYYY-MM-DD) → időbélyeg; hiányzik → most."""
    if raw:
        try:
            d = date.fromisoformat(str(raw)[:10])
            return datetime(d.year, d.month, d.day, 12, 0, tzinfo=UTC)
        except ValueError:
            pass
    return now


def paid_event_ctx(s: Settlement, partner: Partner | None, source: str) -> dict:
    return {
        "_settlement_id": str(s.id),
        "_partner_id": s.partner_id,
        "partner_nev": partner.name if partner else "?",
        "partner_kod": (partner.partner_code if partner else "") or "",
        "szamla_szam": s.billingo_document_number or "",
        "vegosszeg_brutto": round(float(s.total_gross or 0)),
        "fizetes_datum": (s.paid_at or datetime.now(UTC)).date().isoformat(),
        "forras": source,
    }


async def fire_paid_event(db: AsyncSession, s: Settlement, partner: Partner | None, source: str) -> None:
    """Esemény + beépített Telegram-üzenet (best effort)."""
    from app.services.wfm.automation import fire_event
    from app.services.wfm.telegram import notify_event

    ctx = paid_event_ctx(s, partner, source)
    try:
        fire_event("settlement.paid", ctx)
    except Exception:  # noqa: BLE001
        logger.warning("settlement.paid event failed", exc_info=True)
    try:
        await notify_event(db, "settlement.paid", ctx)
    except Exception:  # noqa: BLE001
        logger.warning("settlement.paid telegram failed", exc_info=True)


async def sync_payments(db: AsyncSession, now: datetime | None = None, *, force: bool = False) -> dict:
    """Egy szinkron-kör. ``force`` nélkül óránként legfeljebb egyszer fut.
    Visszaad: {checked, paid, errors, skipped}."""
    global _last_run
    now = now or datetime.now(UTC)
    if not force and _last_run is not None and now - _last_run < SYNC_INTERVAL:
        return {"checked": 0, "paid": 0, "errors": 0, "skipped": "interval"}
    _last_run = now

    from app.services.wfm import invoicing

    rows = (
        await db.execute(
            select(Settlement, Partner)
            .join(Partner, Partner.id == Settlement.partner_id, isouter=True)
            .where(
                Settlement.invoiced.is_(True),
                Settlement.payment_status == "outstanding",
                Settlement.billingo_document_id.is_not(None),
            )
            .order_by(Settlement.due_date.asc().nulls_last(), Settlement.created_at.asc())
            .limit(MAX_PER_RUN)
        )
    ).all()
    result = {"checked": 0, "paid": 0, "errors": 0, "skipped": None}
    if not rows:
        return result
    paid_now: list[tuple[Settlement, Partner | None]] = []
    consecutive = 0
    for s, partner in rows:
        if (s.billingo_status or "") == "storno":
            continue
        try:
            info = await invoicing.fetch_payment_info(db, s.billingo_document_id, s.invoicing_company)
        except ValueError:
            result["skipped"] = "not_configured"
            break
        except Exception:  # noqa: BLE001
            result["errors"] += 1
            consecutive += 1
            logger.warning("payment sync failed for %s", s.billingo_document_id, exc_info=True)
            if consecutive >= MAX_CONSECUTIVE_ERRORS:
                result["skipped"] = "too_many_errors"
                break
            continue
        consecutive = 0
        result["checked"] += 1
        if info is None:
            result["skipped"] = "provider"
            break
        status = str(info.get("status") or "")
        if status != "paid":
            continue
        s.payment_status = "paid"
        s.paid_at = _paid_datetime(info.get("paid_date"), now)
        if s.paid_amount is None:
            s.paid_amount = float(s.total_gross or 0)
        db.add(AuditEvent(
            actor_user_id=None, action="settlement.payment_synced",
            entity_type="settlement", entity_id=str(s.id),
            detail={"number": s.billingo_document_number, "billingo_status": status, "auto": True,
                    "paid_date": s.paid_at.date().isoformat()},
        ))
        paid_now.append((s, partner))
        result["paid"] += 1
    if paid_now:
        await db.commit()
        from app.api.consignment import _cashbook_autopush

        for s, partner in paid_now:
            try:
                await _cashbook_autopush(db, s)
            except Exception:  # noqa: BLE001
                logger.warning("cashbook autopush after sync failed", exc_info=True)
            await fire_paid_event(db, s, partner, "Billingó")
    return result
