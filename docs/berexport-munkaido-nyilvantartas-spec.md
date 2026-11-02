# Bérexport: havi munkaidő-nyilvántartás (specifikáció a mintapéldány alapján)

**Szabály:** az Iwfm által készített munkaidő-nyilvántartás export **pontosan olyan legyen**, mint a
mintapéldány (olmunkaido.hu által generált „Munkaidő nyilvántartás”, X-Presso Coffee Kft., 2026. június,
2 dolgozó, 2 oldal). Ugyanaz a szerkezet, ugyanazok a címkék és rövidítések, ugyanaz a számítási logika,
hogy a könyvelőnek és a dolgozóknak ne kelljen új lapot megtanulniuk. A minta maga **nem kerül a repóba**
(valódi adóazonosítókat tartalmaz); a user asztalán van.

Készült: 2026-11-01. Forrás: a minta szövegrétege (pdftotext) és pozicionált szövegelemei (pypdf).

---

## 1. Mi ez a dokumentum és mi nem

- Ez a **Mt. 134. § szerinti munkaidő-nyilvántartás** nyomtatott, aláírható változata: dolgozónként egy
  oldal (hosszú hónapnál kettő), havi összesítővel és napi sorokkal.
- **Nem** bérprogram-importfájl (nem Nexon/Kulcs-Bér/Novitax formátum). A meglévő összesített CSV/XLSX
  bérexport (`payroll_export.py`) megmarad mellette, de a számait ugyanabból a számítási rétegből kell
  vennie, hogy a két export sose mondjon mást.

## 2. A minta fizikai paraméterei

| Tulajdonság | Érték a mintában | Nálunk |
|---|---|---|
| Lapméret | A4 álló (595,3 × 841,9 pt) | ugyanaz (reportlab `A4`) |
| Bal margó | 72 pt (25,4 mm) | 20–25 mm |
| Betűtípus | FreeSans / FreeSans Bold (TCPDF) | a `worksheet_pdf.FONT` / `FONT_BOLD` (DejaVu), ékezetes |
| Címsor | 12 pt | 12 pt félkövér |
| Cégnév | 11 pt, jobbra zárva a fejléc jobb oldalán | ugyanígy |
| Táblázat-szöveg | 7 pt | 7 pt |
| Napi sor magassága | 11,25 pt | ~11 pt, 31 nap + fejléc elfér egy oldalon |
| Oldal / dolgozó | 1 oldal (31 napos hónapnál is) | 1 oldal, dolgozónként új oldal (`PageBreak`) |
| Generátor | TCPDF | reportlab (már bent van: monthly_report, settlement_pdf, qr_label) |

## 3. Az oldal felépítése, felülről lefelé

### 3.1 Fejléc

```
Munkaidő nyilvántartás                                   (12 pt, bal)
2026-06-01 - 2026-06-30                                  (12 pt, bal; ISO dátum, kötőjel körül szóköz)
                                        X-Presso Coffee Kft.   (11 pt, jobb)
Jager Mátyás                       Jogviszonyok: 2018-05-17 -  (12 pt név; 7 pt jogviszony)
Adóazonosító jele: 8xxxxxxxxx                            (7 pt)
```

- **Név**: vezetéknév + keresztnév (magyar sorrend), ahogy az Employee-ben van.
- **Jogviszonyok**: `<belépés ISO> - <kilépés ISO vagy üres>`. Folyamatos jogviszonynál a kötőjel után
  nincs semmi. Több jogviszony esetén (kilépett, majd visszavett) vesszővel felsorolva; nálunk ma egy
  `hire_date` + `termination_date` van, ez elég az első körre.
- **Adóazonosító jele**: a titkosított mezőből exportkor fejtve (`decrypt_pii(emp.tax_id_encrypted)`),
  10 számjegy, szóköz nélkül. Ha hiányzik, a sor üresen marad, de a dolgozó akkor is kap lapot
  (ez különbség a CSV-hez képest, ahol a TAJ-nélkülieket kihagyjuk mint külsősöket; lásd 6.9).

### 3.2 Havi összesítő (három blokk egymás mellett)

A minta rajzolt rácsot használ; a blokkok sorrendje és címkéi **betűre pontosan**:

**„Hónap” blokk (Munkaidő)**

| Címke | Érték (minta, 1. dolgozó) | Számítás |
|---|---|---|
| Nyilvántartott idő | 176:00 | Σ napi NYI (ledolgozott + fizetett távollét órái) |
| Munkaidő | 176:00 | a havi keret: Munkanapok × napi óra |
| Pihenőnapok | 8 | a hónap heti pihenőnapjai (szo + v) + áthelyezett pihenőnapok |
| Havi órák | 176:00 | = Munkaidő (keret), külön sorban megismételve |
| Munkanapok | 22 | a hónap munkanapjai (hétköznap mínusz ünnep és áthelyezett pihenőnap, plusz ledolgozó szombat) |

