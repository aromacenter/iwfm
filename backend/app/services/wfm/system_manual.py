"""Rendszer-kézikönyv (X-admin / Iwfm): a felület működésének és folyamatainak
részletes leírása — az AI-asszisztens ebben keres (how_to eszköz), a
/kezikonyv oldal megjeleníti, és később az onboarding alapja.

Szekciók: key, title, keywords (kereső-szavak), body (markdown-szerű szöveg:
'##' alcím, '1.' lépések, '-' felsorolás). A szöveg magyar; a felület a
felhasználó nyelvén foglalja össze."""
from __future__ import annotations

import re
import unicodedata

SECTIONS: list[dict] = [
    {
        "key": "attekintes",
        "title": "Áttekintés — mi micsoda a rendszerben",
        "keywords": "bevezetés kezdés mi ez a program menü navigáció modulok licenc szerepkörök onboarding első lépések",
        "body": """## Mi ez?
Az X-admin (Iwfm) a kávégép-üzemeltetés és a munkaerő-kezelés egy rendszerben: partnerek és gépek, bizományi elszámolás és számlázás, szerviz (jegyek, átvétel, munkalap, átadás), saját raktár és alkatrész-raktár, kassza, beosztás/jelenlét/távollét, bérexport, vending-telemetria (a gépeken futó ügynök) és AI-asszisztens.

## Hogyan épül fel a menü?
- ÁTTEKINTÉS: Naptár (mindenki ezt látja belépés után), Vezérlőpult.
- MUNKAVÉGZÉS: Feladatok, Szerviz, Átvétel, Átadás, Szerelő-átadás, Saját raktár, Saját alkatrészeim, Tudásbázis, Beosztás, Jelenlét, Távollét.
- TÖRZSADATOK: Dolgozók, Partnerek, Szerződések, Ajánlatkérések, Gépek, Termékek, Raktár, Leltár.
- PÉNZÜGY: Elszámolás, Csomagok (GLS), Üzletkötő-elszámolás, Kassza, Statisztika, Munkaidő.
- ALKATRÉSZ: Alkatrészek, Beszerzés, Riportok.
- VENDING: Kávé-fizetés, Vending telemetria, Vending promóció.
- RENDSZER: Import/Export, Automatizálások, Hibajegyek, Napló, Beállítások.
- SZEMÉLYES: Beosztásom, Feladataim.
Amit nem látsz a menüben, ahhoz vagy nincs jogod (Beállítások → Jogosultságok), vagy a modul nincs bekapcsolva a licencben.

## Első lépések egy új cégnél
1. Beállítások → Cég-arculat (logó, cégnév, cím), Számlázó (Billingó vagy Számlázz.hu kulcs), E-mail (SMTP), Telegram.
2. Dolgozók felvétele szerepkörrel (admin, manager, üzletkötő, szervizes, dolgozó — vagy saját szerepkör).
3. Termékek (kávék, kellékek) és Partnerek (PT-kód, adószám, számlázó cég) felvitele; gépek felvétele és telepítése a partnerhez.
4. Raktárak: telephely + üzletkötőnként egy autó; kezdőkészlet bevételezése.
5. Szerződések a partnereknél (min. adag, árak, elszámolási ciklus, automata számlázás).
6. Jogosultságok finomhangolása, majd a hibajelentő gomb használata, ha valami nem úgy megy.""",
    },
    {
        "key": "jogosultsagok",
        "title": "Jogosultságok és szerepkörök (ki mit tehet)",
        "keywords": "jogosultság szerepkör admin manager üzletkötő szervizes dolgozó auditor saját szerepkör létrehozás megtekintés létrehozás módosítás törlés tiltás napló 403 nincs jogosultságod",
        "body": """## Alapelv
Minden funkcióhoz (Feladatok, Partnerek, Elszámolás, Raktár…) szerepkörönként állítható, hogy látható-e, és azon belül négy művelet külön kapcsolható: megtekintés, létrehozás, módosítás, törlés. Az admin mindig mindent elér, ő nem szűkíthető.

## Beépített szerepkörök
- Admin: minden, beleértve a beállításokat, jogosultságokat, naplót, törlést.
- Manager (vezetői szint): feladatok, beosztás, elszámolás, raktár létrehozása, kassza-áttekintés, betét/kivét.
- Üzletkötő: partnerek, gépek, elszámolás, saját raktár (autó), saját kassza.
- Szervizes (külsős): átvétel, tudásbázis, saját KSZ-munkalapok.
- Dolgozó: saját beosztás, saját feladatok.

## Saját szerepkör létrehozása (pl. auditor)
1. Beállítások → Jogosultságok → „+ Új szerepkör”.
2. Kulcs (pl. auditor), megjelenített név, leírás; szint: dolgozói vagy vezetői (a vezetői szint a kassza/raktár „csak vezető” szabályainál számít).
3. „Jogok másolása innen”: induljon egy meglévő szerepkör jogaival (pl. manager), vagy üresen.
4. A mátrixban pipáld ki a funkciókat; a ⚙ ikonnal nyisd meg a művelet-szintű kapcsolókat (pl. auditor: feladatok csak megtekintés).
5. Mentés. A dolgozó-űrlapon a szerepkör legördülőben megjelenik az új szerepkör.
6. Törölni csak akkor lehet, ha már senki nincs benne.

## Mi történik, ha valaki tiltott helyre téved?
A felület piros hibát mutat („Nincs jogosultságod: Feladatok · módosítás”), a kérés nem hajtódik végre, és a Napló oldalon „auth.denied” sor keletkezik: ki, mikor, melyik funkció, melyik művelet, melyik útvonal. Így az admin látja, ki próbálkozott.""",
    },
    {
        "key": "naptar",
        "title": "Naptár (feladatok ütemezése, időkapu)",
        "keywords": "naptár hónap idővonal határidő átütemezés indok időkapu partner mikor lehet menni kereső",
        "body": """## Mire való?
Belépés után mindenki a Naptárt látja: hónap-rács vagy idővonal nézet a feladatokkal. Vezetői joggal minden feladat, dolgozói nézetben a sajátok.

## Feladat átütemezése
1. Kattints a feladat-címkére (hónap-rácsban) vagy a nap részletezőjében a „Szerkeszt” gombra.
2. Új határidő — indok KÖTELEZŐ (legalább 3 karakter); a feladatra rákerül „régi → új dátum: indok”, a naplóba is.
3. Időkapu: mikor lehet a partnerhez menni (pl. „8–10 h”). „Az időkaput a partnerhez is mentem” pipával a következő feladatoknál is látszik.

## Keresés
A jobb felső keresőmező címre, partnerre, ügyfélre, gépre, dolgozóra, munkalap-számra és időkapura szűr.""",
    },
    {
        "key": "feladatok_munkalap",
        "title": "Feladatok és munkalap (vezetői oldal)",
        "keywords": "feladat munkalap kiosztás dolgozó határidő ML KSZ külső szerviz helyszíni ár-szerkesztés timer nyomtatás email lezárás AI javaslat",
        "body": """## Új feladat / munkalap kiállítása
1. Feladatok → „+ Új munkalap / feladat”.
2. Cím, leírás, szükséges skill (opcionális), dolgozó (az „AI javaslat” megmondja, kit érdemes), határidő, ügyfél, helyszín.
3. Gép kiválasztása (vagy új gép felvétele) — a munkalap a géphez kötődik, a gép történetében látszik.
4. Típus: sima munkalap (ML-sorszám), „Külső szerviznek átadott gép” (KSZ-sorszám, ajánlat-folyamat, a –1-es példány az ügyfélé), vagy „Helyszíni javítás” (egyszerűsített, nincs ajánlat).
5. Mentés: a munkalap sorszámot kap; a dolgozó a Feladataim alatt látja.

## Átvételből érkező munkalap
Az Átvétel után ez az űrlap automatikusan, előtöltve nyílik (gép, ügyfél, hibaleírás). Csak a szervizest és a határidőt kell választani; Mégse nincs — a munkalapot ki kell állítani.

## A munkalap kezelése
- Részletek: a dolgozó által kitöltött munkák, anyagok, beépített alkatrészek (sorozatszám, garancia), mért munkaidő (timer).
- Ár-szerkesztő: a mi áraink a tételekhez (a külsős szerviz az ügyfél-árainkat SOHA nem látja).
- Irodai nyomtatás (nyomtató-ügynökön át) vagy Email az ügyfélnek; Lezárás, ha kész.
- Ajánlat (KSZ): az ügyfél e-mailben kapja a konstrukciókat, a nyilvános linken elfogad vagy elutasít; belső elfogadás is lehetséges.""",
    },
    {
        "key": "feladataim",
        "title": "Feladataim (dolgozói munkalap, timer, alkatrészek)",
        "keywords": "feladataim saját feladat munkalap kitöltés timer munkaidő szünet utazás alkatrész beépítés szett offline ügyfél megjegyzés fotó",
        "body": """## Munkalap kitöltése a terepen
1. Feladataim → a feladat megnyitása: a timer automatikusan indul (munka / utazás / várakozás mód váltható, szünet gombbal).
2. Munkák: név + nettó költség; anyagok; „Beépített alkatrészek”: az autó készletéből választva, sorozatszámmal (garancia automatikusan a cikk szerint), vagy szett (kit) beépítése egyben.
3. Fotó csatolása, ügyfél-megjegyzés (a –1-es példányra kerül).
4. Mentés / lezárás: a timer megáll; a mért idő a vezetőnél látszik, és a Munkaidő modulban összegződik.
5. Ha nincs hálózat: az alkatrész-felhasználás sorba áll (offline), és újracsatlakozáskor beküldődik.

## Munkaidő-korrekció
Ha a timer rosszul mért (elfelejtett leállítás), a feladatnál „± korrekció” kérés írható indokkal; a vezető a Munkaidő oldalon hagyja jóvá.""",
    },
    {
        "key": "szerviz",
        "title": "Szerviz: hibajegyek és karbantartás",
        "keywords": "szerviz hibajegy jegy karbantartás ütemezett karbantartás partner gép hiba bejelentés telegram értesítés",
        "body": """## Hibajegy nyitása
1. Szerviz → „+ Új jegy” (vagy az AI-nak: „nyiss jegyet a Pláza Kávézó gépére, nem fűt”).
2. Partner / gép, hiba leírása, prioritás; a jegyből feladat készíthető a dolgozónak.
3. A jegy státusza: nyitott → folyamatban → lezárt; a gép történetében megmarad.

## Karbantartás
A gépeknél beállítható karbantartási ciklus; az esedékesek a Szerviz oldalon és a napi összefoglalóban jelennek meg. Telegram/e-mail értesítés a Beállítások → Értesítések alatt kapcsolható eseményenként.""",
    },
    {
        "key": "atvetel",
        "title": "Átvétel (gép behozatala, elismervény)",
        "keywords": "átvétel elismervény AT sorszám ügyfél gép behozott tartozékok hibaleírás cseregép fotó nyomtatás példány saját tárgyi gép munkalap automatikus",
        "body": """## Gép átvétele az ügyféltől
1. Átvétel → „+ Új átvétel”. Gép: vonalkód beolvasása / kiválasztás, vagy új gép felvétele (ügyfél tulajdona).
2. Ügyfél: meglévő partner (adatai automatikusan kitöltődnek), új partner teljes adattal, vagy szabad szöveg. Telefon és e-mail KÖTELEZŐ — kivéve saját tárgyi gépnél (nem az ügyfélé), ott nem kell.
3. Tartozékok, hibaleírás, cseregép vonalkódja (ha adunk), állapot-fotók.
4. Mentés: AT-ÉÉÉÉ-NNNN sorszámú elismervény. Nyomtatás az irodai nyomtatóra (ügynökön át) vagy helyi nyomtatási ablak; saját tárgyi gépnél 1, ügyfél gépénél 2 példány (egy az ügyfélé). A lap alján a 60 napos tárolási záradék kötelezően szerepel (szövege a Beállítások → Munkalap kártyán).
5. Ezután automatikusan a munkalap-űrlap nyílik előtöltve: szervizes és határidő választása, mentés (Mégse nincs).
6. Új gépnél a rendszer felajánlja a kávérendelős QR-címkét.""",
    },
    {
        "key": "atadas",
        "title": "Átadás (megjavított gép visszaadása, fizetés, számla)",
        "keywords": "átadás visszaadás fizetés készpénz kártya átutalás határidő számla billingó kedvezmény kassza saját tárgyi gép QR",
        "body": """## Folyamat
1. A szerelőtől elhozott (KSZ) vagy lezárt munkalapos gépek az Átadás várólistán jelennek meg a MI árainkkal (munka + karbantartási díj).
2. A sor megnyitása (QR-ről is: ?task=…): fizetési mód: készpénz / kártya / átutalás. Átutalásnál fizetési határidő, alapból 8 nap (átírható).
3. Kedvezmény pipa: NEM készül számla (és semmilyen megjegyzés nem kerül mellé).
4. Átadás gomb: számla készül a partner számlázó cégével (Billingó/Számlázz.hu), a gép „átadott” státuszba kerül, a feladat lezárul.
5. Kassza: csak a KÉSZPÉNZES átadás bruttó összege kerül az átadó kasszájába; kártya/átutalás nem; saját tárgyi gépnél mindig 0 Ft.""",
    },
    {
        "key": "szerelo_atadas",
        "title": "Szerelő-átadás (külső szerviztől elhozott gépek, kifizetés)",
        "keywords": "szerelő átadás külső szerviz KSZ elhozás kifizetés készpénz átutalás folyószámla szerelő díj értesítés ügyfél",
        "body": """## Folyamat
1. Szerelő-átadás: a külső szerviznél lévő, elkészült KSZ-munkalapok listája a szerelő díjával (a konstrukció költsége, duplán nem számol).
2. Kijelölés → „Elhozás”: az ügyfél e-mailt kap (a készülék átvehető), ha a pipa be van kapcsolva.
3. Kifizetett összeg: készpénz (a kitöltő kasszájából kimegy és a szerelő folyószámlájára könyvelődik) vagy átutalás (csak a folyószámlára, kassza-mozgás nélkül; a szerelő számlázza).
4. A gép az Átadás várólistára kerül az ügyfélnek.""",
    },
    {
        "key": "partnerek",
        "title": "Partnerek, szerződések, ajánlatkérések",
        "keywords": "partner PT-kód adószám számlázó cég xp pc X-Presso Premium Caffe kapcsolattartó e-mail szerződés minimum adag ár ciklus automata számlázás számla norma ajánlatkérés térkép geokódolás",
        "body": """## Partner felvétele
1. Partnerek → „+ Új partner”: név, PT-kód, adószám, cím, kapcsolattartó (telefon, e-mail — több e-mail vesszővel), számlázó cég (X-Presso vagy Premium Caffe — ez dönti el, melyik cég számláz és mi áll a bizonylat fejlécében).
2. Időkapu (mikor lehet menni) a feladat-ütemezéshez.
3. Térkép: a cím geokódolódik; a Statisztika térképen minden aktív partner látszik.

## Szerződés
Partner → Szerződések: érvényesség, minimum adag és ár alatta, elszámolási ciklus (hetek), fizetési határidő, alap kávé-termék, „számlára kerülő lefőzhető adag” opció, automata számlázás (mentés után azonnal számláz). A tükör a partner-soron az AKTÍV szerződést mutatja.

## Ajánlatkérés
A nyilvános űrlapról érkező ajánlatkérésből partner készíthető egy kattintással.""",
    },
    {
        "key": "gepek",
        "title": "Gépek (eszköz-nyilvántartás, telepítés, QR, címke)",
        "keywords": "gép vonalkód QR címke Godex nyomtatás telepítés partnerhez számláló norma több számláló tárgyi eszköz ügyfél gépe bérelt gép történet beépített alkatrész",
        "body": """## Gép felvétele
1. Gépek → „+ Új gép”: vonalkód (generálható), típus, gyártó, cikkszám, gyári szám, számlálók száma és normák (g/adag), alap termék, jelleg: saját tárgyi eszköz / ügyfél gépe / bérelt.
2. Mentés után a rendszer felajánlja a vonalkód-címkét a Godex nyomtatási sorba (az üzleti PC-n futó nyomtató-ügynök küldi ki).
3. Telepítés partnerhez: „Telepítés” → partner; innen a gép az elszámolásban számlálóval szerepel.

## Gép-történet
A gép soránál: szervizjegyek, munkalapok, beépített alkatrészek sorozatszámmal és garanciával, számláló-állások. A QR-címke a kávérendeléshez és az átadáshoz is használható.""",
    },
    {
        "key": "elszamolas",
        "title": "Elszámolás (bizományi készlet, számlálók, számla, bizonylat)",
        "keywords": "elszámolás leltár fogyás adag ár fizetési mód készpénz kártya átutalás utánvét határidő késedelmi felár tartozás aláírás bizonylat PDF 50 mm 80 mm nyugta nyomtatás számla billingó e-mail címzett feltöltés GLS offline azonnali eladás",
        "body": """## Elszámolás rögzítése egy partnernél
1. Elszámolás → partner választása (kereső). A partner külső raktárának készlete, gépei és az esedékesség látszik.
2. Tételek: a fizikai leltár (megmaradt mennyiség) beírása → a fogyás, adag és összeg automatikusan számolódik a szerződéses ár szerint. Gép-soros elszámolásnál a számláló-állás (több számlálós gépnél számlálónként).
3. Fizetési mód: készpénz / kártya / átutalás (határidő napokban) / utánvét. Késedelmi felár (30+ napos tartozásnál +10%) ki-kapcsolható indokkal.
4. „Számla címzettje”: ahol van szerződéses számla-norma vagy automata számlázás, itt átírható, hova menjen a számla (vesszővel több cím); üresen a partner e-mailje.
5. GLS-feladás pipa: mentés után a Csomagok oldal nyílik előtöltve (utánvétnél az összeggel).
6. Mentés: elszámolás ELSZ-sorszámmal; automata számlázásnál a számla azonnal elkészül.
7. Aláírás: a partner a képernyőn aláír (név kötelező), a bizonylat e-mailben megy (ha az automatikus bizonylat-küldés be van kapcsolva).
8. Nyomtatás: aláírás után kérdés — A4 / 50 mm / 80 mm nyugtanyomtató / most nem. Az előzményekben a bizonylat ikon ugyanezt kínálja.

## Fejléc, cég
A bizonylaton az a cég szerepel, amelyik a partnernél számlázó cégként be van állítva; a logó a Beállítások arculatából jön.

## Feltöltés (készlet a partnernek)
„Feltöltés” gomb: termék, mennyiség, beszerzési ár, forrás-raktár (az autó). GLS-sel is feladható.

## Azonnali eladás, offline
Azonnali eladás nem bizományi tételekre bruttó árral. Hálózat nélkül az elszámolás offline mentődik, és újracsatlakozáskor beküldődik.""",
    },
    {
        "key": "raktar",
        "title": "Raktár (telephely, autók, mozgások, láthatóság)",
        "keywords": "raktár telephely autó van bevételezés átadás autók között átvétel igazolás készlet minimum újrarendelés beszerzési rendelés láthatóság ki láthatja csak admin manager hozhat létre",
        "body": """## Raktárak
Telephely (site) és üzletkötőnként egy autó (van). Raktárat csak admin/manager hozhat létre, módosíthat, törölhet. Láthatóság: admin és manager mindent lát; mindenki más a saját autóját és azokat a raktárakat, amiket az admin a raktár szerkesztőjében („Ki láthatja még”) bepipált neki.

## Mozgások
1. Bevételezés: termék + mennyiség a telephelyre (vagy autóra).
2. Átadás autók/raktárak között: a fogadó félnek igazolnia kell (függő átadás → Elfogad / Elutasít); a feladó visszavonhatja.
3. Kiadás a partnernek: az Elszámolás „Feltöltés” gombja csökkenti az autó készletét.
4. Korrekció/leltár: a Leltár oldalon a számolt mennyiség rögzítése.

## Minimum készlet, beszerzés
Termékenként minimum állítható; a Raktár oldal újrarendelési javaslatot ad, ebből beszerzési rendelés készíthető (megrendelve → beérkeztetés).""",
    },
    {
        "key": "alkatreszek",
        "title": "Alkatrész-raktár (ERP: cikkek, mozgatás, munkalap, beszerzés, leltár)",
        "keywords": "alkatrész cikk cikkszám AUTO automatikus bevételezés mozgatás helykód szkennelés címke átlagár készletérték min max autó feltöltőlista kérés kiadás vételezés beszállító rendelés beérkeztetés visszáru szett kit leltár vak leltár ABC riport garancia sorozatszám szabad szavas",
        "body": """## Cikkek
Alkatrészek → cikk: cikkszám (üresen automatikus AUTO-0001), név, egység, min. készlet, ár/költség, garancia (hónap), kompatibilis típusok, helyettesítők, csomagolási egység. A bevételezés/mozgatás ablakban a cikk-mező szabad szavas: gépelésre szűr, ha nincs találat, „+ Új alkatrész” hozza létre azonnal.

## Mozgások
1. Bevételezés: raktár + helykód (A-1), mennyiség, egységköltség (mozgó átlagár frissül), bizonylatszám.
2. Áthelyezés raktárak/helyek között; autónkénti min/max → „Saját alkatrészeim” feltöltőlista; kérés a központnak (kiadás → átvétel).
3. Munkalapra: a Feladataim munkalapján a saját autó készletéből, sorozatszámmal; storno lehetséges.
4. Szkennelés: PL|raktár|hely (helykód-címke) és PP|cikkszám (cikk-címke) — a címkék PDF-ben nyomtathatók.

## Beszerzés
Beszállítók árlistával, újrarendelési javaslat → rendelés (vázlat → megrendelve → részben/teljesen beérkezett), visszáru, szettek (kit) beépítése egyben.

## Leltár és riportok
Leltár-munkamenet (vak leltár, okkód, jóváhagyás), ABC-besorolás, fogyás- és egészség-riportok, készletérték.""",
    },
    {
        "key": "kassza",
        "title": "Kassza (készpénz-kezelés, költség, átadás, leadás)",
        "keywords": "kassza készpénz költség rögzítése betét kivét pénz átadása leadása címzett képviselő manager admin egyenleg archívum központi áttekintés átadás elfogadása",
        "body": """## Saját kassza
Minden dolgozónak saját kasszája van: készpénz-bevétel (elszámolások, készpénzes átadás), költség (számla, beszállító, bizonylatszám), átvett/átadott pénz.

## Betét és kivét
Csak admin és manager (vezetői szint) rögzíthet; a képviselőnél a gombok sem látszanak.

## Pénz átadása / leadása
1. „Pénz átadása”: címzett (manager, másik képviselő vagy admin), összeg, megjegyzés. A címzettnek el kell fogadnia (függő átadás), elutasíthatja.
2. „Pénz leadása” az adminnak: az adminnál landolt pénz leadottnak számít (nem növeli az egyenleget).
3. Központi áttekintés (minden dolgozó egyenlege): csak admin/manager; archívum a kassza-újraindítás előtti időszakra.""",
    },
    {
        "key": "uzletkoto_elszamolas",
        "title": "Üzletkötő-elszámolás és statisztika",
        "keywords": "üzletkötő elszámolás jutalék forgalom partnerenként statisztika térkép fogyás anomália napi összefoglaló havi zárás riport",
        "body": """## Üzletkötő-elszámolás
Üzletkötőnként a rögzített elszámolások, forgalom és jutalék a kiválasztott időszakra; export.

## Statisztika
Térkép (minden aktív partner, gép nélkül szürke), fogyás-trendek, anomáliák; havi zárás riport cégenként (X-Presso / Premium Caffe) PDF-ben.

## Napi összefoglaló
E-mailben (Beállítások → Értesítések): esedékes elszámolások, kifogyó készletek, nyitott rendelések/jegyek, kintlévőség; az AI-asszisztens is elmondja („mi a napi helyzet?”).""",
    },
    {
        "key": "munkaido",
        "title": "Munkaidő-mérés (timer, korrekció, bér-CSV)",
        "keywords": "munkaidő timer mért idő munka utazás várakozás szünet korrekció jóváhagyás bér CSV mért idő átvétele munkalapra",
        "body": """## Hogyan mér?
A dolgozó munkalapjának megnyitása indítja a timert (munka / utazás / várakozás mód, szünet). Mentés/lezárás állítja le. A dolgozó a számokat nem látja, a vezető igen.

## Munkaidő oldal (vezető)
1. Összesítés dolgozónként és módonként, időszakra.
2. Korrekció-kérések jóváhagyása / elutasítása.
3. „Mért idő átvétele”: a munkalap munkaidő-tételét a mért időre állítja.
4. Bér-CSV export a bérszámfejtéshez.""",
    },
    {
        "key": "beosztas",
        "title": "Beosztás, jelenlét, távollét",
        "keywords": "beosztás műszak jelenlét bejelentkezés kijelentkezés távollét szabadság jóváhagyás éves keret helyettes vezetői rögzítés",
        "body": """## Beosztás
Műszakok dolgozónként; a dolgozó a Beosztásom alatt látja. Skill-alapú kiosztás.

## Jelenlét
Be-/kijelentkezés (önkiszolgáló); vezetői nézetben a napi jelenlét, hiányzók.

## Távollét
A dolgozó kérelmez, a vezető jóváhagy (éves keret ellenőrzéssel). Vezető közvetlenül is rögzíthet („Távollét rögzítése”, azonnal jóváhagyott). Távollét idejére helyettes állítható be a felhasználónál.""",
    },
    {
        "key": "dolgozok",
        "title": "Dolgozók (felvétel, belépés, szerepkör, skillek)",
        "keywords": "dolgozó felvétel belépés e-mail felhasználónév jelszó kezdő jelszó szerepkör skill TAJ bérexport inaktiválás jelszó-visszaállítás",
        "body": """## Dolgozó felvétele
1. Dolgozók → „+ Új dolgozó”: név, elérhetőség, belépési azonosító (e-mail VAGY felhasználónév), kezdő jelszó (vagy generált), szerepkör (beépített vagy saját), skillek.
2. Mentés után a dolgozó beléphet; jelszót a saját profiljában változtat, az admin visszaállító linket küldhet.
3. Inaktiválás: a fiók nem léphet be, az adatok megmaradnak.

## Szerepkör módosítása
A dolgozó szerkesztésénél a szerepkör átírható; a jogok a Beállítások → Jogosultságok mátrixból jönnek, a változás a következő oldalbetöltéskor él.""",
    },
    {
        "key": "vending",
        "title": "Vending telemetria (X-admin config ügynök a gépen)",
        "keywords": "vending telemetria ügynök X-admin config APK gépszám API-kulcs ping online offline parancs jelentés hibák promó receptek adag ár módosítás katalógus italok és adagok képek frissítés verzió fogyás számláló hely visszaszámlálás",
        "body": """## Beüzemelés
1. Vending telemetria → „+ Új gép”: gépszám (6 jegyű Jetinno), név, megjegyzés → API-kulcs keletkezik („API-kulcs másolása”).
2. A gépen az X-admin config appban: szerver URL, gépszám, API-kulcs → „Mentés + ütemezés”. Az app 5 percenként pingel (online-pont, hely), naponta jelent, és végrehajtja az adminból sorba tett parancsokat.
3. A gépkártya csempéi: Előzmények, Fogyás (italonként, számláló), Italok és adagok, Hibák (diagnózis + AI-magyarázat), Fájlok, Most (frissítés + parancsok; a „Parancs vár” sorban visszaszámlálás a következő pingig).

## Receptek és árak módosítása távolról
1. „Italok és adagok” → a gombnál „Szerkeszt” (vagy „Recept létrehozása”, ha a gombnak még nincs receptje): ár, lépésenként víz ml / kávé g, új lépés hozzáadása.
2. Mentés → a gép a következő pingnél beírja a receptfájlba (előtte az eredetit lementi gépen és szerveren), visszajelez „alkalmazva”.
3. „Eredeti adagok visszaállítása” egy gombbal.

## Promóció, hibák, frissítés
Promóciós képek/videó slotokba (a gyári képek backupba kerülnek, visszaállítható). Hibák: a gép naplójából csoportosítva, magyar diagnózissal. Az ügynök új verziója az adminból feltölthető; a gép naponta ellenőrzi, a képernyőn a „Telepítés” gombbal frissül (3.5+ parancsra is).""",
    },
    {
        "key": "csomagok",
        "title": "Csomagok (GLS feladás, címke, utánvét)",
        "keywords": "csomag GLS feladás címke utánvét nyomkövetés elszámolás feltöltés csomagként",
        "body": """## GLS-feladás
1. Csomagok → „+ Új csomag” vagy az Elszámolás/Feltöltés „GLS” pipájával előtöltve érkezik (partner, tartalom, utánvét-összeg).
2. Címke nyomtatása, csomagszám; státusz-követés a listában.""",
    },
    {
        "key": "beallitasok",
        "title": "Beállítások (számlázó, e-mail, Telegram, arculat, automatizálások, licenc)",
        "keywords": "beállítások billingó számlázz.hu API kulcs két cég SMTP e-mail telegram bot értesítés események arculat logó munkalap lábléc tárolási záradék AI szolgáltató licenc modulok automatizálás trigger import export biztonsági mentés",
        "body": """## Fontosabb kártyák
- Számlázó: Billingó (két fiók: X-Presso és Premium Caffe) vagy Számlázz.hu Agent; teszt-mód (proforma).
- E-mail (SMTP) a bizonylatokhoz, ajánlatokhoz, napi összefoglalóhoz.
- Értesítések: Telegram/WhatsApp események (rendelés, alkatrész-hiány, kérés…), automatikus bizonylat-küldés aláírás után, heti mentés.
- Munkalap/arculat: logó, cégnév, cím, lábléc, színek, átvételi záradék szövege.
- AI: szolgáltató és kulcs (Anthropic/Gemini), kiosztási prompt.
- Jogosultságok: szerepkörök és mátrix.
- Automatizálások: eseményekre (trigger) futó akciók.
- Import/Export: partnerek, gépek, termékek tömegesen; teljes mentés.
- Licenc: a bekapcsolt modulok (a Flotta-pult állítja).""",
    },
    {
        "key": "hibajelentes",
        "title": "Hibajelentés és napló",
        "keywords": "hibajelentés bogár gomb képernyőkép hibajegy státusz napló audit ki mit csinált tiltás",
        "body": """## Hiba vagy kérés jelzése
A jobb alsó piros bogár gombbal: leírás + automatikus képernyőkép és oldal-cím. A fejlesztő lezárja megjegyzéssel; újranyitható.

## Napló
Rendszer → Napló: minden fontos művelet (ki, mikor, mit), szűrhető akcióra és felhasználóra; a jogosultság-tiltások („auth.denied”) is itt látszanak.""",
    },
    {
        "key": "ai_asszisztens",
        "title": "AI-asszisztens (mit tud, hogyan kérdezz)",
        "keywords": "AI asszisztens chat kérdés hogyan csináljam kézikönyv partner keresés gép történet jegy nyitás számláló feltöltés munkalap elszámolás napi összefoglaló navigálás",
        "body": """## Mire használd?
A jobb alsó ✨ gomb. Beszélgetve (diktálva is) tudsz: partnert/gépet keresni, gép-történetet kérni, szervizjegyet nyitni, számlálót jelenteni, készletet feltölteni, munkalapot kitölteni, elszámolást rögzíteni (megerősítés után), napi összefoglalót kérni, oldalt megnyitni.

## „Hogyan csináljam?”
Kérdezd meg bármelyik folyamatot („hogyan adok át egy gépet átutalással?”, „hogyan hozok létre auditor szerepkört?”): az asszisztens a rendszer-kézikönyvben keres, és a teljes folyamatot lépésről lépésre elmondja. A kézikönyv a /kezikonyv oldalon is olvasható és kereshető.""",
    },
]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "").lower()
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", _norm(s)) if len(t) >= 3]


