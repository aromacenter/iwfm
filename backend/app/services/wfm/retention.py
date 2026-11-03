"""GDPR adatmegőrzés: lejárt sorok napi takarítása.

A megőrzési időket a Settings adja (WFM_RETENTION_AUDIT_DAYS stb., napban;
0 = kikapcsolva). Egy futás:
  * audit_events: N napnál régebbi sorok törlése — KIVÉVE az elszámoltatási
    célból védett műveletek (employee.anonymize*, admin.*, retention.*);
  * intake_photos + worksheet_photos: N napnál régebbi fotó-sorok törlése
    (a photo_count nem tárolt, lekérdezéskor számolódik);
  * bug_reports: N napnál régebbi jegyek képernyőképének nullázása (a jegy
    maga megmarad).
Minden futásról egy "retention.run" audit-sor készül a számokkal.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete as sa_delete, func, not_, or_, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import record_audit
from app.core.config import get_settings
from app.models import AuditEvent, BugReport, IntakePhoto, WorksheetPhoto

logger = logging.getLogger(__name__)

# Ezek az audit-sorok sosem törlődnek: a destruktív / adminisztratív műveletek
# nyoma a GDPR elszámoltathatóság miatt megmarad.
PROTECTED_AUDIT_PREFIXES: tuple[str, ...] = ("employee.anonymize", "admin.", "retention.")

RETENTION_RUN_ACTION = "retention.run"


def current_settings() -> dict[str, int]:
    """A három megőrzési beállítás (nap; 0 = kikapcsolva)."""
    s = get_settings()
    return {
        "audit_days": max(int(s.retention_audit_days or 0), 0),
        "photo_days": max(int(s.retention_photo_days or 0), 0),
        "screenshot_days": max(int(s.retention_screenshot_days or 0), 0),
    }


def is_enabled() -> bool:
    return any(v > 0 for v in current_settings().values())


def _protected_audit_clause():
    return or_(*(AuditEvent.action.like(f"{p}%") for p in PROTECTED_AUDIT_PREFIXES))


async def _purge_audit(db: AsyncSession, cutoff: datetime) -> int:
    where = and_(AuditEvent.created_at < cutoff, not_(_protected_audit_clause()))
    n = (await db.execute(select(func.count()).select_from(AuditEvent).where(where))).scalar_one()
    if n:
        await db.execute(sa_delete(AuditEvent).where(where))
    return int(n)


async def _purge_photos(db: AsyncSession, model, cutoff: datetime) -> int:
    where = model.created_at < cutoff
    n = (await db.execute(select(func.count()).select_from(model).where(where))).scalar_one()
    if n:
        await db.execute(sa_delete(model).where(where))
    return int(n)


async def _clear_screenshots(db: AsyncSession, cutoff: datetime) -> int:
    where = and_(BugReport.created_at < cutoff, BugReport.screenshot.is_not(None))
    n = (await db.execute(select(func.count()).select_from(BugReport).where(where))).scalar_one()
    if n:
        await db.execute(
            sa_update(BugReport).where(where).values(screenshot=None, screenshot_mime=None)
        )
    return int(n)


async def run_retention(
    session_factory: async_sessionmaker[AsyncSession], *, now: datetime | None = None
) -> dict:
    """Egy takarítási kör. Visszaadja a kategóriánkénti számokat; a
    "retention.run" audit-sor ugyanabban a tranzakcióban készül (actor=None,
    rendszer-művelet). Ha minden beállítás 0, nem töröl semmit és nem
    naplóz."""
    now = now or datetime.now(UTC)
    cfg = current_settings()
    counts = {
        "audit_events": 0,
        "intake_photos": 0,
        "worksheet_photos": 0,
        "bug_screenshots": 0,
    }
    if not any(v > 0 for v in cfg.values()):
        return {"ran": False, "settings": cfg, "counts": counts, "at": now.isoformat()}

    async with session_factory() as db:
        if cfg["audit_days"] > 0:
            counts["audit_events"] = await _purge_audit(db, now - timedelta(days=cfg["audit_days"]))
        if cfg["photo_days"] > 0:
            cutoff = now - timedelta(days=cfg["photo_days"])
            counts["intake_photos"] = await _purge_photos(db, IntakePhoto, cutoff)
            counts["worksheet_photos"] = await _purge_photos(db, WorksheetPhoto, cutoff)
        if cfg["screenshot_days"] > 0:
            counts["bug_screenshots"] = await _clear_screenshots(
                db, now - timedelta(days=cfg["screenshot_days"])
            )
        await record_audit(
            db, actor=None, action=RETENTION_RUN_ACTION, entity_type="retention",
            detail={"counts": counts, "settings": cfg, "at": now.isoformat()},
        )
        await db.commit()

    logger.info("retention run: %s", counts)
    return {"ran": True, "settings": cfg, "counts": counts, "at": now.isoformat()}


async def last_run(db: AsyncSession) -> dict | None:
    """A legutóbbi "retention.run" audit-sor (ha volt)."""
    row = (
        await db.execute(
            select(AuditEvent)
            .where(AuditEvent.action == RETENTION_RUN_ACTION)
            .order_by(AuditEvent.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    detail = row.detail or {}
    return {
        "at": row.created_at.isoformat() if row.created_at else None,
        "counts": detail.get("counts"),
        "settings": detail.get("settings"),
    }
