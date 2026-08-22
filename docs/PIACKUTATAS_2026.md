# Iwfm — Összehasonlító piackutatás (2026. augusztus)

*Készült: 2026-08-22. Módszer: az Iwfm kódbázis funkcióleltára (v0.25 körüli
állapot) összevetve a piaci szereplők nyilvános anyagaival, árlistáival és
felhasználói értékeléseivel (Capterra/G2/Trustpilot/fórumok). A bizonytalan
adatok jelölve.*

Az Iwfm két piacon versenyez egyszerre, ezért a kutatás két részből áll:

1. **Kávégép-/vending-üzemeltetési szoftverek** (VMS / OCS management) — a
   partner-, gép-, készlet-, szerviz-, elszámolás-oldal versenytársai.
2. **Munkaerő-kezelő (WFM) szoftverek** — a beosztás-, jelenlét-, bérexport-
   oldal versenytársai.

---

## Az Iwfm jelenlegi képességei (viszonyítási alap)

A kódbázisból felmért, ténylegesen működő funkciók:

**Üzemeltetési oldal (OCS/vending):**
- Partner-CRM + tokenes, csak-olvasható **partnerportál**
- Gépnyilvántartás egyedi vonalkóddal, életciklussal (raktáron → kihelyezve →
  visszavéve), mozgás-előzménnyel; számlálónkénti norma
- **Szerviz:** hibajegyek + karbantartás-esedékesség a gép számlálója és
  normája alapján; külső szervizes munkalapok; gépenkénti **tudásbázis**
- **Gép-QR ügyféltámogatás:** matrica → publikus oldal → hibabejelentés /
  termékrendelés bejelentkezés nélkül
- **Készletlánc:** telephelyi és autó-raktárak, beszerzési rendelés →
  beérkeztetés → áthelyezés autóra → kiadás partnernek; bizományosi
  (konszignációs) készlet partnerenként; leltár; digitális szállítólevél
- **Elszámolás:** számláló-alapú partner-elszámolás, bizonylat-PDF,
  **Billingó-számlázás** (NAV-kompatibilis magyar számla)
- **Értékesítés:** publikus árajánlat-oldal → online szerződés-aláírás →
  automatikus partner-létrehozás; üzletkötő-hozzárendelés
- **Útvonal-optimalizálás:** magyar irányítószám-alapú, offline (GeoNames),
  Held–Karp optimalizálóval; Google Maps-link
- E-mail sablonok + **automatizálási szabályok** (Flow-szerű), SMTP
- **AI-asszisztens** chat, diktálás, kamerás számláló-leolvasás
- Multifunkciós **import/export** (Excel/CSV, oszlop-hozzárendeléssel)

**Munkaerő-oldal (WFM):**
- Dolgozói törzsadat a bérszámfejtéshez (adóazonosító, TAJ, bankszámla, bér —
  **Fernet-titkosítva, maszkolva, auditált felfedéssel**; checksum-validáció)
- Heti beosztás-rács, piszkozat → közzététel; **Mt.-megfelelőségi motor**
  (99.§, 104.§, 105–106.§, 97.§ (4)–(5) kódolva, §-hivatkozásokkal);
  elérhetőség-alapú beosztás-generálás magyar ünnepnapokkal; archiválás
- Távollét-kérelmek jóváhagyással; jelenlét (mobil be-/kijelentkezés, kézi
  korrekció auditálva); **kiosk-terminál** törzsszámos blokkoláshoz
- **Bérexport** CSV/XLSX bármely magyar bérprogramhoz
- Dolgozói önkiszolgálás mobilra optimalizálva; feladatkiosztás
  skill-követelménnyel

**Platform:** PWA (offline service worker), kétnyelvű (magyar/angol),
szerepkörök (admin/manager/employee + finomabb jogok), teljes auditnapló,
GDPR-szempontú titkosítás. **Nincs:** gép-telemetria (ütemezve, v0.29),
közúti távolság-mátrix (ütemezve, v0.26), natív mobilapp (tudatosan PWA).

---

