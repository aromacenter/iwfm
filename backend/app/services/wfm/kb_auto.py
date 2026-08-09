"""Automatikus tudásbázis-bővítés új gépekhez.

Amikor új gép kerül a nyilvántartásba (kézzel vagy importból), a háttérben
ellenőrizzük, hogy a gyártó+típus szerepel-e már a támogatási tudásbázisban.
Ha nem, az AI-val legeneráljuk a hibakód/kijelző-üzenet szekciókat a QR-oldali
chat formátumában ("## <Gép> — <kód>" címsorok), és hozzáfűzzük a
tudásbázishoz. A generált blokk elé rejtett marker kerül, így ugyanahhoz a
géptípushoz nem generálunk kétszer.

A művelet best-effort: AI-kulcs híján vagy hiba esetén csendben kimarad —
a gép-rögzítést soha nem akadályozza.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import select

from app.models import AuditEvent, SupportSettings
from app.services.wfm import ai_service

logger = logging.getLogger(__name__)

_MAX_KB_CHARS = 400_000  # e fölött nem bővítünk automatikusan


def machine_label(manufacturer: str | None, name: str | None) -> str:
    """„Jura" + „Jura X8 Professional" → „Jura X8 Professional" (nincs duplázás)."""
    manufacturer = (manufacturer or "").strip()
    name = (name or "").strip()
    if not manufacturer:
        return name
    if not name:
        return manufacturer
    if name.lower().startswith(manufacturer.lower()):
        return name
    return f"{manufacturer} {name}"


def _marker(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return f"<!-- kb-auto: {slug} -->"


def has_section_for(kb: str, manufacturer: str | None, name: str | None) -> bool:
    """Van-e már „## " címsor, amely lefedi ezt a gyártót/típust?

    Gyártóval: a címsornak a gyártót kell tartalmaznia. Gyártó nélkül a típus
    első értelmes szavát keressük."""
    terms: list[str] = []
    if manufacturer and len(manufacturer.strip()) >= 3:
        terms.append(manufacturer.strip().lower())
    else:
        terms.extend(w.lower() for w in (name or "").split() if len(w) >= 3)
    if not terms:
        return True  # nincs mi alapján generálni
    for line in kb.splitlines():
        if line.startswith("## "):
            head = line[3:].lower()
            if any(term in head for term in terms):
                return True
    return False


def _build_prompt(label: str) -> str:
    return (
        f"Készíts magyar nyelvű ügyfél-támogatási tudásbázis-szekciót a(z) "
        f"{label} kávégéphez.\n\n"
        "Formátum (pontosan tartsd be):\n"
        f"- Minden bejegyzés külön '## {label} — <hibakód vagy üzenet>' "
        "címsorral kezdődjön.\n"
        "- A címsor után 2–5 mondat: mit jelent, milyen lépésekkel háríthatja "
        "el egy NEM szakember ügyfél, és mikor kell szervizigényt bejelenteni.\n"
        "- Vedd sorra a gyártónál ismert hibakódokat (Error/E-kódok) ÉS a "
        "gyakori kijelző-üzeneteket (víztartály, szemes kávé, zacctartó, "
        "csepptálca, öblítés, tisztítás, vízkőtelenítés, szűrőcsere).\n"
        "- Csak olyan kódot írj le, amely ennél a gyártónál/típusnál valóban "
        "használatos; ha egy kódban bizonytalan vagy, írd oda: "
        "'(ellenőrizendő)'.\n"
        "- Ne írj bevezetőt, összegzést vagy egyéb szöveget a szekciókon "
        "kívül.\n"
        "- Az utolsó sor legyen pontosan: "
        "'(AI által generált szekció — szerviztechnikusi ellenőrzés ajánlott)'"
    )


async def ensure_machine_kb(manufacturer: str | None, name: str | None) -> bool:
    """Háttérfeladat: tudásbázis-szekció generálása az új géptípushoz.

    True, ha bővítettük a tudásbázist; False, ha nem volt rá szükség vagy nem
    lehetett (AI nincs beállítva, ki van kapcsolva, már létezik)."""
    from app.db import get_session_factory

    label = machine_label(manufacturer, name)
    if len(label) < 3:
        return False

    factory = get_session_factory()
    async with factory() as db:
        row = (
            await db.execute(select(SupportSettings).where(SupportSettings.id == 1))
        ).scalar_one_or_none()
        if row is None:
            row = SupportSettings(id=1)
            db.add(row)
            await db.flush()
        if not row.auto_kb:
            return False

        kb = row.knowledge_base or ""
        marker = _marker(label)
        if marker in kb or has_section_for(kb, manufacturer, name):
            return False
        if len(kb) > _MAX_KB_CHARS:
            logger.warning("Auto-KB kihagyva (%s): a tudásbázis túl nagy", label)
            return False

        try:
            text = await ai_service.generate(db, _build_prompt(label), max_tokens=3000)
        except ValueError:
            logger.info("Auto-KB kihagyva (%s): AI nincs beállítva", label)
            return False
        except Exception:
            logger.warning("Auto-KB generálás sikertelen (%s)", label, exc_info=True)
            return False

        text = (text or "").strip()
        if "## " not in text:
            logger.warning("Auto-KB (%s): a válasz nem szekcionált, eldobva", label)
            return False

        row.knowledge_base = f"{kb.rstrip()}\n\n{marker}\n{text}\n".lstrip()
        db.add(
            AuditEvent(
                actor_user_id=None,
                action="support.kb_auto",
                entity_type="settings",
                entity_id="support",
                detail={"machine": label, "chars": len(text)},
            )
        )
        await db.commit()
        logger.info("Auto-KB: tudásbázis bővítve — %s (%d karakter)", label, len(text))
        return True
