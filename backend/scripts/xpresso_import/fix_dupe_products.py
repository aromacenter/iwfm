# -*- coding: utf-8 -*-
"""A stage3 első futása által duplán létrehozott kávé-termékek összevonása.

Ok: a jelölő-visszafejtés a note-beli `kod`-ot kv_id-ként értelmezte, így a
már importált 252 kávé újra létrejött. Ez a szkript kod-onként megtartja a
LEGKORÁBBI terméket, minden hivatkozást (settlement_lines, settlement_machines,
partner_prices, partner_stock, stock_movements, assets.default_product_id)
az eredetire irányít, majd törli a ma keletkezett duplikátumot. Idempotens.
"""
import asyncio
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import asyncpg

CUT = "2026-10-05"  # a duplikátumok ma keletkeztek


async def main() -> None:
    url = re.sub(r"^postgresql\+\w+://", "postgresql://", os.environ["ARCHIVE_DB_URL"])
    conn = await asyncpg.connect(url, timeout=30)
    try:
        rows = await conn.fetch(
            "SELECT id, name, notes, created_at FROM products "
            "WHERE notes LIKE '%Xpresso kód: %' ORDER BY created_at"
        )
        by_kod: dict[str, list] = {}
        for r in rows:
            m = re.search(r"Xpresso kód: (\d+)", r["notes"] or "")
            if m:
                by_kod.setdefault(m.group(1), []).append(r)

        merged = 0
        async with conn.transaction():
            for kod, group in by_kod.items():
                if len(group) < 2:
                    continue
                original = group[0]
                for dupe in group[1:]:
                    if str(dupe["created_at"])[:10] < CUT:
                        print(f"KIHAGYVA (nem mai): {dupe['name']}")
                        continue
                    o, d = original["id"], dupe["id"]
                    await conn.execute(
                        "UPDATE settlement_lines SET product_id=$1 WHERE product_id=$2", o, d)
                    await conn.execute(
                        "UPDATE settlement_machines SET product_id=$1 WHERE product_id=$2", o, d)
                    await conn.execute(
                        "UPDATE stock_movements SET product_id=$1 WHERE product_id=$2", o, d)
                    await conn.execute(
                        "UPDATE assets SET default_product_id=$1 WHERE default_product_id=$2", o, d)
                    # partner_prices / partner_stock: ha az (eredeti, partner) pár már
                    # létezik, a duplikált sor törlendő, különben átirányítandó.
                    await conn.execute(
                        "DELETE FROM partner_prices pp WHERE pp.product_id=$2 AND EXISTS ("
                        " SELECT 1 FROM partner_prices e WHERE e.partner_id=pp.partner_id"
                        " AND e.product_id=$1)", o, d)
                    await conn.execute(
                        "UPDATE partner_prices SET product_id=$1 WHERE product_id=$2", o, d)
                    await conn.execute(
                        "DELETE FROM partner_stock ps WHERE ps.product_id=$2 AND EXISTS ("
                        " SELECT 1 FROM partner_stock e WHERE e.partner_id=ps.partner_id"
                        " AND e.product_id=$1)", o, d)
                    await conn.execute(
                        "UPDATE partner_stock SET product_id=$1 WHERE product_id=$2", o, d)
                    await conn.execute("DELETE FROM products WHERE id=$1", d)
                    merged += 1
        print(f"Összevont duplikátum: {merged}")
        left = await conn.fetchval(
            "SELECT COUNT(*) FROM products WHERE notes LIKE '%Xpresso kód: %'")
        print(f"Xpresso-kódos termék maradt: {left}")
    finally:
        await conn.close()


asyncio.run(main())
