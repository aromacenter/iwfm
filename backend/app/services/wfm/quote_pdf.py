"""Hivatalos javítási árajánlat PDF (145. kör, x-presso jegy 7ca9c6f6).

Konstrukciónként külön A4-es, letölthető és nyomtatható ajánlat: sorszám
(AJ-ÉÉÉÉ-NNNN / konstrukció sorszáma), 10 munkanapos érvényesség, az ügyfél,
a gép és a munkalap adatai, a konstrukció nettó/ÁFA/bruttó ára, PO-szám (ha az
ügyfél megadta), valamint az ügyvezető aláírása és a cég bélyegzője (a
Beállításokban feltöltött képek). A céglogó/cégnév/szín a munkalap-PDF
beállításaiból jön."""

from __future__ import annotations

import io
from datetime import date, timedelta

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas

from app.services.wfm.worksheet_pdf import FONT, FONT_BOLD, _hex_rgb, _logo_image

QUOTE_VALID_WORKING_DAYS = 10
VAT_PERCENT = 27


def add_working_days(start: date, days: int) -> date:
    """`days` munkanap (hétfő–péntek) hozzáadása — a kezdőnap nem számít."""
    d = start
    left = days
    while left > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            left -= 1
    return d


def _money(value: float) -> str:
    return f"{value:,.0f} Ft".replace(",", " ")


