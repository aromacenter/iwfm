# -*- coding: utf-8 -*-
"""1. lépcső: xpresso MySQL-dump → staging SQLite (xpresso_staging.db).

Csak az importhoz kellő táblákat töltjük be, a MySQL string-escape-eket
(\\' \\" \\\\ \\n ...) SQLite-kompatibilisre alakítva.
"""
import os, re, sqlite3, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DUMP = r"C:\Users\palvo\Desktop\xpresso-dump-260809.sql"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xpresso_staging.db")

TABLES = [
    "Partner_LISTA", "Partner_FAJTA", "Term_ESZ_VK", "Term_ESZ_HELYFAJTA",
    "Term_GYARTOK", "Term_GYARTOK_TIPUSOK", "Term_KV_KAVE", "Term_ALK_CIKKSZ",
    "User_LISTA", "Partner_ELSZAM_SZERZODESEK", "Elszamolas", "Elszamolas_KAVEGEP",
    "Elszamolas_TERMEKEK", "Szerviz_MUNKALAP", "Szerviz_STADIUM", "Szerviz_ALLAPOT",
    "Szerviz_MUNKA", "Szerviz_MUNKAFAJTA", "Mozgas_RESZLETES", "Term_CSOPORTOK",
]

_ESC = {"'": "''", "n": "\n", "r": "\r", "t": "\t", "0": " ", "Z": "", "b": ""}


def _unescape(match: re.Match) -> str:
    ch = match.group(1)
    return _ESC.get(ch, ch)


def main() -> None:
    if os.path.exists(OUT):
        os.remove(OUT)
    text = open(DUMP, encoding="utf-8", errors="replace").read()
    db = sqlite3.connect(OUT)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")

    for m in re.finditer(r"CREATE TABLE `(\w+)` \((.*?)\) ENGINE[^;]*;", text, re.S):
        name, body = m.group(1), m.group(2)
        if name not in TABLES:
            continue
        cols = re.findall(r"^\s*`(\w+)`", body, re.M)
        db.execute(f'CREATE TABLE "{name}" ({", ".join(cols)})')
        rows = 0
        for im in re.finditer(rf"INSERT INTO `{name}` VALUES (.+?);\n", text, re.S):
            vals = re.sub(r"\\(.)", _unescape, im.group(1))
            before = db.total_changes
            db.execute(f'INSERT INTO "{name}" VALUES {vals}')
            rows += db.total_changes - before
        print(f"{name}: {rows} sor")
    db.commit()

    # gyors ellenőrzések az 5 éves ablakhoz
    cur = db.execute(
        "SELECT COUNT(*) FROM Elszamolas WHERE befejezve=1 AND elszamolas_end >= '2021-08-09'"
    )
    print("Elszámolás (5 év, befejezett):", cur.fetchone()[0])
    cur = db.execute(
        "SELECT COUNT(*) FROM Elszamolas_KAVEGEP g JOIN Elszamolas e ON e.elszamolas_id=g.elszamolas_id "
        "WHERE e.befejezve=1 AND e.elszamolas_end >= '2021-08-09'"
    )
    print("  gépsorok:", cur.fetchone()[0])
    cur = db.execute(
        "SELECT COUNT(*) FROM Elszamolas_TERMEKEK t JOIN Elszamolas e ON e.elszamolas_id=t.elszamolas_id "
        "WHERE e.befejezve=1 AND e.elszamolas_end >= '2021-08-09'"
    )
    print("  terméksorok:", cur.fetchone()[0])
    cur = db.execute(
        "SELECT COUNT(*), COUNT(DISTINCT vonalkod) FROM Term_ESZ_VK"
    )
    print("Eszközök / egyedi vonalkód:", cur.fetchone())
    db.close()
    print("Kész:", OUT)


if __name__ == "__main__":
    main()