2. dolgozó (2 nap szabadsággal): Nyilvántartott idő **176:00** (160 ledolgozott + 16 szabadság),
Munkaidő 176:00, Pihenőnapok 8, Havi órák 176:00, Munkanapok 22. Tehát a fizetett távollét **beleszámít**
a nyilvántartott időbe.

**„Bérpótlékok” blokk**

| Címke | Érték | Jelentés |
|---|---|---|
| RM 100% | 00:00 | rendkívüli munkaidő 100%-os pótlékkal (heti pihenőnapon / munkaszüneti napon végzett munka, Mt. 143. §) |
| RM 50% | 00:00 | rendkívüli munkaidő 50%-os pótlékkal (munkanapon a beosztáson túli óra) |
| VP 50% | 00:00 | vasárnapi pótlék 50% (Mt. 140. §, a rendes munkaidőben vasárnap dolgozott órákra, ahol jár) |

Formátum mindig `Ó:PP` két számjegyű órával legalább (`00:00`, `176:00`).

**„Távollétek” blokk** (oszlopok: kód, Nap, Havi órák)

| Kód | Nap | Havi órák | Jelentés | Iwfm típus |
|---|---|---|---|---|
| SZ | 0 / **2** | 00:00 / **16:00** | szabadság | `annual` |
| BSZ | 0 | 00:00 | betegszabadság (évi 15 munkanap, 70%) | `sick` (első 15 nap) |
| TVL | 0 | 00:00 | **tisztázandó**: valószínűleg (igazolt) távollét / táppénz | `other` vagy `sick` 15 nap felett |
| KN | 0 | *nincs óra oszlop* | **tisztázandó**: valószínűleg „kieső nap” / igazolatlan vagy fizetés nélküli | `unpaid` |

A KN sorban a mintában csak napszám van, óra nincs (tehát nem fizetett, nem számít a nyilvántartott időbe).

### 3.3 Napi táblázat

Fejléc két sorban, oszlopcsoportokkal:

```
Dátum  Nap | Beosztás            | Munkaidő            | RM    RM    VP
           | Kezdet  Vég   BI    | Kezdet  Vég   NYI   | 100%  50%   50%
```

| Oszlop | Minta | Szabály |
|---|---|---|
| Dátum | `01.jún` | `NN.` + magyar hónaprövidítés: jan, feb, márc, ápr, máj, jún, júl, aug, szept, okt, nov, dec |
| Nap | `h` `k` `sze` `cs` `p` `szo` `v` | magyar napnév-rövidítés, kisbetű |
| Beosztás Kezdet / Vég | `08:00` `16:20` | a publikált beosztás bruttó ideje (szünettel együtt) |
| BI | `08:00` | **beosztott idő** = (vég − kezdet) − szünet; 1. dolgozó: 8:20 − 0:20 = 8:00 |
| Munkaidő Kezdet / Vég | `08:00` `16:20` | a tényleges munkaidő; beosztás-alapú bérszámfejtésnél = beosztás, blokkolás-alapúnál a blokkolt be/ki |
| NYI | `08:00` | **nyilvántartott idő** = (vég − kezdet) − szünet; távollétnél a napi keretóra (8:00) |
| RM 100% / RM 50% / VP 50% | üres | csak ha van érték; `Ó:PP` |
| ÜP 100% / ÉP 15% / MP 30% | nincs a mintában (125. kör, könyvelői egyeztetés) | munkaszüneti napi, éjszakai, műszakpótlék órái; csak ha van érték |
| Távollét-nap | Munkaidő Kezdet helyén **`SZ`**, Vég üres, NYI `08:00` | a kód a Munkaidő-oszlopcsoport első cellájába kerül, a Beosztás-oszlopok a beosztott műszakot mutatják |
| Pihenőnap (szo, v) | csak dátum és nap, minden más üres | ha nincs beosztás és nincs blokkolás |
| Ünnepnap | nincs a mintában (2026. június ünnepmentes) | ugyanúgy üres, mint a pihenőnap; a Munkanapok számából kimarad |

Minden naptári nap sort kap, a hónap 1-jétől az utolsó napjáig, akkor is, ha üres. Sorok között üres
választóvonal (a minta dupla sorközzel rajzol).

### 3.4 Lábléc

```
Munkavállaló aláírása                 1. oldal                 2026-07-10
https://rendszer.olmunkaido.hu - Minden jog fenntartva. ©
```

- Bal: „Munkavállaló aláírása” felirat (aláírási vonal fölött/mellett).
- Közép: `N. oldal` (dolgozónként újraindul? a mintában a 2. dolgozó „2. oldal”, tehát **folyamatos**
  oldalszámozás az egész PDF-en belül).
