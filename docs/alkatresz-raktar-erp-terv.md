# Alkatrész-raktár (ERP-szintű készletezés) és munkaidő-mérés — terv

Hibajegy: 1d553d2e (2026-10-20, Szabó László). Státusz: **A–D fázis implementálva és élesben (2026-10-20/21).**

## Alapelvek (a jegy és a megrendelő kérése alapján)

1. **A jelenlegi raktárfunkciót nem érinti.** A mai „Raktár / Saját raktár / Átadások / Leltár” a
   termék- (kávé-) készletre marad, változatlan adatmodellel és API-val. Az alkatrész-készletezés
   **külön modul**, külön táblákkal, külön menüvel.
2. **Opcionális.** Licenc-kapcsoló a Flotta-pulton (`parts`), alapból kikapcsolva; az X-Presso saját
   példányán sem kötelező bekapcsolni („én továbbra sem szeretném nyilvántartani”).
3. **Más rendszerek mintájára** (Odoo Inventory, Dynamics BC, SAP B1 „bin location”): helykódos
   raktár (raktár → oszlop → sor), mozgásnapló, vételezés / kiadás / felhasználás, szerelői
   (autós) részraktár.

## 1. Adatmodell (új táblák, `parts_` előtaggal)

| Tábla | Mezők (lényeg) | Megjegyzés |
|---|---|---|
| `parts_warehouses` | id, name (szabad szöveg, pl. „Főraktár”), kind (`central` / `vehicle`), owner_user_id (autós raktárnál a szerelő), columns (szöveg-lista, pl. A–Z), rows (int, 1–N), is_active | A sorok száma admin által állítható, **dinamikusan nő** (a felső korlát módosítható) |
| `parts_locations` | id, warehouse_id, column (pl. „B”), row (pl. 12), code (generált: `FŐ-B-12`), note | Helykód; a raktár létrehozásakor nem generáljuk le mind, csak amit használnak (lazy) |
| `parts` | id, sku, name, manufacturer_code, unit (db/m/kg), min_stock, price_net, supplier, is_active | Cikktörzs |
| `parts_stock` | part_id, location_id, qty | Aktuális készlet helyenként (materializált; a mozgásokból újraszámolható) |
| `parts_movements` | id, part_id, qty (+/-), kind (`receipt` vételezés / `issue` kiadás / `use` felhasználás / `transfer` áthelyezés / `adjust` leltári korrekció), from_location_id, to_location_id, worksheet_id?, actor_user_id, note, created_at | Minden változás itt; **ebből** számolódik a készlet és a statisztika |

Kulcs-szabály: **a szerelő csak a saját (autós) raktárában lévő alkatrészt építheti be** — a munkalap
„felhasználás” tétele csak `vehicle` raktárból, csak `owner_user_id == szerelő` esetén engedett
(backend-ellenőrzés, nem csak UI).

## 2. Folyamatok

- **Vételezés (receipt):** szállítótól a központi raktár egy helykódjára; opcionálisan szállítólevél-szám.
- **Kiadás (issue / transfer):** központi → szerelő autós raktára (jóváhagyás ugyanúgy, mint a mai
  termék-átadásnál: kérés → elfogadás a szerelőnél).
- **Felhasználás (use):** a munkalapon (meglévő `worksheet`), új „Beépített alkatrészek” blokk:
  alkatrész + darabszám → azonnal `use` mozgás a szerelő raktárából; a munkalap PDF-jén és az
  árajánlaton tételként megjelenik (ár a cikktörzsből, felülírható).
- **Leltár:** helykódonként; a mai leltár-modul mintájára, de külön (nem keverjük a kávéleltárral).
- **Min. készlet riasztás:** Telegram-esemény (`parts.low`), mint a `stock.low`.

## 3. Munkaidő-mérés (a jegy második fele)

Külön, de ugyanebben a körben:

- `worksheet_timers`: worksheet_id, user_id, started_at, paused_at/resumed_at szegmensek (JSON-lista),
  closed_at, total_active_seconds (számított).
- A munkalap megnyitásakor **automatikusan indul** a számláló (a szerelő nem látja mint „óra”, csak
  egy diszkrét pont jelzi), **„Szünet” gomb** (kávé/cigi/mosdó) pauzál, „Folytatás” indít.
