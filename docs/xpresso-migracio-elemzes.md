# Xpresso → X-admin migrációs elemzés

**Forrás:** `xpresso-2026-10-05_19-15.sql` (MySQL 5.6 dump, `coffee_xpresso` adatbázis, 41 MB)
**Készült:** 2026-10-05
**Terjedelem:** 111 tábla, 532 341 sor

## Legfontosabb megállapítások

1. **A régi rendszer MA IS ÉL** — a mentésben aznapi (2026-10-05) adatok vannak.
   Az augusztus 9-i import óta keletkezett kb.: ~110 új elszámolás, ~205
   kassza-mozgás, ~520 jutalék-tétel, 15 szerződés-módosítás, 10 szerviz-munkalap,
   8 karbantartás, 2 gép-beszerzés és 4 új partner.
2. **A meglévő import-pipeline újrahasznosítható**: `backend/scripts/xpresso_import/`
   (stage1: dump→staging SQLite, stage2: staging→Iwfm). 20 táblát fed le,
   idempotens ("Xpresso-ID" jelölés a partner-notes-ban), 5 éves cutoff
   (2021-08-09), bruttó→nettó /1,27 konverzió.
3. **Két cég adatai vannak egyben**: X-Presso Coffee Kft. (`ceg_kod=xp`) és
   Premium Caffe Kft. (`ceg_kod=pc`) — a kassza- és mozgás-táblákban `ceg_id`
   választja szét. Az új rendszer egy-bérlős → döntés kell (lásd Nyitott kérdések).
4. **Adatvesztés-mentes stratégia = 3 réteg**:
   - **A réteg — élő entitások**: delta-merge az új rendszerbe (partner, gép,
     szerződés, új elszámolások, kassza-nyitóállapot).
   - **B réteg — előzmények, amiknek VAN új megfelelője**: historikus importként
     (elszámolás-történet, kassza-mozgások → AgentExpense, szerviz-munkalapok).
   - **C réteg — minden más**: teljes dump betöltése egy csak-olvasható
     **xpresso_archive** sémába (Postgres) — SEMMI nem vész el, az sem,
     amit nem képezünk le; később bármikor lekérdezhető.

---

## Tábla-térkép (mit → hová)

### 1. Már lefedett a meglévő importtal (delta-frissítés kell)

| Régi tábla | Sor | Új cél | Megjegyzés |
|---|---|---|---|
| `Partner_LISTA` | 1 699 | `Partner` | +4 új az aug. óta; `partner_fajta` 1=Partner / 2=**Ügyfél** — ez pont a 88. köri ügyfél-mód! Importnál érdemes jelölni (pl. notes/típus). |
| `Partner_ELSZAM_SZERZODESEK` | 4 366 | `PartnerContract` + `Asset` kapcsolat | 15 friss módosítás; `adag_ar/minimum/berleti_dij` mezők megvannak az új sémában. |
| `Term_ESZ_VK` (gépek) | 2 310 | `Asset` | +2 új gép; `szamlalo`, `norma`, `targyi`, hely-fajta (Polc/Szerviz/Partner/Külső raktár/Ügyfél/cseregép) lefedett. `utolso_vizkotelenites` + figyelmeztetés → Asset-be átvihető (vízkő-emlékeztető). |
| `Term_KV_KAVE` | 252 | `Product` (kávé) | adagár-mezők (7g/8g, cukorral/nélkül) a partner-árakban hasznosultak. |
| `Term_ALK_CIKKSZ` | 1 134 | `Product` (alkatrész) | min_darab → minimum-készlet jelzés. |
| `Term_GYARTOK` + `_TIPUSOK` | 119+432 | Asset márka/típus | lefedett. |
| `Elszamolas` + `_KAVEGEP` + `_TERMEKEK` | 22 372 + 36 850 + 37 324 | `Settlement` + `SettlementLine` | ~110 ÚJ elszámolás aug. 9. óta → delta-import. A korábbi 5 éves cutoff miatt a 2021 ELŐTTI elszámolások csak az archívumban lesznek (C réteg). |
| `Szerviz_MUNKALAP` + `_MUNKA` + `_ALKATRESZ` | 3 421 + 4 267 + 6 695 | `ServiceTicket` / munkalap | 10 friss; tartozék-pipák (zaccfiók, víztartály…) és `polchely_id` szövegesen a jegybe. |
| `User_LISTA` | 29 | referencia | ⚠️ `password`+`salt` SHA1 — **jelszót NEM migrálunk**, az új rendszer argon2id-s; a felhasználókat kézzel hozzuk létre. |