- Jobb: a generálás dátuma ISO formában.
- Második sor: a forrás-rendszer. Nálunk: a példány saját X-admin-es domainje, pl.
  `https://ceg.x-admin.hu - X-admin` (user döntése, 6.8).
- A fejlécben a cégnév mellett a telephely is szerepel: „X-Presso Coffee Kft. · <telephely>” (6.8).

## 4. Számítási szabályok (levezetve a mintából)

1. **Napi keretóra** = `weekly_hours / 5` (40 → 8:00). Részmunkaidősnél 30 → 6:00.
2. **Munkanapok** = a hónap napjai, amelyek `not is_non_working_day(d)` (holidays.py: ünnep, áthelyezett
   pihenőnap, ledolgozó szombat kezelve). 2026. június: 22.
3. **Pihenőnapok** = hónap napjai − munkanapok (szo, v, ünnep, áthelyezett pihenőnap). 2026. június: 8.
   *Kérdés:* az ünnepnap a mintás rendszerben pihenőnapnak számít-e vagy külön sor (a mintában nem
   dönthető el). Első körre pihenőnapnak vesszük.
4. **Munkaidő (keret) = Havi órák** = Munkanapok × napi keretóra = 22 × 8 = 176:00.
5. **BI** (beosztott idő) naponta = beosztás hossza − `break_minutes`.
6. **NYI** (nyilvántartott idő) naponta:
   - `payroll_source = schedule`: = BI (a minta is pontosan a beosztást írja munkaidőnek);
   - `payroll_source = attendance`: = blokkolt (ki − be) − szünet, ahol a szünet = max(kézi `break_minutes`,
     a dolgozó/cég `break_rule` szerinti levonás, ha a szünet nem fizetett); a Kezdet/Vég a nyers blokkolás;
   - fizetett távollét napján (SZ, BSZ): = napi keretóra, a kód kiírva;
   - nem fizetett távollét (KN): 0, a kód kiírva, a Nap számláló nő.
7. **Nyilvántartott idő (havi)** = Σ NYI (tehát a fizetett távollét benne van: 160 + 16 = 176).
8. **Pótlékok** (a mintában mind 00:00, a szabályokat nekünk kell megírni, kapcsolhatóan):
   - RM 50%: munkanapon `max(NYI − BI, 0)` ha van beosztás; beosztás nélküli munkanapon dolgozott óra;
   - RM 100%: pihenőnapon vagy ünnepnapon dolgozott NYI;
   - VP 50%: vasárnap rendes munkaidőben dolgozott NYI (csak ha a dolgozónál „vasárnapi pótlék jár”
     kapcsoló be van; kereskedelemben jár, a többinél a beosztástól függ);
   - a mintában nincs: éjszakai 15% (22–06 h) és műszakpótlék 30%: ha a user kéri, külön oszlop,
     de az alapsablonban **nem** szerepel, mert a minta sem mutatja.
9. **Kerekítés**: percre pontos, `Ó:PP`. Nincs tizedes óra sehol a lapon (a CSV-ben van, az más).
10. **Hónap közbeni belépés/kilépés**: a jogviszony előtti/utáni napok üresek, a Munkanapok és a keret
    csak a jogviszonyon belüli munkanapokból számolódik.

## 5. Mezőleképezés: Iwfm → minta

| Minta mező | Iwfm forrás | Állapot |
|---|---|---|
| Cégnév | LicenseSettings / cégadatok (beállítások) | ✓ van |
| Időszak | paraméter (év, hónap) | ✓ |
| Név | `Employee.last_name + first_name` | ✓ |
| Jogviszonyok | `Employee.hire_date`, `termination_date` | ✓ |
| Adóazonosító | `Employee.tax_id_encrypted` (decrypt exportkor, audit) | ✓ |
| Napi keretóra | `Employee.weekly_hours / 5` | ✓ |
| Beosztás kezdet/vég/szünet | `Shift` (status=published) | ✓ |
| Tényleges be/ki | `TimeEntry.clock_in/out`, `break_minutes` | ✓ |
| Bérszámfejtés alapja | `Employee.payroll_source` | ✓ |
| Munkanap / pihenőnap / ünnep | `services/wfm/holidays.py` | ✓ |
| SZ, BSZ | `TimeOffRequest.type in (annual, sick)`, status=approved | ✓ (BSZ 15 nap feletti rész: hiányzik) |
| TVL, KN | `other`, `unpaid` | ⚠ leképezés tisztázandó |
| RM 100 / RM 50 / VP 50 | nincs | ✗ számítási réteg kell |
| Vasárnapi pótlék jár-e | nincs | ✗ Employee-mező (bool) vagy beállítás |
| Pihenőidő-szabály (perc / óránként / fizetett-e) | nincs (csak kézi `break_minutes`) | ✗ cégszintű alapérték + dolgozói felülírás |
| Telephely a fejlécben, csoportosítás | `Shift.location` / dolgozó telephelye | ⚠ dolgozó–telephely kapcsolat ellenőrizendő |
| Lábléc domain | példány `PUBLIC_URL` / beállítás | ✓ van |
| Oldalszám, generálás dátuma, aláírás | reportlab canvas | ✗ sablon kell |
| PDF dolgozónként egy oldal | nincs | ✗ új service: `timesheet_pdf.py` |

