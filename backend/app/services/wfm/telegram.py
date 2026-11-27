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

# Telegram-összekapcsolás: rossz törzsszám-próbálkozások csevegésenként
# (monoton időbélyegek; folyamatonkénti memória, mint a login-throttle).
_link_failures: dict[str, list[float]] = {}

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
    "settlement.paid": "💰 Számla kiegyenlítve: {{partner_nev}} — {{szamla_szam}}, {{vegosszeg_brutto}} Ft ({{forras}})",
    "price.indexation_applied": "🏷️ Áremelés alkalmazva: {{nev}} — {{partnerek}} partner, {{tetelek}} ár ({{kihagyva}} kihagyva), hatály: {{hataly}}",
    "partner.consumption_drop": "📉 Fogyás-csökkenés: {{partner_nev}} ({{varos}}) — {{csokkenes_pct}}% az utolsó két ciklusban ({{mostani_kg_nap}} ← {{korabbi_kg_nap}} kg/nap), képviselő: {{kepviselo}}",
    "stock.expiring": "⏳ Lejáró tétel: {{termek_nev}} {{mennyiseg}} {{egyseg}} — {{raktar}} {{tarhely}}, lejárat: {{lejarat}} ({{napok}} nap)",
    "ticket.created": "🔧 Új szervizjegy: {{cim}} — {{partner_nev}} ({{gep_nev}})",
    "ticket.done": "✅ Szervizjegy lezárva: {{cim}} — {{partner_nev}}, költség: {{koltseg_osszesen}} Ft",
    "worksheet.rated": "{{csillagok}} Ügyfél-értékelés: {{ertekeles}}/5 — {{sorszam}} {{ugyfel}} (szerelő: {{dolgozo}}) {{megjegyzes}}",
    "ticket.sla_warning": "⏰ SLA hamarosan lejár: {{jegy}} {{cim}} — {{partner_nev}}, {{fazis}} határidő {{hatarido}}, felelős: {{felelos}}",
    "ticket.sla_breached": "🚨 SLA LEJÁRT: {{jegy}} {{cim}} — {{partner_nev}}, {{fazis}} határidő {{hatarido}}, felelős: {{felelos}}",
    "order.created": "📦 Új rendelés (QR): {{rendeles_szam}} — {{partner_nev}}: {{tetel_lista}}",
    "partner.created": "🤝 Új partner: {{partner_nev}} ({{varos}})",
    "counter.reported": "🔢 Számláló bejelentve: {{gep_nev}} — {{partner_nev}}: {{szamlalo}}",
    "timeclock.far": "📍 Blokkolás a telephelytől távol: {{dolgozo}} — {{muvelet}} {{idopont}}, {{tavolsag_m}} m a legközelebbi ismert helytől ({{legkozelebbi}}), {{forras}}, pontosság ±{{pontossag_m}} m\n🗺️ {{terkep}}",
    "vending.error": "🚨 Kávégép hiba: {{gep_nev}} ({{gep_kod}}) — {{hiba}} ({{forras}}, {{darab}}×)",
    "stock.low": "⚠️ Alacsony készlet: {{partner_nev}} — {{termek_nev}}: {{keszlet_kg}} kg (küszöb: {{kuszob_kg}} kg)",
    "task.assigned": "📋 Feladat kiosztva: {{cim}} → {{dolgozo}} (határidő: {{hatarido}})",
    "worksheet.signed": "✍️ Munkalap aláírva: {{sorszam}} — {{cim}} ({{dolgozo}})",
    "worksheet.quote_ready": "🧾 Ajánlat elkészült: {{sorszam}} — {{gep_nev}} · {{opciok}} konstrukció ({{dolgozo}}) · {{ugyfel}} · 🔗 {{link}}",
    "worksheet.quote_accepted": "🟢 Árajánlat elfogadva: {{sorszam}} — {{opcio}} ({{ugyfel}})",
    "worksheet.picked_up": "📦 Gép elhozva a szerelőtől: {{sorszam}} — {{gep_nev}} · az ügyfél értesítve, a gép átvehető",
    "worksheet.handed_over": "🤝 Gép átadva: {{sorszam}} — {{ugyfel}} · {{osszeg_netto}} Ft nettó ({{fizetes_mod}})",
    "intake.created": "📥 Gép átvéve: {{sorszam}} — {{gep_nev}} ({{ugyfel}}) · {{hibak}}",
    "warehouse.movement": "📦 Raktármozgás — {{muvelet}}: {{termek_nev}} {{mennyiseg}} {{egyseg}} · {{raktar}} {{reszlet}} ({{aki}})",
    "warehouse.transfer_pending": "🚚 Átadás jóváhagyásra vár: {{termek_nev}} {{mennyiseg}} {{egyseg}} · {{honnan}} → {{hova}} ({{inditotta}})",
    "warehouse.transfer_accepted": "✅ Átadás átvéve: {{termek_nev}} {{mennyiseg}} {{egyseg}} · {{honnan}} → {{hova}} ({{dontott}})",
    "parts.low": "⚠️ Alkatrész min. alatt: {{nev}} ({{cikkszam}}) — összesen {{keszlet}} {{egyseg}} (min: {{minimum}} {{egyseg}})",
    "parts.request": "🔩 Alkatrész-kérés: {{nev}} ({{cikkszam}}) {{mennyiseg}} {{egyseg}} → {{kero}} {{megjegyzes}}",
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
    from app.services.wfm.links import with_link

    with_link(ctx, event)
    text = render(template, ctx)
    # 183. kör: minden üzenet végén kattintható link a céloldalra (ha a sablon még nem tartalmazza)
    if ctx.get("link") and "{{link}}" not in template:
        text = f"{text}\n🔗 {ctx['link']}"
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
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        # átmeneti hálózati hiba (Telegram API lassú/elérhetetlen): egy sor, stack nélkül —
        # a következő körben újrapróbálja, a naplót ne öntse el (169. kör)
        logger.warning("telegram getUpdates: %s — következő körben újra", exc.__class__.__name__)
        return []
    except (httpx.HTTPError, ValueError):
        logger.warning("telegram getUpdates failed", exc_info=True)
        return []


