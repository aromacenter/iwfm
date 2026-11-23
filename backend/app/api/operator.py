"""Üzemeltetői (Flotta-pult) végpontok: a példány licencének táv-kezelése.

Csak a WFM_OPERATOR_TOKEN birtokában hívhatók (X-Operator-Token fejléc) —
a tokent a Flotta-pult tárolja titkosítva; beállítatlan tokennél a végpontok
teljesen zárva vannak. Lejárt licencnél is működnek (a hosszabbításhoz épp
ez kell), ezért a lejárat-középréteg kivétel-listáján szerepelnek.
"""

from __future__ import annotations

import os
import secrets as _secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.settings import LicenseBody, _license_status_payload, apply_license_update
from app.core.config import get_settings
from app.db import get_db

router = APIRouter()


async def require_operator(
    x_operator_token: str | None = Header(default=None),
) -> None:
    token = get_settings().operator_token
    if (
        not token
        or not x_operator_token
        or not _secrets.compare_digest(token, x_operator_token)
    ):
        raise HTTPException(status_code=403, detail={"code": "operator.forbidden"})


@router.get("/status", dependencies=[Depends(require_operator)])
async def operator_status(db: AsyncSession = Depends(get_db)):
    """Példány-állapot a Flotta-pultnak: licenc + kihasználtság + a futó
    verzió (Railway RAILWAY_GIT_COMMIT_SHA) a központi frissítés-ellenőrzéshez."""
    from app.services.wfm.license import MODULE_REQUIRES, MODULES

    commit = (os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("WFM_GIT_COMMIT") or "")[:7] or None
    return {
        "app": "iwfm", "commit": commit,
        "module_catalog": list(MODULES),
        "module_requires": {k: list(v) for k, v in MODULE_REQUIRES.items()},
        **(await _license_status_payload(db)),
        **(await _health_signals(db)),
    }