_STOP = {"hogyan", "hogy", "kell", "lehet", "tudok", "tudom", "mit", "van", "egy", "the", "how", "and", "vagy", "azt", "ezt", "ami", "amit", "csinálni", "csinalni", "csinaljam", "csinaljak"}


def search(query: str, limit: int = 3) -> list[dict]:
    """Egyszerű, ékezet-érzéketlen rangsor: cím 4, kulcsszó 3, törzs 1 pont
    tokenenként (előtag-egyezés is számít). Üres kérdésre az első szekciók."""
    toks = [t for t in _tokens(query) if t not in _STOP]
    if not toks:
        return [dict(s) for s in SECTIONS[:limit]]
    scored = []
    for sec in SECTIONS:
        title = _norm(sec["title"])
        kw = _norm(sec["keywords"])
        body = _norm(sec["body"])
        score = 0
        for t in toks:
            if t in title:
                score += 4
            if t in kw:
                score += 3
            score += min(body.count(t), 5)
        if score:
            scored.append((score, sec))
    scored.sort(key=lambda x: -x[0])
    return [dict(s) for _, s in scored[:limit]]


def index() -> list[dict]:
    return [{"key": s["key"], "title": s["title"]} for s in SECTIONS]


def get(key: str) -> dict | None:
    return next((dict(s) for s in SECTIONS if s["key"] == key), None)
