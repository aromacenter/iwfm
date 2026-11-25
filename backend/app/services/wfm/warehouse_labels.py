"""Tárhely-címkék (174. kör): konfigurálható címkelap a tárhelyezett raktárhoz.

A címke mérete szabadon állítható (mm), a papír A4/A5/Letter ív vagy
címketekercs/szalag (egy címke = egy oldal; címkenyomtató — Brother QL/P-touch,
Dymo, Zebra — az illesztőjén át a saját szalagjára teszi). A tartalom mezőkből
áll össze: tárhely-kód, QR (link a tárhely adataira vagy csak a kód),
Code128 vonalkód (a kód), raktár neve, koordináták, a tárhely megjegyzése és
egy szabadon beírható csoport-felirat (pl. „Szerszámok”).

A betűméret a címke méretéből számolódik, és szélességre is illeszkedik, így
a 25 mm-es tekercscímkétől az A5-ös polctábláig ugyanaz a rajzoló megy.
"""

from __future__ import annotations

import io
import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import code128
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4, A5, letter
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as pdf_canvas

from app.services.wfm.worksheet_pdf import FONT, FONT_BOLD

FIELDS = ("code", "qr", "barcode", "group", "coords", "warehouse", "note")
PAPERS = {"A4": A4, "A5": A5, "Letter": letter}
MIN_LABEL_W_MM = 20.0
MIN_LABEL_H_MM = 6.0  # 6 mm-es P-touch szalag is mehet
MAX_LABEL_MM = 300.0


class LabelConfig(BaseModel):
    """Egy címke-minta — ezt menti a `warehouse_label_templates` tábla is."""

    paper: Literal["A4", "A5", "Letter", "roll"] = "A4"
    landscape: bool = False
    label_w: float = Field(default=63.5, ge=MIN_LABEL_W_MM, le=MAX_LABEL_MM)  # mm
    label_h: float = Field(default=33.9, ge=MIN_LABEL_H_MM, le=MAX_LABEL_MM)  # mm (szalagnál a szalag szélessége)
    margin_x: float | None = Field(default=None, ge=0, le=50)  # mm; None = középre
    margin_y: float | None = Field(default=None, ge=0, le=50)
    gap_x: float = Field(default=0, ge=0, le=30)
    gap_y: float = Field(default=0, ge=0, le=30)
    border: bool = True
    fields: list[str] = Field(default_factory=lambda: ["code", "qr", "coords", "warehouse"])
    group_text: str | None = Field(default=None, max_length=40)
    qr_content: Literal["link", "code"] = "link"
    font_scale: float = Field(default=1.0, ge=0.5, le=2.0)

    @field_validator("fields")
    @classmethod
    def _known_fields(cls, v: list[str]) -> list[str]:
        out = [f for f in dict.fromkeys(v) if f in FIELDS]
        return out

    @field_validator("group_text")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v or None


class LabelItem(BaseModel):
    code: str
    row_code: str
    bay: int
    level: int
    slot: int
    note: str | None = None
    link: str


def page_size(cfg: LabelConfig) -> tuple[float, float]:
    if cfg.paper == "roll":
        return cfg.label_w * mm, cfg.label_h * mm
    w, h = PAPERS[cfg.paper]
    return (h, w) if cfg.landscape else (w, h)


def grid(cfg: LabelConfig) -> tuple[int, int, float, float]:
    """(oszlopok, sorok, bal margó pt, felső margó pt). Tekercsnél 1×1."""
    pw, ph = page_size(cfg)
    if cfg.paper == "roll":
        return 1, 1, 0.0, 0.0
    lw, lh = cfg.label_w * mm, cfg.label_h * mm
    gx, gy = cfg.gap_x * mm, cfg.gap_y * mm
    min_mx = cfg.margin_x * mm if cfg.margin_x is not None else 4 * mm
    min_my = cfg.margin_y * mm if cfg.margin_y is not None else 4 * mm
    cols = int(math.floor((pw - 2 * min_mx + gx) / (lw + gx) + 1e-6))
    rows = int(math.floor((ph - 2 * min_my + gy) / (lh + gy) + 1e-6))
    if cols < 1 or rows < 1:
        raise ValueError("label_too_big")
    mx = min_mx if cfg.margin_x is not None else (pw - cols * lw - (cols - 1) * gx) / 2
    my = min_my if cfg.margin_y is not None else (ph - rows * lh - (rows - 1) * gy) / 2
    return cols, rows, mx, my


def _fit(text: str, font: str, size: float, max_w: float) -> float:
    """Betűméret, amivel a szöveg belefér ``max_w`` pontba (legfeljebb ``size``)."""
    if not text:
        return size
    w = pdfmetrics.stringWidth(text, font, size)
    if w <= max_w or w <= 0:
        return size
    return max(3.0, size * max_w / w)


