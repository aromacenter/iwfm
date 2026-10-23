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
from app.models import AuditEvent, Employee, PermissionSettings, Role, User

# ─── Szerepkörök + funkció/művelet-alapú jogosultságok (RBAC v2) ────────────
# Az admin mindig mindent tehet. A többi szerepkör (beépített + saját) jogait
# a permission_settings adja: ``matrix`` = funkciók szerepkörönként,
# ``actions`` = a funkción belül engedett műveletek (megtekintés / létrehozás /
# módosítás / törlés — a HTTP-metódusból). Tiltáskor 403 + naplósor.

SYSTEM_ROLES: dict[str, int] = {"admin": 2, "manager": 1, "uzletkoto": 0, "szervizes": 0, "employee": 0}
ROLE_ORDER = {"employee": 0, "szervizes": 0, "uzletkoto": 0, "manager": 1, "admin": 2}  # beépítettek szintje

ACTIONS = ("view", "create", "edit", "delete")
METHOD_ACTION = {"GET": "view", "HEAD": "view", "OPTIONS": "view", "POST": "create", "PUT": "edit", "PATCH": "edit", "DELETE": "delete"}

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
    "service",        # szerviz (hibajegyek, karbantartás) — magában foglalja az átvételt+tudásbázist
    "intake",         # gép-átvétel (elismervény) — önállóan is adható (pl. alvállalkozó)
    "knowledge",      # tudásbázis — önállóan is adható
    "import_export",  # import/export
    "payroll",        # bérexport
    "my_schedule",    # saját beosztás (önkiszolgáló)
    "my_tasks",       # saját feladatok/munkalap (önkiszolgáló)
    "delete",         # törlés funkciók (alapból csak admin)
    "parts",          # alkatrész-raktár kezelése (raktáros) — a parts modulhoz
)

DEFAULT_MATRIX: dict[str, list[str]] = {
    "manager": [
        "dashboard", "tasks", "schedule", "attendance", "timeoff", "employees",
        "partners", "machines", "products", "settlements", "invoicing",
        "agent_report", "service", "import_export", "payroll", "parts",
    ],
    "uzletkoto": [
        "dashboard", "partners", "machines", "products", "settlements",
        "invoicing", "agent_report",
    ],
    # Külsős szervizes (alvállalkozó): a szervizJEGYEKET nem látja — átvétel,
    # tudásbázis és a saját KSZ-munkalapjai kellenek neki.
    "szervizes": ["intake", "knowledge", "my_tasks"],
    "employee": ["my_schedule", "my_tasks"],
}

# Visszafelé kompatibilitás a régi hívókkal (settings/tesztek): a konfigurálható
# beépített szerepkörök — a tényleges lista a roles táblából jön.
CONFIGURABLE_ROLES = ("manager", "uzletkoto", "szervizes", "employee")

# Szerepkör-szint gyorsítótár (kulcs → level); a roles API módosításkor üríti.
_LEVELS: dict[str, int] | None = None


def invalidate_role_cache() -> None:
    global _LEVELS
    _LEVELS = None


SYSTEM_ROLE_META = {
    "admin": ("Admin", "Mindent elér, minden beállítást kezel. Nem szűkíthető."),
    "manager": ("Manager", "Vezetői szint: feladatok, beosztás, elszámolás, raktár, kassza-áttekintés."),
    "uzletkoto": ("Üzletkötő", "Partnerek, gépek, elszámolás, saját kassza — terepi értékesítő."),
    "szervizes": ("Szervizes", "Külsős szerviz: átvétel, tudásbázis, saját KSZ-munkalapok."),
    "employee": ("Dolgozó", "Önkiszolgáló: saját beosztás, saját feladatok."),
}