# 1. rész — Kávégép-/vending-üzemeltetési szoftverek

## Szereplőnkénti összevetés

### Cantaloupe Seed (USA) — a piacvezető

Enterprise VMS a vending/micro market/OCS piacra, három szinten (Seed Live /
Cashless+ / Pro). Teljes vertikum: telemetria, kártyás fizetés (saját ePort
olvasók), dinamikus útvonal-ütemezés, pre-kitting, gépszintű fogyás-riasztás,
távoli árazás, sofőr-app.

- **Ár:** ajánlat-alapú; közösségi források szerint ~8–12 USD/gép/hó + ~6%
  tranzakciós díj + hardver előre.
- **Iwfm jobb:** nincs hardver-lock-in és tranzakciós díj; magyar nyelv és
  Billingó/NAV-számlázás; online szerződéskötés; WFM-modul; visszatérő
  Cantaloupe-panaszok (hibás számlázás, hosszú szerződések, gyenge support,
  elavult riportok) nálunk nem terhelnek.
- **Iwfm gyengébb:** nincs élő telemetria, nincs beépített kártyás fizetés,
  nincs dinamikus (valós idejű) útvonal-újratervezés; a riporting-mélység és
  a skálázottság (több száz gép) messze a Seed alatt.

### Nayax Core + MoMa (Izrael; magyar disztribútor: Handav Kft)

Fizetési terminál (VPOS Touch) + a hozzá járó menedzsment-felület. Telemetria
a fizetőeszközön keresztül, távoli árazás, riasztások, restock-előrejelzés,
planogram, 2025-től AI-réteg (asszisztens, planogram-javaslat).

- **Ár (US-referencia):** ~399 USD/eszköz + 7,95 USD/hó/eszköz + ~5,95%
  tranzakciós díj.
- **Iwfm jobb:** teljes üzletirányítás (szerződés, elszámolás, számlázás,
  raktárlánc, szerviz-munkalap) — a Nayax ebből szinte semmit nem ad;
  nincs eszközönkénti havidíj; magyar számlázás natívan.
- **Iwfm gyengébb:** fizetés-elfogadás és élő gépadat egyáltalán nincs — ahol
  a fogyasztó fizet (vending-irány), ott a Nayax megkerülhetetlen; a Handav
  révén magyar nyelvű support is jár hozzá. (A terv szerint ez tudatosan
  "parkolóban" van.)

### Vendon (Lettország, Azkoyen-csoport) — a kávé-specialista

IoT-telemetria kifejezetten kávégépekre/OCS-re (vBox gateway): valós idejű
monitoring, adag-/alapanyag-számlálás, **csészeszám-alapú megelőző
karbantartás** előrejelzéssel, alapanyag-alapú ügyfél-elszámolási adatok,
API ERP-integrációhoz. 90+ országban.

- **Ár:** nem publikus (hardver + havi felhő-díj).
- **Iwfm jobb:** a Vendon nem ERP — számlázás, szerződés, ajánlat, raktárlánc,
  útvonal, WFM nincs benne; az Iwfm-ben a karbantartás-esedékesség
  számláló+norma alapon már működik (helyszíni leolvasásból), és a teljes
  üzleti folyamatot lefedi.
- **Iwfm gyengébb:** a Vendon élő csészeszám-adata pontosabb és azonnali; a
  fogyás-előrejelzésünk önbevallásra/leolvasásra épül (v0.29 1. fokozat ezt
  csökkenti, de nem éri el az élő telemetriát).

### Televend (Intis, Horvátország) — a legközelebbi "teljes" versenytárs

350 000+ csatlakoztatott eszköz; telemetria (protokoll-univerzális Televend
Box) + felhő: monitoring, analitika, **szerződéskezelés + számlázás**,
**raktármodul** (telephelyek, járművek, alkatrészek), útvonal-tervezés,
sofőr-app, cashless. 9 nyelvű support, régiós (CEE) jelenlét.

