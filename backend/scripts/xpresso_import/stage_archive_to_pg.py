# -*- coding: utf-8 -*-
"""Archívum-réteg: a TELJES staging (xpresso_staging_full.db) betöltése a
prod Postgres `xpresso_archive` sémájába — csak-olvasható történeti másolat,
hogy SEMMILYEN régi adat ne vesszen el (jutalék, zárások, logok, webshop stb.).

Kapcsolat: ARCHIVE_DB_URL env (postgresql://...). A séma újratöltéskor
eldobódik és újraépül — ez a dump szó szerinti tükre, nem élő adat.
"""
import asyncio
import io
import os
import re
import sqlite3
import sys
from datetime import date, datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\palvo\Desktop\appok shopify\Iwfm\backend")

import asyncpg  # noqa: E402

STAGING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xpresso_staging_full.db")
SCHEMA = "xpresso_archive"
BATCH = 2000


def pg_type(mysql_type: str) -> str:
    t = mysql_type.lower()
    if t.startswith("tinyint"):
        return "SMALLINT"
    if t.startswith(("smallint", "mediumint", "int", "bigint")):
        return "BIGINT"
    if t.startswith(("decimal", "double", "float")):
        return "DOUBLE PRECISION"
    if t.startswith("datetime") or t.startswith("timestamp"):
        return "TIMESTAMP"
    if t.startswith("date"):
        return "DATE"
    return "TEXT"


def conv(value, pgt: str):
    if value is None:
        return None
    if pgt == "TIMESTAMP":
        s = str(value)
        if s.startswith("0000"):
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s[:19], fmt)
            except ValueError:
                continue
        return None
    if pgt == "DATE":
        s = str(value)
        if s.startswith("0000"):
            return None
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None
    if pgt in ("SMALLINT", "BIGINT"):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if pgt == "DOUBLE PRECISION":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return str(value)


async def main() -> None:
    url = os.environ["ARCHIVE_DB_URL"]
    # asyncpg nem ismeri a sqlalchemy-s sémát, sima postgresql:// kell
    url = re.sub(r"^postgresql\+\w+://", "postgresql://", url)
    st = sqlite3.connect(STAGING)
    st.row_factory = sqlite3.Row

    schema_rows = st.execute("SELECT tbl, col, pos, mysql_type FROM _schema ORDER BY tbl, pos").fetchall()
    tables: dict[str, list[tuple[str, str]]] = {}
    for r in schema_rows:
        tables.setdefault(r["tbl"], []).append((r["col"], pg_type(r["mysql_type"])))

    conn = await asyncpg.connect(url, timeout=30)
    try:
        await conn.execute(f'DROP SCHEMA IF EXISTS {SCHEMA} CASCADE')
        await conn.execute(f'CREATE SCHEMA {SCHEMA}')
        total = 0
        for tbl, cols in tables.items():
            coldefs = ", ".join(f'"{c}" {t}' for c, t in cols)
            await conn.execute(f'CREATE TABLE {SCHEMA}."{tbl}" ({coldefs})')
            colnames = [c for c, _ in cols]
            pgtypes = [t for _, t in cols]
            rows = st.execute(f'SELECT * FROM "{tbl}"').fetchall()
            records = [tuple(conv(row[i], pgtypes[i]) for i in range(len(colnames))) for row in rows]
            for i in range(0, len(records), BATCH):
                await conn.copy_records_to_table(
                    tbl, records=records[i : i + BATCH], columns=colnames, schema_name=SCHEMA
                )
            total += len(records)
            print(f"{tbl}: {len(records)}")
        await conn.execute(
            f"COMMENT ON SCHEMA {SCHEMA} IS "
            f"'Xpresso (regi rendszer) teljes archivuma — dump: 2026-10-05 19:15. Csak olvasasra.'"
        )
        print(f"KÉSZ: {len(tables)} tábla, {total} sor a(z) {SCHEMA} sémában.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
