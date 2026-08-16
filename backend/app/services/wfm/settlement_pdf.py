"""Elszámolási bizonylat PDF (reportlab, A4).

A munkalap-PDF-fel közös font-regisztrációt és segédeket használja
(worksheet_pdf), a cég-arculat (logó, szín, lábléc) szintén a
WorksheetSettings-ből jön. A partner aláírása PNG data URL-ként érkezik a
képernyős aláírás-vászonról.
"""

from __future__ import annotations

import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas

from app.services.wfm.worksheet_pdf import (
    FONT,
    FONT_BOLD,
    _hex_rgb,
    _logo_image,
    _signature_image,
)

PAYMENT_LABELS = {
    "cash": "Készpénz", "card": "Bankkártya", "transfer": "Átutalás", "cod": "Utánvét",
}


def _fmt_money(value: float) -> str:
    return f"{value:,.0f} Ft".replace(",", " ")


def _fmt_qty(value: float) -> str:
    return f"{value:g}"


def build_settlement_pdf(data: dict, settings: dict | None = None) -> bytes:
    """Bizonylat PDF. ``data`` kulcsai: receipt_no, created_at, partner_name,
    partner_code, partner_address, partner_tax_number, settled_by_name,
    payment_method, note, lines [{product_name, previous_qty, physical_qty,
    consumed_qty, portions, price_per_portion, amount_net, amount_gross}],
    total_net, total_gross, invoiced, billingo_document_id, partner_signature,
    generated_at.

    ``settings``: company_name, company_address, footer_text, accent_color,
    logo_bytes (a munkalap-beállításokból)."""
    s = settings or {}
    accent = _hex_rgb(s.get("accent_color"), (0.15, 0.25, 0.65))
    accent_tint = tuple(ch + (1 - ch) * 0.88 for ch in accent)

    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    left = 18 * mm
    right = width - 18 * mm
    y = height - 18 * mm

    def line(label: str, value: str, *, label_w: float = 42 * mm) -> None:
        nonlocal y
        c.setFont(FONT_BOLD, 9)
        c.drawString(left, y, label)
        c.setFont(FONT, 9)
        c.drawString(left + label_w, y, value or "—")
        y -= 5.5 * mm

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

    # ─── Fejléc ───
    logo = _logo_image(s.get("logo_bytes"))
    company_name = s.get("company_name")
    if logo is not None:
        try:
            c.drawImage(
                logo, left, y - 9 * mm, width=48 * mm, height=14 * mm,
                preserveAspectRatio=True, anchor="sw", mask="auto",
            )
        except Exception:  # pragma: no cover - hibás képadat
            logo = None
    if logo is None:
        c.setFillColorRGB(*accent)
        c.setFont(FONT_BOLD, 22)
        c.drawString(left, y, company_name or "iwfm")
        c.setFillColorRGB(0.35, 0.4, 0.5)
        c.setFont(FONT, 8)
        c.drawString(left, y - 4.5 * mm, s.get("company_address") or "Intelligence Workforce Management")
    elif company_name:
        c.setFillColorRGB(0.35, 0.4, 0.5)
        c.setFont(FONT, 8)
        c.drawString(left, y - 12 * mm, company_name)

    c.setFillColorRGB(0, 0, 0)
    c.setFont(FONT_BOLD, 16)
    c.drawRightString(right, y, "ELSZÁMOLÁSI BIZONYLAT")
    c.setFont(FONT_BOLD, 11)
    c.drawRightString(right, y - 6 * mm, data.get("receipt_no", ""))
    c.setFont(FONT, 8)
    c.drawRightString(right, y - 11 * mm, f"Kelt: {data.get('created_at', '')}")
    y -= 18 * mm
    c.setLineWidth(1)
    c.setStrokeColorRGB(*accent)
    c.line(left, y, right, y)
    c.setStrokeColorRGB(0, 0, 0)
    y -= 8 * mm

    # ─── Partner ───
    section("Partner (külső raktár)")
    line("Név:", data.get("partner_name", ""))
    if data.get("partner_code"):
        line("Azonosító:", data["partner_code"])
    if data.get("partner_address"):
        line("Cím:", data["partner_address"])
    if data.get("partner_tax_number"):
        line("Adószám:", data["partner_tax_number"])

    # ─── Elszámolás adatai ───
    section("Elszámolás")
    if data.get("previous_at"):
        line("Előző elszámolás:", data["previous_at"])
    line("Elszámolást végezte:", data.get("settled_by_name", ""))
    line("Fizetési mód:", PAYMENT_LABELS.get(data.get("payment_method", ""), data.get("payment_method", "")))
    if data.get("invoiced"):
        doc_id = data.get("billingo_document_id")
        line("Számla:", f"kiállítva (Billingó #{doc_id})" if doc_id else "kiállítva")
    if data.get("note"):
        line("Megjegyzés:", str(data["note"])[:90])

    # ─── Gépek (gép-soros elszámolás) ───
    machines = data.get("machines") or []
    if machines:
        section("Gépek")
        m_headers = [
            ("Vonalkód", left),
            ("Gép", left + 30 * mm),
            ("Előző", left + 78 * mm),
            ("Számláló", left + 96 * mm),
            ("Szerviz", left + 116 * mm),
            ("Adag", left + 132 * mm),
            ("Nettó", right - 22 * mm),
        ]
        c.setFont(FONT_BOLD, 8.5)
        for label, x in m_headers:
            if label == "Nettó":
                c.drawRightString(x + 22 * mm, y, label)
            else:
                c.drawString(x, y, label)
        y -= 5 * mm
        c.setFont(FONT, 8.5)
        for m in machines[:20]:
            c.drawString(left, y, str(m.get("barcode", ""))[:16])
            name = str(m.get("asset_name", ""))[:26]
            if m.get("discount_pct"):
                name += f" (−{m['discount_pct']:g}%)"
            c.drawString(left + 30 * mm, y, name)
            c.drawString(left + 78 * mm, y, str(m.get("prev_counter", 0)))
            c.drawString(left + 96 * mm, y, str(m.get("new_counter", 0)))
            c.drawString(left + 116 * mm, y, str(m.get("service_portions", 0)))
            c.drawString(left + 132 * mm, y, f"{m.get('portions_billed', 0):.0f}")
            c.drawRightString(right, y, _fmt_money(m.get("amount_net", 0)))
            y -= 4.5 * mm

    # ─── Tételek ───
    section("Tételek")
    headers = [
        ("Termék", left),
        ("Előző", left + 62 * mm),
        ("Leltár", left + 82 * mm),
        ("Fogyás", left + 102 * mm),
        ("Adag", left + 122 * mm),
        ("Ft/adag", left + 138 * mm),
        ("Nettó", right - 22 * mm),
    ]
    c.setFont(FONT_BOLD, 8.5)
    for label, x in headers:
        if label in ("Nettó",):
            c.drawRightString(x + 22 * mm, y, label)
        else:
            c.drawString(x, y, label)
    y -= 5 * mm
    c.setFont(FONT, 8.5)
    for item in (data.get("lines") or [])[:30]:
        c.drawString(left, y, str(item.get("product_name", ""))[:38])
        c.drawString(left + 62 * mm, y, _fmt_qty(item.get("previous_qty", 0)))
        c.drawString(left + 82 * mm, y, _fmt_qty(item.get("physical_qty", 0)))
        c.drawString(left + 102 * mm, y, _fmt_qty(item.get("consumed_qty", 0)))
        c.drawString(left + 122 * mm, y, f"{item.get('portions', 0):.0f}")
        c.drawString(left + 138 * mm, y, _fmt_money(item.get("price_per_portion", 0)))
        c.drawRightString(right, y, _fmt_money(item.get("amount_net", 0)))
        y -= 4.5 * mm

    y -= 2 * mm
    c.setLineWidth(0.5)
    c.line(left, y + 1 * mm, right, y + 1 * mm)
    y -= 4 * mm
    c.setFont(FONT_BOLD, 10)
    c.drawString(left, y, "Összesen (nettó):")
    c.drawRightString(right, y, _fmt_money(data.get("total_net", 0)))
    y -= 6 * mm
    c.drawString(left, y, "Összesen (bruttó):")
    c.drawRightString(right, y, _fmt_money(data.get("total_gross", 0)))
    # Tartozás-görgetés: korábbi egyenleg + összes fizetendő + fizetve
    debt_before = data.get("debt_before")
    paid_amount = data.get("paid_amount")
    if debt_before:
        y -= 6 * mm
        c.drawString(left, y, "Korábbi tartozás:")
        c.drawRightString(right, y, _fmt_money(debt_before))
        y -= 6 * mm
        c.drawString(left, y, "Összes fizetendő:")
        c.drawRightString(right, y, _fmt_money(data.get("total_gross", 0) + debt_before))
    if paid_amount is not None:
        y -= 6 * mm
        c.drawString(left, y, "Fizetve:")
        c.drawRightString(right, y, _fmt_money(paid_amount))
        remaining = data.get("total_gross", 0) + (debt_before or 0) - paid_amount
        if remaining > 0.5:
            y -= 6 * mm
            c.setFillColorRGB(0.7, 0.1, 0.1)
            c.drawString(left, y, "Fennmaradó tartozás:")
            c.drawRightString(right, y, _fmt_money(remaining))
            c.setFillColorRGB(0, 0, 0)

    # ─── Aláírások (lap alján) ───
    sig_y = 38 * mm
    box_w = 70 * mm
    for x, sig_key, label in (
        (left, "settled_by_signature", "Elszámolást végezte"),
        (right - box_w, "partner_signature", "Partner aláírása"),
    ):
        img = _signature_image(data.get(sig_key))
        if img is not None:
            c.drawImage(
                img, x + 5 * mm, sig_y + 2 * mm, width=box_w - 10 * mm, height=18 * mm,
                preserveAspectRatio=True, mask="auto",
            )
        elif sig_key == "settled_by_signature":
            # aláírás-kép híján a név kerül a vonal fölé
            c.setFont(FONT, 9)
            c.drawCentredString(x + box_w / 2, sig_y + 2 * mm, data.get("settled_by_name", ""))
        c.line(x, sig_y, x + box_w, sig_y)
        c.setFont(FONT, 8)
        c.drawCentredString(x + box_w / 2, sig_y - 4.5 * mm, label)

    # ─── Lábléc ───
    footer_text = s.get("footer_text")
    if footer_text:
        c.setFont(FONT, 8)
        c.setFillColorRGB(0.3, 0.3, 0.35)
        c.drawCentredString(width / 2, 17 * mm, footer_text[:160])
    c.setFont(FONT, 7)
    c.setFillColorRGB(0.5, 0.5, 0.55)
    c.drawCentredString(
        width / 2, 12 * mm,
        f"{data.get('receipt_no', '')} · generálva: {data.get('generated_at', '')}",
    )

    c.showPage()
    c.save()
    return buf.getvalue()
