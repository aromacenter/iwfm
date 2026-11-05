"""Azonosító-generálás: dolgozói törzsszám (6 jegy) + ügyfél-kód (PT-NNNN) +
beszerzési rendelésszám (PO-NNNN)."""

from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Employee, Partner, User


async def generate_unique_employee_code(session: AsyncSession) -> str:
    """Random 6 jegyű kód (100000–999999), ütközés esetén újrapróbál."""
    for _ in range(50):
        code = str(secrets.randbelow(900000) + 100000)
        exists = (
            await session.execute(select(Employee.id).where(Employee.employee_code == code))
        ).first()
        # 148. kör: a fiók-szintű számokkal (User.user_code) sem ütközhet — a bot
        # ugyanabból a számtérből azonosít dolgozót és karton nélküli fiókot
        taken = (
            await session.execute(select(User.id).where(User.user_code == code))
        ).first()
        if exists is None and taken is None:
            return code
    raise RuntimeError("Nem sikerült egyedi törzsszámot generálni 50 próbából.")


async def ensure_user_code(session: AsyncSession, user: User) -> tuple[str | None, bool]:
    """A fiók saját száma: ha van dolgozói kartonja törzsszámmal, AZ; különben a
    fiók saját user_code-ja (első kéréskor generálódik). Vissza: (szám, most_készült)."""
    emp_code = (
        await session.execute(
            select(Employee.employee_code).where(
                Employee.user_id == user.id, Employee.employee_code.is_not(None)
            )
        )
    ).scalars().first()
    if emp_code:
        return emp_code, False
    if user.user_code:
        return user.user_code, False
    user.user_code = await generate_unique_employee_code(session)
    return user.user_code, True


async def backfill_employee_codes(session: AsyncSession) -> int:
    """Töltsd fel a törzsszám nélküli (migrált) dolgozókat. Visszaadja a darabszámot."""
    rows = (
        (await session.execute(select(Employee).where(Employee.employee_code.is_(None))))
        .scalars()
        .all()
    )
    for emp in rows:
        emp.employee_code = await generate_unique_employee_code(session)
    if rows:
        await session.commit()
    return len(rows)


def _next_partner_number(codes: list[str | None]) -> int:
    nums = []
    for code in codes:
        if code and code.startswith("PT-") and code[3:].isdigit():
            nums.append(int(code[3:]))
    return (max(nums) + 1) if nums else 1


async def generate_partner_code(session: AsyncSession) -> str:
    """Sorfolytonos ügyfél-azonosító: PT-0001, PT-0002, …"""
    codes = list((await session.execute(select(Partner.partner_code))).scalars())
    return f"PT-{_next_partner_number(codes):04d}"


async def generate_po_number(session: AsyncSession) -> str:
    """Sorfolytonos beszerzési rendelésszám: PO-0001, PO-0002, …"""
    from app.models import PurchaseOrder

    nums = []
    for no in (await session.execute(select(PurchaseOrder.order_no))).scalars():
        if no and no.startswith("PO-") and no[3:].isdigit():
            nums.append(int(no[3:]))
    return f"PO-{(max(nums) + 1) if nums else 1:04d}"


async def backfill_partner_codes(session: AsyncSession) -> int:
    """Kód nélküli (korábban felvett) partnerek azonosítóval való feltöltése."""
    rows = (
        (
            await session.execute(
                select(Partner)
                .where(Partner.partner_code.is_(None))
                .order_by(Partner.created_at)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return 0
    codes = list((await session.execute(select(Partner.partner_code))).scalars())
    next_num = _next_partner_number(codes)
    for i, partner in enumerate(rows):
        partner.partner_code = f"PT-{next_num + i:04d}"
    await session.commit()
    return len(rows)