async def _health_signals(db: AsyncSession) -> dict:
    """141. kör: a pult egészség-pontszámához — aktivitás az utolsó 7 napból
    (belépések, elszámolások, feladatok), utolsó aktivitás, nyitott hibajegyek."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func as sa_func

    from app.models import AuditEvent, BugReport, Settlement, Task

    since = datetime.now(UTC) - timedelta(days=7)

    async def _count(stmt) -> int:
        return int((await db.execute(stmt)).scalar_one() or 0)

    logins = await _count(select(sa_func.count()).select_from(AuditEvent).where(AuditEvent.action == "auth.login", AuditEvent.created_at >= since))
    settlements = await _count(select(sa_func.count()).select_from(Settlement).where(Settlement.created_at >= since))
    tasks = await _count(select(sa_func.count()).select_from(Task).where(Task.created_at >= since))
    last = (await db.execute(select(sa_func.max(AuditEvent.created_at)))).scalar_one()
    open_bugs = await _count(
        select(sa_func.count()).select_from(BugReport).where(
            BugReport.kind == "bug", BugReport.status.in_(("new", "confirmed", "reopened", "needs_info", "answered"))
        )
    )
    return {
        "activity": {
            "logins_7d": logins, "settlements_7d": settlements, "tasks_7d": tasks,
            "last_activity_at": last.isoformat() if last else None,
        },
        "open_bugs": open_bugs,
    }


# ─── Hibajelentések táv-kezelése a Flotta-pultból ───────────────────────────


@router.get("/bugs", dependencies=[Depends(require_operator)])
async def operator_bugs(
    status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    from app.api.bugs import KINDS, _outs
    from app.models import BugReport

    q = select(BugReport).order_by(BugReport.created_at.desc()).limit(300)
    if status:
        q = q.where(BugReport.status == status)
    if severity:
        q = q.where(BugReport.severity == severity)
    if kind in KINDS:
        q = q.where(BugReport.kind == kind)
    rows = (await db.execute(q)).scalars().all()
    return await _outs(db, rows)


@router.post("/bugs/{bug_id}/comments", dependencies=[Depends(require_operator)])
async def operator_bug_comment(
    bug_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    body: dict | None = None,
):
    """Az üzemeltető kérdez / válaszol a jegyen (114. kör). Státusz nélkül a
    nyitott jegy needs_info-ra kerül (visszadobva a bejelentőnek), aki a
    felületen felugró ablakban kapja meg a kérdést."""
    from app.api.bugs import _bug_or_404, _outs, add_comment
    from app.api.deps import record_audit

    body = body or {}
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail={"code": "bugs.empty_comment"})
    b = await _bug_or_404(db, bug_id)
    await add_comment(
        db, b, kind="operator", author_name=(body.get("author_name") or "Üzemeltető"),
        text=text, status=body.get("status") or None,
    )
    await record_audit(
        db, actor=None, action="bug.operator_comment", entity_type="bug",
        entity_id=str(b.id), detail={"status": b.status}, request=request,
    )
    await db.commit()
    return (await _outs(db, [b]))[0]


@router.post("/bugs/{bug_id}/seen", dependencies=[Depends(require_operator)])
async def operator_bug_seen(bug_id: str, db: AsyncSession = Depends(get_db)):
    from datetime import UTC as _UTC, datetime as _dt

    from app.api.bugs import _bug_or_404

    b = await _bug_or_404(db, bug_id)
    b.operator_seen_at = _dt.now(_UTC)
    await db.commit()
    return {"ok": True}


@router.patch("/bugs/{bug_id}", dependencies=[Depends(require_operator)])
async def operator_bug_update(
    bug_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    body: dict | None = None,
):
    from datetime import UTC as _UTC, datetime as _dt

    from app.api.bugs import SEVERITIES, _bug_or_404, _out, statuses_for
    from app.api.deps import record_audit

    b = await _bug_or_404(db, bug_id)
    body = body or {}
    if body.get("status") is not None:
        if body["status"] not in statuses_for(b.kind or "bug"):
            raise HTTPException(status_code=422, detail={"code": "bugs.bad_status"})
        b.status = body["status"]
    if body.get("priority") is not None and (b.kind or "bug") == "idea":
        try:
            pr = int(body["priority"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail={"code": "bugs.bad_priority"})
        if pr not in (1, 2, 3):
            raise HTTPException(status_code=422, detail={"code": "bugs.bad_priority"})
        b.priority = pr
    if body.get("severity") is not None:
        if body["severity"] not in SEVERITIES:
            raise HTTPException(status_code=422, detail={"code": "bugs.bad_severity"})
        b.severity = body["severity"]
    if "fix_group" in body:
        b.fix_group = (body.get("fix_group") or "").strip()[:64] or None
    if "resolution_note" in body:
        b.resolution_note = (body.get("resolution_note") or "").strip()[:512] or None
    b.updated_at = _dt.now(_UTC)
    await record_audit(
        db, actor=None, action="bug.operator_update", entity_type="bug",
        entity_id=str(b.id), detail={"status": body.get("status")}, request=request,
    )
    await db.commit()
    return _out(b)


@router.delete("/bugs/{bug_id}", dependencies=[Depends(require_operator)])
async def operator_bug_delete(
    bug_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Hibajegy végleges törlése a Flotta-pultból — teszt-időszaki
    takarításhoz."""
    from app.api.bugs import _bug_or_404
    from app.api.deps import record_audit

    b = await _bug_or_404(db, bug_id)
    await record_audit(
        db, actor=None, action="bug.operator_delete", entity_type="bug",
        entity_id=str(b.id), detail={"description": b.description[:80]},
        request=request,
    )
    await db.delete(b)
    await db.commit()
    return {"ok": True}


@router.get("/bugs/{bug_id}/screenshot", dependencies=[Depends(require_operator)])
async def operator_bug_screenshot(
    bug_id: str,
    db: AsyncSession = Depends(get_db),
):
    from app.api.bugs import _bug_or_404

    b = await _bug_or_404(db, bug_id)
    if b.screenshot is None:
        raise HTTPException(status_code=404, detail={"code": "bugs.no_screenshot"})
    return Response(
        content=bytes(b.screenshot), media_type=b.screenshot_mime or "image/png"
    )


ROADMAP_STATUSES = ("planned", "in_progress", "done")


class RoadmapPushBody(BaseModel):
    # [{title, description, status, eta, sort}] — teljes csere (a Flotta a forrás)
    items: list[dict] = Field(max_length=200)


@router.put("/roadmap", dependencies=[Depends(require_operator)])
async def operator_set_roadmap(
    body: RoadmapPushBody,
    db: AsyncSession = Depends(get_db),
):
    """A Flotta-pult roadmap-lenyomása (115. kör): a példány 🗺 Roadmap
    menüje ebből épül. Teljes csere, tisztítva."""
    from app.models import LicenseSettings
    from app.services.wfm import license as license_service

    row = await license_service.get_license_row(db)
    if row is None:
        row = LicenseSettings(id=1)
        db.add(row)
    cleaned = []
    for it in body.items[:200]:
        title = str(it.get("title") or "").strip()[:256]
        if not title:
            continue
        status = str(it.get("status") or "planned")
        cleaned.append({
            "title": title,
            "description": str(it.get("description") or "").strip()[:4000],
            "status": status if status in ROADMAP_STATUSES else "planned",
            "eta": str(it.get("eta") or "").strip()[:64],
            "sort": int(it.get("sort") or 0),
        })
    cleaned.sort(key=lambda x: x["sort"])
    row.roadmap = cleaned
    await db.commit()
    license_service.invalidate_cache()
    return {"ok": True, "stored": len(cleaned)}


