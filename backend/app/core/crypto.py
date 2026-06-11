"""Fernet encryption for employee PII at rest.

Encrypted fields: adóazonosító jel, TAJ szám, bankszámlaszám, bér. These are
GDPR personal data — they must never be stored or logged in plaintext.

In development (no WFM_ENC_KEY) we derive a stable key from the secret key so
the app works out of the box; production refuses to boot without an explicit
key (enforced in config.get_settings).
"""

from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _codec() -> Fernet:
    settings = get_settings()
    if settings.enc_key:
        return Fernet(settings.enc_key.encode("ascii"))
    logger.warning("WFM_ENC_KEY not set — deriving dev-only encryption key from secret_key.")
    derived = hashlib.sha256(f"iwfm-pii:{settings.secret_key}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_pii(plaintext: str | None) -> bytes | None:
    if not plaintext:
        return None
    return _codec().encrypt(plaintext.encode("utf-8"))


def decrypt_pii(ciphertext: bytes | None) -> str | None:
    if not ciphertext:
        return None
    return _codec().decrypt(bytes(ciphertext)).decode("utf-8")


def mask_tail(value: str | None, visible: int = 4) -> str | None:
    """Display helper: '••• 1234' — never reveals the full identifier."""
    if not value:
        return None
    tail = value[-visible:] if len(value) > visible else value
    return f"••• {tail}"