- **Ár:** csak egyedi ajánlat.
- **Iwfm jobb:** átlátható működés hardver nélkül; magyar nyelv + Billingó/NAV
  (a Televend NAV-támogatása nem igazolt); online ajánlat→szerződés folyamat;
  gép-QR ügyféltámogatás; WFM + Mt.-megfelelés; kis üzemeltetőnek nem
  ajánlat-függő, azonnal bevezethető.
- **Iwfm gyengébb:** funkcionálisan a Televend a legerősebb átfedés —
  telemetriában, valós idejű analitikában, skálában és sofőr-app érettségben
  előrébb jár.

### VendSoft — az olcsó, hardver-független kihívó

Webes VMS kis/közepes üzemeltetőknek: készlet raktár/autó/gép szinten,
pick-listák, AI-útvonaloptimalizálás, mobil-app; telemetria csak külső
integrációval. **Ár: 1–2 USD/gép/hó (min. 19–49 USD/hó)** — a kategória
legjobb ár/érték aránya, jó értékelésekkel.

- **Iwfm jobb:** kávé/OCS-specifikus logika (számláló-alapú elszámolás,
  bizományosi készlet, szerviz-munkalap, szerződések, magyar számlázás) a
  VendSoftban nincs; nyelv és NAV-megfelelés hiányzik náluk; WFM nincs.
- **Iwfm gyengébb:** a VendSoft polírozott, sok éve piacon lévő termék
  publikus árazással és referenciákkal; DEX/telemetria-integrációi készen
  vannak.

### Gimme VMS (USA)

Mobil-first, AI-s VMS: sofőr-elszámoltatás valós időben, fotó-alapú
bizonylatolás, planogram, offline mód; Gimme Key Bluetooth DEX-olvasó.
**Ár: 2–3 USD/gép/hó**, hardver a díjban.

- **Iwfm jobb:** az Iwfm-ben is van fotó/bizonylat-PDF irány (v0.28-ban
  kötelezővé tehető fotó), plusz a teljes ERP-réteg; a Gimme iOS-függő és
  US/DEX-központú — az EU-s kávégépeken a DEX ritka.
- **Iwfm gyengébb:** a Gimme sofőr-élménye (nagy gombos, offline, fotós
  workflow) ma még előrébb jár a PWA-nknál — pont ezt célozza a v0.28.

### Moqa (getmoqa.com) — kávégép-szerviz specialista

Field service + CMMS kifejezetten kávégépes cégeknek: munkalapok teljes
eszköz-előzménnyel, megelőző karbantartás, **bérleti/szerződés-követés**,
alkatrész-készlet, diszpécser, technikus-app helyszíni számlázással.
Moduláris árazás, nem publikus.

- **Iwfm jobb:** a Moqa-nak nincs telemetria-terve, útvonal-logikája,
  elszámolás/feltöltés folyamata, magyar nyelve, NAV-számlázása; az Iwfm a
  szerviz mellett a teljes kereskedelmi kört is viszi (ajánlat → szerződés →
  elszámolás → számla).
- **Iwfm gyengébb:** SLA-kezelés, egyedi űrlapok, bérleti díj-követés
  kidolgozottabb; a szerviz-mélységben (PM-ütemezések változatossága) előrébb
  jár.

### Simple-Simon (Hollandia)

Általános digitális munkalap-app (kávégép-karbantartóknak is ajánlva):
diszpécser, munkalap → számla folyamat, GPS, holland könyvelő-integrációk.
**Ár: 39 €/felhasználó/hó-tól.**

- **Iwfm jobb:** nulla kávé/vending-domain-logika a Simonban (se számláló, se
  alapanyag, se útvonal-optimalizálás); felhasználónkénti árazása egy 10 fős
  csapatnál drágább, mint egy teljes Iwfm-üzemeltetés; magyar támogatás nincs.
- **Iwfm gyengébb:** a terepi munkalap-UX és a könyvelő-integrációk érettek;
  gyors bevezethetőség referenciákkal.

### További szereplők röviden