## 6. Kérdések és válaszok

**Megválaszolva (2026-11-01, a user továbbította):**

6. **Blokkolás-alapú dolgozók**: a Munkaidő Kezdet/Vég és az NYI **a nyers blokkolásból** megy, nem a
   beosztásra kerekítve. Emellett **pihenőidő-szabály** állítható be: a user ismerete szerint 4 óránként
   30 perc, ami **nem számít bele a munkaidőbe, ha a munkaszerződés így rögzíti**. Tervezés:
   - dolgozónként (vagy cégszinten alapértelmezés + dolgozói felülírás): `break_rule` = {`minutes`: 30,
     `per_hours`: 4, `paid`: false}; a napi NYI = blokkolt idő − (floor(blokkolt óra / per_hours) × minutes),
     ha `paid = false`; ha `paid = true`, nincs levonás, de a szünet a lapon látszik;
   - a kézzel blokkolt szünet (`TimeEntry.break_minutes`) és a szabály közül a nagyobb érvényes, nem adódik össze;
   - jogi megjegyzés: az Mt. 103. § minimuma 6 óra felett 20 perc, 9 óra felett további 25 perc; a 4 óránként
     30 perc ennél szigorúbb, tehát megengedett, de a sablonban a törvényi minimum legyen az alapérték,
     és a 4/30 a cég beállítása.
7. **Több telephely**: **egy fájl**, de telephelyenként **jól elkülönítve** (telephelyenként csoportosított
   dolgozók, a telephely neve a fejlécben a cégnév mellett, telephely-váltásnál új oldal). Legyen
   **választható opció**: „egy fájl, telephelyenként csoportosítva” / „telephelyenként külön fájl” /
   „egy adott telephely”.
8. **Lábléc**: a példány **saját X-admin-es domainje** (pl. `https://ceg.x-admin.hu - X-admin`); a
   **cégnév mindenképpen** szerepel, **a telephellyel együtt** (fejlécben: „X-Presso Coffee Kft. · Budapest, Pláza”).
9. **TAJ nélküli külsősök**: **úgy, mint eddig**, kimaradnak a nyilvántartásból (ugyanaz a szűrő, mint a CSV-nél).

**A könyvelő válaszai (2026-11-02) — a user kérésére ÁLTALÁNOSÍTVA beépítve (minden elem használható, kapcsolható):**

1. **TVL** = igazolt távollét, **KN** = kiküldetési nap (egyik sem távollét-kód a cégnél). Alap-leképezés mostantól:
   annual → SZ, sick → BSZ, other → TVL (igazolt távollét, óra-oszloppal), unpaid → **FN** (fizetés nélküli, óra nélkül).
   A KN-t (kiküldetés) a cég a Beállításokban saját kódként felveheti; a kódok szabadon átírhatók.
2. **Vasárnapi pótlék (50%)**: minden normál munkarendű dolgozónak jár, munkakörtől függetlenül; kivétel a
   munkaidőkeretben dolgozó, pihenőnapra is beosztható dolgozó. → céges alapértelmezés **BE**, a dolgozó-kartonon
   dolgozónként kikapcsolható (`Employee.sunday_bonus = False`). Migráció a meglévő sorokat is bekapcsolja.
3. **Éjszakai / műszakpótlék**: ha jár, külön jelölendő. → két új oszlop a lapon: **ÉP 15%** (22–06 óra közötti
   munka, ha > 1 óra, Mt. 142. §) és **MP 30%** (18–06 óra, a műszakpótlékra jogosult dolgozónál, Mt. 141. §;
   nála ÉP nem jár). Az éjszakai sáv a blokkolásból vagy a beosztásból számolódik.
4. **Betegszabadság → táppénz**: nem kell külön jelölni, a bérprogram és a könyvelő számolja. → csak BSZ, nincs TP kód.
5. **Ünnepnap**: nem sima pihenőnap, 100% pótlék jár, külön jelölendő; vasárnapra eső ünnepnél (pl. húsvétvasárnap)
   a vasárnapi 50% ÉS az ünnepi 100% is jár. → új **ÜP 100%** oszlop (munkaszüneti napi pótlék) minden ünnepnapi
   dolgozott órára, a beosztás nélküli / beosztáson felüli rész emellett RM 100% (rendkívüli); vasárnapon a VP 50%
   is. Az összesítőben külön sor: **Munkaszüneti napok** (nem a Pihenőnapok között). A húsvétvasárnap és a
   pünkösdvasárnap felkerült a munkaszüneti napok közé (Mt. 102. §).
