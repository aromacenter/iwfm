"""Email értesítések (SMTP) — beosztás-közlés, módosítás, távollét-döntés.

A beállítások a DB-ben élnek (email_settings, jelszó Fernet-titkosítva).
A küldés best-effort: hiba esetén logolunk, a fő művelet (pl. közzététel)
sosem bukik el az email miatt. A blokkoló smtplib hívás külön szálon fut.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_pii
from app.models import EmailSettings

logger = logging.getLogger(__name__)


@dataclass
class SmtpConfig:
    host: str
    port: int
    username: str | None
    password: str | None
    from_address: str
    use_tls: bool


async def load_smtp_config(db: AsyncSession) -> SmtpConfig | None:
    """None, ha nincs bekapcsolva vagy hiányos a konfiguráció."""
    row = (
        await db.execute(select(EmailSettings).where(EmailSettings.id == 1))
    ).scalar_one_or_none()
    if row is None or not row.enabled or not row.host or not row.from_address:
        return None
    return SmtpConfig(
        host=row.host,
        port=row.port,
        username=row.username,
        password=decrypt_pii(row.password_encrypted),
        from_address=row.from_address,
        use_tls=row.use_tls,
    )


def _send_sync(config: SmtpConfig, to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = config.from_address
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    if config.port == 465:
        with smtplib.SMTP_SSL(config.host, config.port, timeout=15) as server:
            if config.username:
                server.login(config.username, config.password or "")
            server.send_message(msg)
    else:
        with smtplib.SMTP(config.host, config.port, timeout=15) as server:
            if config.use_tls:
                server.starttls()
            if config.username:
                server.login(config.username, config.password or "")
            server.send_message(msg)


async def send_email(config: SmtpConfig, to: str, subject: str, body: str) -> bool:
    """Egy email elküldése. False hibánál (logolva), True ha elment."""
    try:
        await asyncio.to_thread(_send_sync, config, to, subject, body)
        return True
    except Exception:
        logger.warning("Email küldés sikertelen: %s → %s", subject, to, exc_info=True)
        return False


async def send_many(config: SmtpConfig, messages: list[tuple[str, str, str]]) -> None:
    """Háttérfeladatként futtatott tömeges küldés: (to, subject, body) lista."""
    for to, subject, body in messages:
        await send_email(config, to, subject, body)
