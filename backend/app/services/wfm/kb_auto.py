"""Automatikus tudásbázis-bővítés új gépekhez.

Amikor új gép kerül a nyilvántartásba (kézzel vagy importból), a háttérben
ellenőrizzük, hogy a gyártó+típushoz van-e már AI-generált bejegyzés a
strukturált tudásbázisban (knowledge_entries tábla). Ha nincs, az AI-val
legeneráljuk a hibakód/kijelző-üzenet bejegyzéseket — kereshetően, gépre
szűrhetően.

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


def _build_prompt(label: str, hint: str | None = None) -> str:
    """Géptípus-TUDATOS prompt (113. kör, d49cb1fd): a modell először
    besorolja a gépet (karos presszó / szuperautomata / kapszulás / filteres /
    vending / daráló / egyéb), és CSAK az abban a kategóriában létező
    alkatrészekről, üzenetekről ír. Karos gépnél így nem jön szemeskávé-tartály
    és zacctartó. A ``hint`` a felhasználó pontosítása (pl. „karos gép,
    kétkörös kazán") — minden feltételezést felülír."""
    hint_block = ""
    if hint and hint.strip():
        hint_block = (
            "\nFONTOS pontosítás a felhasználótól — ez minden feltételezést felülír: "
            f"{hint.strip()[:500]}\n"
        )
    return (
        f"A(z) {label} géphez készíts magyar nyelvű ügyfél-támogatási tudásbázist.\n"
        f"{hint_block}\n"
        "ELSŐ LÉPÉS: állapítsd meg a gép kategóriáját a gyártó és a típus alapján "
        "(ha van pontosítás, az irányadó):\n"
        "  A) karos / hagyományos (félautomata) presszógép — pl. La Marzocco, "
        "Di Giorgio, Faema, Rancilio, Nuova Simonelli, La Cimbali, Gaggia Classic, "
        "Bezzera, ECM, Rocket, Lelit, Astoria, Wega — szűrőkosaras karral, "
        "gőzpipával, kazánnal, manométerrel;\n"
        "  B) szuperautomata — pl. Jura, Saeco/Philips, DeLonghi, Siemens, Melitta, "
        "Franke, Schaerer, WMF, Nivona, Krups — beépített darálóval, zacctartóval, "
        "kijelzővel;\n"
        "  C) kapszulás / podos gép;\n"
        "  D) filteres / csepegtetős kávéfőző vagy teafőző;\n"
        "  E) kávé- vagy ital-automata (vending);\n"
        "  F) kávédaráló;\n"
        "  G) egyéb kávézós eszköz (vízlágyító, hűtő, jégkészítő, tejhabosító stb.).\n\n"
        "Add vissza KIZÁRÓLAG egy JSON tömböt, minden más szöveg nélkül, ebben a "
        "formában:\n"
        '[{"code": "E8", "title": "rövid cím", "body": "2-5 mondat"}]\n'
        "Az ELSŐ elem a besorolás legyen: "
        '{"code": "Géptípus", "title": "<kategória neve>", '
        '"body": "<1-2 mondat: mi jellemzi ezt a gépet, mire figyeljen a használó>"}.\n\n'
        "Tartalmi szabályok:\n"
        "- CSAK olyan alkatrészt, üzenetet, jelzést említs, ami EBBEN a kategóriában "
        "létezik. Karos gépnél NINCS szemeskávé-tartály, daráló, zacctartó, automata "
        "öblítés vagy kijelző-üzenet — ott a témák: kazánnyomás és manométer, "
        "nyomásfokozó/vibrációs szivattyú, mágnesszelep, csoportfej-tömítés és "
        "zuhanyszűrő, gőzpipa és gőzcsap, vízszint-szonda és automata vízfeltöltés, "
        "fűtőszál / termosztát / presszosztát / PID, biztonsági szelep, vízkövesedés, "
        "kar és szűrőkosár, csepegés, nem jön ki a kávé, túl gyors vagy lassú "
        "kifolyás, nem gőzöl, hangos szivattyú, hálózati víz hiánya, "
        "hátsó-nyomás szelep (expanziós szelep).\n"
        "- Szuperautomatánál: a gyártó ismert hibakódjai (E-kódok) ÉS a kijelző-"
        "üzenetek (víztartály, szemes kávé, zacctartó, csepptálca, öblítés, "
        "tisztítás, vízkőtelenítés, szűrőcsere, fűtés).\n"
        "- Automatánál (vending): pénzkezelő, pohár-adagoló, keverőegység, "
        "portartály, vízbekötés, a vezérlő hibakódjai.\n"
        "- body: mit jelent, milyen lépésekkel háríthatja el egy NEM szakember "
        "ügyfél (biztonságosan — ahol kell, kikapcsolt, lehűlt gépen), és mikor "
        "kell szervizigényt bejelenteni.\n"
        "- 8–20 bejegyzés. Csak olyan kódot/jelzést írj le, amely ennél a "
        "gyártónál/típusnál valóban használatos; bizonytalan kódnál a title "
        "végére írd: '(ellenőrizendő)'. Ha a típusnak nincsenek hibakódjai, a "
        "code a tünet rövid neve legyen (pl. 'Nem gőzöl', 'Alacsony kazánnyomás').\n"
        "- code: a hibakód vagy a tünet/üzenet rövid azonosítója."
    )


def parse_articles(text: str) -> list[dict]:
    """A modellválasz JSON-tömbjének kinyerése (kódblokk-kerítés tűrve)."""
    import json

    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        items = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or "").strip()
        if code and body:
            out.append({"code": code, "title": title, "body": body})
    return out


