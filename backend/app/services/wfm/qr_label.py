"""Gép QR-címkék (reportlab).

Címkénként: QR-kód (a nyilvános támogatási oldal URL-je), a gép típusa,
vonalkódja (gép kód), gyári száma és a tulajdonos-felirat. A partner neve
SZÁNDÉKOSAN nincs rajta — gépcserénél így nem kell matricát cserélni.

Két formátum:
- A4 ív: 2×4 címke (105 × 74,25 mm), sima papírra / öntapadós ívre
- 51 × 25 mm: címkenyomtatóra (VOID címke), címkénként egy oldal
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

OWNER_TEXT = "X-Presso Coffee Kft tulajdona"


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
    c.setFillColorRGB(0.35, 0.4, 0.5)
    c.drawString(text_x, text_y, OWNER_TEXT)
    c.setFillColorRGB(0, 0, 0)

    # alsó sáv: felszólítás + a QR-oldal funkciói (link nélkül)
    c.setFont(FONT_BOLD, 10.5)
    c.drawString(x + pad, y + 15 * mm, "Olvassa be a QR-kódot a telefonjával!")
    c.setFont(FONT, 8)
    c.setFillColorRGB(0.3, 0.35, 0.45)
    c.drawString(x + pad, y + 10 * mm, "Hibabejelentés fotóval  ·  Azonnali segítség a géphez")
    c.drawString(x + pad, y + 5.5 * mm, "Kávérendelés  ·  Számláló-állás bejelentése")
    c.setFillColorRGB(0, 0, 0)


SMALL_W = 51 * mm
SMALL_H = 25 * mm


def _draw_small_label(c: pdf_canvas.Canvas, item: dict) -> None:
    """51 × 25 mm-es címke (címkenyomtató, VOID címke): QR balra, mellette a
    gép típusa, kódja és a tulajdonos-felirat."""
    pad = 1.5 * mm
    qr_size = SMALL_H - 2 * pad  # ~22 mm
    _draw_qr(c, item["url"], pad, pad, qr_size)

    text_x = pad + qr_size + 2 * mm
    max_w = SMALL_W - text_x - pad
    c.setFillColorRGB(0, 0, 0)
    c.setFont(FONT_BOLD, 7)
    name = str(item.get("name") or "")
    while name and c.stringWidth(name, FONT_BOLD, 7) > max_w:
        name = name[:-1]
    c.drawString(text_x, SMALL_H - pad - 3 * mm, name)
    c.setFont(FONT, 6)
    c.drawString(text_x, SMALL_H - pad - 6.5 * mm, f"Kód: {item.get('barcode') or '—'}")
    if item.get("serial_number"):
        c.drawString(text_x, SMALL_H - pad - 9.5 * mm, f"Gy.sz.: {str(item['serial_number'])[:16]}")
    c.setFont(FONT, 5.5)
    c.setFillColorRGB(0.3, 0.35, 0.45)
    c.drawString(text_x, pad + 4.5 * mm, OWNER_TEXT)
    c.drawString(text_x, pad + 1.5 * mm, "Olvassa be a QR-kódot!")
    c.setFillColorRGB(0, 0, 0)


def build_qr_labels_small_pdf(items: list[dict]) -> bytes:
    """51 × 25 mm-es címkék — címkénként egy PDF-oldal (címkenyomtatóhoz)."""
    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=(SMALL_W, SMALL_H))
    for index, item in enumerate(items):
        if index > 0:
            c.showPage()
            c.setPageSize((SMALL_W, SMALL_H))
        _draw_small_label(c, item)
    c.showPage()
    c.save()
    return buf.getvalue()


def build_qr_labels_pdf(items: list[dict]) -> bytes:
    """Címkeív PDF (A4). ``items`` elemei: url, name, barcode, serial_number."""
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
