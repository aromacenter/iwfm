"""Havi munkaidő-nyilvántartás PDF — a mintapéldány (olmunkaido.hu) szerkezete.

Oldalanként egy dolgozó: fejléc → három összesítő blokk (Hónap / Bérpótlékok /
Távollétek) → napi tábla (minden naptári nap) → lábléc (aláírás, oldalszám,
generálás dátuma, forrás). Spec: docs/berexport-munkaido-nyilvantartas-spec.md.
"""

from __future__ import annotations

import io
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as pdf_canvas

from app.services.wfm.timesheet_calc import ABSENCE_ORDER, MonthSheet, fmt_hm, fmt_t
from app.services.wfm.worksheet_pdf import FONT, FONT_BOLD

PAGE_W, PAGE_H = A4
LEFT = 56.0           # 20 mm
RIGHT = PAGE_W - 56.0
LINE = colors.HexColor("#9aa3b2")
LIGHT = colors.HexColor("#e6e9f0")
INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#475569")

# napi tábla oszlopai: (fejléc 1. sor, fejléc 2. sor, szélesség pt, igazítás)
COLUMNS = (
    ("Dátum", "", 36, "L"),
    ("Nap", "", 22, "L"),
    ("Beosztás", "Kezdet", 34, "C"),
    ("", "Vég", 34, "C"),
    ("", "BI", 36, "C"),
    ("Munkaidő", "Kezdet", 34, "C"),
    ("", "Vég", 34, "C"),
    ("", "NYI", 36, "C"),
    ("RM", "100%", 36, "C"),
    ("RM", "50%", 36, "C"),
    ("VP", "50%", 36, "C"),
)
ROW_H = 11.25


def _draw_text(c, x: float, y: float, text: str, size: float = 7, bold: bool = False, align: str = "L", color=INK):
    c.setFont(FONT_BOLD if bold else FONT, size)
    c.setFillColor(color)
    if align == "R":
        c.drawRightString(x, y, text)
    elif align == "C":
        c.drawCentredString(x, y, text)
    else:
        c.drawString(x, y, text)


