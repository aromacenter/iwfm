"""Gép-kihelyezési / visszavételi / csere JEGYZŐKÖNYV (A4 PDF) — x-presso jegy
f8e12fda (121. kör). A szerződés mellékleteként kezelendő, ezt a lap is kimondja.

``data`` kulcsai: serial, kind (deploy|return|swap), created_at, location,
company_name, company_address, company_tax_number,
partner_name, partner_address, partner_tax_number, partner_contact,
asset (dict: name, manufacturer, model, serial_number, barcode), counters (list),
counter_names (list), replacement (dict | None, csere), replacement_counters,
old_counters (csere: a leszerelt gép záró állásai), note, created_by_name,
on_site, signer_name, signature (data URL), employee_signature, signed_at.
``settings``: a munkalap-PDF beállításai (logó, cégnév, szín)."""

from __future__ import annotations

import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas

from app.services.wfm.worksheet_pdf import FONT, FONT_BOLD, _hex_rgb, _logo_image, _signature_image

TITLES = {
    "deploy": "KIHELYEZÉSI JEGYZŐKÖNYV",
    "return": "VISSZAVÉTELI JEGYZŐKÖNYV",
    "swap": "GÉPCSERE JEGYZŐKÖNYV",
}
KIND_LABEL = {"deploy": "kihelyezés", "return": "visszavétel", "swap": "gépcsere"}
ATTACHMENT_CLAUSE = (
    "Ez a jegyzőkönyv a Kihelyező és a Bérlő között létrejött gépkihelyezési / "
    "bérleti szerződés elválaszthatatlan melléklete. A felek a gép átadás-átvételét, "
    "állapotát és a számlálóállásokat a jelen jegyzőkönyvben rögzített tartalommal "
    "elfogadják."
)


def _counters_text(counters, names) -> str:
    if not counters:
        return "—"
    out = []
    for i, val in enumerate(counters):
        name = (names[i] if names and i < len(names) and names[i] else f"{i + 1}. számláló")
        out.append(f"{name}: {int(val):,}".replace(",", " "))
    return " · ".join(out)