| Szereplő | Mi az | Iwfm-viszony |
|---|---|---|
| **Parlevel / 365 VMS** (USA) | Vending+micro market+OCS egyben, Lightspeed raktár-integráció | Erős multi-format lefedettség, de US-fókusz, EU-jelenlét gyenge, 365-ökoszisztéma lock-in |
| **Vendman/SmartVend** (Vianet, UK) | UK-piacvezető VMS + telemetria | UK-centrikus, ajánlat-alapú; HU-támogatás nincs |
| **VendingMetrics** (PL) | Budget telemetria+készlet | 25–99 USD/hó; angol UI, vékony funkciókészlet |
| **Telemetron** (EE) | Olcsó telemetria+cashless CEE-ben | Csak telemetria — az Iwfm üzleti rétegét nem fedi |
| **icoreon** (DE) | Vending-vertikum MS Dynamics 365 BC-n | A "VMS + valódi EU-számlázás" modell — de ERP-projektköltségen |
| **uSys SmartVend** (HU!) | Az egyetlen talált magyar vending-telemetria | Telemetria+dashboard+számlázás-kezelés; kis cég, a funkciómélység (útvonal, raktár, pre-kit) korlátozottnak tűnik *(bizonytalan)* |
| **Handav Kft** (HU) | Nayax-disztribútor, magyar support | Nem szoftvergyártó — a Nayax "lokalizált" csatornája |

## 1. rész — piaci szintézis

- **Tipikus alapfunkció-szint 2025–26-ban:** valós idejű telemetria +
  riasztások, távoli árazás, fogyás-előrejelzéses pre-kit, dinamikus útvonal,
  sofőr-app, cashless, KPI-riport. Az AI (planogram-javaslat, asszisztens,
  képfelismerés) a friss differenciáló front — **AI-asszisztense az Iwfm-nek
  már van**, ami a kategóriában a nagyokkal egy szinten van.
- **Tipikus árazás:** szoftver-only 1–3 USD/gép/hó; hardveres platformok
  ~8–12 USD/gép/hó + 300–400 USD/eszköz + tranzakciós %.
- **Piaci rések, amiket az Iwfm ténylegesen betölt:**
  1. **Senki nem ad integrált OCS-csomagot magyar (NAV/Billingó) számlázással**
     — a Vendonnak telemetria van, számlázás nincs; a Moqa/Simon szerviz+
     számla, telemetria nincs; a Televend a legközelebbi, de NAV-támogatása
     nem igazolt. *Ez az Iwfm legerősebb megkülönböztetője.*
  2. **A kávé/OCS-specifikus logika alulszolgált**: alapanyag-alapú
     elszámolás + csészeszám-alapú karbantartás + bérleti/szerződés-kezelés
     együtt sehol sincs — az Iwfm-ben (telemetria nélkül, de) mindhárom
     megvan.
  3. **Hardver-lock-in-fáradtság**: a Cantaloupe/Nayax számlázási panaszok
     kereslete a hardver-független, átlátható árazásnak — az Iwfm pontosan ez.
  4. **Magyar lokalizáció** gyakorlatilag üres piac (Handav-disztribúció + a
     kis uSys kivételével).
- **Ahol a piac egésze előrébb jár az Iwfm-nél:** élő telemetria (a v0.29
  fokozatos terve ezt címzi), fizetés-elfogadás (tudatosan parkolóban),
  valós közúti útvonal-mátrix (v0.26), dedikált sofőr-app-szintű terepi UX
  (v0.28), skálázottsági referenciák.

---

# 2. rész — Munkaerő-kezelő (WFM) szoftverek

## Nemzetközi szereplők

### Microsoft Shifts (Teams) — az "ingyenes alapszint"

Beosztás-rács a Teamsben, csere/kérelem, helyalapú blokkolás, Excel-export.
M365-előfizetésben benne van. **Nincs** szabály-motor, megfelelőség-ellenőrzés
és bér-integráció (a Microsoft maga is jelzi). Magyar UI: van.

