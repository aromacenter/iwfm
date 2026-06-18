"""Password hashing (argon2id) and JWT session tokens.

The JWT is issued as an httpOnly cookie (`wfm_session`) and also accepted as
an `Authorization: Bearer` header (API clients, tests). HS256 with the app
secret; 12h lifetime by default.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import get_settings

SESSION_COOKIE = "wfm_session"
ALGORITHM = "HS256"

_hasher = PasswordHasher()  # argon2id with library defaults (OWASP-aligned)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, candidate: str) -> bool:
    try:
        return _hasher.verify(password_hash, candidate)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def mint_token(*, user_id: uuid.UUID, role: str, token_version: int = 0) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "tv": token_version,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.session_hours)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Raises jwt.PyJWTError on invalid/expired tokens."""
    settings = get_settings()
    return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