6. **Blokkolás**: a valós be- és kilépés mutatása, túlmunka elszámolása → a blokkolás-alapú NYI a nyers
   blokkolásból megy, az RM 50% a beosztáson (vagy a napi kereten) felüli időre.
7. **Egy PDF a cégre, telephelyenként elkülönítve** → ez az alapértelmezés (`group_by_site`), a telephelyenkénti
   ZIP opció megmarad.

## 7. Megvalósítási terv

**Backend**
- `app/services/wfm/timesheet_calc.py`: napi sorok + havi összesítő számítása egy dolgozóra
  (tiszta függvény, bemenet: employee, shifts, entries, leaves, hónap; kimenet: dataclass). A meglévő
  `payroll_export.py` is erre áll át, hogy a két export ugyanazt mondja.
- `app/services/wfm/timesheet_pdf.py`: reportlab sablon a 3. pont szerint, dolgozónként `PageBreak`,
  folyamatos oldalszám, aláírás-sor, generálás dátuma.
- `GET /api/payroll/timesheet.pdf?year=2026&month=6[&employee_id=…]` – `require_perm("payroll")`,
  modul-kapu `payroll` (licenc-kapcsoló már létezik), `record_audit(action="payroll.timesheet")`,
  `Content-Disposition: munkaido_nyilvantartas_2026-06.pdf`.
- Beállítások (LicenseSettings / settings JSON): `sunday_bonus` (bool), `overtime_rules` (bool),
  `timesheet_codes` {TVL, KN} leképezése a 4 távollét-típusra.
- Alembic: Employee `sunday_bonus` bool, ha dolgozónként kell (6.2 válaszától függ); Employee
  `break_rule_json` (nullable, felülírás) + LicenseSettings `break_rule` alapérték.
- Végpont paraméterek: `year`, `month`, `employee_id?`, `site_id?`, `group=site|flat` (alap: `site`),
  `split=none|per_site` (alap: `none`; `per_site` esetén ZIP-ben telephelyenként egy PDF).

**Frontend**
- Jelenlét oldal (`app/jelenlet/page.tsx`) export menüje: „Munkaidő-nyilvántartás (PDF)” a CSV/XLSX mellé,
  hónapválasztóval, opcionálisan egy dolgozóra. i18n `att.timesheetPdf` hu/en.
- Dolgozó-kártyán: „Havi nyilvántartás letöltése” gomb (ugyanaz a végpont `employee_id`-val).

**Tesztek** (a mintából származtatott elvárások, 2026. június):
- 22 munkanap, 8 pihenőnap, keret 176:00 40 órás dolgozónál; 30 órásnál 132:00.
- 08:00–16:20, 20 perc szünet → BI 08:00, NYI 08:00.
- 2 nap jóváhagyott `annual` szabadság → SZ 2 / 16:00, Nyilvántartott idő 176:00, napi soron `SZ` + NYI 08:00.
- Pihenőnapon dolgozott 4:00 → RM 100% 04:00; munkanapon beosztáson túli 1:30 → RM 50% 01:30.
- Vasárnapi műszak `sunday_bonus=True` → VP 50% = NYI; `False` → 00:00.
- Hónap közbeni belépés (jún. 15.) → Munkanapok 12, keret 96:00, korábbi napok üresek.
- Blokkolás-alapú: 07:52–16:31 blokkolva, szabály 30 perc / 4 óra, nem fizetett → Kezdet 07:52, Vég 16:31,
  NYI 08:39 − 1:00 = 07:39; kézi 20 perc szünet mellett is 1:00 a levonás (max, nem összeg).
- Két telephely → egy PDF, telephelyenként új oldal, fejlécben „Cég · Telephely”; `split=per_site` → ZIP 2 PDF-fel.
- TAJ nélküli dolgozó nem kap oldalt; `payroll` modul kikapcsolva → 403 `license.module_off`.
- PDF: oldalszám = dolgozók száma, minden oldalon „Munkavállaló aláírása”, dátumformátum `01.jún`.

## 8. Elfogadási kritériumok (a mintával egymás mellé téve)

- [ ] Ugyanazok a szakaszok ugyanabban a sorrendben: fejléc → 3 összesítő blokk → napi tábla → lábléc.
- [ ] Címkék betűre egyeznek: Nyilvántartott idő, Munkaidő, Pihenőnapok, Havi órák, Munkanapok,
      RM 100%, RM 50%, VP 50%, SZ, BSZ, TVL, KN, Dátum, Nap, Beosztás, Kezdet, Vég, BI, NYI,
      Munkavállaló aláírása.
