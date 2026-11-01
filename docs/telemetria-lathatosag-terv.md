# Vending-telemetria: példányok közötti láthatóság és gép-birtoklás (terv)

**Jegy:** x-presso 4c81e958 (blokkoló, 2026-11-01), Szabó László. **Állapot:** terv, jóváhagyásra vár.

## 1. A helyzet

- A Jetinno kávégépek a **Caffe Perfect** tulajdonában maradnak; az x-presso **bérli** és **albérletbe adja**
  a saját partnereinek. A szervizt a Caffe Perfect végzi és kezeli; az x-presso csak a helyszínen
  javíthatatlan gépet cseréli.
- A Caffe Perfect **is az X-admin rendszert fogja használni**, saját példányon. Tehát ugyanaz a fizikai gép
  két példányban él: a tulajdonosnál és a bérlőnél.
- Kért szabály (a jegy szövege): az x-presso adminja **csak azoknak a gépeknek** a telemetriáját lássa,
  amelyek nála vannak raktáron vagy az ő partnereinél; a Caffe Perfect adminja és managere **csak azokat**
  lássa és kezelje, amelyek fizikailag náluk vagy az ő partnereiknél vannak. A láthatóság tehát **birtoklás**
  (custody) szerinti és **kizárólagos**, a tulajdonjog csak nyilvántartási adat (és a visszaadás útja).

## 2. Ma mi van

- Telemetria modul: `VendingMachine` (machine_code, api_key, telemetry pillanatkép, pending_commands, promo),
  `VendingTelemetry` előzmény; a gépen futó ügynök a **saját példány** `/api/saferpay/...` végpontjaira
  küld (`machine_code` + `key` a törzsben), onnan veszi a parancsokat és a promó-képeket.
- A vending-gépek és a `Asset` (tárgyi eszköz, kihelyezés partnerhez) **külön nyilvántartás**, lazán
  kapcsolva (barcode / gépkód).
- Nincs példányok közötti kapcsolat: a Flotta csak licencet, roadmapet, szabályokat nyom le, telemetriát nem.

## 3. Megoldási irányok

| | A) ügynök két helyre küld | B) példányok egymás között szinkronizálnak | **C) központi telemetria-elosztó a Flottában** |
|---|---|---|---|
| Ügynök-változás | igen, minden gépen | nem | egyszer: az URL a hubra mutat |
| Birtoklás-váltás | kézi mindkét oldalon | páronkénti operator-hívások, N×N | egy helyen (regiszter) |
| Parancs/promó | ütközik (ki parancsol?) | bonyolult | a hub a birtokoshoz irányít |
| Új tulajdonos-partner később | megint ügynök-módosítás | újabb párosítás | semmi |
| Kockázat | kettős forrás, eltérés | sok mozgó alkatrész | a hub kiesése = telemetria-kiesés (de pufferel) |

**Javaslat: C.** A Flotta már most a példányok közös „fölöttese” (licenc, roadmap, szabályok, verzió), és
minden példány operator-API-val elérhető.

## 4. C) tervezet

### 4.1 Gép-birtoklási regiszter (Flotta)

`machine_registry`: `machine_code` (kulcs), `owner_instance_id`, `custodian_instance_id`, `since`,
`custody_note` (pl. „albérlet: Pláza Kávézó”), `api_key_hash`, `last_seen_at`, `last_snapshot` (JSON),
`pending_commands` (JSON, a birtokos példány tölti), `history` (birtoklás-váltások).

- **Tulajdonos** (owner): állandó, csak a pultból módosítható. **Birtokos** (custodian): a gép fizikai
  helyének példánya; alapból = tulajdonos.
- **Láthatóság = birtokos.** A tulajdonos példány a birtokos-váltás pillanatától nem látja a gép élő
  telemetriáját és nem küld rá parancsot; a Gépek listájában „albérletben: x-presso (2026-11-01-től)”
  állapotot lát. Visszaadáskor fordítva.

### 4.2 Hub-végpont az ügynöknek

`POST https://x-admin.hu/api/hub/telemetry` (és a meglévő ügynök-hívások tükrei: parancsok lekérése,
promó-kép letöltés, fájlfeltöltés) — ugyanaz a törzs (`machine_code`, `key`), a hub a regiszter szerint
a **birtokos példány** operator-API-jára továbbítja (`/api/operator/vending/ingest`), és a válaszban onnan
hozza a parancsokat. Első lépésben **tiszta proxy** (a példányok kódja nem változik, az ügynökön csak az
URL cserél), később a hub maga tárol és a példányok tőle kérnek.

### 4.3 Birtokos-váltás (átadás példányok között)

- A tulajdonos példányon a Gépek nézetben „🔁 Átadás másik üzemeltetőnek” (cél: a Flottában regisztrált
  példányok közül, pl. x-presso), opcionális partner-cím. A kérés a Flottához megy, a regiszter frissül,
  a cél példány megkapja a gépet **raktáron** állapotban (VendingMachine + Asset létrejön vagy aktiválódik,
  a gépkód és a kulcs öröklődik), a forrás példányon a gép „albérletben” állapotba kerül (nem törlődik).
- Visszaadás ugyanígy a birtokos oldaláról („↩️ Visszaadás a tulajdonosnak”).
- A 121. kör jegyzőkönyve itt is készül (kihelyezés/visszavétel a két cég között, aláírással).
- Jogosultság: példányon belül a meglévő szerepkörök (admin/manager: lát és kezel; dolgozó: a modul
  szabályai szerint). Példányok között nincs belépés, csak a regiszter.

### 4.4 Értesítés és audit

Minden birtokos-váltásról Telegram a pult-csatornára és mindkét példány adminjának; audit mindkét példányon
(`vending.custody_in` / `custody_out`) és a Flottában.

### 4.5 Ütemezés

1. Regiszter + pult-fül + operator-végpont a példányokon (`/api/operator/vending/ingest`, `/custody`). Teszt.
2. Hub-proxy az ügynöknek; egy gép átállítása az x-presso-nál (URL-csere), ellenőrzés.
3. Átadás/visszaadás UI mindkét példányon, jegyzőkönyvvel.
4. Minden gép átállítása a hubra; a Caffe Perfect példány élesítése, gépek tulajdonosként regisztrálva,
   x-presso birtokosként ott, ahol nála vannak.

## 5. Nyitott kérdések a userhez

1. A Caffe Perfect példánya mikor indul, és ki regisztrálja a gépeket (ők vagy mi a pultból)?
2. A szervizt a Caffe Perfect végzi: a **szervizjegyek** is nála keletkezzenek, miközben a gép nálunk van
   birtokban? (Javaslat: a birtokos példány jelez, a hub továbbítja a tulajdonosnak egy „szerviz-kérés”
   bejegyzésként.)
3. Az ügynök URL-cseréje a gépeken: távolról megoldható (parancs), vagy helyszíni?
4. Kell-e a tulajdonosnak **összesített** (nem élő) kép a kiadott gépekről (darabszám, utolsó jelentkezés)?
