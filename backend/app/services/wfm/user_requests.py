"""170. kör: fiók-kérések a Flotta-pultból.

A pult nem hoz létre fiókot közvetlenül: kérést ad le, amiről a cég adminja a
felületén felugró ablakban dönt. Elfogadáskor jön létre a fiók (üzemeltetői
fióknál mindig admin jogkörrel, a licencbe nem számít), és egy 24 órás
jelszó-beállító link készül — jelszót senki nem lát és nem ad meg más helyett.
A pultból megszüntetett fiókról az admin értesítést kap (felugró + Telegram/e-mail).
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_role_levels, record_audit
from app.core.config import get_settings
from app.core.security import RESET_TOKEN_HOURS, hash_password, mint_reset_token
from app.models import Employee, OperatorUserRequest, User

logger = logging.getLogger(__name__)

PENDING_DAYS = 7  # ennyi nap után a döntetlen kérés lejár


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def req_out(r: OperatorUserRequest) -> dict:
    return {
        "id": str(r.id), "kind": r.kind, "status": r.status,
        "display_name": f"{r.last_name} {r.first_name}",
        "last_name": r.last_name, "first_name": r.first_name,
        "email": r.email, "username": r.username, "login": r.email or r.username,
        "role": "admin" if r.is_operator else r.role,
        "with_employee": r.with_employee, "is_operator": r.is_operator, "note": r.note,
        "requested_at": _iso(r.requested_at), "decided_at": _iso(r.decided_at),
        "decline_reason": r.decline_reason,
        "target_user_id": str(r.target_user_id) if r.target_user_id else None,
        "reset_url": r.reset_url if r.status == "approved" else None,
        "expires_hours": RESET_TOKEN_HOURS,
    }


async def get_request_or_404(db: AsyncSession, rid: str) -> OperatorUserRequest:
    import uuid as _uuid

    try:
        key = _uuid.UUID(rid)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "user_request.not_found"})
    row = (await db.execute(select(OperatorUserRequest).where(OperatorUserRequest.id == key))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "user_request.not_found"})
    return row


async def expire_stale(db: AsyncSession) -> None:
    cutoff = datetime.now(UTC) - timedelta(days=PENDING_DAYS)
    rows = (
        await db.execute(
            select(OperatorUserRequest).where(
                OperatorUserRequest.status == "pending", OperatorUserRequest.requested_at < cutoff
            )
        )
    ).scalars().all()
    for r in rows:
        r.status = "expired"
        r.decided_at = datetime.now(UTC)


async def open_for_admin(db: AsyncSession) -> list[OperatorUserRequest]:
    """Ami az adminnak felugrik: döntetlen kérések + nyugtázatlan megszüntetés-értesítők."""
    await expire_stale(db)
    rows = (
        await db.execute(
            select(OperatorUserRequest)
            .where(OperatorUserRequest.status.in_(("pending", "done")))
            .order_by(OperatorUserRequest.requested_at)
        )
    ).scalars().all()
    return list(rows)


async def recent_for_operator(db: AsyncSession) -> list[OperatorUserRequest]:
    await expire_stale(db)
    cutoff = datetime.now(UTC) - timedelta(days=30)
    rows = (
        await db.execute(
            select(OperatorUserRequest)
            .where((OperatorUserRequest.requested_at >= cutoff) | (OperatorUserRequest.status == "pending"))
            .order_by(OperatorUserRequest.requested_at.desc())
            .limit(100)
        )
    ).scalars().all()
    return list(rows)


async def operator_origin_user_ids(db: AsyncSession) -> set:
    """A pultból (jóváhagyott kéréssel) létrejött fiókok — csak ezek szüntethetők meg a pultból."""
    rows = (
        await db.execute(
            select(OperatorUserRequest.target_user_id).where(
                OperatorUserRequest.kind == "create", OperatorUserRequest.status == "approved",
                OperatorUserRequest.target_user_id.is_not(None),
            )
        )
    ).scalars().all()
    return set(rows)


async def ensure_login_free(db: AsyncSession, email: str | None, username: str | None) -> None:
    if email and (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail={"code": "employee.email_taken"})
    if username and (await db.execute(select(User).where(User.username == username))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail={"code": "employee.username_taken"})


async def approve(db: AsyncSession, req: OperatorUserRequest, actor: User, request: Request | None) -> tuple[User, str]:
    """A cég adminja elfogadta: fiók (+ karton) létrejön, jelszó-beállító link készül."""
    from app.services.wfm.codes import generate_unique_employee_code
    from app.services.wfm.license import check_capacity

    if req.kind != "create" or req.status != "pending":
        raise HTTPException(status_code=409, detail={"code": "user_request.not_pending"})
    role = "admin" if req.is_operator else req.role  # üzemeltetői fiók: mindig teljes jogkör
    if role not in await get_role_levels(db):
        raise HTTPException(status_code=422, detail={"code": "employee.bad_role"})
    await ensure_login_free(db, req.email, req.username)
    if not req.is_operator:
        await check_capacity(db, new_users=1, new_employees=1 if req.with_employee else 0)
    user = User(
        email=req.email, username=req.username,
        password_hash=hash_password(secrets.token_urlsafe(24)),  # senki nem ismeri
        display_name=f"{req.last_name} {req.first_name}", role=role, is_operator=req.is_operator,
    )
    db.add(user)
    await db.flush()
    if req.with_employee:
        emp = Employee(
            user_id=user.id, employee_code=await generate_unique_employee_code(db),
            last_name=req.last_name, first_name=req.first_name, hire_date=date.today(),
            job_title="Üzemeltető (X-systems)" if req.is_operator else None,
        )
        db.add(emp)
        await db.flush()
    token = mint_reset_token(user_id=user.id, token_version=user.token_version)
    url = f"{get_settings().frontend_origin.rstrip('/')}/jelszo?token={token}"
    now = datetime.now(UTC)
    req.status = "approved"
    req.decided_at = now
    req.decided_by_user_id = actor.id
    req.target_user_id = user.id
    req.reset_url = url
    await record_audit(
        db, actor=actor, action="user.request_approved", entity_type="user", entity_id=str(user.id),
        detail={"name": user.display_name, "role": role, "operator": req.is_operator, "login": req.email or req.username},
        request=request,
    )
    return user, url


async def decline(db: AsyncSession, req: OperatorUserRequest, actor: User, reason: str | None, request: Request | None) -> None:
    if req.kind != "create" or req.status != "pending":
        raise HTTPException(status_code=409, detail={"code": "user_request.not_pending"})
    req.status = "declined"
    req.decided_at = datetime.now(UTC)
    req.decided_by_user_id = actor.id
    req.decline_reason = (reason or "").strip()[:256] or None
    await record_audit(
        db, actor=actor, action="user.request_declined", entity_type="user_request", entity_id=str(req.id),
        detail={"name": f"{req.last_name} {req.first_name}", "reason": req.decline_reason}, request=request,
    )


async def deactivate_from_operator(db: AsyncSession, user: User, request: Request | None) -> OperatorUserRequest:
    """A pultból megszüntetett fiók: letiltás + minden munkamenet érvénytelen +
    a karton archivált; az adminnak nyugtázandó értesítő készül."""
    user.is_active = False
    user.token_version += 1  # a piros sáv a következő kérésnél eltűnik — kiléptetve
    emp = (await db.execute(select(Employee).where(Employee.user_id == user.id))).scalar_one_or_none()
    if emp is not None:
        emp.status = "inactive"
        emp.termination_date = date.today()
    parts = (user.display_name or "?").split(" ", 1)
    notice = OperatorUserRequest(
        kind="delete", status="done", last_name=parts[0], first_name=parts[1] if len(parts) > 1 else "",
        email=user.email, username=user.username, role=user.role, with_employee=emp is not None,
        is_operator=user.is_operator, target_user_id=user.id, decided_at=datetime.now(UTC),
    )
    db.add(notice)
    await db.flush()
    await record_audit(
        db, actor=None, action="user.deactivate_operator", entity_type="user", entity_id=str(user.id),
        detail={"name": user.display_name, "operator": user.is_operator}, request=request,
    )
    return notice


async def notify_admins(db: AsyncSession, subject: str, body: str) -> None:
    """Best-effort értesítés: a cég Telegram-csoportja(i), az adminok saját
    Telegramja (ha összekapcsolt), és az értesítési e-mail címek."""
    text = f"{subject}\n\n{body}"[:4000]
    try:
        from app.services.wfm import login_2fa as twofa
        from app.services.wfm.telegram import load_telegram_config, send_telegram

        config = await load_telegram_config(db)
        if config is not None:
            sent: set[str] = set()
            for chat in config["chat_ids"]:
                sent.add(str(chat))
                await send_telegram(config, chat, text)
            admins = (
                await db.execute(select(User).where(User.role == "admin", User.is_active.is_(True), User.is_operator.is_(False)))
            ).scalars().all()
            for adm in admins:
                chat = await twofa.telegram_chat(db, adm)
                if chat and str(chat) not in sent:
                    sent.add(str(chat))
                    await send_telegram(config, chat, text)
    except Exception:
        logger.warning("user_requests notify (telegram) failed", exc_info=True)
    try:
        from app.services.wfm.email_service import load_smtp_config, send_email
        from app.services.wfm.notifier import _recipients, get_or_create_settings

        row = await get_or_create_settings(db)
        smtp = await load_smtp_config(db)
        if smtp is not None:
            for to in _recipients(row):
                await send_email(smtp, to, subject, body)
    except Exception:
        logger.warning("user_requests notify (email) failed", exc_info=True)
