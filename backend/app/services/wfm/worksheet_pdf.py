"""Munkalap PDF generálás (reportlab, A4).

Magyar ékezetek (ő, ű) miatt TTF fontot regisztrálunk: a Docker image-ben
DejaVu Sans (fonts-dejavu-core), Windows fejlesztői gépen Arial. Ha egyik
sincs, Helvetica a tartalék (ott a kettős ékezet torzulhat — csak dev eset).

Az aláírások PNG data URL-ként érkeznek a képernyős aláírás-vászonról.
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas

logger = logging.getLogger(__name__)

# Beépített alapszövegek — a Beállításokban (worksheet_settings) felülírhatók.
DEFAULT_CUSTOMER_FOOTER = (
    "Garanciális feltételek.\n"
    "A készülék tárolása és szállítása 0 fok alatt fagyveszély miatt nem ajánlott! "
    "Az elvégzett munkára és a beépített alkatrészekre 6 hónap garanciát vállalunk. "
    "A garancia nem vonatkozik az aktuális javításkor nem érintett alkatrészekre, "
    "valamint a helytelen használatból, tisztítási hiányosságokból és a "
    "vízkövesedésből adódó meghibásodásokra."
)
# Felmérési díj (nettó Ft): ha az ügyfél az árajánlatból a javítást NEM kéri.
DEFAULT_SURVEY_FEE = 5000.0

DEFAULT_INTAKE_FOOTER = (
    "A javításra átvett gépet maximum 60 napig tároljuk, amennyiben a javításra "
    "nem kerül sor és a gép nem kerül átvételre úgy a gép tulajdonjoga az "
    "X-presso Kft-re száll."
)

_FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
]

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
for regular, bold in _FONT_CANDIDATES:
    if Path(regular).exists() and Path(bold).exists():
        try:
            pdfmetrics.registerFont(TTFont("Munkalap", regular))
            pdfmetrics.registerFont(TTFont("Munkalap-Bold", bold))
            FONT, FONT_BOLD = "Munkalap", "Munkalap-Bold"
            break
        except Exception:  # pragma: no cover - hibás fontfájl
            logger.warning("Font regisztráció sikertelen: %s", regular, exc_info=True)


def _signature_image(data_url: str | None) -> ImageReader | None:
    if not data_url or not data_url.startswith("data:image/png;base64,"):
        return None
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1])
        return ImageReader(io.BytesIO(raw))
    except Exception:
        return None


def _hex_rgb(value: str | None, fallback: tuple[float, float, float]) -> tuple[float, float, float]:
    """'#1e40af' → (0.11, 0.25, 0.68). Hibás érték esetén a fallback."""
    if value and value.startswith("#") and len(value) == 7:
        try:
            return tuple(int(value[i : i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]
        except ValueError:
            pass
    return fallback


def _logo_image(logo_bytes: bytes | None) -> ImageReader | None:
    if not logo_bytes:
        return None
    try:
        return ImageReader(io.BytesIO(logo_bytes))
    except Exception:
        return None


def build_worksheet_pdf(data: dict, settings: dict | None = None) -> bytes:
    """Munkalap PDF. ``data`` kulcsai: serial, task_title, task_description,
    due_date, status_label, employee_name, employee_code, job_title,
    work_description, materials [{name, qty, unit}], hours_spent,
    client_name, client_location, employee_signature, client_signature,
    comments [{author, text, at}], generated_at.

    ``settings`` (opcionális testreszabás): company_name, company_address,
    footer_text, accent_color (#hex), logo_bytes, show_materials, show_hours,
    show_client_signature, show_comments."""
    s = settings or {}
    accent = _hex_rgb(s.get("accent_color"), (0.15, 0.25, 0.65))
    show_materials = s.get("show_materials", True)
    show_hours = s.get("show_hours", True)
    show_client_signature = s.get("show_client_signature", True)
    show_comments = s.get("show_comments", True)

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

    def wrapped(text: str, *, font_size: int = 9, max_chars: int = 100) -> None:
        nonlocal y
        c.setFont(FONT, font_size)
        for paragraph in (text or "—").splitlines() or ["—"]:
            while len(paragraph) > max_chars:
                cut = paragraph.rfind(" ", 0, max_chars)
                cut = cut if cut > 0 else max_chars
                c.drawString(left, y, paragraph[:cut])
                paragraph = paragraph[cut:].lstrip()
                y -= 4.5 * mm
            c.drawString(left, y, paragraph)
            y -= 4.5 * mm

    accent_tint = tuple(ch + (1 - ch) * 0.88 for ch in accent)  # nagyon világos akcentus

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

    # ─── Fejléc (céglogo/cégnév vagy iwfm-alapértelmezés) ───
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
    c.drawRightString(right, y, data.get("title") or "MUNKALAP")
    c.setFont(FONT_BOLD, 11)
    c.drawRightString(right, y - 6 * mm, data.get("serial", ""))
    c.setFont(FONT, 8)
    c.drawRightString(right, y - 11 * mm, f"Kelt: {data.get('generated_at', '')}")
    y -= 18 * mm
    c.setLineWidth(1)
    c.setStrokeColorRGB(*accent)
    c.line(left, y, right, y)
    c.setStrokeColorRGB(0, 0, 0)
    y -= 8 * mm

    # ─── Feladat ───
    section("Feladat")
    line("Megnevezés:", data.get("task_title", ""))
    if data.get("task_description"):
        line("Leírás:", "")
        y += 5.5 * mm - 4.5 * mm  # vissza a leírás sorához
        wrapped(data["task_description"])
    line("Határidő:", data.get("due_date", ""))
    line("Státusz:", data.get("status_label", ""))

    # ─── Dolgozó ───
    section("Munkavégző")
    line("Név:", data.get("employee_name", ""))
    line("Törzsszám:", data.get("employee_code") or "—")
    line("Munkakör:", data.get("job_title") or "—")

    # ─── Ügyfél / helyszín (csak ha kitöltött) ───
    if data.get("client_name") or data.get("client_location"):
        section("Ügyfél / helyszín")
        if data.get("client_name"):
            line("Ügyfél:", data["client_name"])
        if data.get("client_location"):
            line("Helyszín:", data["client_location"])

    # ─── Elvégzett munka ───
    section("Elvégzett munka")
    wrapped(data.get("work_description", ""))
    if show_hours and data.get("hours_spent") is not None:
        y -= 1 * mm
        line("Ráfordított idő:", f"{data['hours_spent']:g} óra")

    # ─── Elvégzett munkák tételesen (soronkénti munkadíjjal) ───
    works = data.get("works") or []
    if works:
        works_field = data.get("works_price_field") or "price_net"
        section("Elvégzett munkák (tételes)")
        c.setFont(FONT_BOLD, 9)
        c.drawString(left, y, "Megnevezés")
        c.drawRightString(right, y, data.get("works_price_label") or "Munkadíj (Ft, nettó)")
        y -= 5 * mm
        c.setFont(FONT, 9)
        works_total = 0.0
        for item in works[:25]:
            c.drawString(left, y, str(item.get("name", ""))[:70])
            price = item.get(works_field)
            if price is not None:
                works_total += float(price)
                c.drawRightString(right, y, f"{float(price):,.0f} Ft".replace(",", " "))
            y -= 4.5 * mm
        if works_total > 0:
            c.setFont(FONT_BOLD, 9)
            c.drawRightString(
                right, y, f"Munkadíj összesen (nettó): {works_total:,.0f} Ft".replace(",", " ")
            )
            y -= 5 * mm

    # ─── Javítási konstrukciók (alternatív ajánlatok — nem összegződnek) ───
    repair_options = data.get("repair_options") or []
    if repair_options:
        r_field = data.get("works_price_field") or "price_net"
        section("Javítási konstrukciók (ajánlat)")
        c.setFont(FONT_BOLD, 9)
        c.drawString(left, y, "Konstrukció")
        c.drawRightString(right, y, "Ár (Ft, nettó)")
        y -= 5 * mm
        c.setFont(FONT, 9)
        for item in repair_options[:25]:
            c.drawString(left, y, str(item.get("name", ""))[:70])
            price = item.get(r_field)
            if price is not None:
                c.drawRightString(right, y, f"{float(price):,.0f} Ft".replace(",", " "))
            y -= 4.5 * mm
        y -= 1 * mm

    # ─── Anyagok / tételek ───
    materials = data.get("materials") or [] if show_materials else []
    # Külső szervizes munkalapon ár-oszlop: belső példányon a szerviz nettó
    # költsége (cost_net), ügyfél-példányon a mi áraink (price_net).
    price_col = data.get("price_column")  # {"field": ..., "label": ...} | None
    if materials:
        section("Felhasznált anyagok / tételek" if price_col else "Felhasznált anyagok")
        c.setFont(FONT_BOLD, 9)
        c.drawString(left, y, "Megnevezés")
        c.drawString(left + 95 * mm, y, "Mennyiség")
        c.drawString(left + 125 * mm, y, "Egység")
        if price_col:
            c.drawRightString(right, y, price_col["label"])
        y -= 5 * mm
        c.setFont(FONT, 9)
        total = 0.0
        for item in materials[:25]:
            c.drawString(left, y, str(item.get("name", ""))[:60])
            c.drawString(left + 95 * mm, y, str(item.get("qty", "")))
            c.drawString(left + 125 * mm, y, str(item.get("unit", "")))
            if price_col:
                price = item.get(price_col["field"])
                if price is not None:
                    try:
                        qty_num = float(str(item.get("qty", "1")).replace(",", "."))
                    except ValueError:
                        qty_num = 1.0
                    amount = float(price) * (qty_num if qty_num > 0 else 1.0)
                    total += amount
                    c.drawRightString(right, y, f"{amount:,.0f} Ft".replace(",", " "))
            y -= 4.5 * mm
        if price_col and total > 0:
            c.setFont(FONT_BOLD, 9)
            c.drawRightString(right, y, f"Összesen (nettó): {total:,.0f} Ft".replace(",", " "))
            y -= 5 * mm

    # ─── Megjegyzések ───
    comments = data.get("comments") or [] if show_comments else []
    if comments:
        section("Megjegyzések")
        for comment in comments[:10]:
            wrapped(f"{comment.get('author') or '?'}: {comment.get('text', '')}")

    # ─── Aláírások (lap alján) ───
    sig_y = 38 * mm
    box_w = 70 * mm
    signature_boxes = [(left, "employee_signature", "Munkavégző aláírása")]
    if show_client_signature:
        signature_boxes.append((right - box_w, "client_signature", "Ügyfél / átvevő aláírása"))
    for x, sig_key, label in signature_boxes:
        img = _signature_image(data.get(sig_key))
        if img is not None:
            c.drawImage(
                img, x + 5 * mm, sig_y + 2 * mm, width=box_w - 10 * mm, height=18 * mm,
                preserveAspectRatio=True, mask="auto",
            )
        c.line(x, sig_y, x + box_w, sig_y)
        c.setFont(FONT, 8)
        c.drawCentredString(x + box_w / 2, sig_y - 4.5 * mm, label)

    # ─── Extra lábléc-blokk (pl. garanciális feltételek az ügyfél-példányon) ───
    extra_footer = data.get("extra_footer")
    if extra_footer:
        c.setFont(FONT, 7)
        c.setFillColorRGB(0.25, 0.25, 0.3)
        fy = 31 * mm
        for paragraph in str(extra_footer).splitlines():
            text = paragraph.strip()
            if not text:
                continue
            while text and fy >= 19 * mm:
                if len(text) <= 125:
                    cut = len(text)
                else:
                    cut = text.rfind(" ", 0, 125)
                    cut = cut if cut > 0 else 125
                c.drawString(left, fy, text[:cut])
                text = text[cut:].lstrip()
                fy -= 3.2 * mm
            if fy < 19 * mm:
                break
        c.setFillColorRGB(0, 0, 0)

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
        f"{data.get('serial', '')} · generálva: {data.get('generated_at', '')}",
    )

    c.showPage()
    c.save()
    return buf.getvalue()


def build_intake_pdf(data: dict, settings: dict | None = None) -> bytes:
    """Átvételi elismervény PDF. ``data`` kulcsai: serial, received_at,
    client_name, client_phone, asset_name, asset_manufacturer, asset_serial,
    asset_barcode, accessories, faults, note, received_by_name, generated_at,
    footer_clause (a 60 napos záradék — beállításból vagy alapszöveg).

    ``settings``: a munkalap-PDF beállításai (céglogo, cégnév, szín)."""
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
        c.setFont(FONT_BOLD, 9)
        c.drawString(left, y, label)
        c.setFont(FONT, 9)
        c.drawString(left + label_w, y, value or "—")
        y -= 5.5 * mm

    def wrapped(text: str) -> None:
        nonlocal y
        c.setFont(FONT, 9)
        for paragraph in (text or "—").splitlines() or ["—"]:
            while len(paragraph) > 100:
                cut = paragraph.rfind(" ", 0, 100)
                cut = cut if cut > 0 else 100
                c.drawString(left, y, paragraph[:cut])
                paragraph = paragraph[cut:].lstrip()
                y -= 4.5 * mm
            c.drawString(left, y, paragraph)
            y -= 4.5 * mm

    accent_tint = tuple(ch + (1 - ch) * 0.88 for ch in accent)

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

    # Fejléc — a munkalap-PDF-fel azonos stílus
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
        c.drawString(left, y, company_name or "X-admin")
        c.setFillColorRGB(0.35, 0.4, 0.5)
        c.setFont(FONT, 8)
        c.drawString(left, y - 4.5 * mm, s.get("company_address") or "")
    elif company_name:
        c.setFillColorRGB(0.35, 0.4, 0.5)
        c.setFont(FONT, 8)
        c.drawString(left, y - 12 * mm, company_name)

    c.setFillColorRGB(0, 0, 0)
    c.setFont(FONT_BOLD, 16)
    c.drawRightString(right, y, "ÁTVÉTELI ELISMERVÉNY")
    c.setFont(FONT_BOLD, 11)
    c.drawRightString(right, y - 6 * mm, data.get("serial", ""))
    c.setFont(FONT, 8)
    c.drawRightString(right, y - 11 * mm, f"Átvétel dátuma: {data.get('received_at', '')}")
    y -= 18 * mm
    c.setLineWidth(1)
    c.setStrokeColorRGB(*accent)
    c.line(left, y, right, y)
    c.setStrokeColorRGB(0, 0, 0)
    y -= 8 * mm

    section("Ügyfél")
    line("Név:", data.get("client_name") or "—")
    if data.get("client_company"):
        line("Cégnév:", data["client_company"])
    if data.get("client_phone"):
        line("Telefon:", data["client_phone"])
    if data.get("client_email"):
        line("E-mail:", data["client_email"])
    if data.get("client_address"):
        line("Cím:", data["client_address"])

    section("Átvett gép")
    line("Megnevezés:", data.get("asset_name") or "—")
    line("Gyártó / márka:", data.get("asset_manufacturer") or "—")
    line("Gyári szám:", data.get("asset_serial") or "—")
    line("Vonalkód:", data.get("asset_barcode") or "—")

    section("Tartozékok")
    wrapped(data.get("accessories") or "—")

    section("Bejelentett hibák / állapot")
    wrapped(data.get("faults") or "—")

    if data.get("note"):
        section("Megjegyzés")
        wrapped(data["note"])

    line_y = y - 4 * mm
    c.setFont(FONT, 9)
    c.drawString(left, line_y, f"Átvette: {data.get('received_by_name') or '—'}")

    # Aláírás-vonalak a lap alján
    sig_y = 46 * mm
    box_w = 70 * mm
    for x, label in ((left, "Átadó (ügyfél) aláírása"), (right - box_w, "Átvevő aláírása")):
        c.line(x, sig_y, x + box_w, sig_y)
        c.setFont(FONT, 8)
        c.drawCentredString(x + box_w / 2, sig_y - 4.5 * mm, label)

    # A 60 napos tárolási záradék — KÖTELEZŐ elem az elismervény alján
    clause = data.get("footer_clause") or DEFAULT_INTAKE_FOOTER
    c.setFont(FONT_BOLD, 8)
    c.setFillColorRGB(0.15, 0.15, 0.2)
    fy = 33 * mm
    text_all = str(clause)
    for paragraph in text_all.splitlines():
        text = paragraph.strip()
        if not text:
            continue
        while text and fy >= 18 * mm:
            if len(text) <= 110:
                cut = len(text)
            else:
                cut = text.rfind(" ", 0, 110)
                cut = cut if cut > 0 else 110
            c.drawString(left, fy, text[:cut])
            text = text[cut:].lstrip()
            fy -= 3.6 * mm
        if fy < 18 * mm:
            break

    c.setFont(FONT, 7)
    c.setFillColorRGB(0.5, 0.5, 0.55)
    c.drawCentredString(
        width / 2, 12 * mm,
        f"{data.get('serial', '')} · generálva: {data.get('generated_at', '')}",
    )
    c.showPage()
    c.save()
    return buf.getvalue()
