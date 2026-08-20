"""Magyar munkaszüneti napok (piros betűs ünnepek) + az évente rendeletben
kihirdetett munkanap-áthelyezések (hosszú hétvégék).

A fix és mozgó (húsvéthoz kötött) ünnepek algoritmikusan számolódnak; a
munkanap-áthelyezések (áthelyezett PIHENŐNAP + a hozzá tartozó LEDOLGOZÓ
szombati munkanap) évente jelennek meg rendeletben — az új év kihirdetésekor
a REST_DAY_OVERRIDES / WORKED_SATURDAYS bővítendő.

Forrás 2026-ra: a 2026-os munkarend-rendelet (pihenőnap: jan. 2., aug. 21.,
dec. 24.; ledolgozó szombat: jan. 10., aug. 8., dec. 12.).
"""

from __future__ import annotations

from datetime import date, timedelta

# Évente kihirdetett áthelyezett pihenőnapok (ezekre NEM tervezünk műszakot).
REST_DAY_OVERRIDES: dict[int, set[date]] = {
    2026: {date(2026, 1, 2), date(2026, 8, 21), date(2026, 12, 24)},
}

# A ledolgozó szombati munkanapok (állami munkanap — nálunk automatikus
# szabadság-kiírás jár rá a beosztás-generáláskor).
WORKED_SATURDAYS: dict[int, set[date]] = {
    2026: {date(2026, 1, 10), date(2026, 8, 8), date(2026, 12, 12)},
}


def easter_sunday(year: int) -> date:
    """Húsvétvasárnap (Anonymous Gregorian / Meeus algoritmus)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    length = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * length) // 451
    month, day = divmod(h + length - 7 * m + 114, 31)
    return date(year, month, day + 1)


def public_holidays(year: int) -> set[date]:
    """Az adott év piros betűs ünnepei (Mt. 102.§)."""
    easter = easter_sunday(year)
    return {
        date(year, 1, 1),    # újév
        date(year, 3, 15),   # nemzeti ünnep
        easter - timedelta(days=2),   # nagypéntek
        easter + timedelta(days=1),   # húsvéthétfő
        date(year, 5, 1),    # munka ünnepe
        easter + timedelta(days=50),  # pünkösdhétfő
        date(year, 8, 20),   # államalapítás
        date(year, 10, 23),  # nemzeti ünnep
        date(year, 11, 1),   # mindenszentek
        date(year, 12, 25),  # karácsony
        date(year, 12, 26),  # karácsony másnapja
    }


def is_public_holiday(d: date) -> bool:
    return d in public_holidays(d.year)


def is_bridge_rest_day(d: date) -> bool:
    """Rendeletben áthelyezett pihenőnap (hosszú hétvége 'hídnapja')."""
    return d in REST_DAY_OVERRIDES.get(d.year, set())


def is_worked_saturday(d: date) -> bool:
    """Ledolgozó szombati munkanap (állami munkanap)."""
    return d in WORKED_SATURDAYS.get(d.year, set())


def is_non_working_day(d: date) -> bool:
    """Ünnep vagy áthelyezett pihenőnap — ezekre nem tervezünk műszakot."""
    return is_public_holiday(d) or is_bridge_rest_day(d)
