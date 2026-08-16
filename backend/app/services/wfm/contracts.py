"""Partner-szerződések: az adott napon érvényes szerződés kiválasztása és a
partner tükör-mezőinek szinkronja.

A partner contract_* mezői a MINDENKOR AKTÍV szerződés tükrei — az elszámolás
bevált számítási kódja változatlanul ezekre épül. A szinkron minden szerződés-
módosításkor és minden elszámolás(-előkészítés)kor lefut, így a jövőbeli
szerződések maguktól élesednek, a lejártak maguktól kikapcsolnak.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Partner, PartnerContract


async def active_contract(
    db: AsyncSession, partner_id, on: date | None = None
) -> PartnerContract | None:
    """A megadott napon (alapból ma) érvényes szerződés — átfedésnél a
    legkésőbb kezdődő nyer."""
    on = on or date.today()
    rows = (
        await db.execute(
            select(PartnerContract)
            .where(
                PartnerContract.partner_id == partner_id,
                PartnerContract.valid_from <= on,
            )
            .order_by(PartnerContract.valid_from.desc())
        )
    ).scalars().all()
    for c in rows:
        if c.valid_to is None or c.valid_to >= on:
            return c
    return None


async def apply_active_contract(db: AsyncSession, partner: Partner) -> None:
    """A partner tükör-mezőinek frissítése az aktív szerződésből (vagy
    nullázása, ha nincs). NEM commitol — a hívó tranzakciójában fut."""
    c = await active_contract(db, partner.id)
    partner.contract_min_portions = c.min_portions if c else None
    partner.contract_below_min_price = c.below_min_price if c else None
    partner.contract_min_kg = c.min_kg if c else None
    partner.contract_below_min_price_kg = c.below_min_price_kg if c else None
    partner.contract_rent_if_below_min = bool(c.rent_if_below_min) if c else False
    # A türelmi időszakot a szerződés-érvényesség váltja ki (nincs aktív
    # szerződés = nincs minimum) — a régi mező kiürül.
    partner.contract_no_min_until = None
