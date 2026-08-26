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
UPDATES_URL = "https://api.telegram.org/bot{token}/getUpdates"


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
    "task.assigned": "📋 Feladat kiosztva: {{cim}} → {{dolgozo}} (határidő: {{hatarido}})",
    "worksheet.signed": "✍️ Munkalap aláírva: {{sorszam}} — {{cim}} ({{dolgozo}})",
    "worksheet.quote_accepted": "🟢 Árajánlat elfogadva: {{sorszam}} — {{opcio}} ({{ugyfel}})",
    "worksheet.picked_up": "📦 Gép elhozva a szerelőtől: {{sorszam}} — {{gep_nev}} · az ügyfél értesítve, a gép átvehető",
    "worksheet.handed_over": "🤝 Gép átadva: {{sorszam}} — {{ugyfel}} · {{osszeg_netto}} Ft nettó ({{fizetes_mod}})",
    "intake.created": "📥 Gép átvéve: {{sorszam}} — {{gep_nev}} ({{ugyfel}}) · {{hibak}}",
    "warehouse.movement": "📦 Raktármozgás — {{muvelet}}: {{termek_nev}} {{mennyiseg}} {{egyseg}} · {{raktar}} {{reszlet}} ({{aki}})",
    "warehouse.transfer_pending": "🚚 Átadás jóváhagyásra vár: {{termek_nev}} {{mennyiseg}} {{egyseg}} · {{honnan}} → {{hova}} ({{inditotta}})",
    "warehouse.transfer_accepted": "✅ Átadás átvéve: {{termek_nev}} {{mennyiseg}} {{egyseg}} · {{honnan}} → {{hova}} ({{dontott}})",
    "warehouse.transfer_rejected": "🚫 Átadás elutasítva: {{termek_nev}} {{mennyiseg}} {{egyseg}} · {{honnan}} → {{hova}} ({{dontott}}) {{indok}}",
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


async def fetch_updates(config: dict, offset: int | None) -> list[dict]:
    """A bot új bejövő üzenetei (getUpdates) — hiba esetén üres lista."""
    params: dict = {"timeout": 0, "allowed_updates": '["message"]'}
    if offset is not None:
        params["offset"] = offset
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(
                UPDATES_URL.format(token=config["token"]), params=params
            )
        data = res.json()
        if not data.get("ok"):
            return []
        return data.get("result") or []
    except (httpx.HTTPError, ValueError):
        logger.warning("telegram getUpdates failed", exc_info=True)
        return []


async def process_updates(db: AsyncSession) -> int:
    """Dolgozói összekapcsolás: a botnak PRIVÁTBAN elküldött 6 jegyű törzsszám
    (vagy "/start <törzsszám>") a dolgozó telegram_chat_id-ját menti, és a bot
    visszaigazol. A notifier 5 perces köre hívja. Vissza: hány összekapcsolás
    történt."""
    import re as _re

    from sqlalchemy import select as _select

    from app.models import Employee
    from app.services.wfm.notifier import get_or_create_settings

    config = await load_telegram_config(db)
    if config is None:
        return 0
    row = await get_or_create_settings(db)
    updates = await fetch_updates(config, row.tg_update_offset)
    if not updates:
        return 0

    linked = 0
    for u in updates:
        row.tg_update_offset = max(row.tg_update_offset or 0, int(u.get("update_id", 0)) + 1)
        msg = u.get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") != "private":
            continue  # csoport-üzenetekre nem reagálunk
        text = (msg.get("text") or "").strip()
        m = _re.fullmatch(r"(?:/start\s+)?(\d{6})", text)
        if not m:
            if text.startswith("/start"):
                await send_telegram(
                    config, str(chat["id"]),
                    "Szia! 👋 Az összekapcsoláshoz küldd el a 6 jegyű törzsszámodat "
                    "(ugyanaz, amivel a blokkoló-terminálon jelentkezel).",
                )
            continue
        emp = (
            await db.execute(
                _select(Employee).where(
                    Employee.employee_code == m.group(1),
                    Employee.status == "active",
                )
            )
        ).scalar_one_or_none()
        if emp is None:
            await send_telegram(
                config, str(chat["id"]),
                "❌ Ismeretlen törzsszám. Ellenőrizd, és küldd el újra a 6 számjegyet.",
            )
            continue
        emp.telegram_chat_id = str(chat["id"])
        linked += 1
        await send_telegram(
            config, str(chat["id"]),
            f"✅ Összekapcsolva, {emp.first_name}! Mostantól itt kapod a neked "
            "kiosztott feladatokról az értesítést.",
        )
    await db.commit()
    return linked


async def send_personal(db: AsyncSession, employee, text: str) -> bool:
    """Személyre szóló üzenet a dolgozó privát csevegésébe (ha összekapcsolta
    magát). Best-effort."""
    if not getattr(employee, "telegram_chat_id", None):
        return False
    config = await load_telegram_config(db)
    if config is None:
        return False
    return await send_telegram(config, employee.telegram_chat_id, text)