def _draw_sheet(c, sheet: MonthSheet, *, company: str, page_no: int, generated: date, source_line: str) -> None:
    y = PAGE_H - 40

    # ── fejléc ──
    _draw_text(c, LEFT, y, "Munkaidő nyilvántartás", 12, bold=True)
    _draw_text(c, RIGHT, y, company[:60], 10, bold=True, align="R")
    y -= 16
    _draw_text(c, LEFT, y, f"{sheet.period_start.isoformat()} - {sheet.period_end.isoformat()}", 11)
    if sheet.site:
        _draw_text(c, RIGHT, y, f"Telephely: {sheet.site}"[:70], 8, align="R", color=MUTED)
    y -= 24
    _draw_text(c, LEFT, y, sheet.name[:60], 12, bold=True)
    jv = f"Jogviszonyok: {sheet.hire_date.isoformat()} - "
    if sheet.termination_date:
        jv += sheet.termination_date.isoformat()
    _draw_text(c, LEFT + 230, y, jv, 7, color=MUTED)
    y -= 13
    _draw_text(c, LEFT, y, f"Adóazonosító jele: {sheet.tax_id or ''}", 7)
    y -= 16

    # ── összesítő blokkok ──
    box_top = y
    box_h = 96
    col_w = (RIGHT - LEFT) / 3
    c.setStrokeColor(LINE)
    c.setLineWidth(0.5)
    for i in range(3):
        c.rect(LEFT + i * col_w, box_top - box_h, col_w, box_h, stroke=1, fill=0)
    # 1. Hónap
    bx = LEFT + 6
    by = box_top - 11
    _draw_text(c, bx, by, "Hónap", 7, bold=True)
    _draw_text(c, bx + 40, by, "Munkaidő", 7, bold=True)
    lines1 = (
        ("Nyilvántartott idő", fmt_hm(sheet.recorded_minutes)),
        ("Munkaidő", fmt_hm(sheet.frame_minutes)),
        ("Pihenőnapok", str(sheet.rest_days)),
        ("Havi órák", fmt_hm(sheet.frame_minutes)),
        ("Munkanapok", str(sheet.workdays)),
    )
    ly = by - 13
    for label, value in lines1:
        _draw_text(c, bx + 24, ly, label, 7)
        _draw_text(c, LEFT + col_w - 8, ly, value, 7, align="R")
        ly -= 12
    # 2. Bérpótlékok
    bx = LEFT + col_w + 6
    _draw_text(c, bx, by, "Bérpótlékok", 7, bold=True)
    ly = by - 13
    for label, value in (("RM 100%", fmt_hm(sheet.rm100)), ("RM 50%", fmt_hm(sheet.rm50)), ("VP 50%", fmt_hm(sheet.vp50))):
        _draw_text(c, bx + 24, ly, label, 7)
        _draw_text(c, LEFT + 2 * col_w - 8, ly, value, 7, align="R")
        ly -= 12
    # 3. Távollétek
    bx = LEFT + 2 * col_w + 6
    _draw_text(c, bx, by, "Távollétek", 7, bold=True)
    _draw_text(c, bx + col_w - 60, by, "Nap", 7, bold=True, align="R")
    _draw_text(c, bx + col_w - 14, by, "Havi órák", 7, bold=True, align="R")
    ly = by - 13
    codes = list(ABSENCE_ORDER) + [k for k in sheet.absences if k not in ABSENCE_ORDER]
    for code in codes:
        days, minutes = sheet.absences.get(code, (0, None))
        _draw_text(c, bx + 24, ly, code, 7)
        _draw_text(c, bx + col_w - 60, ly, str(days), 7, align="R")
        _draw_text(c, bx + col_w - 14, ly, fmt_hm(minutes) if minutes is not None else "", 7, align="R")
        ly -= 12
    y = box_top - box_h - 14

    # ── napi tábla ──
    xs: list[float] = []
    x = LEFT
    for _h1, _h2, w, _a in COLUMNS:
        xs.append(x)
        x += w
    table_right = x
    # csoport-fejlécek
    c.setStrokeColor(LINE)
    _draw_text(c, xs[0], y, "Dátum", 7, bold=True)
    _draw_text(c, xs[1], y, "Nap", 7, bold=True)
    _draw_text(c, (xs[2] + xs[5]) / 2, y, "Beosztás", 7, bold=True, align="C")
    _draw_text(c, (xs[5] + xs[8]) / 2, y, "Munkaidő", 7, bold=True, align="C")
    for idx in (8, 9, 10):
        _draw_text(c, xs[idx] + COLUMNS[idx][2] / 2, y, COLUMNS[idx][0], 7, bold=True, align="C")
    y -= 9
    for idx, (_h1, h2, w, _a) in enumerate(COLUMNS):
        if h2:
            _draw_text(c, xs[idx] + w / 2, y, h2, 7, bold=True, align="C")
    y -= 4
    c.setLineWidth(0.6)
    c.line(LEFT, y, table_right, y)
    y -= ROW_H

    for row in sheet.rows:
        if not row.in_employment:
            _draw_text(c, xs[0], y, row.label, 7, color=MUTED)
            _draw_text(c, xs[1], y, row.weekday_abbr, 7, color=MUTED)
        else:
            muted = row.rest_day
            color = MUTED if muted else INK
            _draw_text(c, xs[0], y, row.label, 7, color=color)
            _draw_text(c, xs[1], y, row.weekday_abbr, 7, color=color)
            vals = [
                fmt_t(row.sched_start), fmt_t(row.sched_end),
                fmt_hm(row.sched_minutes) if row.sched_minutes else "",
            ]
            if row.code:
                vals += [row.code, "", fmt_hm(row.work_minutes) if row.work_minutes else ""]
            else:
                vals += [
                    fmt_t(row.work_start), fmt_t(row.work_end),
                    fmt_hm(row.work_minutes) if row.work_minutes else "",
                ]
            vals += [
                fmt_hm(row.rm100) if row.rm100 else "",
                fmt_hm(row.rm50) if row.rm50 else "",
                fmt_hm(row.vp50) if row.vp50 else "",
            ]
            for i, val in enumerate(vals, start=2):
                if val:
                    _draw_text(c, xs[i] + COLUMNS[i][2] / 2, y, val, 7, align="C", bold=(i == 5 and bool(row.code)))
        c.setStrokeColor(LIGHT)
        c.setLineWidth(0.3)
        c.line(LEFT, y - 3.5, table_right, y - 3.5)
        y -= ROW_H

    # ── lábléc ──
    fy = 42
    c.setStrokeColor(LINE)
    c.setLineWidth(0.5)
    c.line(LEFT, fy + 10, LEFT + 150, fy + 10)
    _draw_text(c, LEFT, fy, "Munkavállaló aláírása", 7)
    _draw_text(c, PAGE_W / 2, fy, f"{page_no}. oldal", 7, align="C")
    _draw_text(c, RIGHT, fy, generated.isoformat(), 7, align="R")
    _draw_text(c, LEFT, fy - 11, source_line[:120], 6.5, color=MUTED)
    _draw_text(c, RIGHT, fy - 11, f"Mt. szabálykészlet: {sheet.rules.valid_from.isoformat()}", 6.5, align="R", color=MUTED)


def build_timesheet_pdf(
    sheets: list[MonthSheet],
    *,
    company: str,
    generated: date | None = None,
    source_line: str = "X-admin",
) -> bytes:
    """Dolgozónként egy oldal, folyamatos oldalszámozás. A hívó rendezi a
    lapokat (telephely → név), a telephely a fejlécben látszik."""
    generated = generated or date.today()
    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    c.setTitle("Munkaidő nyilvántartás")
    c.setAuthor(company)
    page_no = 0
    for sheet in sheets:
        page_no += 1
        _draw_sheet(c, sheet, company=company, page_no=page_no, generated=generated, source_line=source_line)
        c.showPage()
    if page_no == 0:
        _draw_text(c, LEFT, PAGE_H - 60, "Nincs bérszámfejtett dolgozó az időszakban.", 10)
        c.showPage()
    c.save()
    return buf.getvalue()
