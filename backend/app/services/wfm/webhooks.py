"""Kimenő webhookok (187. kör).

Minden esemény, amire Telegram is mehet, beállítható URL-ekre is elmegy JSON-ban,
HMAC-SHA256 aláírással (X-Iwfm-Signature: sha256=<hex>, a nyers törzsre, a
végpont titkával). Három próba (azonnal, 5 s, 30 s), a végpont számlálói
frissülnek; 20 egymás utáni hiba után a végpont magától kikapcsol.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WebhookEndpoint

logger = logging.getLogger(__name__)

RETRY_DELAYS = (0, 5, 30)
MAX_FAILURES = 20
TIMEOUT = 10.0


def build_payload(event: str, ctx: dict) -> dict:
    """A kimenő törzs: a sablon-mezők (data), az azonosítók (ids, `_` nélkül), link."""
    data = {k: v for k, v in ctx.items() if not str(k).startswith("_") and k != "link"}
    ids = {str(k)[1:]: (str(v) if v is not None else None) for k, v in ctx.items() if str(k).startswith("_")}
    return {"event": event, "at": datetime.now(UTC).isoformat(), "data": data, "ids": ids, "link": ctx.get("link")}


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign(secret, body), signature or "")


async def post_once(url: str, secret: str, event: str, body: bytes, delivery_id: str) -> tuple[int, str | None]:
    """Egy HTTP POST; (státuszkód, hibaszöveg). Hálózati hibánál 0."""
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "X-admin-webhook/1",
        "X-Iwfm-Event": event,
        "X-Iwfm-Delivery": delivery_id,
        "X-Iwfm-Signature": sign(secret, body),
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            r = await client.post(url, content=body, headers=headers)
        return r.status_code, None if r.status_code < 400 else (r.text or "")[:200]
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)[:200]


async def _record(endpoint_id: uuid.UUID, status: int, error: str | None) -> None:
    from app.db import get_session_factory

    async with get_session_factory()() as db:
        ep = await db.get(WebhookEndpoint, endpoint_id)
        if ep is None:
            return
        ep.last_status = status
        ep.last_at = datetime.now(UTC)
        ep.last_error = error
        if error is None and 200 <= status < 400:
            ep.failures = 0
            ep.delivered = (ep.delivered or 0) + 1
        else:
            ep.failures = (ep.failures or 0) + 1
            if ep.failures >= MAX_FAILURES and ep.active:
                ep.active = False
                ep.last_error = (error or f"HTTP {status}") + " — 20 hiba után kikapcsolva"
        await db.commit()


async def deliver_with_retries(endpoint_id: uuid.UUID, url: str, secret: str, event: str, body: bytes) -> int:
    delivery_id = uuid.uuid4().hex
    status, error = 0, None
    for i, delay in enumerate(RETRY_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        status, error = await post_once(url, secret, event, body, delivery_id)
        if error is None and 200 <= status < 400:
            break
        if 400 <= status < 500 and status != 429:
            break  # kliens-hiba: újrapróbálni értelmetlen
        logger.info("webhook retry %s/%s for %s (%s)", i + 1, len(RETRY_DELAYS), url, status)
    await _record(endpoint_id, status, error if error is not None else (None if status < 400 else f"HTTP {status}"))
    return status


async def deliver_all(db: AsyncSession, event: str, ctx: dict, *, wait: bool = False) -> int:
    """Az eseményre feliratkozott aktív végpontoknak küldés (háttérben; teszthez wait=True).
    Visszaad: hány végpontnak indult küldés."""
    rows = (await db.execute(select(WebhookEndpoint).where(WebhookEndpoint.active.is_(True)))).scalars().all()
    targets = [r for r in rows if not r.events or event in (r.events or [])]
    if not targets:
        return 0
    body = json.dumps(build_payload(event, ctx), ensure_ascii=False, default=str).encode("utf-8")
    coros = [deliver_with_retries(r.id, r.url, r.secret, event, body) for r in targets]
    if wait:
        await asyncio.gather(*coros, return_exceptions=True)
    else:
        for c in coros:
            try:
                asyncio.get_running_loop().create_task(c)
            except RuntimeError:
                pass
    return len(targets)


async def send_test(ep: WebhookEndpoint) -> tuple[int, str | None]:
    """Próba-küldés a felületről: egy `webhook.test` esemény, azonnali válasszal."""
    body = json.dumps(build_payload("webhook.test", {"uzenet": "Próba az X-adminból", "_endpoint_id": str(ep.id)}),
                      ensure_ascii=False).encode("utf-8")
    status, error = await post_once(ep.url, ep.secret, "webhook.test", body, uuid.uuid4().hex)
    ep.last_status = status
    ep.last_at = datetime.now(UTC)
    ep.last_error = error if error is not None else (None if status < 400 else f"HTTP {status}")
    return status, ep.last_error
