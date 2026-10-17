# -*- coding: utf-8 -*-
"""101. kör adatjavítások (futtatás: IMPORT_DB_URL=<prod> python fix_round101.py):

1) 05f58b58: minden ÉLŐ SZERZŐDÉS NÉLKÜLI, kihelyezett (deployed) gép vissza
   a raktárba (in_stock) — import-maradványok. A visszavett gépek listája
   mozgás-sorban + helyi JSON-ban is megmarad (visszavonható).
2) 05881f0f: a kihelyezett gépek üres default_product_id mezőjének feltöltése
   az utolsó gép-soros elszámolás termékéből (a szerződött kávé így nem
   függ kézi választástól).
"""
import asyncio
import io
import json
import os
import sys
from datetime import UTC, date, datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\palvo\Desktop\appok shopify\Iwfm\backend")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Asset, AssetMovement, AuditEvent, Partner, PartnerContract, SettlementMachine,
)

ROLLBACK_PATH = os.path.join(os.path.dirname(__file__), "fix_round101_visszavett.json")


async def main():
    engine = create_async_engine(os.environ["IMPORT_DB_URL"], pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with Session() as db:
        # ── 2) default_product_id backfill (ELŐBB — a visszavetteknek is jó) ──
        assets = (
            await db.execute(
                select(Asset).where(Asset.default_product_id.is_(None))
            )
        ).scalars().all()
        filled = 0
        for a in assets:
            pid = (
                await db.execute(
                    select(SettlementMachine.product_id)
                    .where(
                        SettlementMachine.asset_id == a.id,
                        SettlementMachine.product_id.is_not(None),
                    )
                    .order_by(SettlementMachine.created_at.desc())
                    .limit(1)
                )
            ).scalars().first()
            if pid is not None:
                a.default_product_id = pid
                filled += 1
        db.add(AuditEvent(
            action="asset.default_product_backfill", entity_type="asset",
            entity_id="bulk", detail={"filled": filled},
        ))
        await db.commit()
        print(f"default_product_id feltöltve: {filled} gépen")

        # ── 1) szerződés nélküli deployed gépek vissza a raktárba ──
        today = date.today()
        contracted = set((await db.execute(
            select(PartnerContract.partner_id).where(
                PartnerContract.valid_from <= today,
                (PartnerContract.valid_to.is_(None)) | (PartnerContract.valid_to >= today),
            )
        )).scalars().all())
        partner_names = {
            p.id: p.name for p in (await db.execute(select(Partner))).scalars()
        }
        rows = (
            await db.execute(
                select(Asset).where(
                    Asset.status == "deployed", Asset.partner_id.is_not(None)
                )
            )
        ).scalars().all()
        orphans = [a for a in rows if a.partner_id not in contracted]
        print(f"deployed: {len(rows)}; élő szerződés nélkül: {len(orphans)} — visszavétel…")
        rollback = []
        for a in orphans:
            rollback.append({
                "asset_id": str(a.id), "barcode": a.barcode, "name": a.name,
                "partner_id": str(a.partner_id),
                "partner": partner_names.get(a.partner_id),
                "deployed_at": a.deployed_at.isoformat() if a.deployed_at else None,
            })
            db.add(AssetMovement(
                asset_id=a.id, action="return", partner_id=a.partner_id,
                detail="Szerződés nélküli import-gép — raktárba (05f58b58)",
            ))
            a.status = "in_stock"
            a.partner_id = None
            a.deployed_at = None
        db.add(AuditEvent(
            action="asset.bulk_return_no_contract", entity_type="asset",
            entity_id="bulk", detail={"count": len(orphans)},
        ))
        await db.commit()
        with open(ROLLBACK_PATH, "w", encoding="utf-8") as f:
            json.dump({"at": datetime.now(UTC).isoformat(), "items": rollback}, f,
                      ensure_ascii=False, indent=1)
        print(f"visszavéve: {len(orphans)} gép — lista: {ROLLBACK_PATH}")
    await engine.dispose()


asyncio.run(main())
