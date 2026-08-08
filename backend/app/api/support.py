"""Gép-QR ügyfél-támogatás.

Minden gép kap egy kitalálhatatlan QR-tokent; a matricán lévő QR a nyilvános
támogatási oldalra visz (/tamogatas/{token}). Ott az ügyfél választhat:

* **Azonnali segítség** — AI chat, amely a Beállításokban szerkesztett
  tudásbázisból és a gép adataiból válaszol (a meglévő AI-bekötésen át).
* **Szervizigény** — előre kitöltött bejelentő (gép + partner adatok),
  kötelező hibaleírással és opcionális fotókkal; automatikusan szervizjegy
  (ServiceTicket) lesz belőle a képekkel együtt.

A nyilvános végpontok IP-alapú rate-limittel védettek (a kiosk mintájára).
"""

from __future__ import annotations

import base64
import binascii
import secrets
import time as _time
import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.api.service import _next_ticket_no
from app.core.config import get_settings
from app.db import get_db
from app.models import (
    Asset,
    Partner,
    ServiceTicket,
    SupportSettings,
    TicketAttachment,
    User,
)

labels_router = APIRouter()  # /api/assets (belső: címke-generálás)
public_router = APIRouter()  # /api/support (nyilvános)

MAX_PHOTOS = 3
MAX_PHOTO_BYTES = 4 * 1024 * 1024  # kép/darab
ALLOWED_MIME = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}

# ─── Rate limit (nyilvános végpontok) ────────────────────────────────────────

_WINDOW_SECONDS = 10 * 60
_MAX_TICKETS = 5  # bejelentés / IP / 10 perc
_MAX_CHAT = 20  # chat-üzenet / IP / 10 perc
_hits: dict[str, list[float]] = defaultdict(list)


def _rate_limited(key: str, limit: int) -> bool:
    now = _time.monotonic()
    hits = [t for t in _hits[key] if now - t < _WINDOW_SECONDS]
    if len(hits) >= limit:
        _hits[key] = hits
        return True
    hits.append(now)
    _hits[key] = hits
    return False


# ─── Belső: QR-címkék ────────────────────────────────────────────────────────


def _support_url(token: str) -> str:
    return f"{get_settings().frontend_origin.rstrip('/')}/tamogatas/{token}"


async def _label_items(db: AsyncSession, assets: list[Asset]) -> list[dict]:
    """Címke-adatok; hiányzó QR-tokent itt generálunk (a hívó commitol)."""
    partner_ids = {a.partner_id for a in assets if a.partner_id}
    partner_names: dict[uuid.UUID, str] = {}
    if partner_ids:
        partner_names = {
            pid: name
            for pid, name in (
                await db.execute(
                    select(Partner.id, Partner.name).where(Partner.id.in_(partner_ids))
                )
            ).all()
        }
    items = []
    for a in assets:
        if not a.qr_token:
            a.qr_token = secrets.token_urlsafe(24)
        items.append(
            {
                "url": _support_url(a.qr_token),
                "name": a.name,
                "barcode": a.barcode,
                "serial_number": a.serial_number,
                "partner_name": partner_names.get(a.partner_id) if a.partner_id else None,
            }
        )
    return items


def _labels_response(pdf: bytes, filename: str):
    from fastapi import Response

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@labels_router.get("/{asset_id}/qr-label")
async def asset_qr_label(
    asset_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("machines")),
):
    """Egy gép QR-címkéje (PDF). Ha még nincs tokenje, itt kap."""
    from app.services.wfm.qr_label import build_qr_labels_pdf

    try:
        aid = uuid.UUID(asset_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "asset.not_found"})
    asset = (await db.execute(select(Asset).where(Asset.id == aid))).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail={"code": "asset.not_found"})

    items = await _label_items(db, [asset])
    await record_audit(
        db, actor=actor, action="asset.qr_label", entity_type="asset",
        entity_id=asset.barcode, request=request,
    )
    await db.commit()
    return _labels_response(build_qr_labels_pdf(items), f"QR-{asset.barcode}.pdf")


class LabelBatchBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


@labels_router.post("/qr-labels")
async def asset_qr_labels(
    body: LabelBatchBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("machines")),
):
    """Több gép címkéje egy PDF-ben (A4, 2×4 rács)."""
    from app.services.wfm.qr_label import build_qr_labels_pdf

    ids = []
    for raw in body.ids:
        try:
            ids.append(uuid.UUID(raw))
        except ValueError:
            continue
    assets = (
        await db.execute(select(Asset).where(Asset.id.in_(ids)).order_by(Asset.barcode))
    ).scalars().all()
    if not assets:
        raise HTTPException(status_code=404, detail={"code": "asset.not_found"})

    items = await _label_items(db, assets)
    await record_audit(
        db, actor=actor, action="asset.qr_labels", entity_type="asset",
        detail={"count": len(items)}, request=request,
    )
    await db.commit()
    return _labels_response(build_qr_labels_pdf(items), "QR-cimkek.pdf")


# ─── Nyilvános: támogatási oldal ─────────────────────────────────────────────


async def _asset_by_token(db: AsyncSession, token: str) -> Asset:
    if not token or len(token) < 16:
        raise HTTPException(status_code=404, detail={"code": "support.not_found"})
    asset = (
        await db.execute(select(Asset).where(Asset.qr_token == token))
    ).scalar_one_or_none()
    if asset is None or asset.status == "retired":
        raise HTTPException(status_code=404, detail={"code": "support.not_found"})
    return asset


async def _asset_partner(db: AsyncSession, asset: Asset) -> Partner | None:
    if asset.partner_id is None:
        return None
    return (
        await db.execute(select(Partner).where(Partner.id == asset.partner_id))
    ).scalar_one_or_none()


@public_router.get("/{token}")
async def support_info(token: str, db: AsyncSession = Depends(get_db)):
    """A QR-oldal induló adatai: gép + ügyfél (előtöltéshez)."""
    asset = await _asset_by_token(db, token)
    partner = await _asset_partner(db, asset)
    return {
        "asset_name": asset.name,
        "barcode": asset.barcode,
        "serial_number": asset.serial_number,
        "location_type": asset.location_type,
        "partner_name": partner.name if partner else None,
        "partner_code": partner.partner_code if partner else None,
        "contact_name": partner.contact_name if partner else None,
        "contact_phone": partner.contact_phone if partner else None,
    }


class SupportTicketBody(BaseModel):
    description: str = Field(min_length=5, max_length=4000)
    contact_name: str | None = Field(default=None, max_length=256)
    contact_phone: str | None = Field(default=None, max_length=64)
    photos: list[str] = Field(default_factory=list, max_length=MAX_PHOTOS)


def _decode_photo(data_url: str, index: int) -> tuple[str, str, bytes]:
    """data URL → (filename, mime, bytes). 422 support.bad_photo hibával."""
    try:
        header, payload = data_url.split(",", 1)
        if not header.startswith("data:") or ";base64" not in header:
            raise ValueError
        mime = header[5:].split(";", 1)[0]
        ext = ALLOWED_MIME.get(mime)
        if ext is None:
            raise ValueError
        raw = base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(status_code=422, detail={"code": "support.bad_photo"})
    if len(raw) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=422, detail={"code": "support.photo_too_large"})
    return f"hiba-foto-{index + 1}.{ext}", mime, raw


