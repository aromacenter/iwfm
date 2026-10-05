# -*- coding: utf-8 -*-
"""4. lépcső: TELJES kassza-történet (Kassza_MOZGAS, 2012→) → AgentExpense /
CashTransfer az éles Iwfm DB-be.

Szabályok:
- Régi user → új user KIZÁRÓLAG név-egyezéssel (fiókot nem hozunk létre).
  A nem párosított dolgozók tételei az xpresso_archive sémában maradnak;
  ha a hiányzó dolgozó felkerül a Dolgozók menübe, az ÚJRAFUTTATÁS az ő
  történetét is beimportálja (a jelölők miatt duplázás nélkül).
- Típus-térkép: 1/5/7 → expense; 2/6 → deposit; 3/8 → withdrawal;
  4 (Pénz átadása képviselőnek) → CashTransfer (accepted) ha mindkét fél
  párosított, különben a párosított oldalon deposit/withdrawal.
- 10 (Utalás) és 11 (GLS) NEM készpénz → kihagyva (archívumban megvan).
- fokassza=1 (központi kassza sorai) → kihagyva, az archívumból riportolható.
- Idempotens: minden tétel note-jában "[Xp#<kassza_mozgas_id>]" jelölő.

Cél: IMPORT_DB_URL. Forrás: xpresso_staging_full.db.
"""
from __future__ import annotations

import asyncio
import io
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import UTC, datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\palvo\Desktop\appok shopify\Iwfm\backend")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.models import AgentExpense, AuditEvent, CashTransfer, User  # noqa: E402

STAGING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xpresso_staging_full.db")
BATCH = 800

TYPE_MAP = {
    1: ("expense", "Képviselő kiadás"),
    2: ("deposit", "Képviselő bevétel"),
    3: ("withdrawal", "Pénz elvétele képviselőtől"),
    5: ("expense", "Kiadás"),
    6: ("deposit", "Bevétel"),
    7: ("expense", "Képviselői vásárlás"),
    8: ("withdrawal", "Elszámolás képviselővel"),
}
SKIP_TYPES = {10: "Utalás (nem készpénz)", 11: "GLS (nem készpénz)"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def dt(s):
    if not s or str(s).startswith("0000"):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s)[:19], fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