- [ ] Időformátum `Ó:PP`, dátum `NN.hónap`, napnév `h k sze cs p szo v`, időszak `ÉÉÉÉ-HH-NN - ÉÉÉÉ-HH-NN`.
- [ ] Minden naptári nap sort kap, pihenőnap üres.
- [ ] Távollét-nap: kód a Munkaidő-oszlopban, NYI = keretóra, Beosztás-oszlopok kitöltve.
- [ ] Szünet levonva a BI/NYI-ből, de a Kezdet/Vég bruttó.
- [ ] A 2026. júniusi mintás esetek (22 / 8 / 176:00 / SZ 2 16:00) a tesztben szám szerint kijönnek.
- [ ] Dolgozónként egy oldal, folyamatos oldalszám, generálás dátuma, aláírás-sor.
- [ ] Modul-kapcsoló, jogosultság (`payroll`), audit-bejegyzés, PII csak a PDF-ben, logban soha.

## 9. Adatvédelem

- Az adóazonosító csak a generált PDF-be kerül, válasz-logba, hibaüzenetbe, tesztkimenetbe soha.
- A mintapéldány (valódi adatokkal) nem kerül a repóba és nem kerül chatbe; a tesztek generált
  mintaadatot használnak (Demó-adat szabály).
- A PDF letöltése auditált (`payroll.timesheet`, ki, mikor, melyik hónap, hány dolgozó).

## 10. Jogszabályi alap (ellenőrizve: 2026-11-01, Jogtár, Mt. hatály 2026. X. 1-től)

| Szabály | Mt. § | Hatályos érték | Hol alkalmazzuk |
|---|---|---|---|
| Teljes napi munkaidő | 92. § | 8 óra (megállapodással max. 12, készenléti jellegű munkakör) | napi keretóra = weekly_hours / 5 |
| Vasárnapi rendes munkaidő esetei | 101. § | rendeltetése folytán vasárnap is működő munkáltató/munkakör, idényjellegű, megszakítás nélküli, több műszakos, készenléti jellegű | `sunday_bonus` jogosultság dolgozónként |
| Munkaközi szünet | 103. § | 6 óra felett 20 perc, 9 óra felett további 25 perc; 3–6 óra munkavégzés után kiadva | `break_rule` alapértéke; a cég 4 ó / 30 perc szabálya ennél szigorúbb, megengedett |
| Betegszabadság | 126. § | évi 15 munkanap, távolléti díj 70%-a | BSZ számláló évente; 15 felett → táppénz (TP/TVL, nyitott kérdés) |
| Munkaidő-nyilvántartás | 134. § | rendes + rendkívüli munkaidő, készenlét, szabadság; kezdő és befejező időpont naprakészen; beosztás hó végi igazolásával is vezethető | ez maga a PDF: Kezdet/Vég minden napra, aláírás a hó végén |
| Pótlék-alap | 139. § | egy órára jutó alapbér; havi bér / 174 | nem a lap dolga (bérprogram), de a CSV-ben óra-alap mehet |
| Vasárnapi pótlék | 140. § (1) | 50% | VP 50% oszlop, csak jogosultnak |
| Munkaszüneti napi pótlék | 140. § (2) | 100% (húsvét- és pünkösdvasárnap is) | RM 100% oszlop (a mintában nincs külön „ünnepnapi” oszlop, oda megy) |
| Műszakpótlék | 141. § | 30%, 18–06 óra között, ha a kezdés rendszeresen változik (munkanapok ⅓-ában, ≥4 óra eltérés) | nincs a mintán; nyitott kérdés 3 |
| Éjszakai pótlék | 142. § | 15%, ha az éjszakai munka > 1 óra; műszakpótlékos nem kapja | nincs a mintán; nyitott kérdés 3 |
| Rendkívüli munkaidő | 143. § | napi beosztáson felül 50% (vagy szabadidő); heti pihenőnapon 100% (50%, ha másik pihenőnap jár); munkaszüneti napon 100% | RM 50% / RM 100% oszlopok |
| Készenlét / ügyelet | 144. § | 20% / 40% (50%, ha nem mérhető) | nincs a mintán; csak ha a cég használ készenlétet |
| Pótlék az alapbérben | 145. § | a 140–142. § pótlékai alapbérbe építhetők, átalány is köthető | dolgozónként „pótlék az alapbérben” jelző → a lapon az órák látszanak, a bérprogram nem fizeti külön |

**Ellenőrzési rend:** a táblát minden év januárjában (és az Mt.-módosítások kihirdetésekor) újra kell
nézni a Jogtár/NJT hatályos szövegén; az ellenőrzés dátuma ide kerül. A munkarend-rendelet (áthelyezett
pihenőnapok, ledolgozó szombatok) évente a `holidays.py`-ba. A kódban a számok nem lehetnek beégetve:
egy `labor_rules.py` konstans-tábla + hatály-dátum, hogy egy módosítás egy helyen legyen átvezethető.

## 11. Szabálymotor: a jogszabályi értékek frissítése és alkalmazása (jóváhagyva 2026-11-01)