- **Iwfm jobb:** Mt.-megfelelőségi motor, bérexport, kiosk, titkosított
  HR-törzsadat, auditnapló — a Shiftsben semmi ilyen nincs; az Iwfm
  beosztás-rácsa eleve a Shifts-mintát követi, így az átállás természetes.
- **Iwfm gyengébb:** a Shifts "ingyen van" minden M365-ös cégnek — ár ellen
  nehéz versenyezni; Teams-chat integráció.

### Deputy (AU) / When I Work (US) / Connecteam (IL)

Erős SMB-szoftverek 2,5–6 USD/fő/hó áron. Deputy: AI-beosztás, US Fair
Workweek / AU Fair Work megfelelés. Connecteam: **10 főig ingyenes**, GPS +
geofence blokkolás, űrlapok, chat — terepi csapatokra a legjobb nemzetközi
UX. Egyiknek sincs magyar nyelve, Mt.-szabálya, magyar bérprogram-exportja.

- **Iwfm jobb:** magyar nyelv, Mt.-validáció, magyar bérexport, magyar
  azonosító-checksumok, GDPR-titkosítás magyar adatkörre.
- **Iwfm gyengébb:** mobil-UX érettség (geofence, csere-piac, chat), AI-alapú
  automatikus beosztás-optimalizálás, integrációs ökoszisztéma, ár-horgony
  (Connecteam ingyenes sávja).

### Planday (DK/Xero), Papershift (DE), Skello (FR), Quinyx (SE), Sloneek (CZ)

Európai szereplők, 2–6 €/fő/hó (Skello: ~79–89 €/telephely/hó; Quinyx:
enterprise, 300+ fő + implementációs projekt). Megfelelőség-motorjaik a saját
joghatóságukra készültek (francia kollektív szerződések, német ArbZG, skandináv
megállapodások) — **egyik sem szállít beépített magyar Mt.-validációt**.
A Skello külön tanulság: bebizonyította, hogy a joghatóság-specifikus
megfelelőség-motor önmagában skálázható üzletet visz.

- **Iwfm jobb:** az Mt.-megfelelés terén mindegyiknél; kis magyar cégnek
  azonnal használható, magyarul.
- **Iwfm gyengébb:** kereslet-alapú AI-tervezés (Quinyx), e-aláírt jelenléti
  ív (Skello), automatikus képesítés-alapú kiosztás érettsége (Papershift) —
  bár az Iwfm elérhetőség-alapú generálása + skill-alapú feladatkiosztása már
  ebbe az irányba indult el.

## Magyar piac — akik ténylegesen léteznek

| Szereplő | Profil | Mt.-validáció | Ár |
|---|---|---|---|
| **NEXON (NEXONtime)** | Piacvezető, enterprise; NEXONbér-csatolás | **Van** (beépített) | Ajánlat-alapú, nehézsúlyú bevezetés |
| **Kulcs-Soft (Kulcs-Beosztás)** | Web-beosztás + jelenlét, natív Kulcs-Bér integráció | **Van** ("100% jogszabálykövető") | Nem publikus (Flow-előfizetés) |
| **OLM / OL Munkaidő** | 5000+ cég; egykattintásos Mt.-szabálysértés-szkennelés | **Van** — a legexplicitebb | **Átalánydíj:** KKV-csomag ~5 583 Ft+áfa/hó korlátlan létszámra; 25 főig ingyenes sáv |
| **JDolBer (Orgware)** | Moduláris HR/bér, közszféra-erős | Van | Ajánlat-alapú |
| **Beosztásom.hu, XL IDŐ, Logzi, Wieldy, Binarit, DVP SimpliTime** | KKV-eszközök különböző mélységben | Állítják | Vegyes/nem publikus |
| **TimeMoto, Bodet/Kelio** | Terminál-központú jelenlét (viszonteladós) | Nincs igazolva | Hardver + ajánlat |

Fontos tanulság az árazásról: a magyar KKV **alacsony átalánydíjhoz szokott**
(az OLM teljes céges csomagja ~14 €/hó), miközben a nemzetközi szereplők
fejenkénti díjat kérnek.

## 2. rész — piaci szintézis

