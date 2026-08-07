"""Audit napló (admin): ki mit csinált és mikor — a record_audit által írt
append-only eseménysor olvasható nézete, szűréssel és lapozással."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db import get_db
from app.models import AuditEvent, User

router = APIRouter()


@router.get("")
async def list_audit_events(
    action: str | None = Query(default=None),  # prefix-szűrés, pl. "settlement."
    entity_type: str | None = Query(default=None),
    actor: str | None = Query(default=None),  # user_id
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    query = select(AuditEvent)
    if action:
        query = query.where(AuditEvent.action.like(f"{action}%"))
    if entity_type:
        query = query.where(AuditEvent.entity_type == entity_type)
    if actor:
        try:
            query = query.where(AuditEvent.actor_user_id == uuid.UUID(actor))
        except ValueError:
            raise HTTPException(status_code=422, detail={"code": "audit.bad_actor"})
    if date_from:
        query = query.where(AuditEvent.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.where(AuditEvent.created_at <= datetime.combine(date_to, datetime.max.time()))

    total = (
        await db.execute(select(func.count()).select_from(query.subquery()))
    ).scalar_one()
    rows = (
        await db.execute(
            query.order_by(AuditEvent.created_at.desc()).offset(offset).limit(limit)
        )
    ).scalars().all()

    # az érintett felhasználók nevei egy körben
    actor_ids = {r.actor_user_id for r in rows if r.actor_user_id}
    names: dict[uuid.UUID, str] = {}
    if actor_ids:
        names = {
            uid: name
            for uid, name in (
                await db.execute(
                    select(User.id, User.display_name).where(User.id.in_(actor_ids))
                )
            ).all()
        }

    return {
        "total": int(total),
        "items": [
            {
                "id": str(r.id),
                "actor_user_id": str(r.actor_user_id) if r.actor_user_id else None,
                "actor_name": names.get(r.actor_user_id) if r.actor_user_id else None,
                "action": r.action,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "detail": r.detail,
                "ip_address": r.ip_address,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


@router.get("/actions")
async def list_audit_actions(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    """Előforduló action-értékek (a szűrő legördülőhöz)."""
    rows = (
        await db.execute(select(AuditEvent.action).distinct().order_by(AuditEvent.action))
    ).scalars().all()
    return rows


@router.get("/actors")
async def list_audit_actors(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    """Előforduló szereplők (user id + név) a szűrőhöz."""
    rows = (
        await db.execute(select(AuditEvent.actor_user_id).distinct())
    ).scalars().all()
    ids = [r for r in rows if r]
    if not ids:
        return []
    users = (
        await db.execute(select(User.id, User.display_name).where(User.id.in_(ids)))
    ).all()
    return [{"id": str(uid), "name": name} for uid, name in sorted(users, key=lambda x: x[1])]