async def ensure_system_roles(db: AsyncSession) -> list[Role]:
    """A beépített szerepkörök sorai mindig létezzenek (friss DB / teszt-DB,
    ahol a migráció seed-je nem futott)."""
    rows = (await db.execute(select(Role))).scalars().all()
    have = {r.key for r in rows}
    missing = [k for k in SYSTEM_ROLES if k not in have]
    if missing:
        for k in missing:
            name, desc = SYSTEM_ROLE_META[k]
            db.add(Role(key=k, name=name, description=desc, level=SYSTEM_ROLES[k], is_system=True))
        await db.commit()
        invalidate_role_cache()
        rows = (await db.execute(select(Role))).scalars().all()
    return list(rows)


async def get_roles(db: AsyncSession) -> list[Role]:
    """Minden szerepkör: beépítettek szint szerint csökkenőben, utána a sajátok név szerint."""
    rows = await ensure_system_roles(db)
    return sorted(rows, key=lambda r: (not r.is_system, -r.level if r.is_system else 0, r.name.lower()))


async def get_role_levels(db: AsyncSession) -> dict[str, int]:
    global _LEVELS
    if _LEVELS is None:
        rows = [(r.key, r.level) for r in await ensure_system_roles(db)]
        levels = {k: int(v) for k, v in rows}
        for k, v in SYSTEM_ROLES.items():  # a tábla nélkül (régi DB) is működjön
            levels.setdefault(k, v)
        _LEVELS = levels
    return _LEVELS


def role_level(role: str) -> int:
    """Szinkron szint-lekérdezés a gyorsítótárból (a get_current_user feltölti)."""
    if _LEVELS is not None and role in _LEVELS:
        return _LEVELS[role]
    return SYSTEM_ROLES.get(role, 0)


def is_manager(user: User) -> bool:
    """Vezetői szint (admin, manager, vagy 1-es szintű saját szerepkör) — a
    kassza/raktár/áttekintés „csak vezető” szabályaihoz."""
    return user.role == "admin" or role_level(user.role) >= 1


async def _configurable_role_keys(db: AsyncSession) -> list[str]:
    levels = await get_role_levels(db)
    return [k for k in levels if k != "admin"]


async def get_permission_matrix(db: AsyncSession) -> dict[str, list[str]]:
    """A mentett mátrix a beépített alapértelmezésekkel kiegészítve — minden
    (beépített + saját) szerepkörre."""
    row = (
        await db.execute(select(PermissionSettings).where(PermissionSettings.id == 1))
    ).scalar_one_or_none()
    stored = row.matrix if row is not None and isinstance(row.matrix, dict) else {}
    out: dict[str, list[str]] = {}
    for role in await _configurable_role_keys(db):
        value = stored.get(role)
        if isinstance(value, list):
            out[role] = [f for f in value if f in FEATURES]
        else:
            out[role] = list(DEFAULT_MATRIX.get(role, []))
        # Visszafelé kompatibilitás: a "service" jog magában foglalja az
        # átvételt és a tudásbázist (régebben egy jog volt mindhárom).
        if "service" in out[role]:
            for extra in ("intake", "knowledge"):
                if extra not in out[role]:
                    out[role].append(extra)
        # Az alkatrész-raktár (parts) jog az elszámolás-jogból örökölt — a
        # mentett (régebbi) mátrixokon se vesszen el a raktáros hozzáférés.
        if "settlements" in out[role] and "parts" not in out[role]:
            out[role].append("parts")
    return out


async def get_action_matrix(db: AsyncSession) -> dict[str, dict[str, list[str]]]:
    """{role: {feature: [engedett műveletek]}} — ami nincs benne, ott minden
    művelet engedett (ha a funkció maga engedett)."""
    row = (
        await db.execute(select(PermissionSettings).where(PermissionSettings.id == 1))
    ).scalar_one_or_none()
    stored = row.actions if row is not None and isinstance(row.actions, dict) else {}
    out: dict[str, dict[str, list[str]]] = {}
    for role, feats in stored.items():
        if not isinstance(feats, dict):
            continue
        clean: dict[str, list[str]] = {}
        for feature, acts in feats.items():
            if feature in FEATURES and isinstance(acts, list):
                clean[feature] = [a for a in ACTIONS if a in acts]
        out[role] = clean
    return out


