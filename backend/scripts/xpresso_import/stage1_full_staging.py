# -*- coding: utf-8 -*-
"""1/b lépcső: a FRISS xpresso MySQL-dump → TELJES staging SQLite
(xpresso_staging_full.db) — MINDEN tábla, adatvesztés nélkül.

A 2026-10-05-i dumphoz készült (111 tábla). A régi, 20 táblás
xpresso_staging.db érintetlen marad.
"""
import io
import os
import re
import sqlite3
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DUMP = os.environ.get(
    "XPRESSO_DUMP",
    r"C:\Users\palvo\Desktop\xpresso friss sql\xpresso-2026-10-05_19-15.sql\xpresso-2026-10-05_19-15.sql",
)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xpresso_staging_full.db")

SKIP = {"sysdiagrams"}  # longblob, üres

_ESC = {"'": "''", "n": "\n", "r": "\r", "t": "\t", "0": " ", "Z": "", "b": ""}

# MySQL típus → SQLite affinitás (az archívum-feltöltés is ebből dolgozik)
def affinity(mysql_type: str) -> str:
    t = mysql_type.lower()
    if t.startswith(("tinyint", "smallint", "int", "bigint", "mediumint")):
        return "INTEGER"
    if t.startswith(("decimal", "double", "float")):
        return "REAL"
    return "TEXT"


def _unescape(match: re.Match) -> str:
    return _ESC.get(match.group(1), match.group(1))


def main() -> None:
    if os.path.exists(OUT):
        os.remove(OUT)
    text = open(DUMP, encoding="utf-8", errors="replace").read()
    db = sqlite3.connect(OUT)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    # típus-metaadat az archívum-feltöltéshez
    db.execute('CREATE TABLE "_schema" (tbl TEXT, col TEXT, pos INTEGER, mysql_type TEXT)')

    total_tables = total_rows = 0
    for m in re.finditer(r"CREATE TABLE `(\w+)` \((.*?)\) ENGINE[^;]*;", text, re.S):
        name, body = m.group(1), m.group(2)
        if name in SKIP:
            continue
        cols: list[tuple[str, str]] = []
        for line in body.splitlines():
            line = line.strip().rstrip(",")
            cm = re.match(r"`(\w+)`\s+(\S+)", line)
            if cm:
                cols.append((cm.group(1), cm.group(2)))
        if not cols:
            continue
        coldefs = ", ".join(f'"{c}" {affinity(t)}' for c, t in cols)
        db.execute(f'CREATE TABLE "{name}" ({coldefs})')
        for i, (c, t) in enumerate(cols):
            db.execute('INSERT INTO "_schema" VALUES (?,?,?,?)', (name, c, i, t))
        rows = 0
        for im in re.finditer(rf"INSERT INTO `{name}` VALUES (.+?);\n", text, re.S):
            vals = re.sub(r"\\(.)", _unescape, im.group(1))
            before = db.total_changes
            db.execute(f'INSERT INTO "{name}" VALUES {vals}')
            rows += db.total_changes - before
        print(f"{name}: {rows} sor")
        total_tables += 1
        total_rows += rows
    db.commit()
    db.close()
    print(f"KÉSZ: {total_tables} tábla, {total_rows} sor → {OUT}")


if __name__ == "__main__":
    main()
