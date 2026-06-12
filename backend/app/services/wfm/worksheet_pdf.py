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


def build_worksheet_pdf(data: dict) -> bytes:
    """Munkalap PDF. ``data`` kulcsai: serial, task_title, task_description,
    due_date, status_label, employee_name, employee_code, job_title,
    work_description, materials [{name, qty, unit}], hours_spent,
    client_name, client_location, employee_signature, client_signature,
    comments [{author, text, at}], generated_at."""
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

    def section(title: str) -> None:
        nonlocal y
        y -= 3 * mm
        c.setFillColorRGB(0.93, 0.94, 0.98)
        c.rect(left, y - 1.5 * mm, right - left, 6.5 * mm, fill=1, stroke=0)
        c.setFillColorRGB(0.15, 0.2, 0.45)
        c.setFont(FONT_BOLD, 10)
        c.drawString(left + 2 * mm, y, title)
        c.setFillColorRGB(0, 0, 0)
        y -= 8 * mm

    # ─── Fejléc ───
    c.setFillColorRGB(0.15, 0.25, 0.65)
    c.setFont(FONT_BOLD, 22)
    c.drawString(left, y, "iwfm")
    c.setFillColorRGB(0.35, 0.4, 0.5)
    c.setFont(FONT, 8)
    c.drawString(left, y - 4.5 * mm, "Intelligence Workforce Management")
    c.setFillColorRGB(0, 0, 0)
    c.setFont(FONT_BOLD, 16)
    c.drawRightString(right, y, "MUNKALAP")
    c.setFont(FONT_BOLD, 11)
    c.drawRightString(right, y - 6 * mm, data.get("serial", ""))
    c.setFont(FONT, 8)
    c.drawRightString(right, y - 11 * mm, f"Kelt: {data.get('generated_at', '')}")
    y -= 18 * mm
    c.setLineWidth(1)
    c.setStrokeColorRGB(0.15, 0.25, 0.65)
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
    if data.get("hours_spent") is not None:
        y -= 1 * mm
        line("Ráfordított idő:", f"{data['hours_spent']:g} óra")

    # ─── Anyagok ───
    materials = data.get("materials") or []
    if materials:
        section("Felhasznált anyagok")
        c.setFont(FONT_BOLD, 9)
        c.drawString(left, y, "Megnevezés")
        c.drawString(left + 110 * mm, y, "Mennyiség")
        c.drawString(left + 145 * mm, y, "Egység")
        y -= 5 * mm
        c.setFont(FONT, 9)
        for item in materials[:25]:
            c.drawString(left, y, str(item.get("name", ""))[:70])
            c.drawString(left + 110 * mm, y, str(item.get("qty", "")))
            c.drawString(left + 145 * mm, y, str(item.get("unit", "")))
            y -= 4.5 * mm

    # ─── Megjegyzések ───
    comments = data.get("comments") or []
    if comments:
        section("Megjegyzések")
        for comment in comments[:10]:
            wrapped(f"{comment.get('author') or '?'}: {comment.get('text', '')}")

    # ─── Aláírások (lap alján) ───
    sig_y = 38 * mm
    box_w = 70 * mm
    for x, sig_key, label in (
        (left, "employee_signature", "Munkavégző aláírása"),
        (right - box_w, "client_signature", "Ügyfél / átvevő aláírása"),
    ):
        img = _signature_image(data.get(sig_key))
        if img is not None:
            c.drawImage(
                img, x + 5 * mm, sig_y + 2 * mm, width=box_w - 10 * mm, height=18 * mm,
                preserveAspectRatio=True, mask="auto",
            )
        c.line(x, sig_y, x + box_w, sig_y)
        c.setFont(FONT, 8)
        c.drawCentredString(x + box_w / 2, sig_y - 4.5 * mm, label)

    c.setFont(FONT, 7)
    c.setFillColorRGB(0.5, 0.5, 0.55)
    c.drawCentredString(
        width / 2, 12 * mm,
        f"Iwfm munkalap · {data.get('serial', '')} · generálva: {data.get('generated_at', '')}",
    )

    c.showPage()
    c.save()
    return buf.getvalue()
