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
from app.models import AuditEvent, Employee, PermissionSettings, User

ROLE_ORDER = {"employee": 0, "szervizes": 0, "uzletkoto": 0, "manager": 1, "admin": 2}

# ─── Funkció-alapú jogosultságok ─────────────────────────────────────────────
# Az admin mindig mindent tehet. A többi szerepkör jogait a permission_settings
# mátrix adja (Beállítások → Jogosultságok); hiányzó szerepkörre a DEFAULT.

CONFIGURABLE_ROLES = ("manager", "uzletkoto", "szervizes", "employee")

FEATURES = (
    "dashboard",      # vezérlőpult
    "tasks",          # feladatok kezelése
    "schedule",       # beosztás
    "attendance",     # jelenlét
    "timeoff",        # távollét
    "employees",      # dolgozók (lista/megtekintés)
    "partners",       # partnerek
    "machines",       # gépek
    "products",       # termékek
    "settlements",    # elszámolás (készlet-feltöltés is)
    "invoicing",      # kiszámlázás (Billingó)
    "agent_report",   # üzletkötő-elszámolás
    "service",        # szerviz (hibajegyek, karbantartás)
    "import_export",  # import/export
    "payroll",        # bérexport
    "my_schedule",    # saját beosztás (önkiszolgáló)
    "my_tasks",       # saját feladatok/munkalap (önkiszolgáló)
    "delete",         # törlés funkciók (alapból csak admin)
)

DEFAULT_MATRIX: dict[str, list[str]] = {
    "manager": [
        "dashboard", "tasks", "schedule", "attendance", "timeoff", "employees",
        "partners", "machines", "products", "settlements", "invoicing",
        "agent_report", "service", "import_export", "payroll",
    ],
    "uzletkoto": [
        "dashboard", "partners", "machines", "products", "settlements",
        "invoicing", "agent_report",
    ],
    "szervizes": ["machines", "service", "my_schedule", "my_tasks"],
    "employee": ["my_schedule", "my_tasks"],
}


async def get_permission_matrix(db: AsyncSession) -> dict[str, list[str]]:
    """A mentett mátrix a beépített alapértelmezésekkel kiegészítve."""
    row = (
        await db.execute(select(PermissionSettings).where(PermissionSettings.id == 1))
    ).scalar_one_or_none()
    stored = row.matrix if row is not None and isinstance(row.matrix, dict) else {}
    out: dict[str, list[str]] = {}
    for role in CONFIGURABLE_ROLES:
        value = stored.get(role)
        if isinstance(value, list):
            out[role] = [f for f in value if f in FEATURES]
        else:
            out[role] = list(DEFAULT_MATRIX.get(role, []))
    return out


def permissions_for(role: str, matrix: dict[str, list[str]]) -> list[str]:
    if role == "admin":
        return list(FEATURES)
    return matrix.get(role, [])


def require_perm(feature: str):
    """Guard: a funkció engedélyezett-e a hívó szerepkörének (admin mindig)."""

    async def _guard(
        user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
    ) -> User:
        if user.role == "admin":
            return user
        matrix = await get_permission_matrix(db)
        if feature not in matrix.get(user.role, []):
            raise HTTPException(status_code=403, detail={"code": "auth.forbidden"})
        return user

    return _guard


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
