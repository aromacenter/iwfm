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


# Beépített értesítés-sablonok eseményenként ({{változók}} az esemény
# kontextusából) — a Beállításokban pipált eseményekről automatikusan megy.
EVENT_TEMPLATES: dict[str, str] = {
    "settlement.created": "🧾 Elszámolás készült: {{partner_nev}} — {{vegosszeg_brutto}} Ft ({{elszamolo}})",
    "settlement.signed": "✍️ Elszámolás aláírva: {{partner_nev}} — {{vegosszeg_brutto}} Ft",
    "ticket.created": "🔧 Új szervizjegy: {{cim}} — {{partner_nev}} ({{gep_nev}})",
    "ticket.done": "✅ Szervizjegy lezárva: {{cim}} — {{partner_nev}}, költség: {{koltseg_osszesen}} Ft",
    "order.created": "📦 Új rendelés (QR): {{rendeles_szam}} — {{partner_nev}}: {{tetel_lista}}",
    "partner.created": "🤝 Új partner: {{partner_nev}} ({{varos}})",
    "counter.reported": "🔢 Számláló bejelentve: {{gep_nev}} — {{partner_nev}}: {{szamlalo}}",
    "stock.low": "⚠️ Alacsony készlet: {{partner_nev}} — {{termek_nev}}: {{keszlet_kg}} kg (küszöb: {{kuszob_kg}} kg)",
}


async def notify_event(db: AsyncSession, event: str, ctx: dict) -> bool:
    """Beépített Telegram-értesítés: ha az esemény be van pipálva a
    Beállításokban, a sablon-üzenet kimegy az összes beállított chatre.
    Best-effort — hibája sosem akasztja meg a fő műveletet."""
    from app.services.wfm.automation import render
    from app.services.wfm.notifier import get_or_create_settings

    template = EVENT_TEMPLATES.get(event)
    if template is None:
        return False
    row = await get_or_create_settings(db)
    enabled_events = {
        x.strip() for x in (row.tg_events or "").replace(";", ",").split(",") if x.strip()
    }
    if event not in enabled_events:
        return False
    config = await load_telegram_config(db)
    if config is None or not config["chat_ids"]:
        return False
    text = render(template, ctx)
    ok = False
    for chat in config["chat_ids"]:
        ok = await send_telegram(config, chat, text) or ok
    return ok


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