- Lezáráskor elmentjük: valós kezdés, valós zárás, aktív idő; a munkalapra írt munkaóra külön marad.
- **Statisztika (admin + manager):** szerelőnként nap/hét/hónap/szabad időszak: munkalapra írt órák vs.
  mért aktív idő, arány; munkalaponként is (a munkalap részletében: „írt 2,0 ó · mért 1 ó 12 p · 60 %”).
  Külső helyszíni karbantartásnál ugyanez (a karbantartási munkalapra is megy a timer) — a képviselő is.

## 4. UI

- Új menücsoport **„Alkatrészek”** (modulhoz kötve): Cikktörzs · Raktárak és helyek · Mozgások ·
  Szerelői raktárak · Leltár.
- Raktár-szerkesztő: név, oszlopok (chipek: A, B, C… hozzáadható), sorok száma (szám, bármikor
  növelhető), helykód-előnézet rács (oszlop × sor), kattintva a hely tartalma.
- Munkalap: „🔩 Beépített alkatrészek” blokk + a timer-sáv.

## 5. Ütemezés (javaslat)

1. **Fázis A (1 kör) — KÉSZ (2026-10-20, 5f6b8d0e2a7c):** cikktörzs + raktárak/helyek + vételezés/áthelyezés + mozgásnapló + modulkapcsoló.
2. **Fázis B (1 kör) — KÉSZ (2026-10-20, 6a7b9c0d1e2f):** szerelői raktár + munkalap-felhasználás + jóváhagyásos kiadás + min. készlet.
   - `GET /api/parts/my-warehouse` (saját autós raktár készlete, saját kérések, kérhető cikkek ár nélkül); `/sajat-alkatreszek` oldal.
   - `POST /api/parts/use` (task_id, part_id, quantity, price_net?): szerelőnek KIZÁRÓLAG a saját autós raktárból, saját feladatra (a warehouse_id-t a szerver eldobja); settlements-joggal bármely raktár/helykód. `use` mozgás − mennyiséggel, `price_net` pillanatkép a mozgáson (új oszlop), `unit_cost` = beszerzési ár. `DELETE /api/parts/use/{movement_id}` = storno (+ mozgás, document_no = eredeti id). A munkalap API-n `used_parts` (csak olvasható), a PDF-en anyag-sorként árral (sima ML-en emiatt ár-oszlop); külsős szervizesnek az árak lehúzva.
   - Kiadás-kérés: `parts_requests` tábla, `POST /api/parts/requests` → pending (Telegram `parts.request`), `…/issue` (settlements; helykódról `issue` mozgás − forrás / + autó, a készlet AZONNAL az autón), `…/decline`, `…/receive` (csak a kérő nyugtáz), `…/cancel` (csak a kérő, pending). Alkatrészek oldalon „Kérések” fül (nyitott darabszám a fülön, kiadás-modal helykód-előtöltéssel).
   - Min. készlet: `parts.low` Telegram-esemény, csak a küszöb ÁTLÉPÉSEKOR (céges összes < min, előtte ≥ min) — nem spamel.
   - A Feladataim munkalap-modalban „🔩 Beépített alkatrészek” blokk (SearchSelect az autó készletéből, azonnali könyvelés, 🗑 storno); a Feladatok ár-szerkesztőjében csak olvasható lista.
3. **Fázis C (1 kör) — KÉSZ (2026-10-21, 7b8c0d1e2f3a):** munkaidő-timer + szünet + statisztikák — külön `worktime` licenc-modul (Flotta-kapcsoló).
   - `worksheet_timers` (munkalap × szerelő): `sessions` JSON [{start, end, pauses:[{p, r}]}], számított active/pause_seconds, first/last.
   - `POST /api/worktime/{task_id}/start|pause|resume|stop` (idempotens; csak a saját feladatra), `GET /api/worktime/{task_id}` — a dolgozói válasz SZÁNDÉKOSAN csak állapot (running/paused/stopped) + kezdés + szünetek száma, idő-összeg nélkül („sunyi” mód).
   - A Feladataim munkalap-modal megnyitása indítja (idempotens), „☕ Szünet / ▶ Folytatás” gomb, a mentés és a ✕ zár. A vezetői `GET /api/tasks/{id}/worksheet` `timer` összegzést ad (Feladatok ár-szerkesztőben egy sorban: kezdés → zárás, mért aktív, szünetek, írt óra, arány).
   - `GET /api/worktime/stats/summary?from&to&employee_id` (agent_report): szerelőnként és munkalaponként írt óra vs. mért aktív idő, arány, szünet, idővonal-szegmensek; `/munkaido` oldal (nap/hét/hónap/időszak, KPI-k, két tábla, idővonal-sáv). Europe/Budapest nap-határok a timer első indulása szerint.