def permissions_for(role: str, matrix: dict[str, list[str]]) -> list[str]:
    if role == "admin":
        return list(FEATURES)
    return matrix.get(role, [])


def action_allowed(role: str, feature: str, action: str, actions: dict[str, dict[str, list[str]]]) -> bool:
    if role == "admin":
        return True
    acts = actions.get(role, {}).get(feature)
    return True if acts is None else action in acts


def denied_actions_for(role: str, actions: dict[str, dict[str, list[str]]]) -> list[str]:
    """A felületnek: „feature.action” lista, amit a szerepkör NEM tehet."""
    if role == "admin":
        return []
    out: list[str] = []
    for feature, acts in actions.get(role, {}).items():
        for a in ACTIONS:
            if a not in acts:
                out.append(f"{feature}.{a}")
    return out


async def _deny(db: AsyncSession, request: Request | None, user: User, feature: str, action: str) -> None:
    """403 + naplósor: ki, mit, hol próbált (a tiltás is naplózva — user-kérés)."""
    try:
        await record_audit(
            db, actor=user, action="auth.denied", entity_type="permission", entity_id=feature,
            detail={"feature": feature, "action": action, "role": user.role,
                    "method": request.method if request else None,
                    "path": str(request.url.path) if request else None},
            request=request,
        )
        await db.commit()
    except Exception:  # pragma: no cover — a napló hibája nem állíthatja meg a tiltást
        await db.rollback()
    raise HTTPException(
        status_code=403,
        detail={"code": "auth.forbidden_action", "feature": feature, "action": action},
    )


def _action_of(request: Request | None) -> str:
    return METHOD_ACTION.get((request.method if request else "GET").upper(), "view")


def require_any_perm(*features: str):
    """Guard: legalább az egyik funkció engedélyezett (admin mindig) — pl. a
    gép-lista a szervizeseknek is kell a gép-választóhoz. A művelet (HTTP-
    metódus) is engedett kell legyen az adott funkción."""

    async def _guard(
        request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
    ) -> User:
        if user.role == "admin":
            return user
        matrix = await get_permission_matrix(db)
        allowed = matrix.get(user.role, [])
        granted = [f for f in features if f in allowed]
        action = _action_of(request)
        if not granted:
            await _deny(db, request, user, features[0], action)
        actions = await get_action_matrix(db)
        if not any(action_allowed(user.role, f, action, actions) for f in granted):
            await _deny(db, request, user, granted[0], action)
        return user

    return _guard


def require_perm(feature: str):
    """Guard: a funkció ÉS a művelet (GET=megtekintés, POST=létrehozás,
    PUT/PATCH=módosítás, DELETE=törlés) engedélyezett-e a hívó szerepkörének
    (admin mindig). Tiltáskor 403 auth.forbidden_action + naplósor."""

    async def _guard(
        request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
    ) -> User:
        if user.role == "admin":
            return user
        matrix = await get_permission_matrix(db)
        action = _action_of(request)
        if feature not in matrix.get(user.role, []):
            await _deny(db, request, user, feature, action)
        if not action_allowed(user.role, feature, action, await get_action_matrix(db)):
            await _deny(db, request, user, feature, action)
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
    await get_role_levels(db)  # a szint-gyorsítótár (is_manager / require_role) feltöltése
    return user


def require_role(minimum: str):
    """Guard: caller's role must be at least ``minimum`` in the hierarchy."""

    async def _guard(
        request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
    ) -> User:
        # admin-szint: CSAK a beépített admin; manager-szint: 1-es szintű (saját) szerepkör is
        need = ROLE_ORDER[minimum]
        have = 2 if user.role == "admin" else min(role_level(user.role), 1)
        if have < need:
            await _deny(db, request, user, f"role:{minimum}", _action_of(request))
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
