"""Havi zárás riport PDF — cégenként (X-Presso / Premium Caffe) vagy összes.

Tartalma: időszaki összesítés fizetési módonként (darab/nettó/bruttó),
számlázott vs. nem számlázott, kintlévőség, majd az elszámolások listája.
Könyvelő-barát, A4 fekvő táblázattal.
"""

from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle

from app.services.wfm.worksheet_pdf import FONT, FONT_BOLD

PAYMENT_HU = {"cash": "Készpénz", "card": "Bankkártya", "transfer": "Utalás"}


def _ft(value: float) -> str:
    return f"{value:,.0f} Ft".replace(",", " ")


def build_monthly_report_pdf(
    *,
    company_label: str,
    period_label: str,
    rows: list[dict],  # {date, partner, method, net, gross, invoiced, payment_status}
) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
    )
    h1 = ParagraphStyle("h1", fontName=FONT_BOLD, fontSize=15, spaceAfter=2)
    sub = ParagraphStyle("sub", fontName=FONT, fontSize=10, textColor=colors.HexColor("#475569"))
    h2 = ParagraphStyle("h2", fontName=FONT_BOLD, fontSize=11, spaceBefore=8, spaceAfter=4)

    story = [
        Paragraph("Havi elszámolás-zárás", h1),
        Paragraph(f"{company_label} · {period_label}", sub),
        Spacer(1, 6 * mm),
    ]

    # összesítés fizetési módonként
    by_method: dict[str, dict] = {}
    invoiced_gross = outstanding_gross = 0.0
    for r in rows:
        b = by_method.setdefault(r["method"], {"count": 0, "net": 0.0, "gross": 0.0})
        b["count"] += 1
        b["net"] += r["net"]
        b["gross"] += r["gross"]
        if r["invoiced"]:
            invoiced_gross += r["gross"]
        if r["payment_status"] == "outstanding":
            outstanding_gross += r["gross"]

    story.append(Paragraph("Összesítés", h2))
    sum_data = [["Fizetési mód", "Darab", "Nettó", "Bruttó"]]
    for method in ("cash", "card", "transfer"):
        b = by_method.get(method)
        if b:
            sum_data.append([PAYMENT_HU[method], str(b["count"]), _ft(b["net"]), _ft(b["gross"])])
    sum_data.append([
        "Összesen", str(len(rows)),
        _ft(sum(r["net"] for r in rows)), _ft(sum(r["gross"] for r in rows)),
    ])
    sum_data.append(["Ebből kiszámlázott", "", "", _ft(invoiced_gross)])
    sum_data.append(["Kintlévő (lejárt/nyitott utalás)", "", "", _ft(outstanding_gross)])
    sum_table = Table(sum_data, colWidths=[60 * mm, 25 * mm, 45 * mm, 45 * mm])
    sum_table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), FONT_BOLD, 9),
        ("FONT", (0, 1), (-1, -1), FONT, 9),
        ("FONT", (0, -3), (-1, -3), FONT_BOLD, 9),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, colors.HexColor("#334155")),
        ("LINEABOVE", (0, -3), (-1, -3), 0.5, colors.HexColor("#94a3b8")),
        ("TEXTCOLOR", (0, -1), (-1, -1), colors.HexColor("#b91c1c")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(sum_table)

    story.append(Paragraph(f"Elszámolások ({len(rows)} db)", h2))
    data = [["Dátum", "Partner", "Fiz. mód", "Nettó", "Bruttó", "Számla"]]
    for r in rows:
        data.append([
            r["date"], r["partner"][:42], PAYMENT_HU.get(r["method"], r["method"]),
            _ft(r["net"]), _ft(r["gross"]), "igen" if r["invoiced"] else "—",
        ])
    table = Table(
        data, colWidths=[24 * mm, 66 * mm, 22 * mm, 28 * mm, 28 * mm, 14 * mm],
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), FONT_BOLD, 8),
        ("FONT", (0, 1), (-1, -1), FONT, 8),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, colors.HexColor("#334155")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    story.append(table)

    doc.build(story)
    return buf.getvalue()
