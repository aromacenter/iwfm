"""Auth endpoints: bootstrap-first-admin, login, logout, me.

Login issues an httpOnly JWT cookie. A short, in-memory failed-attempt
throttle protects against credential stuffing (per email+IP, 5 attempts /
15 minutes) — kept deliberately simple for a single-instance deployment.
"""

from __future__ import annotations

import time as _time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, record_audit
from app.core.config import get_settings
from app.core.security import SESSION_COOKIE, hash_password, mint_token, verify_password
from app.db import get_db
from app.models import User

router = APIRouter()

_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 15 * 60
_failed_logins: dict[str, list[float]] = defaultdict(list)


def _throttled(key: str) -> bool:
    now = _time.monotonic()
    attempts = [t for t in _failed_logins[key] if now - t < _WINDOW_SECONDS]
    _failed_logins[key] = attempts
    return len(attempts) >= _MAX_ATTEMPTS


def _record_failure(key: str) -> None:
    _failed_logins[key].append(_time.monotonic())


class BootstrapBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)
    token: str | None = None  # csak ha WFM_BOOTSTRAP_TOKEN be van állítva


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: str


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=str(user.id), email=user.email, display_name=user.display_name, role=user.role
    )


def set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/bootstrap", response_model=UserOut)
async def bootstrap_admin(
    body: BootstrapBody,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create the very first admin account. Only works while the users table
    is empty — afterwards it always returns 403. Ha be van állítva a
    WFM_BOOTSTRAP_TOKEN, a hívásnak tartalmaznia kell az egyező tokent."""
    settings = get_settings()
    if settings.bootstrap_token and body.token != settings.bootstrap_token:
        raise HTTPException(status_code=403, detail={"code": "auth.bootstrap_token"})
    count = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    if count > 0:
        raise HTTPException(status_code=403, detail={"code": "auth.bootstrap_closed"})
    user = User(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        role="admin",
    )
    db.add(user)
    await db.flush()
    await record_audit(
        db, actor=user, action="auth.bootstrap", entity_type="user",
        entity_id=str(user.id), request=request,
    )
    await db.commit()
    set_session_cookie(response, mint_token(user_id=user.id, role=user.role, token_version=user.token_version))
    return _user_out(user)


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginBody,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ip = request.client.host if request.client else "?"
    throttle_key = f"{body.email.lower()}|{ip}"
    if _throttled(throttle_key):
        raise HTTPException(status_code=429, detail={"code": "auth.rate_limited"})

    user = (
        await db.execute(select(User).where(User.email == body.email.lower()))
    ).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(
        user.password_hash, body.password
    ):
        _record_failure(throttle_key)
        # Egységes hibaüzenet — nem áruljuk el, hogy az email létezik-e.
        raise HTTPException(status_code=401, detail={"code": "auth.bad_credentials"})

    _failed_logins.pop(throttle_key, None)
    await record_audit(
        db, actor=user, action="auth.login", entity_type="user",
        entity_id=str(user.id), request=request,
    )
    await db.commit()
    set_session_cookie(response, mint_token(user_id=user.id, role=user.role, token_version=user.token_version))
    return _user_out(user)


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return _user_out(user)