@public_router.post("/{token}/ticket", status_code=201)
async def create_support_ticket(
    token: str,
    body: SupportTicketBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Szervizigény bejelentése a nyilvános oldalról → szervizjegy."""
    ip = request.client.host if request.client else "?"
    if _rate_limited(f"ticket:{ip}", _MAX_TICKETS):
        raise HTTPException(status_code=429, detail={"code": "support.rate_limited"})

    asset = await _asset_by_token(db, token)
    partner = await _asset_partner(db, asset)
    photos = [_decode_photo(p, i) for i, p in enumerate(body.photos)]

    contact_bits = " · ".join(x for x in (body.contact_name, body.contact_phone) if x)
    description = body.description.strip()
    if contact_bits:
        description = f"{description}\n\nBejelentő: {contact_bits}"

    ticket = ServiceTicket(
        ticket_no=await _next_ticket_no(db),
        kind="repair",
        priority="normal",
        title=f"Ügyfél-bejelentés — {asset.name} ({asset.barcode})",
        description=description,
        asset_id=asset.id,
        asset_label=f"{asset.name} ({asset.barcode})",
        partner_id=partner.id if partner else None,
        partner_label=partner.name if partner else None,
    )
    db.add(ticket)
    await db.flush()
    for filename, mime, raw in photos:
        db.add(TicketAttachment(ticket_id=ticket.id, filename=filename, mime=mime, data=raw))

    await record_audit(
        db, actor=None, action="support.ticket", entity_type="service_ticket",
        entity_id=ticket.ticket_no,
        detail={"asset": asset.barcode, "photos": len(photos)}, request=request,
    )
    await db.commit()
    return {"ticket_no": ticket.ticket_no}


class ChatMessage(BaseModel):
    role: str  # 'user' | 'assistant'
    content: str = Field(min_length=1, max_length=2000)


class SupportChatBody(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=12)


@public_router.post("/{token}/chat")
async def support_chat(
    token: str,
    body: SupportChatBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """AI-segítség a tudásbázis alapján. 422 support.ai_not_configured, ha az
    AI nincs bekötve — a kliens ilyenkor a bejelentő űrlapra terel."""
    from app.services.wfm.ai_service import generate

    ip = request.client.host if request.client else "?"
    if _rate_limited(f"chat:{ip}", _MAX_CHAT):
        raise HTTPException(status_code=429, detail={"code": "support.rate_limited"})
    if body.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail={"code": "support.bad_chat"})

    asset = await _asset_by_token(db, token)
    partner = await _asset_partner(db, asset)
    kb_row = (
        await db.execute(select(SupportSettings).where(SupportSettings.id == 1))
    ).scalar_one_or_none()
    kb = (kb_row.knowledge_base or "").strip() if kb_row else ""

    transcript = "\n".join(
        f"{'Ügyfél' if m.role == 'user' else 'Asszisztens'}: {m.content.strip()}"
        for m in body.messages[-8:]
    )
    prompt = (
        "Te egy kávégép-üzemeltető cég ügyfélszolgálati asszisztense vagy. "
        "Magyarul, röviden, lépésenként segíts a gép hibájának elhárításában a "
        "TUDÁSBÁZIS alapján. Ha a tudásbázisból nem oldható meg a probléma, "
        "vagy fizikai beavatkozás/alkatrészcsere kell, javasold udvariasan a "
        "szervizigény bejelentését ezen az oldalon (erre külön gomb van). "
        "Ne találj ki tényeket a gépről.\n\n"
        f"GÉP: {asset.name} · kód: {asset.barcode}"
        + (f" · gyári szám: {asset.serial_number}" if asset.serial_number else "")
        + (f"\nÜGYFÉL: {partner.name}" if partner else "")
        + f"\n\nTUDÁSBÁZIS:\n{kb or '(üres — általános kávégép-ismeretek alapján segíts)'}"
        + f"\n\nEDDIGI BESZÉLGETÉS:\n{transcript}\n\nAsszisztens:"
    )

    try:
        reply = await generate(db, prompt, max_tokens=700)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "support.ai_not_configured"})
    except Exception:
        raise HTTPException(status_code=502, detail={"code": "support.ai_failed"})
    return {"reply": reply.strip()}
