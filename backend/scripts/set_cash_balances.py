# -*- coding: utf-8 -*-
"""Kassza-nyitóegyenlegek kézi beállítása (b5a36526, a user kérésére):
a megadott dolgozók egyenlegét a MAI napon korrekciós tétellel a kért
értékre állítja. Az ÚJ szabályokkal számol (adminnál a leadás végállomás).

Futtatás: IMPORT_DB_URL=<prod url> python set_cash_balances.py
"""
import asyncio
import io
import os
import sys
from datetime import UTC, date, datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\palvo\Desktop\appok shopify\Iwfm\backend")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.stats import _cash_register
from app.models import AgentExpense, AuditEvent, User

TARGETS = {
    "Szakál Tamás": -1_448_309,
    "Iván László": 0,
    "Jäger Mátyás": 11_908,
    "Szabó László": 0,
}
NOTE = "Kassza-nyitóegyenleg beállítása (2026-10-11, kézi korrekció a vezető kérésére)"


async def main():
    url = os.environ["IMPORT_DB_URL"]
    engine = create_async_engine(url, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with Session() as db:
        for name, target in TARGETS.items():
            user = (
                await db.execute(select(User).where(User.display_name == name))
            ).scalars().first()
            if user is None:
                print(f"NINCS ILYEN USER: {name}")
                continue
            reg = await _cash_register(db, user.id, name, None, None, False, user_role=user.role)
            diff = target - reg.balance
            if abs(diff) < 1:
                print(f"OK  {name}: már {reg.balance:.0f} Ft (cél {target})")
                continue
            db.add(AgentExpense(
                user_id=user.id,
                expense_date=date.today(),
                amount_gross=abs(diff),
                entry_type="deposit" if diff > 0 else "withdrawal",
                note=NOTE,
            ))
            db.add(AuditEvent(
                action="cash.balance_set", entity_type="user", entity_id=str(user.id),
                detail={"name": name, "from": reg.balance, "to": target},
            ))
            await db.commit()
            reg2 = await _cash_register(db, user.id, name, None, None, False, user_role=user.role)
            print(f"BEÁLLÍTVA {name}: {reg.balance:,.0f} → {reg2.balance:,.0f} Ft (cél {target:,})".replace(",", " "))
    await engine.dispose()


asyncio.run(main())
