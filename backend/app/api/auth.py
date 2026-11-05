"""Auth endpoints: bootstrap-first-admin, login, logout, me.

Login issues an httpOnly JWT cookie. A short, in-memory failed-attempt
throttle protects against credential stuffing (per email+IP, 5 attempts /
15 minutes) — kept deliberately simple for a single-instance deployment.
"""

from __future__ import annotations

import secrets as _secrets
import time as _time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    denied_actions_for,
    get_action_matrix,
    get_current_user,
    get_permission_matrix,
    get_roles,
    permissions_for,
    record_audit,
    role_level,
)
from app.core.config import get_settings
from app.core.security import SESSION_COOKIE, hash_password, mint_token, verify_password
from app.db import get_db
from app.models import User
from app.services.wfm import login_2fa as twofa

router = APIRouter()

_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 15 * 60
# IP-től független korlát fiókonként: hamisított X-Forwarded-For fejléccel
# (IP-váltogatással) sem lehet korlátlanul jelszót próbálgatni egy emailre.
# Magas plafon: ez másodlagos fék elosztott próbálgatás ellen, nem fiók-zár —
# 20-nál bárki névtelenül kizárhatta az admint (DoS); az elsődleges védelem az
# e-mail+IP kulcs (5 / 15 perc), a valós IP-t a proxy-headers adja.
_MAX_EMAIL_ATTEMPTS = 100
_EMAIL_WINDOW_SECONDS = 60 * 60
_failed_logins: dict[str, list[float]] = defaultdict(list)


def _throttled(key: str, limit: int = _MAX_ATTEMPTS, window: int = _WINDOW_SECONDS) -> bool:
    now = _time.monotonic()
    attempts = [t for t in _failed_logins[key] if now - t < window]
    _failed_logins[key] = attempts
    return len(attempts) >= limit


def _record_failure(key: str) -> None:
    _failed_logins[key].append(_time.monotonic())


class BootstrapBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)
    token: str | None = None  # csak ha WFM_BOOTSTRAP_TOKEN be van állítva


class LoginBody(BaseModel):
    # Email VAGY felhasználónév — a mező történelmi okból "email" néven jár.
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: str
    email: str | None
    username: str | None = None
    display_name: str
    role: str
    role_name: str | None = None  # a szerepkör megjelenített neve (saját szerepkörnél is)
    level: int = 0  # 0 dolgozói, 1 vezetői, 2 admin
    permissions: list[str] = []
    denied_actions: list[str] = []  # „feature.action” amit NEM tehet
    substitute_user_id: str | None = None  # helyettes távollét idejére
    # 148. kör: a fiók saját száma (törzsszám vagy fiók-szám) + van-e összekapcsolt Telegram
    user_code: str | None = None
    telegram_linked: bool = False


def _user_out(
    user: User, permissions: list[str] | None = None, denied: list[str] | None = None, role_name: str | None = None,
) -> UserOut:
    return UserOut(
        id=str(user.id),
        email=user.email,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        role_name=role_name,
        level=2 if user.role == "admin" else role_level(user.role),
        permissions=permissions or [],
        denied_actions=denied or [],
        substitute_user_id=(
            str(user.substitute_user_id) if user.substitute_user_id else None
        ),
    )


async def _me(db: AsyncSession, user: User) -> UserOut:
    matrix = await get_permission_matrix(db)
    actions = await get_action_matrix(db)
    name = next((r.name for r in await get_roles(db) if r.key == user.role), None)
    out = _user_out(user, permissions_for(user.role, matrix), denied_actions_for(user.role, actions), role_name=name)
    from app.services.wfm.codes import ensure_user_code

    code, generated = await ensure_user_code(db, user)
    if generated:
        await db.commit()
    out.user_code = code
    out.telegram_linked = (await twofa.telegram_chat(db, user)) is not None
    return out


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
    if settings.bootstrap_token and not _secrets.compare_digest(
        settings.bootstrap_token, body.token or ""
    ):
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
    return await _me(db, user)


