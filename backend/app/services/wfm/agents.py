"""Felelős képviselő feloldása értesítésekhez.

A partnerhez rendelt képviselő (partner.agent_user_id) kapja a partner
igényeivel kapcsolatos értesítéseket. Ha a képviselő éppen távol van
(jóváhagyott szabadság/betegség fedi a mai napot) és kijelölt helyettest
(user.substitute_user_id), az értesítés automatikusan a helyetteshez fut be.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Employee, TimeOffRequest, User


async def _absent_today(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Igaz, ha a felhasználó dolgozó-rekordjához tartozik olyan jóváhagyott
    távollét, amely a mai napot fedi."""
    emp = (
        await db.execute(select(Employee).where(Employee.user_id == user_id))
    ).scalar_one_or_none()
    if emp is None:
        return False
    today = date.today()
    row = (
        await db.execute(
            select(TimeOffRequest.id).where(
                TimeOffRequest.employee_id == emp.id,
                TimeOffRequest.status == "approved",
                TimeOffRequest.start_date <= today,
                TimeOffRequest.end_date >= today,
            ).limit(1)
        )
    ).scalar_one_or_none()
    return row is not None


async def resolve_agent_user(
    db: AsyncSession, agent_user_id: uuid.UUID | None
) -> User | None:
    """A ténylegesen értesítendő felhasználó: a képviselő, vagy távolléte
    idején az (aktív) helyettese. None, ha nincs kihez irányítani."""
    if agent_user_id is None:
        return None
    user = (
        await db.execute(select(User).where(User.id == agent_user_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    if user.substitute_user_id and await _absent_today(db, user.id):
        sub = (
            await db.execute(select(User).where(User.id == user.substitute_user_id))
        ).scalar_one_or_none()
        if sub is not None and sub.is_active:
            return sub
    return user
