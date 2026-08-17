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
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.api.service import _next_ticket_no
from app.core.config import get_settings
from app.db import get_db
from app.models import (
    Asset,
    AssetMovement,
    Partner,
    Product,
    ProductOrder,
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
    fmt: str = "a4",
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("machines")),
):
    """Egy gép QR-címkéje (PDF). fmt=small → 51×25 mm címkenyomtatóra."""
    from app.services.wfm.qr_label import build_qr_labels_pdf, build_qr_labels_small_pdf

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
    if fmt == "ezpl":
        from app.services.wfm.qr_label import build_qr_labels_ezpl

        from fastapi import Response

        return Response(
            content=build_qr_labels_ezpl(items),
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="QR-{asset.barcode}.ezp"'},
        )
    build = build_qr_labels_small_pdf if fmt == "small" else build_qr_labels_pdf
    return _labels_response(build(items), f"QR-{asset.barcode}.pdf")


class LabelBatchBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)
    fmt: str = "a4"  # a4 (2×4 ív) | small (51×25 mm címkenyomtató)


@labels_router.post("/qr-labels")
async def asset_qr_labels(
    body: LabelBatchBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("machines")),
):
    """Több gép címkéje egy PDF-ben (A4 ív vagy 51×25 mm címkék)."""
    from app.services.wfm.qr_label import build_qr_labels_pdf, build_qr_labels_small_pdf

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
    if body.fmt == "ezpl":
        from app.services.wfm.qr_label import build_qr_labels_ezpl

        from fastapi import Response

        return Response(
            content=build_qr_labels_ezpl(items),
            media_type="text/plain",
            headers={"Content-Disposition": 'attachment; filename="QR-cimkek.ezp"'},
        )
    build = build_qr_labels_small_pdf if body.fmt == "small" else build_qr_labels_pdf
    return _labels_response(build(items), "QR-cimkek.pdf")


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
    """A QR-oldal induló adatai: gép + ügyfél (előtöltéshez), aktuális
    számláló-állás és a rendelhető termékek (nevek, árak nélkül)."""
    asset = await _asset_by_token(db, token)
    partner = await _asset_partner(db, asset)
    products = (
        await db.execute(
            select(Product).where(Product.is_active.is_(True)).order_by(Product.name)
        )
    ).scalars().all()
    return {
        "asset_name": asset.name,
        "barcode": asset.barcode,
        "serial_number": asset.serial_number,
        "location_type": asset.location_type,
        "counter": asset.counter,
        "counter_count": asset.counter_count or 1,
        "counters": asset.counters,
        "partner_name": partner.name if partner else None,
        "partner_code": partner.partner_code if partner else None,
        "contact_name": partner.contact_name if partner else None,
        "contact_phone": partner.contact_phone if partner else None,
        "products": [
            {"id": str(p.id), "name": p.name, "unit": p.unit} for p in products
        ],
    }


class SupportTicketBody(BaseModel):
    description: str = Field(min_length=5, max_length=4000)
    contact_name: str | None = Field(default=None, max_length=256)
    contact_phone: str | None = Field(default=None, max_length=64)
    contact_email: EmailStr | None = None  # ha a partnernek nincs, elmentjük
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

    contact_bits = " · ".join(
        x for x in (body.contact_name, body.contact_phone, body.contact_email) if x
    )
    description = body.description.strip()
    if contact_bits:
        description = f"{description}\n\nBejelentő: {contact_bits}"
    # E-mail-gyűjtés: csak ÜRES partner-e-mailt töltünk (kézit nem írunk felül).
    if body.contact_email and partner is not None and not partner.contact_email:
        partner.contact_email = str(body.contact_email)

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


class CounterBody(BaseModel):
    counter: int = Field(ge=0, le=100_000_000)  # egy számlálós gép / összeg
    # Több számlálós gép: az egyes állások külön-külön (a counter az összeg)
    counters: list[int] | None = Field(default=None, max_length=8)
    stock_kg: float | None = Field(default=None, ge=0, le=10_000)  # jelenlegi készlet (kg)
    reporter_name: str | None = Field(default=None, max_length=256)


