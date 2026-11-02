"""Bérprogram-formátumú jogcím-export (139. kör, ötlet #14).

A havi munkaidő-nyilvántartás (MonthSheet) számaiból dolgozó × jogcím sorok
készülnek. A jogcím-kódokat a cég a bérprogramja (Kulcs-Bér, Novitax, RLB…)
import-kódjaira cserélheti a Munkaidő-szabályok beállításban — a program neve
nem számít, a kód szabad szöveg.
"""

from __future__ import annotations

import csv
import io

from app.services.wfm.timesheet_calc import MonthSheet

# (kulcs, alap-kód, név, egység)
PAYCODE_DEFS: tuple[tuple[str, str, str, str], ...] = (
    ("worked_hours", "MUNKAORA", "Nyilvántartott munkaidő", "ora"),
    ("frame_hours", "KERETORA", "Havi keretóra", "ora"),
    ("rm50", "TULORA50", "Rendkívüli munka munkanapon (50%)", "ora"),
    ("rm100", "TULORA100", "Rendkívüli munka pihenőnapon / ünnepen (100%)", "ora"),
    ("vp50", "VASARNAP50", "Vasárnapi pótlék (50%)", "ora"),
    ("up100", "UNNEP100", "Munkaszüneti napi pótlék (100%)", "ora"),
    ("ep15", "EJSZAKA15", "Éjszakai pótlék (15%)", "ora"),
    ("mp30", "MUSZAK30", "Műszakpótlék (30%)", "ora"),
)
PAYCODE_KEYS = tuple(k for k, _, _, _ in PAYCODE_DEFS)
HEADER = ("adoazonosito", "nev", "jogcim_kod", "jogcim_nev", "mennyiseg", "egyseg", "ev", "honap")


def effective_paycodes(custom: dict | None) -> dict[str, str]:
    """Alap-kódok + a céges felülírások (üres érték = alap)."""
    out = {k: code for k, code, _, _ in PAYCODE_DEFS}
    for k, v in (custom or {}).items():
        v = (v or "").strip()
        if v:
            out[k] = v[:16]
    return out


def _hours(minutes: int) -> str:
    return f"{minutes / 60:.2f}".replace(".", ",")


def sheet_rows(sheet: MonthSheet, codes: dict[str, str]) -> list[tuple]:
    """Egy dolgozó jogcím-sorai: csak a nullától eltérő mennyiségek."""
    rows: list[tuple] = []
    tax = sheet.tax_id or ""
    base = (tax, sheet.name)
    tail = (str(sheet.year), f"{sheet.month:02d}")
    values = {
        "worked_hours": sheet.recorded_minutes,
        "frame_hours": sheet.frame_minutes,
        "rm50": sheet.rm50, "rm100": sheet.rm100, "vp50": sheet.vp50,
        "up100": sheet.up100, "ep15": sheet.ep15, "mp30": sheet.mp30,
    }
    names = {k: name for k, _, name, _ in PAYCODE_DEFS}
    for key, minutes in values.items():
        if minutes and minutes > 0:
            rows.append(base + (codes[key], names[key], _hours(minutes), "ora") + tail)
    # távollétek: kódonként nap (és óra, ha a kód órás)
    for code, (days, minutes) in sorted(sheet.absences.items()):
        if days and days > 0:
            rows.append(base + (codes.get(f"abs_{code}", code), f"Távollét {code}", str(days), "nap") + tail)
            if minutes:
                rows.append(base + (codes.get(f"abs_{code}_ora", f"{code}_ORA"), f"Távollét {code} (óra)", _hours(minutes), "ora") + tail)
    return rows


def build_paycodes_csv(sheets: list[MonthSheet], custom: dict | None, delimiter: str = ";") -> str:
    codes = effective_paycodes(custom)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(HEADER)
    for sheet in sheets:
        for row in sheet_rows(sheet, codes):
            w.writerow(row)
    return buf.getvalue()