4. **Fázis D — KÉSZ (2026-10-21):** leltár alkatrészekre + export.
   - `GET /api/parts/stocktake/sheet?warehouse_id` (leltárív helykódonként), `POST /api/parts/stocktake` (csak az eltérő sorok → `adjust` mozgás, megjegyzés „leltár: …”), `POST /api/parts/my-stocktake` (a szerelő a saját autóján: „📋 Számoltam”, megjegyzés „leltár (szerelő)”).
   - `GET /api/parts/export.csv?what=stock|movements|parts` — Excel-barát CSV (UTF-8 BOM, pontosvessző, tizedes vessző). Alkatrészek oldalon „Leltár” fül + „⬇ Export” menü.

**Minden fázis kész — a 1d553d2e jegy lezárható.** Nyitott döntések lentebb (polcszint, külsős szervizes autós raktára).

## 6. Nyitott kérdések a megrendelőnek

- Helykód formátum: `RAKTÁR-OSZLOP-SOR` (pl. `FŐ-B-12`) jó? Kell-e polcszint (3. dimenzió)?
- Alkatrész-ár: nettó beszerzési + eladási, vagy csak eladási (ajánlatba)?
- A timer a dolgozóknak legyen látható (óra fut), vagy csak diszkrét jelzés? (A jegy „sunyi” módot kér —
  javaslat: a szerelő lássa, hogy fut, de a számokat csak a vezető.)
- A külső szervizcégek szerelőinek is legyen autós raktára (ők saját alkatrészt hoznak)?

## 7. Hiányelemzés más ERP-khez képest (2026-10-21)

Viszonyítás: Odoo Inventory, Dynamics 365 Business Central, SAP Business One; a timerhez Odoo Field Service, Dynamics Field Service, ServiceTitan.

**Készlet / raktár:** vonalkód-QR a helykódokra és cikkekre (szkennelés vételezésnél, áthelyezésnél, leltárnál — CameraScanner + qr_label.py újrahasznosítható); polcszint (3. dimenzió), zónák, betárolási szabály, kapacitás; sarzs / sorozatszám / lejárat + garancia példányonként; foglalás és szedőlista; úton lévő (in transit) készlet kiadás és átvétel között; raktáronkénti (autónkénti) min/max és feltöltési javaslat; karantén-hely, visszáru beszállítónak, garanciaigény; kit (szerviz-szett) és helyettesítő cikk; gép-kompatibilitási mátrix.

**Beszerzés / költség:** beszállítói törzs (ma szöveg) árlistával és szállítási idővel; beszerzési rendelés + vételezés rendelésre, hátralék; újrarendelési javaslat; FIFO / mozgó átlagár és készletérték-riport (ma csak utolsó beszerzési ár); csomagolási egységek átváltása.

**Leltár / riport / folyamat:** vak leltár, ABC ciklikus számolás, eltérés-jóváhagyás küszöb felett; korrekciós okkódok; riportok (fogyás gépenként / szerelőnként / cikkenként, forgás, halott készlet, öregedés, alkatrész-költség munkalaponként és ügyfelenként); gép-szintű beépítési történet felület (adat megvan: munkalap → gép → use-mozgás); raktáronkénti jogosultság, raktáros szerep; offline beépítés; könyvelési szinkron (Billingó tételek, készletérték).

**Timer vs. field-service:** időtípusok (utazás / várakozás / munka), GPS-alapú automatikus indítás; számlázható idő és bér-modul kapcsolat; utólagos korrekció és vezetői jóváhagyás.

**Javasolt sorrend (kávégép-szervizre méretezve):** 1) gép-szintű beépítési történet + sorozatszám + garancia; 2) vonalkód helykódra és cikkre; 3) autónkénti min/max + feltöltőlista; 4) beszállítói törzs + egyszerű rendelés + újrarendelési javaslat; 5) mozgó átlagár + készletérték-riport; 6) vak leltár okkódokkal; 7) offline beépítés a munkalap-várólista mintájára.