class LaborRulesPushBody(BaseModel):
    # [{valid_from: "ÉÉÉÉ-HH-NN", sunday_pct, holiday_pct, …}] — teljes csere
    items: list[dict] = Field(max_length=50)


@router.put("/labor-rules", dependencies=[Depends(require_operator)])
async def operator_set_labor_rules(
    body: LaborRulesPushBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """A Flotta-pult munkajogi szabály-felülírásának lenyomása (117. kör):
    a példány labor_rules.rules_for() ezt egyesíti a kódban élő készletekkel.
    Csak érvényes elemeket tárolunk; a példány-admin ezt nem szerkesztheti."""
    from app.api.deps import record_audit
    from app.models import LicenseSettings
    from app.services.wfm import license as license_service
    from app.services.wfm.labor_rules import ruleset_from_dict

    row = await license_service.get_license_row(db)
    if row is None:
        row = LicenseSettings(id=1)
        db.add(row)
    cleaned = []
    for it in body.items[:50]:
        rs = ruleset_from_dict(it)
        if rs is not None:
            cleaned.append(rs.as_dict())
    cleaned.sort(key=lambda x: x["valid_from"])
    row.labor_rules_override = cleaned or None
    await record_audit(
        db, actor=None, action="labor_rules.pushed", entity_type="license",
        entity_id="1", detail={"count": len(cleaned),
                               "valid_from": [c["valid_from"] for c in cleaned]},
        request=request,
    )
    await db.commit()
    license_service.invalidate_cache()
    return {"ok": True, "stored": len(cleaned)}


class PlanCatalogBody(BaseModel):
    # [{code, name, max_users, max_employees, price_monthly, price_yearly}]
    plans: list[dict] = Field(max_length=50)


@router.put("/plan-catalog", dependencies=[Depends(require_operator)])
async def operator_set_plan_catalog(
    body: PlanCatalogBody,
    db: AsyncSession = Depends(get_db),
):
    """A Flotta-pult csomag-katalógusának lenyomása — az ügyfél-oldali
    "Elérhető csomagok" kártyák ebből épülnek (név + limit + ár)."""
    from app.models import LicenseSettings
    from app.services.wfm import license as license_service

    row = await license_service.get_license_row(db)
    if row is None:
        row = LicenseSettings(id=1)
        db.add(row)
    cleaned = []
    for p in body.plans[:50]:
        code = str(p.get("code") or "").strip().lower()[:16]
        if not code:
            continue
        cleaned.append({
            "code": code,
            "name": str(p.get("name") or code.upper())[:64],
            "max_users": p.get("max_users"),
            "max_employees": p.get("max_employees"),
            "price_monthly": p.get("price_monthly"),
            "price_yearly": p.get("price_yearly"),
        })
    row.plan_catalog = cleaned
    await db.commit()
    license_service.invalidate_cache()
    return {"ok": True, "count": len(cleaned)}


class OperatorLogin2faBody(BaseModel):
    mode: str = Field(pattern="^(off|linked|required)$")


@router.put("/login-2fa", dependencies=[Depends(require_operator)])
async def operator_login_2fa(body: OperatorLogin2faBody, db: AsyncSession = Depends(get_db)):
    """Vészkapcsoló a Flotta-pultról (147. kör): a kétlépcsős belépés módjának
    átállítása, ha a cég kizárta magát (pl. a bot leállt)."""
    from app.api.deps import record_audit
    from app.services.wfm import login_2fa as twofa

    await twofa.set_mode(db, body.mode)
    await record_audit(
        db, actor=None, action="settings.login_2fa_operator", entity_type="settings",
        entity_id="login_2fa", detail={"mode": body.mode}, request=None,
    )
    await db.commit()
    return {"mode": body.mode}


# ── 170. kör: fiók-kérés a Flotta-pultból ───────────────────────────────────
# A pult nem hoz létre fiókot: kérést ad le, amiről a cég adminja a felületén
# felugró ablakban dönt (app/api/user_requests.py). A pult sosem lát és sosem
# ad meg jelszót — a jóváhagyott fiók 24 órás jelszó-beállító linket kap.
# Üzemeltetői fiók: mindig admin jogkörű, nem számít a licencbe, piros sáv jelzi.


class OperatorUserBody(BaseModel):
    last_name: str = Field(min_length=1, max_length=128)
    first_name: str = Field(min_length=1, max_length=128)
    email: EmailStr | None = None
    username: str | None = Field(default=None, max_length=64)
    role: str = Field(default="employee", pattern="^[a-z][a-z0-9_]{1,31}$")
    with_employee: bool = True  # dolgozói karton is (beosztás, munkaidő, tananyag)
    is_operator: bool = True  # üzemeltetői (X-systems) fiók
    note: str | None = Field(default=None, max_length=512)  # üzenet az adminnak

    @field_validator("username")
    @classmethod
    def _username(cls, v: str | None) -> str | None:
        from app.api.employees import _clean_username

        return _clean_username(v)


def _user_row(u, has_employee: bool, from_operator: bool = False) -> dict:
    return {
        "id": str(u.id), "display_name": u.display_name, "email": u.email,
        "username": u.username, "role": u.role, "is_active": bool(u.is_active),
        "is_operator": bool(getattr(u, "is_operator", False)),
        "has_employee": has_employee, "from_operator": from_operator,
    }


@router.get("/users", dependencies=[Depends(require_operator)])
async def operator_users(db: AsyncSession = Depends(get_db)):
    """A példány fiókjai (jelszó nélkül), szerepkörei, licenc fiók-limitje és a
    pult kéréseinek állapota — a pult felhasználó-kezelő ablakához."""
    from app.api.deps import get_roles
    from app.models import Employee, User
    from app.services.wfm import user_requests as ur
    from app.services.wfm.license import get_license_row, limits_for, usage

    rows = (
        await db.execute(
            select(User, Employee.id).outerjoin(Employee, Employee.user_id == User.id)
            .order_by(User.display_name)
        )
    ).all()
    origin = await ur.operator_origin_user_ids(db)
    roles = await get_roles(db)
    lim = limits_for(await get_license_row(db))
    reqs = await ur.recent_for_operator(db)
    await db.commit()  # a lejáratás írt
    return {
        "users": [_user_row(u, emp_id is not None, u.id in origin) for u, emp_id in rows],
        "roles": [{"key": r.key, "name": r.name} for r in roles],
        "max_users": lim["max_users"], "usage": await usage(db),
        "requests": [ur.req_out(r) for r in reqs],
        "pending_days": ur.PENDING_DAYS,
    }


@router.post("/users", status_code=202, dependencies=[Depends(require_operator)])
async def operator_request_user(
    body: OperatorUserBody, request: Request, db: AsyncSession = Depends(get_db)
):
    """Fiók-kérés: ellenőrzés előre (szerepkör, azonosító, ütközés, licenc), aztán
    a kérés vár a cég adminjának döntésére; az adminok értesítést kapnak."""
    from app.api.deps import get_role_levels, record_audit
    from app.models import OperatorUserRequest, User
    from app.services.wfm import user_requests as ur
    from app.services.wfm.license import check_capacity

    role = "admin" if body.is_operator else body.role
    if role not in await get_role_levels(db):
        raise HTTPException(status_code=422, detail={"code": "employee.bad_role"})
    email = body.email.lower() if body.email else None
    if not email and not body.username:
        raise HTTPException(status_code=422, detail={"code": "employee.login_id_required"})
    has_admin = (
        await db.execute(
            select(User.id).where(User.role == "admin", User.is_active.is_(True), User.is_operator.is_(False)).limit(1)
        )
    ).first()
    if has_admin is None:
        raise HTTPException(status_code=422, detail={"code": "user_request.no_admin"})
    await ur.ensure_login_free(db, email, body.username)
    dupe = (
        await db.execute(
            select(OperatorUserRequest.id).where(
                OperatorUserRequest.status == "pending",
                (OperatorUserRequest.email == email) if email else (OperatorUserRequest.username == body.username),
            ).limit(1)
        )
    ).first()
    if dupe is not None:
        raise HTTPException(status_code=409, detail={"code": "user_request.duplicate"})
    if not body.is_operator:
        await check_capacity(db, new_users=1, new_employees=1 if body.with_employee else 0)
    req = OperatorUserRequest(
        kind="create", status="pending", last_name=body.last_name.strip(), first_name=body.first_name.strip(),
        email=email, username=body.username, role=role, with_employee=body.with_employee,
        is_operator=body.is_operator, note=(body.note or "").strip() or None,
    )
    db.add(req)
    await db.flush()
    await record_audit(
        db, actor=None, action="user.request_operator", entity_type="user_request", entity_id=str(req.id),
        detail={"name": f"{req.last_name} {req.first_name}", "role": role, "operator": req.is_operator,
                "login": email or body.username},
        request=request,
    )
    await db.commit()
    kind = "üzemeltetői (X-systems, admin jogkörű)" if req.is_operator else f"{role} szerepkörű"
    await ur.notify_admins(
        db, "X-admin: fiók-kérés az üzemeltetőtől",
        f"Az üzemeltető (X-systems) {kind} fiókot kér hozzáadni: {req.last_name} {req.first_name} "
        f"({email or body.username}).\n{('Üzenet: ' + req.note) if req.note else ''}\n"
        "A döntés az X-admin felületén, adminként belépve a felugró ablakban hozható meg "
        f"({ur.PENDING_DAYS} napig él a kérés).",
    )
    return {"request": ur.req_out(req)}


@router.delete("/users/requests/{rid}", dependencies=[Depends(require_operator)])
async def operator_withdraw_request(rid: str, request: Request, db: AsyncSession = Depends(get_db)):
    from datetime import UTC, datetime

    from app.api.deps import record_audit
    from app.services.wfm import user_requests as ur

    req = await ur.get_request_or_404(db, rid)
    if req.status != "pending":
        raise HTTPException(status_code=409, detail={"code": "user_request.not_pending"})
    req.status = "withdrawn"
    req.decided_at = datetime.now(UTC)
    await record_audit(
        db, actor=None, action="user.request_withdrawn", entity_type="user_request", entity_id=str(req.id),
        detail={"name": f"{req.last_name} {req.first_name}"}, request=request,
    )
    await db.commit()
    return {"request": ur.req_out(req)}


async def _operator_user_or_404(db: AsyncSession, user_id: str):
    import uuid as _uuid

    from app.models import User
    from app.services.wfm import user_requests as ur

    try:
        uid = _uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "user.not_found"})
    user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    if user is None or uid not in await ur.operator_origin_user_ids(db):
        raise HTTPException(status_code=404, detail={"code": "user.not_found"})
    return user


