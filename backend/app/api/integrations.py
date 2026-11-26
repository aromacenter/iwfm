"""Integrációk (187. kör): kimenő webhookok + olvasó API-kulcsok. Csak admin;
modul-kapcsoló: integrations."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, record_audit
from app.models import ApiKey, User, WebhookEndpoint
from app.services.wfm.license import require_module

router = APIRouter(dependencies=[Depends(require_module("integrations"))])

MAX_WEBHOOKS = 20
MAX_KEYS = 20


def _admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "auth.admin_only"})
    return user


def key_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------- webhookok ----------
class WebhookBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    url: str = Field(min_length=8, max_length=512, pattern=r"^https?://")
    events: list[str] = Field(default_factory=list, max_length=100)  # üres = minden esemény
    active: bool = True


class WebhookPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    url: str | None = Field(default=None, min_length=8, max_length=512, pattern=r"^https?://")
    events: list[str] | None = Field(default=None, max_length=100)
    active: bool | None = None
    rotate_secret: bool = False


class WebhookOut(BaseModel):
    id: str
    name: str
    url: str
    events: list[str]
    active: bool
    secret: str | None = None  # csak létrehozáskor / titok-cserénél
    secret_hint: str
    delivered: int
    failures: int
    last_status: int | None
    last_at: datetime | None
    last_error: str | None
    created_at: datetime


def _wh_out(w: WebhookEndpoint, secret: str | None = None) -> WebhookOut:
    return WebhookOut(
        id=str(w.id), name=w.name, url=w.url, events=list(w.events or []), active=bool(w.active), secret=secret,
        secret_hint=(w.secret[:4] + "…" + w.secret[-2:]) if w.secret else "", delivered=w.delivered or 0,
        failures=w.failures or 0, last_status=w.last_status, last_at=w.last_at, last_error=w.last_error, created_at=w.created_at,
    )


async def _wh(db: AsyncSession, wid: str) -> WebhookEndpoint:
    try:
        w = await db.get(WebhookEndpoint, uuid.UUID(wid))
    except ValueError:
        w = None
    if w is None:
        raise HTTPException(status_code=404, detail={"code": "integr.not_found"})
    return w


def _check_events(events: list[str]) -> list[str]:
    from app.services.wfm.automation import TRIGGERS

    bad = [e for e in events if e not in TRIGGERS]
    if bad:
        raise HTTPException(status_code=422, detail={"code": "integr.bad_event", "events": bad})
    return sorted(set(events))


@router.get("/events")
async def list_events(_: User = Depends(_admin)):
    """Az események és mezőik — a feliratkozáshoz és a dokumentációhoz."""
    from app.services.wfm.automation import TRIGGERS

    return [{"event": k, "fields": list(v)} for k, v in TRIGGERS.items()]


@router.get("/webhooks", response_model=list[WebhookOut])
async def list_webhooks(db: AsyncSession = Depends(get_db), _: User = Depends(_admin)):
    rows = (await db.execute(select(WebhookEndpoint).order_by(WebhookEndpoint.created_at))).scalars().all()
    return [_wh_out(w) for w in rows]


@router.post("/webhooks", response_model=WebhookOut, status_code=201)
async def create_webhook(body: WebhookBody, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(_admin)):
    n = len((await db.execute(select(WebhookEndpoint.id))).all())
    if n >= MAX_WEBHOOKS:
        raise HTTPException(status_code=422, detail={"code": "integr.too_many"})
    secret = secrets.token_urlsafe(32)
    w = WebhookEndpoint(name=body.name.strip(), url=body.url.strip(), secret=secret, events=_check_events(body.events),
                        active=body.active, created_by=actor.id)
    db.add(w)
    await record_audit(db, actor=actor, action="webhook.create", entity_type="webhook", detail={"name": w.name, "url": w.url}, request=request)
    await db.commit()
    await db.refresh(w)
    return _wh_out(w, secret)


@router.patch("/webhooks/{wid}", response_model=WebhookOut)
async def update_webhook(wid: str, body: WebhookPatch, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(_admin)):
    w = await _wh(db, wid)
    secret = None
    if body.name is not None:
        w.name = body.name.strip()
    if body.url is not None:
        w.url = body.url.strip()
    if body.events is not None:
        w.events = _check_events(body.events)
    if body.active is not None:
        w.active = body.active
        if body.active:
            w.failures = 0
    if body.rotate_secret:
        secret = secrets.token_urlsafe(32)
        w.secret = secret
    await record_audit(db, actor=actor, action="webhook.update", entity_type="webhook", entity_id=str(w.id),
                       detail={"name": w.name, "active": w.active, "rotated": body.rotate_secret}, request=request)
    await db.commit()
    await db.refresh(w)
    return _wh_out(w, secret)


@router.delete("/webhooks/{wid}", status_code=204)
async def delete_webhook(wid: str, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(_admin)):
    w = await _wh(db, wid)
    await record_audit(db, actor=actor, action="webhook.delete", entity_type="webhook", entity_id=str(w.id), detail={"name": w.name}, request=request)
    await db.delete(w)
    await db.commit()


@router.post("/webhooks/{wid}/test")
async def test_webhook(wid: str, db: AsyncSession = Depends(get_db), _: User = Depends(_admin)):
    from app.services.wfm.webhooks import send_test

    w = await _wh(db, wid)
    status, error = await send_test(w)
    await db.commit()
    return {"ok": error is None and 200 <= status < 400, "status": status, "error": error}


# ---------- API-kulcsok ----------
class ApiKeyBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class ApiKeyOut(BaseModel):
    id: str
    name: str
    prefix: str
    key: str | None = None  # csak létrehozáskor
    created_by_name: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


def _key_out(k: ApiKey, raw: str | None = None) -> ApiKeyOut:
    return ApiKeyOut(id=str(k.id), name=k.name, prefix=k.prefix, key=raw, created_by_name=k.created_by_name or "",
                     created_at=k.created_at, last_used_at=k.last_used_at, revoked_at=k.revoked_at)


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_keys(db: AsyncSession = Depends(get_db), _: User = Depends(_admin)):
    rows = (await db.execute(select(ApiKey).order_by(ApiKey.created_at))).scalars().all()
    return [_key_out(k) for k in rows]


@router.post("/api-keys", response_model=ApiKeyOut, status_code=201)
async def create_key(body: ApiKeyBody, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(_admin)):
    live = [k for k in (await db.execute(select(ApiKey))).scalars().all() if k.revoked_at is None]
    if len(live) >= MAX_KEYS:
        raise HTTPException(status_code=422, detail={"code": "integr.too_many"})
    raw = "xa_" + secrets.token_urlsafe(30)
    k = ApiKey(name=body.name.strip(), prefix=raw[:10], key_hash=key_hash(raw), created_by=actor.id, created_by_name=actor.display_name)
    db.add(k)
    await record_audit(db, actor=actor, action="api_key.create", entity_type="api_key", detail={"name": k.name, "prefix": k.prefix}, request=request)
    await db.commit()
    await db.refresh(k)
    return _key_out(k, raw)


@router.delete("/api-keys/{kid}", response_model=ApiKeyOut)
async def revoke_key(kid: str, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(_admin)):
    try:
        k = await db.get(ApiKey, uuid.UUID(kid))
    except ValueError:
        k = None
    if k is None:
        raise HTTPException(status_code=404, detail={"code": "integr.not_found"})
    if k.revoked_at is None:
        k.revoked_at = datetime.now(UTC)
        await record_audit(db, actor=actor, action="api_key.revoke", entity_type="api_key", entity_id=str(k.id), detail={"name": k.name, "prefix": k.prefix}, request=request)
        await db.commit()
        await db.refresh(k)
    return _key_out(k)