- **Alapelvárás (mindenki tudja):** heti rács + sablonok, mobil önkiszolgálás
  cserével, mobil-GPS vagy kiosk blokkolás, szabadságkezelés, bérexport.
- **A kulcskérdés — beépített Mt.-validáció:** *kizárólag magyar fejlesztésű
  termékekben van* (OLM, NEXONtime, Kulcs-Beosztás, JDolBer, Beosztásom,
  XL IDŐ…), de a marketingjük általában csak annyit mond, "jogszabálykövető".
  **Egyetlen szereplő sem hirdet név szerinti, automatizált ellenőrzést a
  168 órás közlési / 96 órás módosítási szabályra** — az Iwfm §-hivatkozásos,
  blokkoló/megerősítő + auditált szabálymotorja ennél átláthatóbb és
  mélyebb. *(A versenytársak tényleges szabálymélysége bizonytalan —
  próbavásárlással ellenőrizendő.)*
- **Magyar piaci rések, ahol az Iwfm ül:**
  1. **UX-rés:** a magyar Mt.-képes eszközök (OLM, JDolBer, XL IDŐ) elavult
     felületűek a Deputy/Connecteam-osztályhoz képest; a modern nemzetköziek
     viszont magyarul/Mt.-ben/bérexportban üresek. *Senki nem tartja mindkét
     oldalt — az Iwfm modern stackkel + Mt.-motorral pont ezt fedi.*
  2. **Terepi dolgozó-rés:** a magyar szereplők iroda/terminál-központúak;
     mobil terepi blokkolás + kiosk együtt az Iwfm-ben adott.
  3. **Bérexport-semlegesség:** a magyar integrációk silósak (Kulcs↔Kulcs-Bér,
     NEXON↔NEXONbér); az Iwfm bérprogram-független CSV/XLSX exportja
     semleges — cserébe nem "mély" API-integráció (lásd gyengeségek).

---

# Összegzés — miben jobb és miben gyengébb az Iwfm?

## Ahol az Iwfm objektíven erősebb a piacnál

1. **Egyedülálló kombináció:** sehol a piacon nincs egyben OCS-üzletirányítás
   (gép + szerviz + készletlánc + elszámolás + számlázás) **és** WFM
   (Mt.-beosztás + jelenlét + bérexport). A versenytársak vagy az egyiket,
   vagy a másikat adják — egy magyar kávégép-üzemeltetőnek 3–4 külön
   szoftvert kellene összeraknia (pl. Televend + Billingó + OLM + Moqa)
   ugyanezért.
2. **Magyar megfelelés natívan:** Billingó/NAV-számlázás, magyar
   azonosító-checksumok, Mt.-szabálymotor §-hivatkozásokkal, magyar
   ünnepnapok, magyar nyelv. A nemzetközi VMS-ek és WFM-ek közül **senki**
   nem adja ezt.
3. **Hardver-függetlenség, tranzakciós díj nélkül:** a Cantaloupe/Nayax-modell
   (300–400 USD/eszköz + havi díj + 6% jutalék) ellen pont az az ellenérv,
   amit a felhasználói panaszok is mutatnak. Az Iwfm nulla hardverrel indul.
4. **Adatvédelem/audit-mélység:** Fernet-titkosított érzékeny mezők, maszkolt
   válaszok, auditált felfedés, teljes auditnapló — a KKV-kategóriában ez
   kivételes (a versenytársak GDPR-ígéretei általában tárolás-szintűek).
5. **Értékesítési lánc digitálisan:** publikus ajánlat → online aláírt
   szerződés → automatikus partner — a VMS-piacon ilyen self-service
   onboarding-folyamat nincs.
6. **Gép-QR ügyfélcsatorna:** bejelentkezés nélküli hibabejelentés +
   termékrendelés a matricáról — a nagyoknál is ritka, ügyfélélmény-előny.
7. **AI-asszisztens + automatizálások:** a 2025–26-os differenciáló fronton
   (Nayax MoMa AI, Gimme) az Iwfm már ott van, a magyar versenytársak nem.

