"""Átadás-átvételi elismervény (155. kör, bbbede05): a megjavított készülék
ügyfélnek történő átadásáról készülő, aláírandó A4-es lap — fejléc a cég
logójával, munkalap-sorszám, ügyfél, gép, tételek, végösszeg, fizetési mód,
nyilatkozat és két aláírás-vonal (átadó / átvevő)."""

from __future__ import annotations

import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas

from app.services.wfm.worksheet_pdf import FONT, FONT_BOLD, _hex_rgb, _logo_image

DECLARATION = (
    "Alulírott átvevő kijelentem, hogy a fent megjelölt készüléket a munkalapon "
    "részletezett javítás elvégzése után, működőképes állapotban, tartozékaival együtt "
    "átvettem. A javításra a munkalapon feltüntetett garanciális feltételek vonatkoznak."
)


def _ft(v) -> str:
    try:
        return f"{int(round(float(v or 0))):,}".replace(",", " ") + " Ft"
    except (TypeError, ValueError):
        return "—"


def build_handover_receipt_pdf(data: dict, settings: dict | None = None) -> bytes:
    s = settings or {}
    accent = _hex_rgb(s.get("accent_color"), (0.15, 0.25, 0.65))
    accent_tint = tuple(ch + (1 - ch) * 0.88 for ch in accent)
    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    left, right = 18 * mm, width - 18 * mm
    y = height - 18 * mm

    def line(label: str, value, *, label_w: float = 44 * mm) -> None:
        nonlocal y
        c.setFont(FONT_BOLD, 9)
        c.drawString(left, y, label)
        c.setFont(FONT, 9)
        c.drawString(left + label_w, y, str(value) if value not in (None, "") else "—")
        y -= 5.5 * mm

    def wrapped(text: str, size: float = 9, width_chars: int = 105) -> None:
        nonlocal y
        c.setFont(FONT, size)
        for paragraph in (text or "—").splitlines() or ["—"]:
            while len(paragraph) > width_chars:
                cut = paragraph.rfind(" ", 0, width_chars)
                cut = cut if cut > 0 else width_chars
                c.drawString(left, y, paragraph[:cut])
                paragraph = paragraph[cut:].lstrip()
                y -= 4.5 * mm
            c.drawString(left, y, paragraph)
            y -= 4.5 * mm

    def section(title: str) -> None:
        nonlocal y
        y -= 3 * mm
        c.setFillColorRGB(*accent_tint)
        c.rect(left, y - 1.5 * mm, right - left, 6.5 * mm, fill=1, stroke=0)
        c.setFillColorRGB(*accent)
        c.setFont(FONT_BOLD, 10)
        c.drawString(left + 2 * mm, y, title)
        c.setFillColorRGB(0, 0, 0)
        y -= 8 * mm

    # ── fejléc ──
    logo = _logo_image(s.get("logo_bytes"))
    company_name = s.get("company_name") or "X-admin"
    if logo is not None:
        try:
            c.drawImage(logo, left, y - 9 * mm, width=48 * mm, height=14 * mm, preserveAspectRatio=True, anchor="sw", mask="auto")
        except Exception:  # pragma: no cover
            logo = None
    if logo is None:
        c.setFillColorRGB(*accent)
        c.setFont(FONT_BOLD, 22)
        c.drawString(left, y, company_name[:40])
    c.setFillColorRGB(0, 0, 0)
    c.setFont(FONT_BOLD, 16)
    c.drawRightString(right, y, "ÁTADÁS-ÁTVÉTELI ELISMERVÉNY")
    c.setFont(FONT_BOLD, 11)
    c.drawRightString(right, y - 6 * mm, str(data.get("serial") or ""))
    c.setFont(FONT, 8)
    c.drawRightString(right, y - 11 * mm, f"Kelt: {data.get('created_at', '')}")
    y -= 18 * mm
    c.setLineWidth(1)
    c.setStrokeColorRGB(*accent)
    c.line(left, y, right, y)
    c.setStrokeColorRGB(0, 0, 0)
    y -= 6 * mm

    section("Átadó (szerviz)")
    line("Cég:", company_name)
    line("Cím:", s.get("company_address"))
    section("Átvevő (ügyfél)")
    line("Név:", data.get("client_name"))
    line("Cím / helyszín:", data.get("client_location"))
    line("E-mail:", data.get("quote_email"))
    section("Készülék")
    line("Gép:", data.get("machine") or data.get("title"))
    line("Munkalap:", data.get("serial"))

    section("Elvégzett munka és tételek")
    items = data.get("items") or []
    if not items:
        wrapped("— a munkalapon nincs tételes elszámolás —")
    c.setFont(FONT_BOLD, 9)
    if items:
        c.drawString(left, y, "Megnevezés")
        c.drawRightString(right, y, "Nettó")
        y -= 5 * mm
        c.setFont(FONT, 9)
        for it in items[:40]:
            c.drawString(left, y, str(it.get("name") or "")[:90])
            c.drawRightString(right, y, _ft(it.get("amount_net")))
            y -= 5 * mm
    y -= 1 * mm
    c.setFont(FONT_BOLD, 10)
    if data.get("handover_discount"):
        c.drawRightString(right, y, "Kedvezményes átadás — számla nélkül")
    else:
        c.drawRightString(right, y, f"Összesen nettó: {_ft(data.get('total_net'))}   bruttó: {_ft(data.get('total_gross'))}")
    y -= 6 * mm
    c.setFont(FONT, 9)
    pay = data.get("pay_label")
    if pay:
        c.drawString(left, y, f"Fizetési mód: {pay}")
        y -= 5.5 * mm

    section("Nyilatkozat")
    wrapped(DECLARATION)

    # ── aláírások ──
    y -= 16 * mm
    c.setLineWidth(0.6)
    col_w = (right - left - 20 * mm) / 2
    for i, label in enumerate(("átadó (szerviz)", "átvevő (ügyfél)")):
        x0 = left + i * (col_w + 20 * mm)
        c.line(x0, y, x0 + col_w, y)
        c.setFont(FONT, 8)
        c.drawCentredString(x0 + col_w / 2, y - 4 * mm, label)
    y -= 12 * mm
    c.setFont(FONT, 7)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawString(left, y, "Egy példány az ügyfélé, egy a szervizé marad. " + (s.get("footer_text") or ""))
    c.showPage()
    c.save()
    return buf.getvalue()