async def main() -> None:
    url = os.environ["IMPORT_DB_URL"]
    engine = create_async_engine(url, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    st = sqlite3.connect(STAGING)
    st.row_factory = sqlite3.Row

    old_users = {r["user_id"]: r["nev"] or r["username"]
                 for r in st.execute("SELECT user_id, nev, username FROM User_LISTA")}
    beszallitok = {r["besz_id"]: (r["besz_nev"] or "").strip()[:256]
                   for r in st.execute("SELECT besz_id, besz_nev FROM Beszallitok")}

    async with Session() as db:
        by_norm: dict[str, User] = {}
        for u in (await db.execute(select(User))).scalars():
            by_norm.setdefault(norm(u.display_name), u)
        user_map = {}  # old id → User
        for oid, nev in old_users.items():
            u = by_norm.get(norm(nev or ""))
            if u:
                user_map[oid] = u

        # már importált jelölők
        done: set[int] = set()
        for (note,) in (
            await db.execute(select(AgentExpense.note).where(AgentExpense.note.like("[Xp#%")))
        ).all():
            m = re.match(r"\[Xp#(\d+)\]", note or "")
            if m:
                done.add(int(m.group(1)))
        for (note,) in (
            await db.execute(select(CashTransfer.note).where(CashTransfer.note.like("[Xp#%")))
        ).all():
            m = re.match(r"\[Xp#(\d+)\]", note or "")
            if m:
                done.add(int(m.group(1)))
        print(f"Párosított dolgozók: {len(user_map)}; már importált tétel: {len(done)}")

        stats = {"expense": 0, "deposit": 0, "withdrawal": 0, "transfer": 0,
                 "skip_type": 0, "skip_fokassza": 0, "skip_nouser": 0, "skip_done": 0}
        per_user: dict[str, float] = {}
        added = 0
        for r in st.execute("SELECT * FROM Kassza_MOZGAS ORDER BY kassza_mozgas_id"):
            mid = r["kassza_mozgas_id"]
            if mid in done:
                stats["skip_done"] += 1
                continue
            t = r["kassza_mozgas_tipus_id"]
            when = dt(r["datum"])
            amount = float(r["osszeg"] or 0)
            if not when or amount <= 0:
                continue
            if r["fokassza"]:
                stats["skip_fokassza"] += 1
                continue
            if t in SKIP_TYPES:
                stats["skip_type"] += 1
                continue
            megn = (r["megnevezes"] or "").strip()
            ceg = " — Premium Caffe" if r["ceg_id"] == 1 else ""
            src = user_map.get(r["userid"])
            dst = user_map.get(r["cel_user_id"]) if r["cel_user_id"] else None

            if t == 4:
                # pénz átadása képviselőnek
                if src and dst and src.id != dst.id:
                    db.add(CashTransfer(
                        from_user_id=src.id, to_user_id=dst.id, amount=amount,
                        note=f"[Xp#{mid}] {megn or 'Pénz átadása (Xpresso)'}{ceg}"[:512],
                        status="accepted", created_at=when, decided_at=when,
                    ))
                    stats["transfer"] += 1
                    per_user[src.display_name] = per_user.get(src.display_name, 0) - amount
                    per_user[dst.display_name] = per_user.get(dst.display_name, 0) + amount
                elif src and not dst:
                    db.add(AgentExpense(
                        user_id=src.id, expense_date=when.date(), amount_gross=amount,
                        entry_type="withdrawal",
                        note=f"[Xp#{mid}] Pénz átadása: {old_users.get(r['cel_user_id'], '?')}{ceg}"[:512],
                    ))
                    stats["withdrawal"] += 1
                    per_user[src.display_name] = per_user.get(src.display_name, 0) - amount
                elif dst and not src:
                    db.add(AgentExpense(
                        user_id=dst.id, expense_date=when.date(), amount_gross=amount,
                        entry_type="deposit",
                        note=f"[Xp#{mid}] Pénz átvéve: {old_users.get(r['userid'], '?')}{ceg}"[:512],
                    ))
                    stats["deposit"] += 1
                    per_user[dst.display_name] = per_user.get(dst.display_name, 0) + amount
                else:
                    stats["skip_nouser"] += 1
                    continue
            else:
                entry_type, type_name = TYPE_MAP[t]
                if not src:
                    stats["skip_nouser"] += 1
                    continue
                supplier = beszallitok.get(r["beszallito_id"]) or None
                db.add(AgentExpense(
                    user_id=src.id, expense_date=when.date(), amount_gross=amount,
                    entry_type=entry_type,
                    supplier=supplier,
                    receipt_no=(r["szamla_srsz"] or "").strip()[:64] or None,
                    note=f"[Xp#{mid}] {megn or type_name}{ceg}"[:512],
                ))
                stats[entry_type] += 1
                sign = 1 if entry_type == "deposit" else -1
                per_user[src.display_name] = per_user.get(src.display_name, 0) + sign * amount
            added += 1
            if added % BATCH == 0:
                await db.flush()
                await db.commit()
        await db.commit()

        db.add(AuditEvent(
            action="import.xpresso_kassza", entity_type="import",
            detail={"dump": "2026-10-05", **{k: v for k, v in stats.items()}},
        ))
        await db.commit()

        print("KÉSZ.", stats)
        print("Importált nettó kassza-hatás dolgozónként (deposit−withdrawal−expense±transfer):")
        for name, bal in sorted(per_user.items()):
            print(f"  {name}: {bal:,.0f} Ft".replace(",", " "))

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