## Ahol az Iwfm gyengébb (őszintén)

1. **Nincs élő telemetria** — a Vendon/Televend/Nayax valós idejű gép- és
   csészeadatot ad; az Iwfm leolvasásra/önbevallásra épül. *(A v0.29
   fokozatos terve — önbevallás → okoskonnektor → gyári API — jó válasz,
   de ma még hátrány.)*
2. **Nincs fizetés-elfogadás** — vending-irányú (fogyasztó fizet) üzletben
   a Nayax-szal nem versenyez. *(Tudatos döntés, de piaci korlát.)*
3. **Útvonal-tervezés légvonalban** — a valós közúti mátrix (v0.26) és a
   dinamikus napközbeni újratervezés (Seed-szint) még hiányzik.
4. **Terepi mobil-UX** — a dedikált sofőr-appok (Gimme, AveriGo) és a
   Connecteam-osztályú WFM-appok (geofence, csere-piac, push) előrébb
   járnak a PWA-nál. *(v0.28 célozza.)*
5. **Nincs mély bérprogram-API** — a CSV/XLSX export semleges, de a
   Kulcs-Bér/NEXONbér natív csatolások kényelmesebbek a könyvelőnek.
6. **Nincs referencia és piaci jelenlét** — a versenytársak ezres
   ügyfélbázisokkal, review-lábnyommal, support-szervezettel bírnak; az
   Iwfm egy üzemeltetőre szabott, skálázottsági bizonyíték nélkül.
7. **Egy-tenant fókusz:** SaaS-értékesítéshez (több üzemeltető kiszolgálása)
   multi-tenant réteg, önregisztráció, csomagolt onboarding kellene.

## Stratégiai következtetés

Az Iwfm nem "gyengébb VMS" és nem "gyengébb WFM" — hanem egy **más
kategória**: integrált, magyar-natív OCS-üzletirányítás, amelyet jelenleg
senki más nem szállít. A védhető pozíció a kutatás alapján:

> *Modern (Connecteam-szintű) terepi UX + név szerint hirdetett, auditált
> Mt.-szabálymotor + NAV/Billingó-natív elszámolás + hardver-független,
> átalánydíj-toleráns árazás — egy termékben.*

A fejlesztési terv (v0.26–v0.29) sorrendje a kutatás fényében helyes: a
közúti útvonal és a terepi UX zárja a leggyakoribb funkcióréseket hardver
nélkül, a telemetria-önbevallás pedig a Vendon-féle előnyt csökkenti
pénzégetés nélkül. Két kiegészítő javaslat a kutatásból:

1. **A megfelelőség legyen marketing:** a §-szintű Mt.-ellenőrzéseket
   (168 órás közlés, 96 órás módosítás, pihenőidők) érdemes név szerint,
   nyilvánosan hirdetni — ezt ma senki nem teszi, és azonnal hihető
   különbségtétel.
2. **Bérexport-semlegesség bizonyítása:** dokumentált, tesztelt
   exportsablonok a 2–3 vezető bérprogramhoz (Kulcs-Bér, Novitax, NEXONbér)
   — kis munka, és a silós magyar integrációkkal szemben eladható érv.

---

*Fő források: gyártói oldalak és árlisták (cantaloupe.com, nayax.com,
vendon.net, televend.com, vendsoft.com, gimmevending.com, getmoqa.com,
simple-simon.com, deputy.com, connecteam.com, planday.com, skello.io,
quinyx.com, nexon.hu, ks.hu / kulcsbeosztas.hu, olm.hu / olmunkaido.hu,
orgware.hu, beosztasom.hu, xl-ido.hu), értékelő oldalak (Capterra, GetApp,
G2, Trustpilot, Forbes Advisor), iparági sajtó (Vending Times, Vending
Market Watch, Planet Vending) és felhasználói fórumok (VENDiscuss, BBB).
A .hu-oldalak egy részénél csak kereső-kivonat volt elérhető; a megjelölt
bizonytalan adatok próbavásárlással ellenőrizendők.*

