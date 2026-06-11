"""6 jegyű dolgozói törzsszám generálás (blokkoló-terminálhoz)."""

from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Employee


async def generate_unique_employee_code(session: AsyncSession) -> str:
    """Random 6 jegyű kód (100000–999999), ütközés esetén újrapróbál."""
    for _ in range(50):
        code = str(secrets.randbelow(900000) + 100000)
        exists = (
            await session.execute(select(Employee.id).where(Employee.employee_code == code))
        ).first()
        if exists is None:
            return code
    raise RuntimeError("Nem sikerült egyedi törzsszámot generálni 50 próbából.")


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