**Meglévő építőkövek:** `calendar_watch.ensure_next_year_calendar` (AI-tól kért munkarend, admin-jóváhagyással),
`calendar_watch.monthly_mt_check` (havi Mt.-változás jelzés), Flotta központi újraépítés (egy commit → minden
példány), Flotta → példány push-minta (`push_roadmap`).

### 11.1 Verziózott szabálykészlet: `app/services/wfm/labor_rules.py`

```python
@dataclass(frozen=True)
class LaborRules:
    valid_from: date                 # hatály kezdete
    source: str                      # pl. "Mt. 2012. évi I. tv., Jogtár, ellenőrizve 2026-11-01"
    daily_full_hours: int = 8        # 92. §
    break_steps: tuple = ((6*60, 20), (9*60, 25))   # 103. §: (napi perc felett, +perc)
    break_paid_default: bool = False
    sick_leave_days: int = 15        # 126. §
    sick_leave_pct: int = 70
    sunday_pct: int = 50             # 140. § (1)
    holiday_pct: int = 100           # 140. § (2)
    shift_pct: int = 30              # 141. §
    night_pct: int = 15              # 142. §
    night_min_minutes: int = 60
    overtime_pct: int = 50           # 143. § (1)
    overtime_restday_pct: int = 100  # 143. § (2)
    overtime_restday_alt_pct: int = 50
    standby_pct: int = 20            # 144. §
    oncall_pct: int = 40
    monthly_divisor: int = 174       # 139. §

RULESETS: list[LaborRules] = [LaborRules(valid_from=date(2026, 10, 1), source="…")]

def rules_for(day: date, overrides: dict | None = None) -> LaborRules:
    """A napra érvényes készlet (a legkésőbbi valid_from ≤ day), a DB-felülírással egyesítve."""
```

- Új Mt.-módosításnál **új elem** kerül a listába új `valid_from`-mal; a régi sosem módosul.
- A kalkuláció minden naphoz `rules_for(day)`-t hív, így egy hónap közbeni hatályba lépés is helyes.
- Teszt: két készlet, hatály-határon átnyúló hónap → a két fél külön értékkel számol.

### 11.2 Felülírás a Flotta-pultból (újraépítés nélkül)

- Flotta `FlottaSettings.labor_rules_json` + pult fül „⚖️ Jogszabályi paraméterek”: űrlap a fenti mezőkkel,
  `valid_from` + forrás-megjegyzés, „Kiküldés minden példányra” gomb (a `push_roadmap` mintájára:
  `POST /api/operator/labor-rules` a példányon, operator-token).
- Példány: `LicenseSettings.labor_rules_override` (JSON, lista hatály-dátummal). `rules_for()` a DB-listát
  a kód-listával egyesíti, azonos `valid_from`-nál a DB nyer.
- Példány-admin **nem** szerkesztheti a törvényi értékeket (csak a céges szigorításokat, 11.3); a
  Beállításokban csak olvasható „Hatályos szabálykészlet: 2026-10-01 (forrás: …)” doboz látszik.
- Audit a példányon: `labor_rules.pushed` (ki, mikor, melyik hatály).

### 11.3 Rétegezés az alkalmazáskor: törvény → cég → dolgozó

| Réteg | Hol | Mit állíthat |
|---|---|---|
| Törvény | `labor_rules.py` + pult-felülírás | minden százalék, szünet-lépcső, BSZ-napok |
| Cég | `LicenseSettings.work_rules` (Beállítások → Munkaügy) | szünet-szabály **csak szigorítva** (pl. 4 ó / 30 perc), szünet fizetett-e, vasárnapi pótlék alapból jár-e, telephely-csoportosítás alapértéke |
| Dolgozó | `Employee` mezők | `sunday_bonus` (bool/null = céges alap), `shift_bonus` (bool), `bonus_in_base_salary` (bool, 145. §), `break_rule_json` (null = céges), `payroll_source` (van) |

Validáció: a céges szünet-szabály nem lehet enyhébb a törvényinél (a mentés 422-t ad `work_rules.below_law`
kóddal); a dolgozói felülírás nem lehet enyhébb a cégesnél.

### 11.4 Egy változás útja

1. Jelzés: `monthly_mt_check` értesítés, vagy a januári kötelező kör (10. pont).
2. Ellenőrzés: a hatályos szöveg a Jogtáron/NJT-n, paragrafus-listával; az eredmény a 10. pont táblájába.
3. Átvezetés: új `LaborRules` elem commitolva (központi újraépítés), sürgős esetben a pultból kiküldve.
4. Alkalmazás: az érintett hónaptól `rules_for()` az új értéket adja; a korábbi hónapok újragenerálva is
   a régi értékkel készülnek.
5. Bizonyíték: a PDF lábléce tartalmazza a használt szabálykészlet hatály-dátumát
   („Mt. szabálykészlet: 2026-10-01”), az audit-bejegyzés (`payroll.timesheet`) ugyanezt.

