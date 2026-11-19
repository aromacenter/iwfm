"""X-Learning borító (164. kör): rajzolt, ház-stílusú SVG-illusztráció + halvány
pasztell alapszín minden tananyaghoz, hogy a csempék egységesek legyenek.

Három forrás, ugyanaz a stílus:
- könyvtár: `learning_library/_art/*.svg` — kulcsszó alapján választott kész rajz;
- AI: a tartalomból a beállított szolgáltató (Beállítások → AI) rajzol új SVG-t
  szigorú stílus-szabályokkal, amit itt megtisztítunk (csak alakzatok, se script,
  se külső hivatkozás); ha nem jó, a könyvtár a tartalék;
- kézi választás a könyvtárból.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

ART_DIR = Path(__file__).resolve().parents[2] / "learning_library" / "_art"

# Kulcsszó → rajz (a sorrend számít: az első találat nyer)
ART_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("warehouse_bins", ("tárhely", "tarhely", "fakk", "regál", "regal", "polc", "bin", "location")),
    ("warehouse_stock", ("raktár", "raktar", "készlet", "keszlet", "alkatrész", "alkatresz", "szerszám", "szerszam", "stock", "warehouse", "leltár", "leltar")),
    ("coffee_machine", ("kávégép", "kavegep", "karbantart", "tisztít", "tisztit", "vízkő", "vizko", "automata", "coffee machine")),
    ("service", ("szerviz", "javítás", "javitas", "hiba", "munkalap", "worksheet", "service", "repair", "ksz")),
    ("intake", ("átvétel", "atvetel", "elismervény", "elismerveny", "behozatal", "intake")),
    ("handover_cash", ("átadás", "atadas", "kassza", "fizetés", "fizetes", "pénz", "penz", "cash", "handover")),
    ("settlement", ("elszámolás", "elszamolas", "számláló", "szamlalo", "bizonylat", "partner", "settlement", "invoice")),
    ("tasks", ("feladat", "naptár", "naptar", "beosztás", "beosztas", "task", "calendar", "ajánlat", "ajanlat")),
    ("basics", ("alap", "belépés", "belepes", "menü", "menu", "hibajelent", "felület", "felulet", "basics", "login")),
    ("knowledge", ("tudás", "tudas", "tananyag", "oktat", "tanul", "learning", "knowledge")),
]

# Halvány alapszínek (egymástól jól elkülönülő pasztellek)
PALETTE = ("#e0e7ff", "#d1fae5", "#fef3c7", "#fee2e2", "#ede9fe", "#cffafe", "#fce7f3", "#ecfccb", "#ffedd5", "#e2e8f0")

_ALLOWED_TAGS = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon"}
_ALLOWED_ATTRS = {
    "xmlns", "viewbox", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "d", "x", "y",
    "width", "height", "rx", "ry", "cx", "cy", "r", "x1", "y1", "x2", "y2", "points", "opacity", "transform",
    "fill-opacity", "stroke-opacity", "stroke-dasharray",
}
_TAG_RE = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9]*)([^>]*?)(/?)\s*>", re.S)
_ATTR_RE = re.compile(r'([a-zA-Z:-]+)\s*=\s*"([^"]*)"')


def art_keys() -> list[str]:
    return sorted(p.stem for p in ART_DIR.glob("*.svg")) if ART_DIR.is_dir() else []


def art_svg(key: str) -> str | None:
    if not re.fullmatch(r"[a-z0-9_]+", key or ""):
        return None
    f = ART_DIR / f"{key}.svg"
    return f.read_text(encoding="utf-8") if f.is_file() else None


def pick_art(title: str | None, category: str | None, body: str | None = None) -> str:
    """Pontozásos választás: a cím+kategória találatai 2, a szöveg elejéé 1 pontot
    érnek; a legtöbb pont nyer, döntetlennél a katalógus sorrendje."""
    head = f"{title or ''} {category or ''}".lower()
    text = (body or "")[:1500].lower()
    best, best_score = "default", 0
    for key, words in ART_KEYWORDS:
        score = sum(2 for w in words if w in head) + sum(1 for w in words if w in text)
        if score > best_score:
            best, best_score = key, score
    return best


def pick_color(seed: str) -> str:
    h = int(hashlib.sha1((seed or "").encode("utf-8")).hexdigest()[:8], 16)
    return PALETTE[h % len(PALETTE)]


def sanitize_svg(raw: str) -> str | None:
    """Csak alakzatok és biztonságos attribútumok maradnak; script/style/kép/
    hivatkozás/szöveg kimegy. None, ha nem SVG vagy üres marad."""
    if not raw:
        return None
    m = re.search(r"<svg\b.*?</svg>", raw, re.S | re.I)
    if not m:
        return None
    src = m.group(0)
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    out: list[str] = []
    shapes = 0

    def repl(mo: re.Match) -> str:
        nonlocal shapes
        closing, tag, attrs, selfclose = mo.group(1), mo.group(2).lower(), mo.group(3), mo.group(4)
        if tag not in _ALLOWED_TAGS:
            return ""
        if closing:
            return f"</{tag}>"
        clean = []
        for name, val in _ATTR_RE.findall(attrs):
            n = name.lower()
            if n not in _ALLOWED_ATTRS or "url(" in val.lower() or "javascript" in val.lower():
                continue
            clean.append(f'{n if n != "viewbox" else "viewBox"}="{val}"')
        if tag != "svg" and tag != "g":
            shapes += 1
        if tag == "svg":
            if not any(c.startswith("xmlns=") for c in clean):
                clean.insert(0, 'xmlns="http://www.w3.org/2000/svg"')
            if not any(c.startswith("viewBox=") for c in clean):
                clean.append('viewBox="0 0 200 150"')
        return f"<{tag} {' '.join(clean)}{'/' if selfclose else ''}>"

    # szöveges tartalom (pl. <text>, <style> belseje) nem kell: csak a tageket tartjuk meg
    for mo in _TAG_RE.finditer(src):
        out.append(repl(mo))
    cleaned = "".join(out)
    if shapes < 3 or len(cleaned) > 60_000 or not cleaned.lower().startswith("<svg"):
        return None
    return cleaned


AI_PROMPT = """Rajzolj egy lapos, vonalas stílusú SVG-illusztrációt egy belső oktatóanyag csempéjére. A téma a következő tananyag:

