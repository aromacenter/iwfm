"""WhatsApp-küldés a Meta Cloud API-n át (belső kommunikáció + dolgozói push).

Beállítás: Beállítások → Értesítések → WhatsApp blokk (token Fernet-tel
titkosítva tárolva, telefonszám-azonosító, alapértelmezett címzettek).
Szabad szöveges üzenet a 24 órás ügyfélszolgálati ablakon belül küldhető;
azon kívül a Meta sablon-üzenetet vár — belső (dolgozói) számokra, ahol a
dolgozók maguk is írnak a számra, a szabad szöveg tartósan működik.
"""

from __future__ import annotations

import logging
import re

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_pii

logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com/v20.0/{phone_id}/messages"


def normalize_phone(raw: str) -> str | None:
    """'+36 30 123 4567' / '06301234567' → '36301234567' (E.164, + nélkül)."""
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    if digits.startswith("06"):
        digits = "36" + digits[2:]
    return digits


async def load_whatsapp_config(db: AsyncSession) -> dict | None:
    """None, ha a WhatsApp nincs bekapcsolva vagy hiányos a beállítás."""
    from app.services.wfm.notifier import get_or_create_settings

    row = await get_or_create_settings(db)
    token = decrypt_pii(row.wa_token_encrypted)
    if not row.wa_enabled or not token or not row.wa_phone_id:
        return None
    return {
        "token": token,
        "phone_id": row.wa_phone_id,
        "recipients": [
            p for p in (
                normalize_phone(x)
                for x in (row.wa_recipients or "").replace(";", ",").split(",")
            ) if p
        ],
    }


async def send_whatsapp(config: dict, to: str, text: str) -> bool:
    """Egy szabad szöveges üzenet küldése. True = a Meta befogadta."""
    phone = normalize_phone(to)
    if not phone:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.post(
                GRAPH_URL.format(phone_id=config["phone_id"]),
                headers={"Authorization": f"Bearer {config['token']}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": phone,
                    "type": "text",
                    "text": {"body": text[:4096]},
                },
            )
        if res.status_code >= 400:
            logger.warning("whatsapp send failed (%s): %s", res.status_code, res.text[:300])
            return False
        return True
    except httpx.HTTPError:
        logger.warning("whatsapp send failed", exc_info=True)
        return False