### 11.5 Ami szándékosan nem automatikus

Az AI és a figyelők csak **jeleznek**; a szabály-táblába ember ír, a munkarend-naptárat admin hagyja jóvá.
Jogszabály-értelmezést a rendszer nem végez.

### 11.6 Tesztek a szabálymotorhoz

- `rules_for(2026-06-15)` → a 2026-10-01-es készlet **nem** érvényes, az azt megelőző igen (vagy a kód
  alapkészlete `valid_from=2012-07-01`-gyel, a mostani értékekkel, ha nem volt közbeni változás).
- Pult-felülírás `sunday_pct=60, valid_from=2027-01-01` → 2026-ban 50, 2027-ben 60.
- Céges szünet 4 ó / 30 perc, nem fizetett: 8:20-as nap → 1:00 levonás; céges 10 perc / 6 óra → 422.
- `bonus_in_base_salary=True` → az RM/VP oszlopokban az órák látszanak, a CSV „pótlék az alapbérben” oszlopa igen.

## 12. Megvalósítás állapota (117. kör, 2026-11-01)

**Kész (a könyvelő válaszától független részek):**

- `app/services/wfm/labor_rules.py` — verziózott `LaborRules`, `RULESETS`, `rules_for(day, overrides)`,
  `ruleset_from_dict`, `merged_rulesets`; a törvényi minimum-szünet `law_break_minutes()`.
- `app/services/wfm/timesheet_calc.py` — `build_month_sheet()` (napi sorok + havi összesítő), `BreakRule`,
  `CompanyWorkRules`, `DEFAULT_ABSENCE_CODES` (annual→SZ, sick→BSZ, other→TVL, unpaid→KN; a TVL/KN
  leképezés a Beállításokban átírható, amíg a könyvelő nem válaszol), `fmt_hm`.
- `app/services/wfm/timesheet_pdf.py` — reportlab sablon a 3. pont szerint (A4 álló, dolgozónként 1 oldal,
  folyamatos oldalszám, aláírás-sor, forrás-sor a példány domainjével, „Mt. szabálykészlet: <hatály>”).
- `GET /api/payroll/timesheet.pdf?year&month[&employee_id][&site][&split=none|per_site]` —
  `require_perm("payroll")`, `payroll` modul-kapu, audit `payroll.timesheet`; `per_site` → ZIP.
- Modell + migráció `d0e1f2a3b4c5`: Employee `site`, `sunday_bonus`, `shift_bonus`, `bonus_in_base_salary`,
  `break_rule`; LicenseSettings `labor_rules_override`; új `work_rules_settings` tábla.
- `GET/PUT /api/settings/work-rules` (admin): céges szünet-szabály (csak szigorítás, különben 422
  `work_rules.below_law`), vasárnapi pótlék alapértelmezés, telephely-csoportosítás, távollét-kódok; a válaszban
  csak olvasható `law` (hatályos készlet), `law_versions`, `law_override_count`.
- `PUT /api/operator/labor-rules` — a Flotta felülírás-listájának fogadása, audit `labor_rules.pushed`.
- Frontend: Jelenlét-oldal „📄 Nyilvántartás PDF” (hónap + egy fájl / telephelyenként ZIP); dolgozó-karton:
  telephely, vasárnapi pótlék (céges alap / jár / nem jár), műszakpótlék, pótlék az alapbérben, egyedi
  szünet-szabály; Beállítások → Csapat → ⚖️ Munkaidő-szabályok fül (modul: attendance|payroll).
- Flotta-pult: Beállítások → „⚖️ Jogszabályi paraméterek (Mt.)” — verziók hatály-dátummal, mentés,
  „Kiküldés minden példányra”; új X-admin példány létrehozásakor automatikus kiküldés.
- Tesztek: `tests/test_round117_timesheet.py` (17), Flotta `test_labor_rules_store_and_push`.

**Várja a könyvelő válaszát (nyitott kérdések 1–5):** TVL/KN végleges jelentése (most: other→TVL óra-oszloppal,
unpaid→KN óra nélkül), vasárnapi pótlék alapértelmezése (most: nem jár, dolgozónként kapcsolható),
éjszakai/műszak oszlop (most: nincs a lapon, a jogosultság-jelző már tárolódik), táppénz-kód a 15. nap után
(most: nincs átmenet), ünnepnap (most: pihenőnapnak számít, a rajta végzett munka RM 100%).

**Később:** a CSV/XLSX bérexport átállítása ugyanerre a kalkulációs rétegre (most még a régi összesítő logika).

**125. kör (2026-11-02):** a könyvelő válaszai általánosítva beépítve (6. pont): FN kód, vasárnapi pótlék alapból,
ÜP/ÉP/MP oszlopok, Munkaszüneti napok sor, húsvét- és pünkösdvasárnap ünnepként. Nyitott kérdés nem maradt.
