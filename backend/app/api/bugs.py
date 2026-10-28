"""Hibabejelentő modul.

Tesztelői oldal: lebegő gombból 3 mezős űrlap (leírás, súlyosság, kép), az
oldal-URL és a böngésző automatikusan rögzül. Admin-oldal: szűrhető triázs-
sor (megerősít / duplikátum / elutasít), kötegelés javítási feladatba, majd
a kör zárása: resolved → a bejelentő újrateszteli → closed vagy reopened.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, record_audit, require_role
from app.db import get_db
from app.models import BugComment, BugReport, User

router = APIRouter()

SEVERITIES = ("blocker", "major", "minor", "cosmetic")
# needs_info: az üzemeltető kérdezett, a jegy a bejelentőnél van;
# answered: a bejelentő válaszolt, a jegy visszakerült az üzemeltetőhöz (114. kör)
STATUSES = (
    "new", "confirmed", "needs_info", "answered", "duplicate", "rejected",
    "resolved", "closed", "reopened",
)
# ezekből a státuszokból dobja vissza a kérdés a jegyet a bejelentőnek
_BOUNCEABLE = ("new", "confirmed", "reopened", "answered")
MAX_SCREENSHOT = 3 * 1024 * 1024  # 3 MB


class BugCreateBody(BaseModel):
    description: str = Field(min_length=5, max_length=4000)
    severity: str = Field(default="minor")
    page_url: str = Field(min_length=1, max_length=512)
    user_agent: str | None = Field(default=None, max_length=256)
    screenshot: str | None = None  # data-URL (png/jpeg)


def _decode_screenshot(data_url: str | None) -> tuple[bytes | None, str | None]:
    if not data_url:
        return None, None
    if not data_url.startswith("data:image/"):
        raise HTTPException(status_code=422, detail={"code": "bugs.bad_screenshot"})
    try:
        header, payload = data_url.split(",", 1)
        mime = header.split(";")[0].removeprefix("data:")
        raw = base64.b64decode(payload)
    except Exception:
        raise HTTPException(status_code=422, detail={"code": "bugs.bad_screenshot"})
    if len(raw) > MAX_SCREENSHOT:
        raise HTTPException(status_code=422, detail={"code": "bugs.screenshot_too_big"})
    return raw, mime[:32]


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _comment_out(c: BugComment) -> dict:
    return {
        "id": str(c.id),
        "author_kind": c.author_kind,
        "author_name": c.author_name,
        "text": c.text,
        "created_at": _aware(c.created_at).isoformat(),
    }


def _out(b: BugReport, comments: list[BugComment] | None = None) -> dict:
    comments = list(comments or [])
    last_staff = max((_aware(c.created_at) for c in comments if c.author_kind != "reporter"), default=None)
    last_rep = max((_aware(c.created_at) for c in comments if c.author_kind == "reporter"), default=None)
    r_seen, o_seen = _aware(b.reporter_seen_at), _aware(b.operator_seen_at)
    return {
        "id": str(b.id),
        "page_url": b.page_url,
        "description": b.description,
        "severity": b.severity,
        "status": b.status,
        "reporter_name": b.reporter_name,
        "user_agent": b.user_agent,
        "has_screenshot": b.screenshot is not None,
        "fix_group": b.fix_group,
        "resolution_note": b.resolution_note,
        "created_at": b.created_at.isoformat(),
        "updated_at": b.updated_at.isoformat() if b.updated_at else None,
        # párbeszéd-szál + olvasatlan-jelzések (114. kör)
        "comments": [_comment_out(c) for c in comments],
        "unread_for_reporter": bool(last_staff and (r_seen is None or last_staff > r_seen)),
        "unread_for_operator": bool(last_rep and (o_seen is None or last_rep > o_seen)),
    }


async def _comments_for(db: AsyncSession, rows: list[BugReport]) -> dict[uuid.UUID, list[BugComment]]:
    out: dict[uuid.UUID, list[BugComment]] = {r.id: [] for r in rows}
    if not rows:
        return out
    cs = (
        await db.execute(
            select(BugComment)
            .where(BugComment.bug_id.in_([r.id for r in rows]))
            .order_by(BugComment.created_at)
        )
    ).scalars().all()
    for c in cs:
        out.setdefault(c.bug_id, []).append(c)
    return out


async def _outs(db: AsyncSession, rows: list[BugReport]) -> list[dict]:
    cmap = await _comments_for(db, rows)
    return [_out(b, cmap.get(b.id)) for b in rows]


async def add_comment(
    db: AsyncSession, b: BugReport, *, kind: str, author_name: str, text: str,
    status: str | None,
) -> None:
    """Hozzászólás + a jegy státusz-logikája. Üzemeltető/admin: a megadott
    státusz, vagy — ha a jegy nyitott — automatikusan needs_info (a jegy a
    bejelentőnél). Bejelentő: needs_info → answered (vissza az üzemeltetőhöz)."""
    now = datetime.now(UTC)
    if kind != "reporter" and status and status not in STATUSES:
        raise HTTPException(status_code=422, detail={"code": "bugs.bad_status"})
    db.add(BugComment(bug_id=b.id, author_kind=kind, author_name=author_name[:128], text=text[:4000]))
    if kind == "reporter":
        if b.status == "needs_info":
            b.status = "answered"
        b.reporter_seen_at = now
    else:
        if status:
            b.status = status
        elif b.status in _BOUNCEABLE:
            b.status = "needs_info"
        b.operator_seen_at = now
    b.updated_at = now


async def _bug_or_404(db: AsyncSession, bug_id: str) -> BugReport:
    try:
        bid = uuid.UUID(bug_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "bugs.not_found"})
    b = (
        await db.execute(select(BugReport).where(BugReport.id == bid))
    ).scalar_one_or_none()
    if b is None:
        raise HTTPException(status_code=404, detail={"code": "bugs.not_found"})
    return b


# ─── Tesztelői oldal ────────────────────────────────────────────────────────


@router.post("", status_code=201)
async def create_bug(
    body: BugCreateBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    if body.severity not in SEVERITIES:
        raise HTTPException(status_code=422, detail={"code": "bugs.bad_severity"})
    raw, mime = _decode_screenshot(body.screenshot)
    b = BugReport(
        page_url=body.page_url.strip()[:512],
        description=body.description.strip(),
        severity=body.severity,
        user_agent=(body.user_agent or "").strip()[:256] or None,
        screenshot=raw,
        screenshot_mime=mime,
        reporter_id=actor.id,
        reporter_name=actor.display_name or actor.email,
    )
    db.add(b)
    await record_audit(
        db, actor=actor, action="bug.create", entity_type="bug",
        entity_id=str(b.id), detail={"severity": body.severity}, request=request,
    )
    await db.commit()
    return _out(b)


@router.get("/mine")
async def my_bugs(
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    rows = (
        await db.execute(
            select(BugReport)
            .where(BugReport.reporter_id == actor.id)
            .order_by(BugReport.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return await _outs(db, rows)


class BugCommentBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    # csak admin adhat státuszt (pl. needs_info = visszadobás kérdéssel)
    status: str | None = None


@router.post("/{bug_id}/comments")
async def comment_bug(
    bug_id: str,
    body: BugCommentBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """Hozzászólás a jegyhez (114. kör). A bejelentő válaszol (needs_info →
    answered); a példány adminja kérdez — a jegy needs_info-ra kerül, és a
    bejelentőnél felugrik az értesítés."""
    b = await _bug_or_404(db, bug_id)
    is_reporter = b.reporter_id == actor.id
    if not is_reporter and actor.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "bugs.not_yours"})
    kind = "reporter" if is_reporter else "admin"
    await add_comment(
        db, b, kind=kind, author_name=actor.display_name, text=body.text.strip(),
        status=body.status if kind == "admin" else None,
    )
    await record_audit(
        db, actor=actor, action="bug.comment", entity_type="bug",
        entity_id=str(b.id), detail={"kind": kind, "status": b.status}, request=request,
    )
    await db.commit()
    return (await _outs(db, [b]))[0]


@router.post("/{bug_id}/seen")
async def bug_seen(
    bug_id: str,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """A bejelentő (vagy admin) megnézte a szálat → az olvasatlan-jelzés törlődik."""
    b = await _bug_or_404(db, bug_id)
    if b.reporter_id != actor.id and actor.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "bugs.not_yours"})
    now = datetime.now(UTC)
    if b.reporter_id == actor.id:
        b.reporter_seen_at = now
    else:
        b.operator_seen_at = now
    await db.commit()
    return {"ok": True}


@router.post("/{bug_id}/retest-ok")
async def retest_ok(
    bug_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """A bejelentő megerősíti: a javítás működik → lezárva."""
    b = await _bug_or_404(db, bug_id)
    if b.reporter_id != actor.id and actor.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "bugs.not_yours"})
    if b.status != "resolved":
        raise HTTPException(status_code=422, detail={"code": "bugs.not_resolved"})
    b.status = "closed"
    await record_audit(
        db, actor=actor, action="bug.retest_ok", entity_type="bug",
        entity_id=str(b.id), request=request,
    )
    await db.commit()
    return _out(b)


class ReopenBody(BaseModel):
    """Újranyitás indoklással: mi nem stimmel még + friss képernyőkép."""

    note: str | None = Field(default=None, max_length=4000)
    screenshot: str | None = None  # data-URL — megadva LECSERÉLI a régit


@router.post("/{bug_id}/reopen")
async def reopen(
    bug_id: str,
    request: Request,
    body: ReopenBody | None = None,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """A bejelentő szerint még mindig hibás → újranyitva, opcionális
    indoklással és friss képernyőképpel (a kép a régit lecseréli)."""
    b = await _bug_or_404(db, bug_id)
    if b.reporter_id != actor.id and actor.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "bugs.not_yours"})
    # lezárt (closed) jegy is újranyitható — "mégsem jó" eset
    if b.status not in ("resolved", "closed"):
        raise HTTPException(status_code=422, detail={"code": "bugs.not_resolved"})
    b.status = "reopened"
    if body and body.note and body.note.strip():
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
        b.description = (
            f"{b.description}\n\n--- Újranyitva ({stamp}) ---\n{body.note.strip()}"
        )[:4000]
    if body and body.screenshot:
        raw, mime = _decode_screenshot(body.screenshot)
        if raw is not None:
            b.screenshot = raw
            b.screenshot_mime = mime
    await record_audit(
        db, actor=actor, action="bug.reopen", entity_type="bug",
        entity_id=str(b.id), request=request,
    )
    await db.commit()
    return _out(b)


# ─── Admin: triázs-sor ──────────────────────────────────────────────────────


@router.get("")
async def list_bugs(
    status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    q = select(BugReport).order_by(BugReport.created_at.desc()).limit(300)
    if status:
        q = q.where(BugReport.status == status)
    if severity:
        q = q.where(BugReport.severity == severity)
    rows = (await db.execute(q)).scalars().all()
    return await _outs(db, rows)


@router.get("/{bug_id}/screenshot")
async def bug_screenshot(
    bug_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    b = await _bug_or_404(db, bug_id)
    if b.screenshot is None:
        raise HTTPException(status_code=404, detail={"code": "bugs.no_screenshot"})
    return Response(
        content=bytes(b.screenshot), media_type=b.screenshot_mime or "image/png"
    )


class BugPatchBody(BaseModel):
    status: str | None = None
    severity: str | None = None
    fix_group: str | None = Field(default=None, max_length=64)
    resolution_note: str | None = Field(default=None, max_length=512)


@router.patch("/{bug_id}")
async def update_bug(
    bug_id: str,
    body: BugPatchBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    b = await _bug_or_404(db, bug_id)
    if body.status is not None:
        if body.status not in STATUSES:
            raise HTTPException(status_code=422, detail={"code": "bugs.bad_status"})
        b.status = body.status
    if body.severity is not None:
        if body.severity not in SEVERITIES:
            raise HTTPException(status_code=422, detail={"code": "bugs.bad_severity"})
        b.severity = body.severity
    if "fix_group" in body.model_fields_set:
        b.fix_group = (body.fix_group or "").strip() or None
    if "resolution_note" in body.model_fields_set:
        b.resolution_note = (body.resolution_note or "").strip() or None
    b.updated_at = datetime.now(UTC)
    await record_audit(
        db, actor=actor, action="bug.update", entity_type="bug",
        entity_id=str(b.id),
        detail={"status": body.status, "severity": body.severity},
        request=request,
    )
    await db.commit()
    return _out(b)
