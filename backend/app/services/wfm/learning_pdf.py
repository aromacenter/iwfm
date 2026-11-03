"""X-Learning oklevél PDF (142. kör): egyoldalas, cégnév/logó, dolgozó, tananyag,
dátum, érvényesség, sorszám (XL-ÉÉÉÉ-NNNN)."""

from __future__ import annotations

import io
from datetime import date

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LearningAssignment, WorksheetSettings
from app.services.wfm.worksheet_pdf import FONT, FONT_BOLD, _hex_rgb, _logo_image


async def certificate_serial(db: AsyncSession, assignment: LearningAssignment) -> str:
    """Sorszám: az adott évben elvégzett kiosztások közt a hely (completed_at sorrend)."""
    year = assignment.completed_at.year
    n = (await db.execute(
        select(sa_func.count()).select_from(LearningAssignment).where(
            LearningAssignment.completed_at.is_not(None),
            sa_func.extract("year", LearningAssignment.completed_at) == year,
            LearningAssignment.completed_at <= assignment.completed_at,
        )
    )).scalar_one()
    return f"XL-{year}-{int(n or 1):04d}"


async def build_certificate_pdf(db: AsyncSession, assignment, material, employee) -> tuple[bytes, str]:
    ws = (await db.execute(select(WorksheetSettings).where(WorksheetSettings.id == 1))).scalar_one_or_none()
    company = (ws.company_name if ws and ws.company_name else None) or "X-admin"
    accent = _hex_rgb(ws.accent_color if ws else None, (0.12, 0.25, 0.69))
    logo = _logo_image(bytes(ws.logo_data)) if ws and ws.logo_data else None
    serial = await certificate_serial(db, assignment)

    buf = io.BytesIO()
    w, h = landscape(A4)
    c = pdf_canvas.Canvas(buf, pagesize=landscape(A4))
    c.setStrokeColorRGB(*accent)
    c.setLineWidth(3)
    c.rect(12 * mm, 12 * mm, w - 24 * mm, h - 24 * mm)
    c.setLineWidth(0.8)
    c.rect(15 * mm, 15 * mm, w - 30 * mm, h - 30 * mm)
    if logo is not None:
        c.drawImage(logo, w / 2 - 20 * mm, h - 48 * mm, width=40 * mm, height=22 * mm, preserveAspectRatio=True, mask="auto")
    c.setFillColorRGB(*accent)
    c.setFont(FONT_BOLD, 34)
    c.drawCentredString(w / 2, h - 68 * mm, "OKLEVÉL")
    c.setFillColorRGB(0.2, 0.2, 0.2)
    c.setFont(FONT, 13)
    c.drawCentredString(w / 2, h - 80 * mm, f"{company} igazolja, hogy")
    c.setFont(FONT_BOLD, 24)
    c.drawCentredString(w / 2, h - 96 * mm, f"{employee.last_name} {employee.first_name}")
    c.setFont(FONT, 13)
    c.drawCentredString(w / 2, h - 108 * mm, "sikeresen elvégezte az alábbi képzést és a hozzá tartozó tesztet:")
    c.setFont(FONT_BOLD, 18)
    c.drawCentredString(w / 2, h - 122 * mm, material.title)
    done: date = assignment.completed_at.date()
    line = f"Elvégezve: {done:%Y. %m. %d.} · Eredmény: 100 %"
    if assignment.valid_until:
        line += f" · Érvényes: {assignment.valid_until:%Y. %m. %d.}-ig"
    c.setFont(FONT, 12)
    c.drawCentredString(w / 2, h - 136 * mm, line)
    c.setFont(FONT, 9)
    c.setFillColorRGB(0.45, 0.45, 0.45)
    c.drawCentredString(w / 2, 20 * mm, f"Oklevél sorszáma: {serial} · X-Learning")
    c.showPage()
    c.save()
    return buf.getvalue(), serial