async def process_updates(db: AsyncSession) -> int:
    """Összekapcsolás egyszeri kóddal (182. kör): a botnak PRIVÁTBAN elküldött
    6 jegyű kód (vagy "/start <kód>") — amit az appban kért a dolgozó, vagy a
    vezető kért le neki — a karton / fiók telegram_chat_id-ját menti, és a bot
    visszaigazol. A kód 10 percig él és egyszer használható; a törzsszámmal
    már NEM lehet összekapcsolni. A notifier 5 perces köre hívja."""
    import re as _re

    from app.models import Employee
    from app.services.wfm.notifier import get_or_create_settings
    from app.services.wfm.telegram_link import consume_code, find_by_code

    config = await load_telegram_config(db)
    if config is None:
        return 0
    row = await get_or_create_settings(db)
    updates = await fetch_updates(config, row.tg_update_offset)
    if not updates:
        return 0

    import time as _time

    linked = 0
    for u in updates:
        row.tg_update_offset = max(row.tg_update_offset or 0, int(u.get("update_id", 0)) + 1)
        msg = u.get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") != "private":
            continue  # csoport-üzenetekre nem reagálunk
        chat_id = str(chat["id"])
        text = (msg.get("text") or "").strip()
        m = _re.fullmatch(r"(?:/start\s+)?(\d{6})", text)
        if not m:
            if text.startswith("/start"):
                await send_telegram(
                    config, chat_id,
                    "Szia! 👋 Az összekapcsoláshoz az X-adminban kérj egy kódot (Beállítások → "
                    "Kétlépcsős belépés kártya, vagy a vezetődtől), és küldd el ide a 6 számjegyet. "
                    "A kód 10 percig érvényes.",
                )
            continue
        # Kód-próbálgatás fékje: csevegésenként legfeljebb 5 rossz kód óránként.
        now = _time.monotonic()
        fails = [t for t in _link_failures.get(chat_id, []) if now - t < 3600]
        _link_failures[chat_id] = fails
        if len(fails) >= 5:
            continue
        target = await find_by_code(db, m.group(1))
        if target is None:
            _link_failures[chat_id].append(now)
            await send_telegram(
                config, chat_id,
                "❌ Ismeretlen vagy lejárt kód. Kérj újat az appban (Beállítások → Kétlépcsős "
                "belépés) vagy a vezetődtől, és küldd el 10 percen belül.",
            )
            continue
        is_emp = isinstance(target, Employee)
        if target.telegram_chat_id and target.telegram_chat_id != chat_id:
            # Már más csevegéshez van kötve: nem írjuk felül némán (eltérítés ellen).
            _link_failures[chat_id].append(now)
            await send_telegram(
                config, chat_id,
                "⚠️ Ez a fiók már össze van kapcsolva egy másik Telegram-fiókkal. "
                + ("Ha te vagy az, kérd a vezetődtől a régi kapcsolat bontását." if is_emp
                   else "Ha te vagy az, bontsd a régi kapcsolatot a Beállítások → Jogosultságok → Kétlépcsős belépés kártyán."),
            )
            await send_telegram(
                config, target.telegram_chat_id,
                "⚠️ Valaki a te számoddal (összekapcsoló kódoddal) próbált Telegramot összekapcsolni. "
                + ("Ha nem te voltál, szólj a vezetődnek." if is_emp else "Ha nem te voltál, változtasd meg a jelszavad."),
            )
            continue
        target.telegram_chat_id = chat_id
        consume_code(target)
        linked += 1
        if is_emp:
            await send_telegram(
                config, chat_id,
                f"✅ Összekapcsolva, {target.first_name}! Mostantól itt kapod a neked "
                "kiosztott feladatokról az értesítést és a belépési kódokat.",
            )
        else:
            await send_telegram(
                config, chat_id,
                f"✅ Összekapcsolva, {target.display_name}! Mostantól ide kapod a belépési kódokat.",
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
