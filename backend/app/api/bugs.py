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
from app.models import BugReport, User

router = APIRouter()

SEVERITIES = ("blocker", "major", "minor", "cosmetic")
STATUSES = ("new", "confirmed", "duplicate", "rejected", "resolved", "closed", "reopened")
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


def _out(b: BugReport) -> dict:
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
    }


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
    return [_out(b) for b in rows]


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
    return [_out(b) for b in rows]


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