### 2. ÚJ megfelelő van a 86–88. körök óta (új import-lépés írandó)

| Régi tábla | Sor | Új cél | Leképezés |
|---|---|---|---|
| `Kassza_MOZGAS` (+`_TIPUSOK`) | 41 940 | `AgentExpense` + `CashTransfer` | Típusok: 1/5=Kiadás→`expense` (van `beszallito_id`+`szamla_srsz` → supplier/receipt_no!), 2/6=Bevétel→`deposit`, 3=Pénz elvétele→`withdrawal`, 4=Pénz átadása képviselőnek→`CashTransfer` (cel_user_id!), 7=Képviselői vásárlás, 8=Elszámolás képviselővel, 10=Utalás, 11=GLS. `fokassza`=központi kassza. Ezzel a kassza-statisztika idősora visszamenőleg is él (az új Teljes/Év/Hónap chipek értelmet kapnak). |
| `Kassza_ZARAS` / `Kassza_USER_ZARAS` / `Kassza_SZERVIZ_ZARAS` / `Kassza_JUTALEK_ZARAS` | 1 532 / 1 666 / 3 / 125 | — | Zárás-pillanatképek; az új rendszer dinamikusan számol → C réteg (archívum), de a LEGUTOLSÓ zárásból **nyitóegyenleg-ellenőrzés**: a migrált mozgások egyenlege egyezzen a záróösszeggel. |
| `Szerviz_KARBANTARTAS` + `_KAVEGEP` + `_DARALO` + `_VIZLAGYITO` | 161+160+11+12 | munkalap / feladat | Karbantartási jegyzőkönyvek mérésekkel (kazánnyomás, hőfokok, vízkeménység) és check-listákkal. 8 friss. Javaslat: feladat+munkalap `public_note`/belső megjegyzésbe strukturált szövegként; HOSSZÚ TÁVON: karbantartás-checklist modul (licenc-kapcsolóval). |
| `User_MUNKAK` | 2 967 | `TechLedger` jellegű | Munkaóra munkalaponként dolgozónként (2014–2026-08). Historikus → archívum + opcionálisan munkalap-szövegbe. |
| `Term_ESZ_BESZER` | 2 008 | `AssetMovement` / Asset notes | Gép beszerzés/eladás ár+garancia. 2 friss. Vételár → asset megjegyzés/beszerzési adat. |
| `Beszallitok` | 200 | beszállító-referencia | Az új kassza/költés `supplier` szabadszöveg — importból datalist/önkitöltés építhető. |

### 3. Nincs új megfelelője — DÖNTÉST igényel

| Régi tábla | Sor | Mi ez | Javaslat |
|---|---|---|---|
| `Kassza_JUTALEK` + `Term_JUTALEK_PARTNER` + jutalek_% mezők (partner/termék/munkafajta) | 77 843 + 3 308 | **Jutalék-rendszer**: eladásonként képviselői jutalék % és összeg | Ha kell jutalék-számítás az új rendszerben → új modul (licenc-kapcsolóval); ha nem → C réteg. A 77 ezer sor a statisztikához is értékes (ki mennyit értékesített). |
| `Mozgas_LISTA` + `Mozgas_RESZLETES` | 44 602 + 142 135 | Minden áru/érték-mozgás tételesen (eladások beszer+elad árral) | A LEGNAGYOBB adattömeg. Az új `/statisztika` (bevétel/költség fajtánként) visszamenőleg ebből táplálható → érdemes a stats-ba historikus forrásként bekötni vagy aggregálva importálni; teljes tartalom C rétegbe. |
| `Elszamolas_TERMEKEK_AR` | 37 682 | Elszámolás-tételek BESZERZÉSI ár-rétegei | Árrés-statisztikához arany; C réteg + később stats-bővítés. |
| `Partner_ELSZAM_SAVOK` + `Elszamolas_SABLON_*` | 15 + 12 | **Sávos adagárazás** (tól-ig-ár) | Az új rendszer nem tud sávos árat! Ellenőrizendő, van-e még élő sávos szerződés (Elszamolas_FAJTA 2='Sávos'); ha igen → funkció-rés. |
| `Leltar_LISTA` + `_TERMEKEK` + `_MENTES` | 318+2 652+1 807 | Leltár-történet (utolsó: 2026-03) | Az új rendszer leltár-korrekciós mozgásokat ír; a történet → C réteg. |
| `Raktar_BELSO` + `Raktar_KULSO` | 1 388+360 | **AKTUÁLIS készlet** (telephely + képviselői autók) | ⚠️ Az új rendszer élő készletét NEM szabad felülírni! Egyeztető riport kell: dump-készlet vs. új WarehouseStock, eltérések listázva, kézi döntéssel. |
| `Log_RIPORT` + `Log_KAVEGEPEK` | 34 961+4 012 | Rendszer-napló + gép-napló | Gép-napló (vonalkódhoz kötött események) értékes szerviz-előzmény → Asset-hez köthető audit-eseményként; a riport-log C rétegbe. |
| `Beallitasok_CEGEK` | 2 | Két cég (X-Presso / Premium Caffe) | **Döntés kell**: egy rendszerbe olvad (ceg jelölés notes-ban) vagy a Premium Caffe adatai külön példányba (Flotta!) mennek. |
| `Term_POLC` | 722 | Szerviz polchelyek | Munkalapokon szövegesen; polc-modul nincs (kicsi igény). |
| `Term_USER_HIANY` | 40 | Képviselői hiány-elszámolás (2013–14) | Csak archívum. |