async def _finish_login(
    db: AsyncSession, user: User, request: Request, response: Response, *, twofa_channel: str | None = None
) -> UserOut:
    """A sikeres belépés közös lezárása (jelszó, vagy jelszó + kód után)."""
    # Aznapi első belépéskor automatikus jelenlét-nyitás (látszik, mikor
    # kezdett) — a kiléptetést a háttérhurok végzi a munkaidő végén.
    from app.services.wfm.timeclock_auto import auto_clockin_on_login

    await auto_clockin_on_login(db, user.id)
    await record_audit(
        db, actor=user, action="auth.login", entity_type="user",
        entity_id=str(user.id), detail=({"twofa": twofa_channel} if twofa_channel else None), request=request,
    )
    await db.commit()
    set_session_cookie(response, mint_token(user_id=user.id, role=user.role, token_version=user.token_version))
    return await _me(db, user)


@router.post("/login")
async def login(
    body: LoginBody,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ip = request.client.host if request.client else "?"
    throttle_key = f"{body.email.lower()}|{ip}"
    email_key = f"email:{body.email.lower()}"
    if _throttled(throttle_key) or _throttled(
        email_key, _MAX_EMAIL_ATTEMPTS, _EMAIL_WINDOW_SECONDS
    ):
        raise HTTPException(status_code=429, detail={"code": "auth.rate_limited"})

    # Belépés email-lel VAGY felhasználónévvel — ugyanabba a mezőbe írható.
    ident = body.email.strip().lower()
    user = (
        await db.execute(
            select(User).where((User.email == ident) | (User.username == ident))
        )
    ).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(
        user.password_hash, body.password
    ):
        _record_failure(throttle_key)
        _record_failure(email_key)
        # Egységes hibaüzenet — nem áruljuk el, hogy az azonosító létezik-e.
        raise HTTPException(status_code=401, detail={"code": "auth.bad_credentials"})

    _failed_logins.pop(throttle_key, None)
    _failed_logins.pop(email_key, None)

    # 147. kör: kétlépcsős belépés — a jelszó után kód Telegramon (tartalék: e-mail).
    # Megbízható eszközön (30 napos süti) nem kérünk kódot.
    mode = await twofa.get_mode(db)
    if mode != "off" and not twofa.trust_token_valid(request.cookies.get(twofa.TRUST_COOKIE), user):
        challenge, channel = await twofa.start_challenge(db, user, mode)
        if channel == "unavailable":
            await record_audit(
                db, actor=user, action="auth.twofa_unavailable", entity_type="user",
                entity_id=str(user.id), request=request,
            )
            await db.commit()
            raise HTTPException(status_code=403, detail={"code": "auth.twofa_unavailable"})
        if channel == "rate_limited":
            raise HTTPException(status_code=429, detail={"code": "auth.rate_limited"})
        if channel == "send_failed":
            # 150. kör: küldési hibánál nincs „jelszó is elég" — zárva marad
            await record_audit(
                db, actor=user, action="auth.twofa_send_failed", entity_type="user",
                entity_id=str(user.id), request=request,
            )
            await db.commit()
            raise HTTPException(status_code=503, detail={"code": "auth.twofa_send_failed"})
        if challenge is not None:
            await record_audit(
                db, actor=user, action="auth.twofa_sent", entity_type="user",
                entity_id=str(user.id), detail={"channel": channel}, request=request,
            )
            await db.commit()
            return {
                "twofa_required": True,
                "challenge_id": str(challenge.id),
                "channel": channel,
                "hint": twofa.challenge_hint(user, channel),
                "ttl_minutes": twofa.CODE_TTL_MINUTES,
            }
    return await _finish_login(db, user, request, response)


class TwofaVerifyBody(BaseModel):
    challenge_id: str = Field(min_length=8, max_length=64)
    code: str = Field(min_length=4, max_length=12)
    trust_device: bool = False


@router.post("/login/verify", response_model=UserOut)
async def login_verify(
    body: TwofaVerifyBody,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Második lépés: a kiküldött kód ellenőrzése. 5 hibás próba után a kihívás
    érvénytelen; a kód 5 percig él. Siker esetén kiadja a munkamenet-sütit."""
    ip = request.client.host if request.client else "?"
    key = f"2fa|{ip}"
    if _throttled(key, 20, _WINDOW_SECONDS):
        raise HTTPException(status_code=429, detail={"code": "auth.rate_limited"})
    ch = await twofa.load_challenge(db, body.challenge_id)
    if not twofa.challenge_usable(ch):
        raise HTTPException(status_code=401, detail={"code": "auth.twofa_expired"})
    user = (await db.execute(select(User).where(User.id == ch.user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail={"code": "auth.twofa_expired"})
    if not twofa.check_code(ch, body.code):
        _record_failure(key)
        left = max(0, twofa.MAX_ATTEMPTS - ch.attempts)
        await record_audit(
            db, actor=user, action="auth.twofa_bad_code", entity_type="user",
            entity_id=str(user.id), request=request,
        )
        await db.commit()
        raise HTTPException(status_code=401, detail={"code": "auth.twofa_bad_code", "attempts_left": left})
    out = await _finish_login(db, user, request, response, twofa_channel=ch.channel)
    if body.trust_device:
        settings = get_settings()
        response.set_cookie(
            twofa.TRUST_COOKIE, twofa.mint_trust_token(user),
            max_age=twofa.TRUST_DAYS * 86400, httponly=True, secure=settings.cookie_secure,
            samesite="lax", path="/",
        )
    return out


class TwofaResendBody(BaseModel):
    challenge_id: str = Field(min_length=8, max_length=64)


@router.post("/login/resend")
async def login_resend(body: TwofaResendBody, db: AsyncSession = Depends(get_db)):
    """Új kód kérése ugyanahhoz a belépéshez (legfeljebb 3 küldés, 30 mp-enként)."""
    ch = await twofa.load_challenge(db, body.challenge_id)
    if ch is None or ch.consumed_at is not None:
        raise HTTPException(status_code=401, detail={"code": "auth.twofa_expired"})
    user = (await db.execute(select(User).where(User.id == ch.user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail={"code": "auth.twofa_expired"})
    result = await twofa.resend(db, ch, user)
    if result == "limit":
        raise HTTPException(status_code=429, detail={"code": "auth.twofa_resend_limit"})
    if result == "too_soon":
        raise HTTPException(status_code=429, detail={"code": "auth.twofa_resend_too_soon"})
    if result == "failed":
        raise HTTPException(status_code=502, detail={"code": "auth.twofa_send_failed"})
    await db.commit()
    return {"ok": True, "channel": ch.channel}


@router.post("/me/telegram-unlink")
async def my_telegram_unlink(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """A SAJÁT Telegram-kapcsolat bontása (fiók-szintű és a saját kartoné is) —
    telefoncsere vagy teszt után a számmal újra összekapcsolható."""
    from app.models import Employee

    user.telegram_chat_id = None
    emp = (await db.execute(select(Employee).where(Employee.user_id == user.id))).scalar_one_or_none()
    if emp is not None:
        emp.telegram_chat_id = None
    await record_audit(
        db, actor=user, action="auth.telegram_unlink", entity_type="user",
        entity_id=str(user.id), request=request,
    )
    await db.commit()
    return {"ok": True}


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Kijelentkezés: a süti törlése MELLETT a token_version emelésével a
    kiadott tokent szerver-oldalon is érvénytelenítjük — egy ellopott token
    sem él tovább a kijelentkezés után. Érvénytelen/hiányzó tokennel is 200."""
    import uuid as _uuid

    import jwt as _pyjwt

    from app.api.deps import _extract_token
    from app.core.security import decode_token

    token = _extract_token(request)
    if token:
        try:
            payload = decode_token(token)
            user = (
                await db.execute(
                    select(User).where(User.id == _uuid.UUID(payload["sub"]))
                )
            ).scalar_one_or_none()
            if user is not None and int(payload.get("tv", 0)) == user.token_version:
                user.token_version += 1
                await db.commit()
        except (_pyjwt.PyJWTError, KeyError, ValueError):
            pass
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _me(db, user)


# ─── Jelszó-visszaállítás admin által küldött linkkel (b3640159) ─────────────
# Publikus "elfelejtett jelszó" oldal NINCS: a linket kizárólag az admin
# generálja és juttatja el a dolgozónak. A jelszót maga a dolgozó adja meg.


@router.post("/users/{user_id}/reset-link")
async def create_reset_link(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    import uuid as _uuid

    from app.core.security import RESET_TOKEN_HOURS, mint_reset_token

    if actor.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "auth.forbidden"})
    try:
        uid = _uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "user.not_found"})
    user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail={"code": "user.not_found"})
    token = mint_reset_token(user_id=user.id, token_version=user.token_version)
    url = f"{get_settings().frontend_origin.rstrip('/')}/jelszo?token={token}"
    await record_audit(
        db, actor=actor, action="auth.reset_link", entity_type="user",
        entity_id=str(user.id), detail={"for": user.display_name}, request=request,
    )
    await db.commit()
    return {"url": url, "expires_hours": RESET_TOKEN_HOURS, "name": user.display_name}


class ForgotBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)


@router.post("/forgot")
async def forgot_password(
    body: ForgotBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Elfelejtett jelszó: ha van ilyen aktív fiók ÉS van SMTP (saját vagy
    platform-tartalék), e-mailben megy a jelszó-link. A válasz mindig
    {ok: true} — nem árulja el, létezik-e a cím. IP-nként throttle-olt."""
    from app.core.security import RESET_TOKEN_HOURS, mint_reset_token
    from app.services.wfm.email_service import load_smtp_config, send_email

    ip = request.client.host if request.client else "?"
    if _throttled(f"forgot:{ip}", limit=5, window=15 * 60):
        raise HTTPException(status_code=429, detail={"code": "auth.throttled"})
    _record_failure(f"forgot:{ip}")
    email = body.email.strip().lower()
    user = (
        await db.execute(select(User).where(func.lower(User.email) == email))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        return {"ok": True}
    config = await load_smtp_config(db)
    if config is None:
        return {"ok": True, "mail_unavailable": True}
    token = mint_reset_token(user_id=user.id, token_version=user.token_version)
    url = f"{get_settings().frontend_origin.rstrip('/')}/jelszo?token={token}"
    await send_email(
        config, user.email, "X-admin — új jelszó beállítása",
        f"Szia {user.display_name}!\n\nÚj jelszót kértél az X-admin fiókodhoz. "
        f"Ezen a linken állíthatod be ({RESET_TOKEN_HOURS} óráig érvényes):\n{url}\n\n"
        "Ha nem te kérted, hagyd figyelmen kívül ezt a levelet — a jelszavad nem változik.",
    )
    await record_audit(
        db, actor=None, action="auth.forgot", entity_type="user",
        entity_id=str(user.id), request=request,
    )
    await db.commit()
    return {"ok": True}


class ResetPasswordBody(BaseModel):
    token: str = Field(min_length=10, max_length=2048)
    password: str = Field(min_length=10, max_length=128)


@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Új jelszó beállítása a visszaállító linkről. A token egyszer
    használatos: a mentés lépteti a token_version-t, ami magát a linket és
    minden korábbi munkamenetet is érvénytelenít."""
    import uuid as _uuid

    import jwt as _pyjwt

    from app.core.security import decode_token

    ip = request.client.host if request.client else "?"
    if _throttled(f"pwreset:{ip}"):
        raise HTTPException(status_code=429, detail={"code": "auth.throttled"})
    try:
        payload = decode_token(body.token)
        if payload.get("purpose") != "pwreset":
            raise _pyjwt.PyJWTError("wrong purpose")
        uid = _uuid.UUID(payload["sub"])
    except (_pyjwt.PyJWTError, KeyError, ValueError):
        _record_failure(f"pwreset:{ip}")
        raise HTTPException(status_code=422, detail={"code": "auth.reset_invalid"})
    user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    if (
        user is None or not user.is_active
        or int(payload.get("tv", -1)) != user.token_version
    ):
        _record_failure(f"pwreset:{ip}")
        raise HTTPException(status_code=422, detail={"code": "auth.reset_invalid"})
    user.password_hash = hash_password(body.password)
    user.token_version += 1  # a link és minden régi munkamenet érvénytelen
    await record_audit(
        db, actor=None, action="auth.password_reset", entity_type="user",
        entity_id=str(user.id), detail={"name": user.display_name}, request=request,
    )
    await db.commit()
    return {"ok": True}


class SignatureSampleBody(BaseModel):
    signature: str | None = None  # None/üres = a minta törlése


@router.get("/me/signature")
async def get_my_signature_sample(
    user: User = Depends(get_current_user),
):
    """A saját elmentett aláírás-minta (PNG data URL) — egy kattintással
    beszúrható a munkalap dolgozói aláírásához."""
    return {"signature": user.signature_sample}


@router.put("/me/signature")
async def set_my_signature_sample(
    body: SignatureSampleBody,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.signature:
        if (
            not body.signature.startswith("data:image/png;base64,")
            or len(body.signature) > 300_000
        ):
            raise HTTPException(status_code=422, detail={"code": "user.bad_signature"})
        user.signature_sample = body.signature
    else:
        user.signature_sample = None
    await record_audit(
        db, actor=user, action="user.signature_sample", entity_type="user",
        entity_id=str(user.id), request=request,
    )
    await db.commit()
    return {"ok": True, "has_signature": user.signature_sample is not None}


class SubstituteBody(BaseModel):
    user_id: str | None = None  # None = helyettes törlése


@router.put("/me/substitute", response_model=UserOut)
async def set_my_substitute(
    body: SubstituteBody,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """A saját helyettes kijelölése távollét idejére: amíg a felhasználónak
    jóváhagyott távolléte fedi a napot, a felelős-képviselői értesítések a
    helyetteshez futnak be."""
    import uuid as _uuid

    from sqlalchemy import select as sa_select

    if body.user_id:
        try:
            sub_id = _uuid.UUID(body.user_id)
        except ValueError:
            raise HTTPException(status_code=404, detail={"code": "user.not_found"})
        if sub_id == user.id:
            raise HTTPException(status_code=422, detail={"code": "user.substitute_self"})
        sub = (
            await db.execute(sa_select(User).where(User.id == sub_id))
        ).scalar_one_or_none()
        if sub is None or not sub.is_active:
            raise HTTPException(status_code=404, detail={"code": "user.not_found"})
        user.substitute_user_id = sub.id
    else:
        user.substitute_user_id = None
    await record_audit(
        db, actor=user, action="user.substitute", entity_type="user",
        entity_id=str(user.id),
        detail={"substitute": body.user_id}, request=request,
    )
    await db.commit()
    return await _me(db, user)


class DeniedBody(BaseModel):
    path: str = Field(max_length=256)
    feature: str | None = Field(default=None, max_length=64)


@router.post("/denied")
async def report_denied(
    body: DeniedBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """A felület jelzi, ha valaki olyan oldalra tévedt, amihez nincs joga —
    naplósor lesz belőle (user-kérés: a tiltás is látszódjon a naplóban)."""
    await record_audit(
        db, actor=user, action="auth.denied", entity_type="permission", entity_id=body.feature or "page",
        detail={"feature": body.feature, "action": "view", "role": user.role, "method": "PAGE", "path": body.path[:256]},
        request=request,
    )
    await db.commit()
    return {"ok": True}
