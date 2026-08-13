# Iwfm fejlesztési terv — a piaci összehasonlítás hiányosságaiból

*Készült: 2026-08-13. Alap: az Iwfm ↔ piaci szoftverek (Cantaloupe, Nayax, Vendon,
Parlevel, Moqa, RouteStar stb.) összevetésében azonosított hiányosságok.*

A sorrend a megtérülés szerint van felállítva: előbb az, ami hardver nélkül,
tisztán szoftverből ad üzleti értéket; a hardverfüggő telemetria fokozatosan,
pilottal épül.

---

## v0.26 — Közúti útvonaltervezés (P1)

**Cél:** a mostani légvonal-alapú optimalizálás valós közúti távolságra és
menetidőre álljon át, és Budapesten belül is pontos legyen.

**Lépések:**
1. **Partner-geokódolás:** `partners.lat` / `partners.lng` oszlop (migráció).
   Mentéskor és egy egyszeri backfill-lel a teljes cím geokódolása (Nominatim —
   OSM), így a budapesti partnerek is háztömb-pontosak, nem kerület-középpontok.
   A meglévő irányítószám-fallback megmarad.
2. **Távolságmátrix közútból:** OSRM `table` szolgáltatás saját Railway
   szolgáltatásként, magyar OSM-kivonattal (offline, külső API-függés nélkül).
   Egyszerűbb alternatíva döntésre: OpenRouteService API (ingyenes kvóta) —
   kevesebb infra, cserébe külső függés. A Held–Karp optimalizáló változatlanul
   használható, csak a mátrix forrása cserélődik.
3. **Kezdőpont:** a körút induljon a telephelyről (beállítható cím), opcióként
   oda-vissza (zárt kör) vagy nyitott útvonal.
4. **Több napos körút-tervező:** az esedékes partnerek szétosztása napokra —
   földrajzi klaszterezés + napi megálló-limit; heti nézet a Körút oldalon,
   naponta egy Google Maps-linkkel.

**Kockázat:** OSRM-build memóriaigénye Railway-en; ha gond, ORS API-ra váltunk.
**Becslés:** 2–3 kör.

---

## v0.27 — Raktár → autó → partner készletlánc + beszerzés (P1)

**Cél:** ne csak a partnernél lévő készletet lássuk, hanem a teljes áruutat,
és a rendszer javasoljon beszerzést.

**Lépések:**
1. **Raktárak:** `warehouses` tábla (telephely / autó típus), `warehouse_stock`
   készletsorok, mozgások (bevét, áthelyezés raktár→autó, kiadás partnernek).
2. **Elszámolás-integráció:** partner-feltöltéskor a mennyiség az üzletkötő
   autójáról fogyjon; autó-feltöltés a telephelyi raktárból egy mozdulattal.
3. **Beszerzési rendelés:** szállító-törzs, rendelés → beérkeztetés folyamat,
   beszerzési ár rögzítése (a jövedelmezőség-modul már használja).
4. **Rendelési javaslat:** a meglévő fogyás-előrejelzésből minimum-készlet
   riasztás raktárszinten + javasolt rendelési mennyiség.
5. **Leltár:** raktári leltár-felvétel eltérés-kimutatással (a partner-leltár
   mintájára).

**Becslés:** 3 kör.

---

## v0.28 — Terepi mobil-élmény (P2)

**Cél:** a PWA érje el a dedikált sofőr-appok (AveriGo, Simple-Simon) szintjét.

**Lépések:**
1. Nagy gombos, lépésenkénti **elszámolás-varázsló** mobilra (gép → számláló →
   leltár → aláírás → kész), a meglévő diktálás és kamerás számláló-leolvasás
   beépítésével.
2. **Offline sor megerősítése:** több elszámolás sorban állása, szinkron-
   ütközés kezelés, látható "függőben" állapot.
3. **Push-értesítések** (web push): esedékes körút, alacsony készlet, új
   hibabejelentés.
4. Terepi **fotórögzítés** kötelezővé tehető szervizmunkánál (bizonyíték-PDF
   a Moqa mintájára — a bizonylat-PDF már megvan, fotó-melléklettel bővül).

**Becslés:** 2 kör.

---

## v0.29 — Telemetria fokozatosan, hardver-pilot (P3)

**Cél:** élő számláló-adat a helyszíni leolvasás helyett — de pénzégetés
nélkül, három fokozatban. Az 1. fokozat hardver nélkül a haszon nagy részét
behozza.

**Fokozatok:**
1. **Partner-önbevallás (hardver nélkül):** havi automata e-mail / portál-link,
   amin a partner beküldi a számláló-állást (fotóval). `machine_readings`
   tábla, a fogyás-becslés és az esedékesség-tervezés ebből frissül; anomália-
   riasztás (kiugró/csökkenő számláló).
2. **Okoskonnektor-pilot (olcsó hardver):** 2–3 kiemelt gépre Shelly Plug S —
   fogyasztási görbéből főzés-detektálás, becsült adagszám naponta. MQTT/HTTP
   beküldés egy új `POST /api/telemetry` végpontra (tokenes hitelesítés).
3. **Gyári telemetria:** ahol a gépflotta engedi (Jura/WMF/Franke cloud API),
   közvetlen integráció — csak a pilot tanulságai után.

**Kockázat:** a 2–3. fokozat hardver- és gyártófüggő; az 1. fokozat önállóan
is megáll. **Becslés:** 1. fokozat 1 kör; pilot külön döntés után.

---

## Parkolóban (tudatosan nem most)

- **Fizetés-elfogadás a gépben** (Nayax/SoftPay): csak akkor releváns, ha a
  fogyasztó fizet, nem a partner — vending-irányú terjeszkedésnél kerül elő.
- **Kettős könyvelés-export / bank-egyeztetés:** a Billingó-integráció fedi a
  számlázást; könyvelői export-igény esetén CSV/NAV-adatszolgáltatás vizsgálat.
- **Natív (bolti) mobilapp:** a PWA-t visszük a plafonig; natív csak akkor, ha
  konkrét platform-képesség hiányzik.

## Folyamatos (minden kör mellett)

- Éles monitorozás: health-check ütemezett riasztással, hibanapló-figyelés.
- Biztonsági önellenőrzés: jogosultság-mátrix teszt, titkosított mezők auditja.
- Adatbázis-mentés ellenőrzött visszaállítási próbával (negyedévente).

---

**Összefoglaló sorrend és ráfordítás:**

| Verzió | Téma | Prioritás | Becslés |
|---|---|---|---|
| v0.26 | Közúti útvonal + geokódolás + több napos tervező | P1 | 2–3 kör |
| v0.27 | Raktár/autó készletlánc + beszerzés | P1 | 3 kör |
| v0.28 | Terepi mobil-élmény (PWA-csúcsra) | P2 | 2 kör |
| v0.29 | Telemetria 1. fokozat (önbevallás) | P3 | 1 kör |
| pilot | Okoskonnektor / gyári telemetria | P3 | döntés után |
