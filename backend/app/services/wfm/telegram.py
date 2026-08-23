"""Telegram-küldés a Bot API-n át (belső kommunikáció + dolgozói push).

Beállítás: Beállítások → Értesítések → Telegram blokk. A bot tokent a
@BotFather adja; a csoport/csevegés chat_id-ját a bot hozzáadása után pl. a
getUpdates hívásból vagy a @userinfobot-tól lehet megtudni.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_pii

logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/sendMessage"


async def load_telegram_config(db: AsyncSession) -> dict | None:
    """None, ha a Telegram nincs bekapcsolva vagy hiányos a beállítás."""
    from app.services.wfm.notifier import get_or_create_settings

    row = await get_or_create_settings(db)
    token = decrypt_pii(row.tg_token_encrypted)
    if not row.tg_enabled or not token:
        return None
    return {
        "token": token,
        "chat_ids": [
            x.strip()
            for x in (row.tg_chat_ids or "").replace(";", ",").split(",")
            if x.strip()
        ],
    }


async def send_telegram(config: dict, chat_id: str, text: str) -> bool:
    """Egy üzenet küldése a megadott csevegésbe. True = a Telegram befogadta."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.post(
                API_URL.format(token=config["token"]),
                json={"chat_id": chat_id, "text": text[:4096]},
            )
        if res.status_code >= 400:
            logger.warning("telegram send failed (%s): %s", res.status_code, res.text[:300])
            return False
        return True
    except httpx.HTTPError:
        logger.warning("telegram send failed", exc_info=True)
        return False
