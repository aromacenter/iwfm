"""Kétlépcsős belépés (147. kör): jelszó + egyszer használatos kód Telegramon
(a dolgozói kartonhoz kapcsolt csevegésbe), tartalékként e-mailben.

Módok (PermissionSettings.login_2fa_mode):
- off      — nincs második lépés;
- linked   — akinek össze van kapcsolva a Telegramja, annak kód is kell;
- required — mindenkinek kell kód: Telegram, annak híján e-mail; ha egyik sem
             megy, a belépés elutasítva (a Flotta-pultról kikapcsolható).

A kód 6 jegyű, 5 percig él, 5 próbálkozás után érvénytelen; a kihívásban csak a
hash tárolódik. „Megbízható eszköz”: 30 napos aláírt süti, ami a jelszó
változásával érvényét veszti."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Employee, LoginChallenge, PermissionSettings, User
from app.services.wfm import email_service as es
from app.services.wfm import telegram as tg

MODES = ("off", "linked", "required")
CODE_TTL_MINUTES = 5
MAX_ATTEMPTS = 5
MAX_SENDS = 3
RESEND_MIN_SECONDS = 30
TRUST_DAYS = 30
TRUST_COOKIE = "wfm_trusted"


async def get_mode(db: AsyncSession) -> str:
    row = (await db.execute(select(PermissionSettings).where(PermissionSettings.id == 1))).scalar_one_or_none()
    mode = (row.login_2fa_mode if row is not None else None) or "off"
    return mode if mode in MODES else "off"


async def set_mode(db: AsyncSession, mode: str) -> None:
    row = (await db.execute(select(PermissionSettings).where(PermissionSettings.id == 1))).scalar_one_or_none()
    if row is None:
        row = PermissionSettings(id=1)
        db.add(row)
    row.login_2fa_mode = mode


async def telegram_chat(db: AsyncSession, user: User) -> str | None:
    """A fiók Telegram-csevegése: a dolgozói kartoné, annak híján a fiók saját
    (148. kör, karton nélküli admin/vezető) kapcsolata."""
    emp_chat = (
        await db.execute(
            select(Employee.telegram_chat_id).where(
                Employee.user_id == user.id, Employee.telegram_chat_id.is_not(None)
            )
        )
    ).scalars().first()
    return emp_chat or user.telegram_chat_id or None


async def available_channel(db: AsyncSession, user: User, mode: str) -> str | None:
    """Melyik csatornán mehet a kód: 'telegram' | 'email' | None."""
    if mode == "off":
        return None
    if await telegram_chat(db, user) is not None and await tg.load_telegram_config(db) is not None:
        return "telegram"
    if mode == "required" and user.email and await es.load_smtp_config(db) is not None:
        return "email"
    return None


def _hash(challenge_id: uuid.UUID, code: str) -> str:
    return hashlib.sha256(f"{challenge_id}:{code}".encode()).hexdigest()


def _mask(value: str) -> str:
    if "@" in value:
        name, domain = value.split("@", 1)
        return (name[:2] + "•••@" + domain) if name else value
    return "Telegram"


async def _send(db: AsyncSession, user: User, channel: str, code: str) -> bool:
    text = (
        f"🔐 X-admin belépési kód: {code}\n"
        f"{CODE_TTL_MINUTES} percig érvényes. Ha nem te lépsz be éppen, NE add meg senkinek, "
        "és változtasd meg a jelszavad."
    )
    if channel == "telegram":
        config = await tg.load_telegram_config(db)
        chat = await telegram_chat(db, user)
        return bool(config is not None and chat and await tg.send_telegram(config, chat, text))
    smtp = await es.load_smtp_config(db)
    if smtp is None or not user.email:
        return False
    return bool(await es.send_email(smtp, user.email, "X-admin belépési kód", text))


async def start_challenge(db: AsyncSession, user: User, mode: str) -> tuple[LoginChallenge | None, str | None]:
    """Kihívás indítása. Vissza: (kihívás, csatorna) — (None, None), ha nem kell
    második lépés; (None, 'unavailable'), ha kötelező, de nincs csatorna vagy a
    küldés nem sikerült."""
    channel = await available_channel(db, user, mode)
    if channel is None:
        return (None, "unavailable") if mode == "required" else (None, None)
    # régi kihívások takarítása (a tábla ne nőjön)
    await db.execute(delete(LoginChallenge).where(LoginChallenge.expires_at < datetime.now(UTC) - timedelta(days=1)))
    code = f"{secrets.randbelow(1_000_000):06d}"
    ch = LoginChallenge(
        id=uuid.uuid4(), user_id=user.id, channel=channel, code_hash="",
        expires_at=datetime.now(UTC) + timedelta(minutes=CODE_TTL_MINUTES),
    )
    ch.code_hash = _hash(ch.id, code)
    ok = await _send(db, user, channel, code)
    if not ok and channel == "telegram" and user.email and await es.load_smtp_config(db) is not None:
        # a Telegram nem ment ki → e-mail tartalék
        channel = ch.channel = "email"
        ok = await _send(db, user, channel, code)
    if not ok:
        # puha módban a küldési hiba nem zárhatja ki a felhasználót; kötelezőben igen
        return (None, "unavailable") if mode == "required" else (None, None)
    db.add(ch)
    return ch, channel


def challenge_hint(user: User, channel: str) -> str:
    return _mask(user.email) if channel == "email" and user.email else "Telegram"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


async def load_challenge(db: AsyncSession, challenge_id: str) -> LoginChallenge | None:
    try:
        cid = uuid.UUID(challenge_id)
    except ValueError:
        return None
    return (await db.execute(select(LoginChallenge).where(LoginChallenge.id == cid))).scalar_one_or_none()


def challenge_usable(ch: LoginChallenge | None) -> bool:
    return bool(
        ch is not None and ch.consumed_at is None and ch.attempts < MAX_ATTEMPTS
        and _aware(ch.expires_at) > datetime.now(UTC)
    )


def check_code(ch: LoginChallenge, code: str) -> bool:
    """Kód ellenőrzése állandó idejű összehasonlítással; hibánál számol."""
    ok = secrets.compare_digest(ch.code_hash, _hash(ch.id, (code or "").strip()))
    if ok:
        ch.consumed_at = datetime.now(UTC)
    else:
        ch.attempts += 1
    return ok


async def resend(db: AsyncSession, ch: LoginChallenge, user: User) -> str:
    """Új kód ugyanarra a kihívásra. Vissza: 'ok' | 'too_soon' | 'limit' | 'failed'."""
    now = datetime.now(UTC)
    if ch.sends >= MAX_SENDS:
        return "limit"
    if (now - _aware(ch.last_sent_at or ch.created_at or now)).total_seconds() < RESEND_MIN_SECONDS:
        return "too_soon"
    code = f"{secrets.randbelow(1_000_000):06d}"
    if not await _send(db, user, ch.channel, code):
        return "failed"
    ch.code_hash = _hash(ch.id, code)
    ch.expires_at = now + timedelta(minutes=CODE_TTL_MINUTES)
    ch.attempts = 0
    ch.sends += 1
    ch.last_sent_at = now
    return "ok"


# ─── Megbízható eszköz (30 napos süti) ───────────────────────────────────────


def _pwd_fp(user: User) -> str:
    return hashlib.sha256((user.password_hash or "").encode()).hexdigest()[:16]


def mint_trust_token(user: User) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id), "purpose": "trusted2fa", "pf": _pwd_fp(user),
        "iat": int(now.timestamp()), "exp": int((now + timedelta(days=TRUST_DAYS)).timestamp()),
    }
    return jwt.encode(payload, get_settings().secret_key, algorithm="HS256")


def trust_token_valid(token: str | None, user: User) -> bool:
    if not token:
        return False
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return False
    return (
        payload.get("purpose") == "trusted2fa"
        and payload.get("sub") == str(user.id)
        and secrets.compare_digest(str(payload.get("pf") or ""), _pwd_fp(user))
    )


async def stats(db: AsyncSession) -> dict:
    """A beállító kártyához: hány fiók kaphat Telegram-kódot."""
    users = (await db.execute(select(User).where(User.is_active.is_(True)))).scalars().all()
    linked_ids = set((await db.execute(
        select(Employee.user_id).where(Employee.telegram_chat_id.is_not(None), Employee.user_id.is_not(None))
    )).scalars())
    return {
        "users": len(users),
        "telegram_linked": sum(1 for u in users if u.id in linked_ids or u.telegram_chat_id),
        "with_email": sum(1 for u in users if u.email),
        "telegram_configured": await tg.load_telegram_config(db) is not None,
        "smtp_configured": await es.load_smtp_config(db) is not None,
    }