def build_protocol_pdf(data: dict, settings: dict | None = None) -> bytes:
    s = settings or {}
    accent = _hex_rgb(s.get("accent_color"), (0.15, 0.25, 0.65))
    accent_tint = tuple(ch + (1 - ch) * 0.88 for ch in accent)
    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    left, right = 18 * mm, width - 18 * mm
    y = height - 18 * mm
    kind = data.get("kind") or "deploy"

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

    # ── fejléc (a munkalap/elismervény stílusa) ──
    logo = _logo_image(s.get("logo_bytes"))
    company_name = data.get("company_name") or s.get("company_name") or "X-admin"
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
    c.drawRightString(right, y, TITLES.get(kind, TITLES["deploy"]))
    c.setFont(FONT_BOLD, 11)
    c.drawRightString(right, y - 6 * mm, data.get("serial", ""))
    c.setFont(FONT, 8)
    c.drawRightString(right, y - 11 * mm, f"Kelt: {data.get('created_at', '')}")
    y -= 18 * mm
    c.setLineWidth(1)
    c.setStrokeColorRGB(*accent)
    c.line(left, y, right, y)
    c.setStrokeColorRGB(0, 0, 0)
    y -= 6 * mm
    c.setFont(FONT_BOLD, 8)
    c.setFillColorRGB(0.35, 0.1, 0.1)
    c.drawString(left, y, "SZERZŐDÉS MELLÉKLETE")
    c.setFillColorRGB(0, 0, 0)
    y -= 4.5 * mm
    wrapped(ATTACHMENT_CLAUSE, size=8, width_chars=120)

    # ── felek ──
    section("Kihelyező (tulajdonos / bérbeadó)")
    line("Cég:", company_name)
    line("Cím:", data.get("company_address") or s.get("company_address"))
    if data.get("company_tax_number"):
        line("Adószám:", data["company_tax_number"])
    section("Bérlő (partner)")
    line("Cég / név:", data.get("partner_name"))
    line("Cím:", data.get("partner_address"))
    line("Adószám:", data.get("partner_tax_number"))
    if data.get("partner_contact"):
        line("Kapcsolattartó:", data["partner_contact"])

    # ── gép(ek) ──
    def machine_block(title: str, m: dict | None, counters, names, counter_label: str) -> None:
        if not m:
            return
        section(title)
        line("Megnevezés:", m.get("name"))
        line("Gyártó / típus:", " ".join(x for x in (m.get("manufacturer"), m.get("model")) if x))
        line("Gyári szám:", m.get("serial_number"))
        line("Vonalkód / azonosító:", m.get("barcode"))
        line(counter_label, _counters_text(counters, names))

    if kind == "swap":
        machine_block("Leszerelt gép", data.get("asset"), data.get("old_counters") or data.get("counters"), data.get("counter_names"), "Záró számlálóállás:")
        machine_block("Cseregép (kihelyezve)", data.get("replacement"), data.get("replacement_counters"), data.get("replacement_counter_names"), "Induló számlálóállás:")
    else:
        machine_block("Gép", data.get("asset"), data.get("counters"), data.get("counter_names"),
                      "Számlálóállás átadáskor:" if kind == "deploy" else "Számlálóállás visszavételkor:")

    section("Esemény")
    line("Esemény:", KIND_LABEL.get(kind, kind).capitalize())
    line("Dátum:", data.get("created_at"))
    line("Helyszín:", data.get("location") or data.get("partner_address"))
    line("Helyszíni készítés:", "igen" if data.get("on_site") else "nem")
    if data.get("delivery_mode"):
        mode = "személyesen" if data["delivery_mode"] == "personal" else "futárszolgálattal"
        line("Átadás módja:", mode + (f" ({data['delivery_ref']})" if data.get("delivery_ref") else ""))
    line("Rögzítette:", data.get("created_by_name"))
    if data.get("note"):
        section("Megjegyzés")
        wrapped(str(data["note"]))

    # ── aláírások ──
    sig_y = 46 * mm
    box_w = 70 * mm
    for x, sig_key, label, name_key in (
        (left, "employee_signature", "Kihelyező képviselője", "created_by_name"),
        (right - box_w, "signature", "Bérlő (partner) aláírása", "signer_name"),
    ):
        img = _signature_image(data.get(sig_key))
        if img is not None:
            try:
                c.drawImage(img, x + 5 * mm, sig_y + 1.5 * mm, width=box_w - 10 * mm, height=16 * mm, preserveAspectRatio=True, mask="auto")
            except Exception:  # hibás képadat nem borítja a jegyzőkönyvet
                img = None
        c.line(x, sig_y, x + box_w, sig_y)
        c.setFont(FONT, 8)
        c.drawCentredString(x + box_w / 2, sig_y - 4.5 * mm, label)
        if data.get(name_key):
            c.setFont(FONT_BOLD, 8)
            c.drawCentredString(x + box_w / 2, sig_y - 8.5 * mm, str(data[name_key])[:40])
        if data.get("signed_at") and img is not None:
            c.setFont(FONT, 7)
            c.setFillColorRGB(0.35, 0.35, 0.4)
            c.drawCentredString(x + box_w / 2, sig_y - 12 * mm, f"Aláírva: {data['signed_at']} (helyszíni, mobilon)")
            c.setFillColorRGB(0, 0, 0)

    c.setFont(FONT, 7)
    c.setFillColorRGB(0.4, 0.4, 0.45)
    c.drawString(left, 12 * mm, f"{data.get('serial', '')} · generálva: {data.get('generated_at', '')} · a jegyzőkönyv a szerződés melléklete")
    c.showPage()
    c.save()
    return buf.getvalue()
