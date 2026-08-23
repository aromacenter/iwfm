"""Naptár- és jogszabály-figyelő (AI-alapú, best-effort).

1) ensure_next_year_calendar: ha a következő (vagy az aktuális) évre nincs
   munkarend-adat, az AI-tól kéri le a rendeletben kihirdetett áthelyezett
   pihenőnapokat + ledolgozó szombatokat, elmenti (source='ai'), és értesíti
   az adminokat, hogy ELLENŐRIZZÉK a Beállításokban.
2) monthly_mt_check: havonta megkérdezi az AI-t, változott-e a Munka
   Törvénykönyve a beosztást/pihenőidőt/túlórát érintő szabályokban —
   tájékoztató jelleggel értesít.

Mindkettő csendben kihagy, ha nincs AI-kulcs; a hibáik sosem akasztják meg a
notifier körét.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> dict | None:
    """Az AI-válaszból az első JSON-objektum (kódblokk-tűrő)."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


async def _notify_admins(db: AsyncSession, subject: str, body: str) -> None:
    """Értesítés a fő Telegram-csoportba és az értesítési e-mail címekre."""
    try:
        from app.services.wfm.telegram import load_telegram_config, send_telegram

        config = await load_telegram_config(db)
        if config is not None:
            for chat in config["chat_ids"]:
                await send_telegram(config, chat, f"{subject}\n\n{body}"[:4000])
    except Exception:
        logger.warning("calendar notify (telegram) failed", exc_info=True)
    try:
        from app.services.wfm.email_service import load_smtp_config, send_email
        from app.services.wfm.notifier import _recipients, get_or_create_settings

        row = await get_or_create_settings(db)
        smtp = await load_smtp_config(db)
        if smtp is not None:
            for to in _recipients(row):
                await send_email(smtp, to, subject, body)
    except Exception:
        logger.warning("calendar notify (email) failed", exc_info=True)


async def ensure_next_year_calendar(db: AsyncSession) -> bool:
    """A hiányzó évek munkarendjének AI-lekérése (havonta legfeljebb egyszer
    próbálkozik). Igaz, ha új év került az adatbázisba."""
    from app.models import CalendarOverride
    from app.services.wfm import ai_service
    from app.services.wfm.holidays import known_years, load_overrides
    from app.services.wfm.notifier import get_or_create_settings

    await load_overrides(db)
    today = date.today()
    targets = [y for y in (today.year, today.year + 1) if y not in known_years()]
    if not targets:
        return False

    row = await get_or_create_settings(db)
    month_key = f"{today:%Y-%m}"
    if row.cal_last_check == month_key:
        return False  # ebben a hónapban már próbáltuk
    row.cal_last_check = month_key
    await db.commit()

    added = False
    for year in targets:
        prompt = (
            f"A magyar kormányrendelet évente kihirdeti a(z) {year}. évi "
            "munkaszüneti napok körüli munkarendet: mely napok lettek "
            "ÁTHELYEZETT PIHENŐNAPOK (hídnapok), és mely SZOMBATOK lettek "
            "ledolgozó munkanapok. Ha ismered a(z) "
            f"{year}. évi rendeletet, add meg PONTOSAN ezt a JSON-t és semmi "
            'mást: {"ismert": true, "pihenonapok": ["ÉÉÉÉ-HH-NN", ...], '
            '"ledolgozo_szombatok": ["ÉÉÉÉ-HH-NN", ...]}. Ha nem ismered '
            'biztosan, válaszold: {"ismert": false}.'
        )
        try:
            text = await ai_service.generate(db, prompt, max_tokens=600)
        except ValueError:
            return False  # nincs AI-kulcs — csendben kihagyjuk
        except Exception:
            logger.warning("calendar AI fetch failed (%s)", year, exc_info=True)
            continue
        data = _extract_json(text)
        if not data or not data.get("ismert"):
            continue
        try:
            rest = [str(date.fromisoformat(x)) for x in data.get("pihenonapok", [])]
            worked = [
                str(date.fromisoformat(x))
                for x in data.get("ledolgozo_szombatok", [])
            ]
            # csak az adott évbe eső dátumok; a "ledolgozó" tényleg szombat-e
            rest = [x for x in rest if x.startswith(str(year))]
            worked = [
                x for x in worked
                if x.startswith(str(year)) and date.fromisoformat(x).weekday() == 5
            ]
        except ValueError:
            continue
        existing = (
            await db.execute(
                select(CalendarOverride).where(CalendarOverride.year == year)
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        db.add(CalendarOverride(
            year=year, rest_days=rest, worked_saturdays=worked,
            source="ai", note=f"AI-frissítés {datetime.now(UTC):%Y-%m-%d}",
        ))
        await db.commit()
        added = True
        await _notify_admins(
            db,
            f"📅 X-admin: {year}. évi munkarend betöltve (AI)",
            (
                f"A(z) {year}. évi munkarend automatikusan bekerült a rendszerbe.\n"
                f"Áthelyezett pihenőnapok: {', '.join(rest) or '—'}\n"
                f"Ledolgozó szombatok: {', '.join(worked) or '—'}\n\n"
                "Kérlek ELLENŐRIZD a Beállítások → Értesítések → Munkarend "
                "blokkban, és javítsd, ha eltér a rendelettől!"
            ),
        )
    if added:
        await load_overrides(db)
    return added


async def monthly_mt_check(db: AsyncSession) -> bool:
    """Havi Mt.-változás-ellenőrzés (tájékoztató). Igaz, ha most futott."""
    from app.services.wfm import ai_service
    from app.services.wfm.notifier import get_or_create_settings

    row = await get_or_create_settings(db)
    month_key = f"{date.today():%Y-%m}"
    if row.mt_last_check == month_key:
        return False

    prompt = (
        "A magyar Munka Törvénykönyve (2012. évi I. törvény) munkaidő-"
        "beosztást, pihenőidőt, munkaszüneti napokat vagy túlórát érintő "
        "szabályai változtak-e az elmúlt fél évben, vagy van-e kihirdetett, "
        "hamarosan hatályba lépő módosítás? Válaszolj PONTOSAN ezzel a "
        'JSON-nal: {"valtozott": true/false, "osszefoglalo": "1-3 mondat '
        'magyarul"}. Ha bizonytalan vagy, valtozott=false és jelezd az '
        "összefoglalóban a bizonytalanságot."
    )
    try:
        text = await ai_service.generate(db, prompt, max_tokens=500)
    except ValueError:
        return False  # nincs AI-kulcs
    except Exception:
        logger.warning("mt check AI failed", exc_info=True)
        return False

    data = _extract_json(text) or {}
    summary = str(data.get("osszefoglalo") or "").strip() or text.strip()[:500]
    changed = bool(data.get("valtozott"))
    row.mt_last_check = month_key
    row.mt_last_result = f"{'VÁLTOZÁS' if changed else 'nincs változás'}: {summary}"
    await db.commit()
    if changed:
        await _notify_admins(
            db,
            "⚖️ X-admin: lehetséges Mt.-változás",
            (
                f"{summary}\n\n"
                "Ez automatikus, tájékoztató jellegű AI-ellenőrzés — a "
                "részleteket jogásszal/könyvelővel érdemes egyeztetni."
            ),
        )
    return True
