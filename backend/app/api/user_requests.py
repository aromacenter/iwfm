"""170. kör: a Flotta-pult fiók-kéréseinek jóváhagyása a cég adminja által.

A felület (AppShell) adminként percenként lekérdezi a nyitott tételeket, és
felugró ablakban mutatja: új fiók kérése (Elfogadom / Elutasítom), illetve a
pultból megszüntetett fiókról szóló értesítő (Rendben)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db import get_db
from app.models import User
from app.services.wfm import user_requests as ur

router = APIRouter()


@router.get("")
async def list_open(db: AsyncSession = Depends(get_db), actor: User = Depends(require_role("admin"))):
    rows = await ur.open_for_admin(db)
    await db.commit()  # a lejáratás írt
    return {"items": [ur.req_out(r) for r in rows]}


@router.post("/{rid}/approve")
async def approve(rid: str, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(require_role("admin"))):
    req = await ur.get_request_or_404(db, rid)
    user, url = await ur.approve(db, req, actor, request)
    await db.commit()
    return {"request": ur.req_out(req), "reset_url": url, "expires_hours": ur.RESET_TOKEN_HOURS, "user_id": str(user.id)}


class DeclineBody(BaseModel):
    reason: str | None = Field(default=None, max_length=256)


@router.post("/{rid}/decline")
async def decline(rid: str, body: DeclineBody, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(require_role("admin"))):
    req = await ur.get_request_or_404(db, rid)
    await ur.decline(db, req, actor, body.reason, request)
    await db.commit()
    return {"request": ur.req_out(req)}


@router.post("/{rid}/ack")
async def ack(rid: str, db: AsyncSession = Depends(get_db), actor: User = Depends(require_role("admin"))):
    from datetime import UTC, datetime

    req = await ur.get_request_or_404(db, rid)
    if req.kind == "delete" and req.status == "done":
        req.status = "acked"
        req.acked_at = datetime.now(UTC)
        req.decided_by_user_id = actor.id
        await db.commit()
    return {"request": ur.req_out(req)}