def build_quote_pdf(data: dict, settings: dict | None = None) -> bytes:
    """``data``: quote_serial, option_index (1-től), option_count, option_name,
    price_net, issued_at (ISO dátum), valid_until (ISO dátum), worksheet_serial,
    client_name, client_location, machine, po_number, public_note,
    accepted (bool), accepted_by, accepted_at.
    ``settings``: company_name, company_address, footer_text, accent_color,
    logo_bytes, signature_bytes, stamp_bytes, signer_name, signer_title."""
    s = settings or {}
    accent = _hex_rgb(s.get("accent_color"), (0.15, 0.25, 0.65))
    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    left = 18 * mm
    right = width - 18 * mm
    y = height - 18 * mm

    def line(label: str, value: str, *, label_w: float = 42 * mm) -> None:
        nonlocal y
        c.setFont(FONT_BOLD, 9.5)
        c.drawString(left, y, label)
        c.setFont(FONT, 9.5)
        c.drawString(left + label_w, y, value or "—")
        y -= 6 * mm

    def wrapped(text: str, *, size: float = 9.5, max_chars: int = 95) -> None:
        nonlocal y
        c.setFont(FONT, size)
        for paragraph in (text or "").splitlines() or [""]:
            while len(paragraph) > max_chars:
                cut = paragraph.rfind(" ", 0, max_chars)
                cut = cut if cut > 0 else max_chars
                c.drawString(left, y, paragraph[:cut])
                paragraph = paragraph[cut:].lstrip()
                y -= 4.8 * mm
            c.drawString(left, y, paragraph)
            y -= 4.8 * mm

    def section(title: str) -> None:
        nonlocal y
        y -= 3 * mm
        c.setFillColorRGB(*tuple(ch + (1 - ch) * 0.88 for ch in accent))
        c.rect(left, y - 1.5 * mm, right - left, 6.5 * mm, fill=1, stroke=0)
        c.setFillColorRGB(*accent)
        c.setFont(FONT_BOLD, 10)
        c.drawString(left + 2 * mm, y, title)
        c.setFillColorRGB(0, 0, 0)
        y -= 8.5 * mm

    # ─── Fejléc ───
    logo = _logo_image(s.get("logo_bytes"))
    company_name = s.get("company_name") or "X-admin"
    if logo is not None:
        try:
            c.drawImage(logo, left, y - 9 * mm, width=48 * mm, height=14 * mm,
                        preserveAspectRatio=True, anchor="sw", mask="auto")
        except Exception:  # pragma: no cover - hibás képadat
            logo = None
    if logo is None:
        c.setFillColorRGB(*accent)
        c.setFont(FONT_BOLD, 20)
        c.drawString(left, y, company_name)
    c.setFillColorRGB(0.35, 0.4, 0.5)
    c.setFont(FONT, 8)
    c.drawString(left, y - 13 * mm, " · ".join(x for x in (company_name if logo is not None else None, s.get("company_address")) if x))
    c.setFillColorRGB(0, 0, 0)
    c.setFont(FONT_BOLD, 16)
    c.drawRightString(right, y, "JAVÍTÁSI ÁRAJÁNLAT")
    serial = f"{data.get('quote_serial') or ''}"
    if data.get("option_count", 1) > 1:
        serial += f" / {data.get('option_index', 1)}"
    c.setFont(FONT_BOLD, 11)
    c.drawRightString(right, y - 6 * mm, serial)
    c.setFont(FONT, 8)
    c.drawRightString(right, y - 11 * mm, f"Kelt: {data.get('issued_at', '')}")
    c.drawRightString(right, y - 15 * mm, f"Érvényes: {data.get('valid_until', '')}-ig ({QUOTE_VALID_WORKING_DAYS} munkanap)")
    y -= 22 * mm
    c.setLineWidth(1)
    c.setStrokeColorRGB(*accent)
    c.line(left, y, right, y)
    c.setStrokeColorRGB(0, 0, 0)
    y -= 8 * mm

    # ─── Ügyfél / gép ───
    section("Ügyfél és készülék")
    line("Ügyfél:", data.get("client_name") or "—")
    if data.get("client_location"):
        line("Cím / helyszín:", data["client_location"])
    line("Készülék:", data.get("machine") or "—")
    line("Munkalap:", data.get("worksheet_serial") or "—")
    if data.get("po_number"):
        line("Megrendelői PO-szám:", data["po_number"])

    # ─── Konstrukció ───
    section("Ajánlott javítási konstrukció")
    c.setFont(FONT_BOLD, 11)
    c.drawString(left, y, str(data.get("option_name") or "")[:110])
    y -= 8 * mm
    net = float(data.get("price_net") or 0)
    vat = round(net * VAT_PERCENT / 100)
    gross = net + vat
    c.setFont(FONT, 9.5)
    for label, val in (("Nettó ár:", _money(net)), (f"ÁFA ({VAT_PERCENT} %):", _money(vat))):
        c.drawString(left + 4 * mm, y, label)
        c.drawRightString(right, y, val)
        y -= 5.5 * mm
    c.setFont(FONT_BOLD, 11)
    c.drawString(left + 4 * mm, y, "Fizetendő (bruttó):")
    c.drawRightString(right, y, _money(gross))
    y -= 9 * mm
    if data.get("public_note"):
        wrapped(str(data["public_note"]), size=9)
        y -= 2 * mm

    # ─── Feltételek ───
    section("Feltételek")
    wrapped(
        f"Az ajánlat a keltétől számított {QUOTE_VALID_WORKING_DAYS} munkanapig, "
        f"{data.get('valid_until', '')}-ig érvényes. Az ár a fent megnevezett konstrukció "
        "szerinti javítást tartalmazza; a javítás során felmerülő, előre nem látható "
        "többletmunkáról külön egyeztetünk. Az elfogadott ajánlat sorszáma és a megadott "
        "PO-szám a munkalapon és a számlán is szerepel."
    )
    if data.get("accepted"):
        y -= 2 * mm
        c.setFont(FONT_BOLD, 9.5)
        c.drawString(
            left, y,
            f"Elfogadva: {data.get('accepted_by') or '—'}"
            + (f" ({str(data.get('accepted_at') or '')[:16].replace('T', ' ')})" if data.get("accepted_at") else ""),
        )
        y -= 6 * mm

    # ─── Aláírás + bélyegző ───
    sig_y = max(y - 10 * mm, 42 * mm)
    y = sig_y
    signature = _logo_image(s.get("signature_bytes"))
    stamp = _logo_image(s.get("stamp_bytes"))
    box_w = 70 * mm
    if signature is not None:
        try:
            c.drawImage(signature, left, y - 2 * mm, width=box_w, height=20 * mm,
                        preserveAspectRatio=True, anchor="sw", mask="auto")
        except Exception:  # pragma: no cover
            pass
    if stamp is not None:
        try:
            c.drawImage(stamp, right - 40 * mm, y - 6 * mm, width=40 * mm, height=30 * mm,
                        preserveAspectRatio=True, anchor="sw", mask="auto")
        except Exception:  # pragma: no cover
            pass
    y -= 4 * mm
    c.setLineWidth(0.6)
    c.line(left, y, left + box_w, y)
    y -= 4.5 * mm
    c.setFont(FONT_BOLD, 9)
    c.drawString(left, y, s.get("signer_name") or company_name)
    y -= 4.5 * mm
    c.setFont(FONT, 8)
    c.drawString(left, y, s.get("signer_title") or "ügyvezető")
    if stamp is None:
        c.setFillColorRGB(0.6, 0.6, 0.65)
        c.drawRightString(right, y, "P. H.")
        c.setFillColorRGB(0, 0, 0)

    # ─── Lábléc ───
    c.setFont(FONT, 7)
    c.setFillColorRGB(0.5, 0.5, 0.55)
    footer = s.get("footer_text") or ""
    if footer:
        c.drawCentredString(width / 2, 16 * mm, str(footer)[:160])
    c.drawCentredString(width / 2, 12 * mm, f"{serial} · {data.get('worksheet_serial') or ''} · generálva: {data.get('generated_at', '')}")
    c.showPage()
    c.save()
    return buf.getvalue()