@router.delete("/users/{user_id}", dependencies=[Depends(require_operator)])
async def operator_deactivate_user(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """A pultból létrejött fiók megszüntetése: letiltás + kiléptetés + értesítő az adminnak."""
    from app.models import Employee
    from app.services.wfm import user_requests as ur

    user = await _operator_user_or_404(db, user_id)
    if not user.is_active:
        raise HTTPException(status_code=409, detail={"code": "user.already_inactive"})
    notice = await ur.deactivate_from_operator(db, user, request)
    has_emp = (await db.execute(select(Employee.id).where(Employee.user_id == user.id))).first() is not None
    await db.commit()
    await ur.notify_admins(
        db, "X-admin: üzemeltetői fiók megszüntetve",
        f"Az üzemeltető (X-systems) megszüntette a pultból létrehozott fiókot: {user.display_name} "
        f"({user.email or user.username}). A fiók letiltva, a belépései érvénytelenek.",
    )
    return {"ok": True, "user": _user_row(user, has_emp, True), "notice": ur.req_out(notice)}


@router.post("/users/{user_id}/login-link", dependencies=[Depends(require_operator)])
async def operator_login_link(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Egygombos belépés a pultból: 2 perces, egyszer használatos link — csak
    aktív üzemeltetői fiókra. Naplózva."""
    from app.api.deps import record_audit
    from app.core.security import OPERATOR_LOGIN_SECONDS, mint_operator_login_token

    user = await _operator_user_or_404(db, user_id)
    if not user.is_active or not user.is_operator:
        raise HTTPException(status_code=409, detail={"code": "user.not_operator"})
    token = mint_operator_login_token(user_id=user.id, token_version=user.token_version)
    url = f"{get_settings().frontend_origin.rstrip('/')}/login?ot={token}"
    await record_audit(
        db, actor=None, action="user.login_link_operator", entity_type="user", entity_id=str(user.id),
        detail={"name": user.display_name}, request=request,
    )
    await db.commit()
    return {"url": url, "expires_seconds": OPERATOR_LOGIN_SECONDS, "name": user.display_name}


@router.put("/license", dependencies=[Depends(require_operator)])
async def operator_set_license(
    body: LicenseBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Licenc-sáv / érvényesség állítása a Flotta-pultból (actor nélkül,
    de audit-naplóval)."""
    return await apply_license_update(db, body, actor=None, request=request)
