"""Shared FastAPI dependencies: current user, role guards, audit helper.

Role model (single-tenant):
* ``admin``   — everything, including sensitive PII reveal and user management
* ``manager`` — scheduling, time-off decisions, attendance, payroll export
* ``employee``— self-service only (own schedule, own time-off, own clock in/out)
"""

from __future__ import annotations

import uuid

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import SESSION_COOKIE, decode_token
from app.db import get_db
from app.models import AuditEvent, Employee, User

ROLE_ORDER = {"employee": 0, "manager": 1, "admin": 2}


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(SESSION_COOKIE)


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User:
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail={"code": "auth.missing"})
    try:
        payload = decode_token(token)
        user_id = uuid.UUID(payload["sub"])
    except (pyjwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail={"code": "auth.invalid"})
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail={"code": "auth.invalid"})
    # Token-verzió: jelszóváltás/kényszerített kijelentkeztetés érvényteleníti
    # a régi tokeneket. (A régi, tv nélküli tokenek 0-nak számítanak.)
    if int(payload.get("tv", 0)) != user.token_version:
        raise HTTPException(status_code=401, detail={"code": "auth.invalid"})
    return user


def require_role(minimum: str):
    """Guard: caller's role must be at least ``minimum`` in the hierarchy."""

    async def _guard(user: User = Depends(get_current_user)) -> User:
        if ROLE_ORDER.get(user.role, -1) < ROLE_ORDER[minimum]:
            raise HTTPException(status_code=403, detail={"code": "auth.forbidden"})
        return user

    return _guard


async def get_own_employee(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> Employee:
    """Resolve the Employee record belonging to the logged-in user (self-service)."""
    emp = (
        await db.execute(select(Employee).where(Employee.user_id == user.id))
    ).scalar_one_or_none()
    if emp is None:
        raise HTTPException(status_code=404, detail={"code": "employee.none_for_user"})
    return emp


async def record_audit(
    db: AsyncSession,
    *,
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    detail: dict | None = None,
    request: Request | None = None,
) -> None:
    """Append an audit row inside the caller's transaction (commit is theirs)."""
    db.add(
        AuditEvent(
            actor_user_id=actor.id if actor else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
            ip_address=request.client.host if request and request.client else None,
        )
    )
