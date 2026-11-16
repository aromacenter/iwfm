"""Számla-megjegyzés fizetési útmutatója (157. kör, x-presso ef1cea75): MINDEN
kimenő bizonylat megjegyzés-rovatának ELEJÉRE kerül, hogy a bankszinkron
automatikusan párosítsa az utalást (a közlemény a számla teljes sorszámával
kezdődjön, az összeg pontos legyen). A szöveg a Beállítások → Munkalap/arculat
kártyán átírható; „-” értékkel kikapcsolható."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_PAYMENT_NOTE = (
    "FONTOS — UTALÁSKOR: a közlemény rovat ELEJÉRE írja a számla TELJES sorszámát, "
    "és PONTOSAN a számla végösszegét utalja! Csak így párosítja a rendszer "
    "automatikusan a befizetést, és így nem kap fizetési emlékeztetőt egy már "
    "kiegyenlített számlára."
)


async def payment_note(db: AsyncSession) -> str:
    """A beállított (vagy alapértelmezett) fizetési útmutató; üres, ha kikapcsolták („-”)."""
    from app.models import WorksheetSettings

    row = (await db.execute(select(WorksheetSettings).where(WorksheetSettings.id == 1))).scalar_one_or_none()
    raw = ((row.invoice_payment_note if row is not None else None) or "").strip()
    if raw == "-":
        return ""
    return raw or DEFAULT_PAYMENT_NOTE


def with_note(note: str, comment: str | None) -> str:
    """Az útmutató a megjegyzés elejére, a meglévő szöveg utána."""
    comment = (comment or "").strip()
    if not note:
        return comment
    return note if not comment else f"{note}\n{comment}"
