"""Számlázó-diszpécser: a beállított szolgáltatóhoz (Billingó / Számlázz.hu)
irányítja a bizonylat-hívásokat. A hívók innen importálnak — a szolgáltató-
választás egyetlen helyen, a billingo_settings.provider mezőben él."""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Partner, Settlement
from app.services.wfm import billingo_service, szamlazz_service


async def _provider(db: AsyncSession) -> str:
    settings = await billingo_service.get_or_create_settings(db)
    return settings.provider or "billingo"


async def create_invoice_for_settlement(
    db: AsyncSession, settlement: Settlement, partner: Partner
) -> tuple[str, str, date]:
    if await _provider(db) == "szamlazz":
        return await szamlazz_service.create_invoice_for_settlement(db, settlement, partner)
    return await billingo_service.create_invoice_for_settlement(db, settlement, partner)


async def create_maintenance_invoice(db: AsyncSession, partner: Partner, **kw) -> tuple[str, str, date]:
    if await _provider(db) == "szamlazz":
        return await szamlazz_service.create_maintenance_invoice(db, partner, **kw)
    return await billingo_service.create_maintenance_invoice(db, partner, **kw)


async def create_handover_invoice(db: AsyncSession, partner: Partner, **kw) -> tuple[str, str]:
    if await _provider(db) == "szamlazz":
        return await szamlazz_service.create_handover_invoice(db, partner, **kw)
    return await billingo_service.create_handover_invoice(db, partner, **kw)


async def fetch_payment_status(
    db: AsyncSession, document_id: str, company: str | None = None
) -> str | None:
    """Fizetési státusz — a Számlázz.hu Agent ezt nem adja vissza olcsón,
    ott None (a tartozás-követés kézi marad)."""
    if await _provider(db) == "szamlazz":
        return None
    return await billingo_service.fetch_payment_status(db, document_id, company)


async def test_connection(db: AsyncSession, company: str | None = None) -> dict:
    if await _provider(db) == "szamlazz":
        return await szamlazz_service.test_connection(db, company)
    return await billingo_service.test_connection(db, company)