async def generate_articles(
    db, manufacturer: str | None, name: str | None, hint: str | None = None
) -> int:
    """Strukturált tudásbázis-bejegyzések generálása egy géptípushoz a
    knowledge_entries táblába (kereshető, gépre szűrhető). Vissza: hány
    bejegyzés készült. ValueError, ha az AI nincs beállítva. A ``hint`` a
    felhasználó pontosítása a géptípusról (újragenerálásnál)."""
    from app.models import KnowledgeEntry

    label = machine_label(manufacturer, name)
    if len(label) < 3:
        return 0
    text = await ai_service.generate(db, _build_prompt(label, hint), max_tokens=4000)
    articles = parse_articles(text)
    for a in articles:
        title = f"{a['code']} — {a['title']}" if a["title"] else a["code"]
        db.add(KnowledgeEntry(
            machine_label=label[:256],
            title=title[:256],
            content=a["body"][:8000],
            source="ai",
            created_by_name="AI generálás",
        ))
    if articles:
        db.add(AuditEvent(
            actor_user_id=None, action="support.kb_auto", entity_type="knowledge_entry",
            entity_id=label[:64], detail={"machine": label, "entries": len(articles)},
        ))
        await db.commit()
    return len(articles)


async def regenerate_articles(db, label: str, hint: str | None = None) -> tuple[int, int]:
    """Újragenerálás (113. kör): a címke AI-bejegyzéseit töröljük, majd a
    pontosítással (hint) újat kérünk. A kézi bejegyzések maradnak. Vissza:
    (törölt, készült). ValueError, ha az AI nincs beállítva."""
    from sqlalchemy import delete as sa_delete

    from app.models import KnowledgeEntry

    label = (label or "").strip()
    if len(label) < 2:
        return 0, 0
    # Előbb a generálás (ha az AI nincs beállítva, a régi bejegyzések maradnak)
    text = await ai_service.generate(db, _build_prompt(label, hint), max_tokens=4000)
    articles = parse_articles(text)
    deleted = (
        await db.execute(
            sa_delete(KnowledgeEntry).where(
                KnowledgeEntry.machine_label == label[:256],
                KnowledgeEntry.source == "ai",
            )
        )
    ).rowcount or 0
    for a in articles:
        title = f"{a['code']} — {a['title']}" if a["title"] else a["code"]
        db.add(KnowledgeEntry(
            machine_label=label[:256],
            title=title[:256],
            content=a["body"][:8000],
            source="ai",
            created_by_name="AI generálás",
        ))
    db.add(AuditEvent(
        actor_user_id=None, action="support.kb_regenerate", entity_type="knowledge_entry",
        entity_id=label[:64],
        detail={"machine": label, "deleted": deleted, "entries": len(articles), "hint": (hint or "")[:200]},
    ))
    await db.commit()
    return deleted, len(articles)


async def has_ai_articles(db, label: str) -> bool:
    from sqlalchemy import func as sa_func

    from app.models import KnowledgeEntry

    count = (
        await db.execute(
            select(sa_func.count()).select_from(KnowledgeEntry).where(
                KnowledgeEntry.machine_label == label[:256],
                KnowledgeEntry.source == "ai",
            )
        )
    ).scalar_one()
    return count > 0


async def ensure_machine_kb(manufacturer: str | None, name: str | None) -> bool:
    """Háttérfeladat: strukturált tudásbázis-bejegyzések generálása az új
    géptípushoz (knowledge_entries tábla — kereshető, gépre szűrhető).

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
        if await has_ai_articles(db, label):
            return False

        try:
            count = await generate_articles(db, manufacturer, name)
        except ValueError:
            logger.info("Auto-KB kihagyva (%s): AI nincs beállítva", label)
            return False
        except Exception:
            logger.warning("Auto-KB generálás sikertelen (%s)", label, exc_info=True)
            return False
        if count:
            logger.info("Auto-KB: %d bejegyzés generálva — %s", count, label)
        return count > 0