### 4. Kihagyható / csak archívum

- `ws_*` (23 tábla): a régi OpenCart webshop maradványa (42 termék, kategóriák,
  adókulcsok) — az új rendszerhez nem kell; a gépfotók (`ws_product_image`,
  48 kép-útvonal) fájljai nincsenek a dumpban, csak útvonalak. → C réteg.
- `TORLES_*` (4 tábla): a régi rendszer "lomtára". → C réteg.
- `*_AR_TORLES`, `*_RAKTAR_TORLES/TEMP`: ár/készlet-réteg törlési naplók. → C réteg.
- `Uzenetek` (8 sor, 2016–2019), `sysdiagrams` (üres), `Raktar_TEMP`. → C réteg.
- `Beallitasok*` (ÁFA-kulcsok, prioritások, bevétel-típusok): kódtáblák,
  az új rendszerben beépítve léteznek. → C réteg.
- `User_JOGOK_2` (30 sor, 70+ jogosultság-oszlop): a régi jogmátrix — az új
  permission-mátrix kézzel már felépült; referencia-értékű. → C réteg.

---

## Javasolt végrehajtási terv

1. **C réteg először** (biztonsági háló): a teljes dump betöltése a prod Postgres
   `xpresso_archive` sémájába (mysql→postgres konverzióval vagy a staging SQLite
   frissítésével). Ezzel a "ne legyen adatvesztés" azonnal teljesül.
2. **Delta-import** a meglévő pipeline bővítésével: stage1 lefuttatása az új
   dumpra → stage2 delta-módban (Xpresso-ID egyeztetés: új partnerek/gépek/
   szerződések/elszámolások beszúrása, meglévők érintetlenül).
3. **Kassza-migráció** (új lépés): Kassza_MOZGAS → AgentExpense/CashTransfer
   a fenti típus-térképpel, user-megfeleltetéssel (User_LISTA ↔ új users kézi
   párosító táblával); végén egyenleg-egyeztetés az utolsó zárásokkal.
4. **Készlet-egyeztető riport** (nem ír, csak összevet): Raktar_* vs WarehouseStock.
5. **Szerviz-kiegészítés**: karbantartási jegyzőkönyvek + gép-napló → munkalap/
   audit szövegek.
6. **Cutover**: amíg a régi rendszer él, ismételhető delta-import (a dump dátuma
   paraméter); a végleges átállás napján utolsó mentés + utolsó delta + a régi
   rendszer csak-olvasásra.

## Nyitott kérdések (döntés a usertől)

1. **Két cég**: egy X-admin példányba (jelöléssel) vagy a Premium Caffe külön
   Flotta-példányba?
2. **Jutalék-modul**: kell-e az új rendszerbe (77 843 historikus tétel + %-konfig),
   vagy elég az archívum?
3. **Kassza-történet mélysége**: teljes (2012-től, 41 940 mozgás) vagy pl. 5 év
   (mint az elszámolásoknál) + nyitóegyenleg?
4. **Sávos adagárazás**: van-e még élő sávos szerződés? Ha igen, kell a funkció
   az új rendszerbe a migráció előtt.
5. **Mozgás/értékesítés-történet**: bekössük-e a /statisztika alá visszamenőleg,
   vagy archívumból lekérdezhető marad?