Cím: {title}
Kategória: {category}
Tartalom eleje: {excerpt}

SZIGORÚ STÍLUS-SZABÁLYOK (ház-stílus, minden csempe egyforma):
- Pontosan egy <svg> elem, viewBox="0 0 200 150", fill="none", stroke="#334155", stroke-width="4", stroke-linecap="round", stroke-linejoin="round" a gyökéren.
- Csak ezek az elemek: path, rect, circle, ellipse, line, polyline, polygon, g. SEMMI text, image, style, script, gradient, filter, href.
- Kitöltéshez kizárólag ezek a színek: #ffffff, #c7d2fe, #a7f3d0, #fde68a, #fecaca, #92400e. Háttér nincs (átlátszó).
- 1–3 egyszerű, felismerhető tárgy a témához (pl. doboz, polc, gép, csésze, lista, naptár, lakat), lekerekített sarkok (rx), 6–20 elem összesen, a rajz töltse ki a 200×150-es területet kb. 20 px margóval.
- Ne írj magyarázatot, csak a nyers SVG-kódot add vissza."""


def ai_prompt(title: str | None, category: str | None, body: str | None) -> str:
    excerpt = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body or "")
    excerpt = re.sub(r"\s+", " ", excerpt).strip()[:900]
    return AI_PROMPT.format(title=title or "", category=category or "", excerpt=excerpt or "—")