def _draw_qr(c: pdf_canvas.Canvas, content: str, x: float, y: float, size: float) -> None:
    widget = QrCodeWidget(content, barLevel="M")
    bx, by, bw, bh = widget.getBounds()
    d = Drawing(size, size, transform=[size / (bw - bx), 0, 0, size / (bh - by), 0, 0])
    d.add(widget)
    renderPDF.draw(d, c, x, y)


def _draw_barcode(c: pdf_canvas.Canvas, value: str, x: float, y: float, max_w: float, h: float) -> None:
    probe = code128.Code128(value, barHeight=h, barWidth=1.0, humanReadable=False, quiet=False)
    bar_w = min(0.5 * mm, max_w / max(probe.width, 1.0))
    bc = code128.Code128(value, barHeight=h, barWidth=bar_w, humanReadable=False, quiet=False)
    bc.drawOn(c, x + (max_w - bc.width) / 2, y)


def coords_text(item: LabelItem) -> str:
    s = f"sor {item.row_code} · állás {item.bay} · szint {item.level}"
    if item.slot:
        s += f" · fakk {item.slot}"
    return s


def draw_label(c: pdf_canvas.Canvas, item: LabelItem, cfg: LabelConfig, warehouse_name: str, x: float, y: float) -> None:
    """Egy címke; (x, y) a bal alsó sarok, pontban."""
    w, h = cfg.label_w * mm, cfg.label_h * mm
    f = set(cfg.fields)
    pad = min(max(0.06 * min(w, h), 1.2 * mm), 4 * mm)
    if cfg.border:
        c.setStrokeColorRGB(0.82, 0.84, 0.88)
        c.setLineWidth(0.4)
        c.rect(x, y, w, h)

    inner_x, inner_w = x + pad, w - 2 * pad
    inner_y, inner_h = y + pad, h - 2 * pad
    if "barcode" in f:
        bc_h = min(max(0.22 * h, 5 * mm), 14 * mm)
        _draw_barcode(c, item.code, inner_x, inner_y, inner_w, bc_h)
        inner_y += bc_h + pad * 0.6
        inner_h -= bc_h + pad * 0.6

    text_x, text_w = inner_x, inner_w
    if "qr" in f:
        q = min(inner_h, 0.42 * inner_w)
        _draw_qr(c, item.link if cfg.qr_content == "link" else item.code, inner_x, inner_y + (inner_h - q) / 2, q)
        text_x += q + pad
        text_w -= q + pad

    # szövegsorok: (szöveg, betű, súly)
    lines: list[tuple[str, str, float]] = []
    if "group" in f and cfg.group_text:
        lines.append((cfg.group_text, FONT_BOLD, 1.4))
    if "code" in f:
        lines.append((item.code, FONT_BOLD, 2.4))
    if "coords" in f:
        lines.append((coords_text(item), FONT, 1.0))
    if "warehouse" in f:
        lines.append((warehouse_name[:40], FONT, 1.0))
    if "note" in f and item.note:
        lines.append((item.note[:40], FONT, 1.0))
    if not lines or text_w <= 4 * mm:
        return
    total = sum(wt for _, _, wt in lines)
    unit = inner_h / total
    c.setFillColorRGB(0, 0, 0)
    top = inner_y + inner_h
    for text, font, wt in lines:
        slot_h = unit * wt
        size = _fit(text, font, slot_h * 0.72 * cfg.font_scale, text_w)
        size = min(size, slot_h * 0.85)
        c.setFont(font, size)
        c.drawString(text_x, top - slot_h * 0.5 - size * 0.35, text)
        top -= slot_h


def build_labels_pdf(items: list[LabelItem], cfg: LabelConfig, warehouse_name: str, copies: int = 1) -> bytes:
    """A teljes PDF; ``copies`` példány minden címkéből egymás után."""
    cols, rows, mx, my = grid(cfg)
    pw, ph = page_size(cfg)
    lw, lh = cfg.label_w * mm, cfg.label_h * mm
    per_page = cols * rows
    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=(pw, ph))
    c.setTitle("Tárhely-címkék")
    seq = [it for it in items for _ in range(max(1, copies))]
    for i, item in enumerate(seq):
        if i and i % per_page == 0:
            c.showPage()
        k = i % per_page
        x = mx + (k % cols) * (lw + cfg.gap_x * mm)
        y = ph - my - (k // cols + 1) * lh - (k // cols) * cfg.gap_y * mm
        draw_label(c, item, cfg, warehouse_name, x, y)
    c.showPage()
    c.save()
    return buf.getvalue()