@public_router.post("/{token}/counter")
async def report_counter(
    token: str,
    body: CounterBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Számláló-állás bejelentése a QR-oldalról: a gép adatlapján frissül a
    számláló, az előzményekbe régi → új bejegyzés kerül a bejelentő nevével."""
    ip = request.client.host if request.client else "?"
    if _rate_limited(f"counter:{ip}", 10):
        raise HTTPException(status_code=429, detail={"code": "support.rate_limited"})

    asset = await _asset_by_token(db, token)
    old = asset.counter
    reporter = (body.reporter_name or "").strip() or "QR-oldali bejelentés"
    new_total = sum(body.counters) if body.counters else body.counter
    if body.counters:
        parts = " + ".join(str(c) for c in body.counters)
        detail = (f"Számláló: {old if old is not None else '—'} → {new_total} "
                  f"({parts}) · bejelentette: {reporter}")
        asset.counters = body.counters
    else:
        detail = f"Számláló: {old if old is not None else '—'} → {new_total} · bejelentette: {reporter}"
    if body.stock_kg is not None:
        detail += f" · készlet: ~{body.stock_kg:g} kg"
    if old is not None and new_total < old:
        detail += " (csökkenés!)"

    asset.counter = new_total
    db.add(AssetMovement(asset_id=asset.id, action="counter", detail=detail[:512]))
    await record_audit(
        db, actor=None, action="support.counter", entity_type="asset",
        entity_id=asset.barcode,
        detail={"old": old, "new": new_total, "counters": body.counters,
                "stock_kg": body.stock_kg, "reporter": reporter},
        request=request,
    )
    await db.commit()

    from app.services.wfm.automation import fire_event

    partner = await _asset_partner(db, asset)
    fire_event("counter.reported", {
        "_partner_id": partner.id if partner else None,
        "gep_vonalkod": asset.barcode,
        "gep_nev": asset.name,
        "partner_nev": partner.name if partner else "",
        "szamlalo": new_total,
        "elozo_szamlalo": old,
        "keszlet_kg": body.stock_kg,
    })
    return {"old_counter": old, "new_counter": new_total}


class OrderItem(BaseModel):
    product_id: str
    quantity: float = Field(gt=0, le=1000)


class OrderBody(BaseModel):
    items: list[OrderItem] = Field(min_length=1, max_length=10)
    note: str | None = Field(default=None, max_length=1000)
    contact_name: str | None = Field(default=None, max_length=256)
    contact_phone: str | None = Field(default=None, max_length=64)


async def _next_order_no(db: AsyncSession) -> str:
    last = (
        await db.execute(
            select(ProductOrder.order_no)
            .where(ProductOrder.order_no.like("R-%"))
            .order_by(ProductOrder.order_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    number = 0
    if last:
        try:
            number = int(last.split("-", 1)[1])
        except (IndexError, ValueError):
            number = 0
    return f"R-{number + 1:04d}"


@public_router.post("/{token}/order", status_code=201)
async def create_order(
    token: str,
    body: OrderBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Termékrendelés a QR-oldalról → az üzletkötő az Elszámolás oldalon látja."""
    ip = request.client.host if request.client else "?"
    if _rate_limited(f"order:{ip}", 5):
        raise HTTPException(status_code=429, detail={"code": "support.rate_limited"})

    asset = await _asset_by_token(db, token)
    partner = await _asset_partner(db, asset)

    items = []
    for item in body.items:
        try:
            pid = uuid.UUID(item.product_id)
        except ValueError:
            raise HTTPException(status_code=422, detail={"code": "support.bad_product"})
        product = (
            await db.execute(
                select(Product).where(Product.id == pid, Product.is_active.is_(True))
            )
        ).scalar_one_or_none()
        if product is None:
            raise HTTPException(status_code=422, detail={"code": "support.bad_product"})
        items.append({"name": product.name, "quantity": item.quantity, "unit": product.unit})

    order = ProductOrder(
        order_no=await _next_order_no(db),
        partner_id=partner.id if partner else None,
        partner_label=partner.name if partner else f"QR: {asset.name} ({asset.barcode})",
        asset_id=asset.id,
        items=items,
        note=(body.note or "").strip() or None,
        contact_name=(body.contact_name or "").strip() or None,
        contact_phone=(body.contact_phone or "").strip() or None,
    )
    db.add(order)
    await db.flush()
    await record_audit(
        db, actor=None, action="support.order", entity_type="product_order",
        entity_id=order.order_no,
        detail={"partner": order.partner_label, "items": len(items)}, request=request,
    )
    await db.commit()

    from app.services.wfm.automation import fire_event

    fire_event("order.created", {
        "_partner_id": partner.id if partner else None,
        "rendeles_szam": order.order_no,
        "partner_nev": order.partner_label,
        "tetel_lista": ", ".join(f"{i['name']} ({i['quantity']:g} {i['unit']})" for i in items),
    })
    return {"order_no": order.order_no}


class ChatMessage(BaseModel):
    role: str  # 'user' | 'assistant'
    content: str = Field(min_length=1, max_length=2000)


class SupportChatBody(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=12)


async def _load_kb(db: AsyncSession, terms: list[str] | None = None) -> str:
    """A teljes tudás összeállítása: a Beállításokban tárolt tudásbázis-szöveg
    + a kereshető tudásbázis-bejegyzések (KnowledgeEntry) „## " szekciókká
    formázva. A bejegyzéseket már SQL-szinten a gépre szűrjük (a tábla több
    ezer bejegyzést tartalmazhat — a teljes betöltés nem skálázódna)."""
    from sqlalchemy import or_

    from app.models import KnowledgeEntry

    row = (
        await db.execute(select(SupportSettings).where(SupportSettings.id == 1))
    ).scalar_one_or_none()
    kb = (row.knowledge_base or "").strip() if row else ""

    query = select(KnowledgeEntry)
    useful = [t.strip() for t in (terms or []) if t and len(t.strip()) >= 3]
    if useful:
        query = query.where(
            or_(*[KnowledgeEntry.machine_label.ilike(f"%{t}%") for t in useful])
        )
    entries = (
        await db.execute(
            query.order_by(KnowledgeEntry.machine_label, KnowledgeEntry.title).limit(300)
        )
    ).scalars().all()
    if entries:
        rendered = "\n\n".join(
            f"## {e.machine_label} — {e.title}"
            f"{' (bevált házi megoldás)' if e.source == 'manual' else ''}\n{e.content}"
            for e in entries
        )
        kb = f"{kb}\n\n{rendered}".strip() if kb else rendered
    return kb


def _relevant_kb(kb: str, terms: list[str]) -> str:
    """A tudásbázis „## " szekcióiból a gépre illők kiválogatása.

    Egy szekció akkor releváns, ha a címsora tartalmazza valamelyik keresett
    kifejezést (gyártó, típus-szavak), vagy általános érvényű. Ha a szűrés
    után túl kevés maradna (pl. a tudásbázis nem szekcionált), a teljes
    szöveget adjuk vissza — inkább több kontextus, mint kevesebb."""
    lowered_terms = [t.lower() for t in terms if t and len(t) >= 3]
    if not lowered_terms or "## " not in kb:
        return kb

    sections: list[tuple[str, str]] = []  # (címsor, teljes szekció-szöveg)
    current_head = ""
    current: list[str] = []
    for line in kb.splitlines():
        if line.startswith("## "):
            if current:
                sections.append((current_head, "\n".join(current)))
            current_head = line[3:].strip().lower()
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append((current_head, "\n".join(current)))

    def is_general(head: str) -> bool:
        return "általános" in head or "altalanos" in head or head == ""

    picked = [
        body
        for head, body in sections
        if is_general(head) or any(term in head for term in lowered_terms)
    ]
    matched_specific = any(
        not is_general(head) and any(term in head for term in lowered_terms)
        for head, body in sections
    )
    if not matched_specific:
        return kb  # nincs gép-specifikus találat → teljes tudásbázis
    return "\n".join(picked)


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

    # A QR-ból beazonosított gép gyártója/típusa alapján a tudásbázis releváns
    # szekcióit válogatjuk be (pl. Jura XJ6 → Jura + általános szekciók).
    terms = [asset.manufacturer or ""] + (asset.name or "").split()
    kb = await _load_kb(db, terms)
    kb_for_machine = _relevant_kb(kb, terms) if kb else ""

    transcript = "\n".join(
        f"{'Ügyfél' if m.role == 'user' else 'Asszisztens'}: {m.content.strip()}"
        for m in body.messages[-8:]
    )
    machine_line = f"{asset.manufacturer + ' ' if asset.manufacturer else ''}{asset.name}"
    prompt = (
        "Te egy kávégép-üzemeltető cég ügyfélszolgálati asszisztense vagy. "
        "Magyarul, röviden, lépésenként segíts a gép hibájának elhárításában a "
        "TUDÁSBÁZIS alapján. A tudásbázisból mindig a LENT AZONOSÍTOTT gép "
        "gyártójához/típusához tartozó részt alkalmazd; ha egy hibakód ennél a "
        "típusnál mást jelent, mint másik gyártónál, a gép típusa szerint "
        "válaszolj. Ha a tudásbázisból nem oldható meg a probléma, vagy "
        "fizikai beavatkozás/alkatrészcsere kell, javasold udvariasan a "
        "szervizigény bejelentését ezen az oldalon (erre külön gomb van). "
        "Ne találj ki tényeket a gépről.\n\n"
        f"AZONOSÍTOTT GÉP (a QR-kód alapján): {machine_line} · kód: {asset.barcode}"
        + (f" · gyári szám: {asset.serial_number}" if asset.serial_number else "")
        + (f" · helye: {asset.location_type}" if asset.location_type else "")
        + (f"\nÜGYFÉL: {partner.name}" if partner else "")
        + f"\n\nTUDÁSBÁZIS (erre a gépre szűrve):\n"
        + (kb_for_machine or "(üres — általános kávégép-ismeretek alapján segíts)")
        + f"\n\nEDDIGI BESZÉLGETÉS:\n{transcript}\n\nAsszisztens:"
    )

    try:
        reply = await generate(db, prompt, max_tokens=700)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "support.ai_not_configured"})
    except Exception:
        raise HTTPException(status_code=502, detail={"code": "support.ai_failed"})
    return {"reply": reply.strip()}
