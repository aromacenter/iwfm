"""Gép QR-címkék (reportlab, A4 rács).

Címkénként: QR-kód (a nyilvános támogatási oldal URL-je), a gép típusa,
vonalkódja (gép kód), gyári száma és a kihelyezési partner. A4-en 2×4 címke
(105 × 74,25 mm), vágójelek nélkül — sima papírra vagy öntapadós címkeívre.
"""

from __future__ import annotations

import io

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas

from app.services.wfm.worksheet_pdf import FONT, FONT_BOLD

LABEL_W = 105 * mm
LABEL_H = 74.25 * mm
COLS = 2
ROWS = 4


def _draw_qr(c: pdf_canvas.Canvas, url: str, x: float, y: float, size: float) -> None:
    widget = QrCodeWidget(url)
    bounds = widget.getBounds()
    w = bounds[2] - bounds[0]
    h = bounds[3] - bounds[1]
    drawing = Drawing(size, size, transform=[size / w, 0, 0, size / h, 0, 0])
    drawing.add(widget)
    renderPDF.draw(drawing, c, x, y)


def _draw_label(c: pdf_canvas.Canvas, item: dict, x: float, y: float) -> None:
    """Egy címke rajzolása; (x, y) a címke bal alsó sarka."""
    pad = 5 * mm
    qr_size = 44 * mm
    # keret (halvány, segít a vágásnál)
    c.setStrokeColorRGB(0.85, 0.87, 0.9)
    c.setLineWidth(0.5)
    c.rect(x, y, LABEL_W, LABEL_H)

    _draw_qr(c, item["url"], x + pad, y + LABEL_H - pad - qr_size, qr_size)

    text_x = x + pad + qr_size + 4 * mm
    text_y = y + LABEL_H - pad - 6 * mm
    c.setFillColorRGB(0, 0, 0)
    c.setFont(FONT_BOLD, 11)
    name = str(item.get("name") or "")[:26]
    c.drawString(text_x, text_y, name)
    c.setFont(FONT, 9)
    text_y -= 6 * mm
    c.drawString(text_x, text_y, f"Kód: {item.get('barcode') or '—'}")
    text_y -= 5 * mm
    if item.get("serial_number"):
        c.drawString(text_x, text_y, f"Gyári szám: {str(item['serial_number'])[:20]}")
        text_y -= 5 * mm
    if item.get("partner_name"):
        c.setFillColorRGB(0.35, 0.4, 0.5)
        c.drawString(text_x, text_y, str(item["partner_name"])[:24])
        c.setFillColorRGB(0, 0, 0)

    # alsó sáv: felszólítás + rövid link
    c.setFont(FONT_BOLD, 10)
    c.drawString(x + pad, y + 14 * mm, "Hiba esetén olvassa be a QR-kódot!")
    c.setFont(FONT, 7.5)
    c.setFillColorRGB(0.35, 0.4, 0.5)
    c.drawString(x + pad, y + 9 * mm, "Azonnali segítség (AI) vagy szervizigény bejelentése")
    c.drawString(x + pad, y + 5 * mm, str(item["url"])[:64])
    c.setFillColorRGB(0, 0, 0)


def build_qr_labels_pdf(items: list[dict]) -> bytes:
    """Címkeív PDF. ``items`` elemei: url, name, barcode, serial_number,
    partner_name."""
    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    _width, height = A4

    for index, item in enumerate(items):
        slot = index % (COLS * ROWS)
        if index > 0 and slot == 0:
            c.showPage()
        col = slot % COLS
        row = slot // COLS
        x = col * LABEL_W
        y = height - (row + 1) * LABEL_H
        _draw_label(c, item, x, y)

    c.showPage()
    c.save()
    return buf.getvalue()
