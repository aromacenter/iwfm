"""Hungarian identifier validators (checksum-verified).

* adóazonosító jel — 10 numeric digits, starts with 8, mod-11 check digit
* TAJ szám — 9 numeric digits, 3/7 weighted mod-10 check digit
* bankszámlaszám (pénzforgalmi jelzőszám, GIRO) — 16 or 24 digits in 8-digit
  blocks, 9-7-3-1 weighted mod-10 checks per MNB rules

All functions accept human formatting (spaces, hyphens) and return bool.
"""

from __future__ import annotations

import re


def _digits(value: str) -> str:
    return re.sub(r"[\s-]", "", value or "")


def is_valid_tax_id(value: str) -> bool:
    """Adóazonosító jel: 10 digits; 1st digit is 8 (magánszemély);
    check digit = sum(d[i] * (i+1), i=1..9) mod 11 (10 → invalid)."""
    v = _digits(value)
    if len(v) != 10 or not v.isdigit() or v[0] != "8":
        return False
    weighted = sum(int(v[i]) * (i + 1) for i in range(9))
    check = weighted % 11
    if check == 10:  # ilyen adóazonosító nem kerül kiadásra
        return False
    return check == int(v[9])


def is_valid_taj(value: str) -> bool:
    """TAJ szám: 9 digits; odd positions ×3, even positions ×7 (1-indexed),
    sum mod 10 = 9. (check) digit."""
    v = _digits(value)
    if len(v) != 9 or not v.isdigit():
        return False
    total = sum(int(d) * (3 if i % 2 == 0 else 7) for i, d in enumerate(v[:8]))
    return total % 10 == int(v[8])


def _giro_block_ok(block: str) -> bool:
    """9-7-3-1 weighted sum of an 8- or 16-digit block must be ≡ 0 (mod 10)."""
    weights = [9, 7, 3, 1]
    total = sum(int(d) * weights[i % 4] for i, d in enumerate(block))
    return total % 10 == 0


def is_valid_bank_account(value: str) -> bool:
    """Magyar pénzforgalmi jelzőszám: 16 (2×8) vagy 24 (3×8) számjegy.

    Az első 8 jegy (hitelintézet + fiók) és a számlaszám rész (8 vagy 16 jegy)
    külön-külön 9-7-3-1 súlyozott mod-10 ellenőrzőösszegre nulla.
    """
    v = _digits(value)
    if len(v) not in (16, 24) or not v.isdigit():
        return False
    return _giro_block_ok(v[:8]) and _giro_block_ok(v[8:])


def normalize_identifier(value: str) -> str:
    """Canonical storage form: digits only."""
    return _digits(value)
