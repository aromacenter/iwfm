"""Jetinno (JL30 és rokonai) hibanapló-tudástár: a gép kínai/angol naplósoraiból
magyar diagnózis — mi a gond, mennyire súlyos, mi a teendő. A felület SOHA nem
mutat kínai szöveget: a nyers sor csak technikai részletként, lehajtva.

Ismeretlen mintára általános diagnózis jön; a felületen kérhető AI-magyarázat
(ai_explain), ha be van állítva AI-szolgáltató."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class Diagnosis:
    code: str
    severity: str  # info | warn | critical
    title: str
    cause: str
    steps: list[str] = field(default_factory=list)
    known: bool = True
    component: str = ""  # a napló forrás-osztálya (ASCII, pl. WaterFilterHelper)

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "title": self.title, "cause": self.cause,
                "steps": self.steps, "known": self.known, "component": self.component}


_NUM = r"(\d+(?:[.,]\d+)?)"


def _nums(message: str) -> list[str]:
    return re.findall(_NUM, message)


# (regex a teljes soron, kis/nagybetű-független) → szabály-függvény
def _rule_descale_counter(m: str) -> Diagnosis:
    n = _nums(m)
    total = n[0] if n else "?"
    return Diagnosis(
        "descale.counter", "info", "Vízkőtelenítési számláló frissült",
        f"A gép a vízkőtelenítésig hátralévő vízmennyiséget számolja; a halmozott érték most {total}. "
        "Ez önmagában nem hiba, csak napló — akkor lesz teendő, amikor a gép a vízkőtelenítő ablakot felteszi.",
        ["Teendő nincs. Ha gyakran látod, nézd meg a „Vízkőtelenítés szükséges” sort is."],
    )


def _rule_descale_update(m: str) -> Diagnosis:
    n = _nums(m)
    this_, last = (n[-2], n[-1]) if len(n) >= 2 else ("?", "?")
    return Diagnosis(
        "descale.update", "info", "Vízkőtelenítési vízmennyiség könyvelve",
        f"Egy főzés után a gép hozzáadta a felhasznált vizet a vízkőtelenítési számlálóhoz (most {this_} ml, előző {last} ml). Napló, nem hiba.",
        ["Teendő nincs."],
    )


def _rule_descale_dialog(_: str) -> Diagnosis:
    return Diagnosis(
        "descale.required", "warn", "Vízkőtelenítés szükséges — a gép felugró ablakot mutat",
        "A vízkőtelenítési számláló elérte a beállított küszöböt, a gép a kijelzőn vízkőtelenítést kér. "
        "Amíg el nem végzik, a figyelmeztetés minden indításnál visszajön, és a vízkő miatt romlik a bojler fűtése és a kávé minősége.",
        [
            "Karbantartó menü → Tisztítás / Vízkőtelenítés program indítása (Jetinno vízkőoldóval, a program ~20–30 perc).",
            "A program végén alapos öblítés (a gép magától kéri), friss vizet a tartályba.",
            "Ha vízszűrő van a gépben: ellenőrizd a szűrő állapotát, esedékes-e a csere.",
            "A programot a gép lezárja és nullázza a számlálót; ha a felugró ablak marad, a karbantartó menüben „Vízkőtelenítés kész” nyugtázás.",
            "Utána ezt a hibát itt zárd le (✔ Lezár).",
        ],
    )


def _rule_filter(_: str) -> Diagnosis:
    return Diagnosis(
        "filter.replace", "warn", "Vízszűrő csere esedékes",
        "A vízszűrő (szűrőbetét) átfolyás-számlálója lejárt. A régi szűrő rontja a víz minőségét és gyorsítja a vízkövesedést.",
        ["Új szűrőbetét behelyezése a gyártó útmutatója szerint.", "Karbantartó menü → szűrő-számláló nullázása.", "Hiba lezárása itt."],
    )


def _rule_beans(m: str) -> Diagnosis:
    can = re.search(r"canister\s*=\s*(\d+)", m)
    where = f" (tartály azonosító: {can.group(1)})" if can else ""
    return Diagnosis(
        "beans.empty", "critical", "Kifogyott a kávébab",
        f"A daráló nem kapott babot, a gép a kávé-tartály kiürülését jelzi{where}. Amíg üres, a kávés italok nem készülnek el, a vásárló hibát lát.",
        ["Babtartály feltöltése (a tartály fedelét vissza, a csúszkát nyitva).", "Egy próbafőzés a karbantartó menüből.", "Ha tele tartállyal is jön: a daráló torka eltömődhetett — tisztítás."],
    )


def _rule_powder(m: str) -> Diagnosis:
    can = re.search(r"canister\s*=\s*(\d+)", m)
    where = f" (tartály azonosító: {can.group(1)})" if can else ""
    return Diagnosis(
        "powder.empty", "critical", "Kifogyott egy portartály (tej-/kakaópor)",
        f"Az egyik por-tartály üres{where}; az érintett italok (tejes, kakaós) nem készülnek el.",
        ["Az érintett portartály feltöltése, a csiga ellenőrzése.", "Próbafőzés.", "Hiba lezárása itt."],
    )


def _rule_water(_: str) -> Diagnosis:
    return Diagnosis(
        "water.empty", "critical", "Nincs víz — a gép nem főz",
        "A gép nem érzékel vizet (üres a tartály, zárva a vízcsap vagy a bekötésen nincs nyomás). Minden ital leáll.",
        ["Tartályos gépnél: tartály feltöltése, helyére pattintása.", "Hálózati bekötésnél: vízcsap, szűrő, tömlő ellenőrzése, nyomás.", "Ha a víz rendben: az úszó/érzékelő vagy a áramlásmérő szorul — szerviz."],
    )


def _rule_waste_water(_: str) -> Diagnosis:
    return Diagnosis(
        "waste.water_full", "critical", "Megtelt a szennyvíz-tartály / csepptálca",
        "A gép a szennyvíz-tartályt tele jelzi. Amíg nem ürítik, az italkészítés letiltva.",
        ["Csepptálca / szennyvíz-tartály kiürítése és visszahelyezése.", "Ha üresen is jelzi: az úszó beragadt — tisztítás."],
    )


def _rule_grounds(_: str) -> Diagnosis:
    return Diagnosis(
        "waste.grounds_full", "critical", "Megtelt a zacc-tartály",
        "A kávézacc-tartály elérte a beállított darabszámot. Amíg nem ürítik, nincs kávé.",
        ["Zacc-tartály kiürítése, tisztítása, visszahelyezése (a gép a visszahelyezéskor nullázza a számlálót).", "Ha a számláló nem nulláz: karbantartó menüben kézzel."],
    )


def _rule_door(_: str) -> Diagnosis:
    return Diagnosis(
        "door.open", "critical", "Nyitva az ajtó / szerviz-fedél",
        "A gép az ajtó- vagy fedélérzékelő szerint nyitva van, ezért biztonsági okból nem főz.",
        ["Ajtó, fedél becsukása, zár ellenőrzése.", "Ha csukva is jelzi: a reed-érzékelő/mágnes elmozdult — szerviz."],
    )


def _rule_cup(_: str) -> Diagnosis:
    return Diagnosis(
        "cup.dispense", "critical", "Pohár-adagolás hiba / nincs pohár",
        "A pohárkiadó nem tudott poharat adni (üres az oszlop, beszorult pohár vagy az érzékelő nem látta a poharat).",
        ["Pohároszlop feltöltése, beszorult pohár eltávolítása.", "Pohár-érzékelő tisztítása.", "Próba-kiadás a karbantartó menüből."],
    )


def _rule_temp(_: str) -> Diagnosis:
    return Diagnosis(
        "boiler.temperature", "critical", "Bojler / fűtés hiba (hőmérséklet)",
        "A bojler nem éri el vagy túllépi a hőmérsékletet, vagy a fűtés túl sokáig tart. Oka lehet vízkő, hibás termosztát/fűtőszál vagy hőérzékelő.",
        ["Gép ki-be kapcsolása, 5 perc várakozás.", "Ha vízkőtelenítés esedékes, elvégezni.", "Ismétlődésnél szerviz: termosztát, fűtőszál, NTC-érzékelő mérése."],
    )


def _rule_brewer(_: str) -> Diagnosis:
    return Diagnosis(
        "brewer.fault", "critical", "Főzőegység hiba (mozgás / pozíció)",
        "A főzőegység nem érte el a kívánt pozíciót (beszorult zacc, elkopott tömítés, motor vagy végálláskapcsoló hiba).",
        ["Gép kikapcsolása, főzőegység kivétele, tisztítása, tömítések zsírozása.", "Visszahelyezés, újraindítás, próbafőzés.", "Ismétlődésnél: motor / végálláskapcsoló — szerviz."],
    )


def _rule_grinder(_: str) -> Diagnosis:
    return Diagnosis(
        "grinder.fault", "critical", "Daráló hiba (túláram / időtúllépés)",
        "A daráló elakadt vagy nem őrölt időben (idegen test a babban, túl finom őrlés, kopott kövek).",
        ["Babtartály leszerelése, idegen test keresése a daráló torkában.", "Őrlési finomság egy fokkal durvább.", "Kopott daráló kő pár cseréje (szerviz)."],
    )


def _rule_milk(_: str) -> Diagnosis:
    return Diagnosis(
        "milk.fault", "warn", "Tej-rendszer hiba (tej elfogyott / habosító)",
        "A tejes italokhoz nincs tej, vagy a tejhabosító/tejcső eltömődött.",
        ["Tej pótlása, tejcső ellenőrzése.", "Tejrendszer tisztítóprogram futtatása.", "Habosító szétszedése, tisztítása."],
    )


def _rule_cleaning(_: str) -> Diagnosis:
    return Diagnosis(
        "cleaning.required", "warn", "Tisztítás / öblítés esedékes",
        "A gép a beállított főzésszám vagy idő után tisztítást kér. Elhalasztva romlik a kávé íze és nő a meghibásodás esélye.",
        ["Karbantartó menü → tisztítóprogram (tisztítótablettával).", "A program végén öblítés.", "Hiba lezárása itt."],
    )


def _rule_payment(_: str) -> Diagnosis:
    return Diagnosis(
        "payment.fault", "warn", "Fizetési modul hiba (kártya / QR / érme)",
        "A fizetési eszköz (kártyaolvasó, QR-fizetés vagy érmevizsgáló) nem válaszolt vagy hibát adott; a vásárló nem tud fizetni.",
        ["Kártyaolvasó áramellátása, kábel.", "Hálózat ellenőrzése (a fizetés internetet igényel).", "Szolgáltató (SaferPay / bank) státusz."],
    )


def _rule_network(_: str) -> Diagnosis:
    return Diagnosis(
        "network.fault", "warn", "Hálózati hiba (szerver nem elérhető)",
        "A gép nem érte el a szervert (nincs internet, DNS-hiba, időtúllépés). A távfelügyelet és a fizetés is szünetel.",
        ["Hálózati kábel / WiFi ellenőrzése, router újraindítása.", "WiFi-n: DNS 8.8.8.8 / 1.1.1.1 beállítása (statikus IP-vel).", "Ha visszajött, a hiba lezárása."],
    )


def _rule_comm(_: str) -> Diagnosis:
    return Diagnosis(
        "board.comm", "critical", "Kommunikációs hiba a vezérlőpanellel",
        "A kijelző (Android) és a gép vezérlőpanelje közti soros kapcsolat megszakadt — a gép nem fogad parancsot.",
        ["Gép teljes áramtalanítása 1 percre, újraindítás.", "A panel és a kijelző közti kábel (soros/USB) ellenőrzése.", "Ismétlődésnél szerviz: vezérlőpanel."],
    )


def _rule_timeout(_: str) -> Diagnosis:
    return Diagnosis(
        "operation.timeout", "warn", "Művelet időtúllépés",
        "Egy belső művelet (fűtés, őrlés, adagolás) nem fejeződött be a várt időn belül.",
        ["Gép újraindítása.", "Ha ismétlődik ugyanannál a műveletnél, a hozzá tartozó egységet ellenőrizni (lásd a többi hibát)."],
    )


RULES: list[tuple[re.Pattern[str], object]] = [
    (re.compile(r"显示除垢弹框|除垢提醒|descal\w*\s*(dialog|remind|required|needed)", re.I), _rule_descale_dialog),
    (re.compile(r"更新除垢水量", re.I), _rule_descale_update),
    (re.compile(r"获取除垢累计|除垢累计", re.I), _rule_descale_counter),
    (re.compile(r"滤芯|滤水器|filter\s*(replace|expired|change)", re.I), _rule_filter),
    (re.compile(r"咖啡豆缺料|beans?\s*empty|no\s*beans|豆缺", re.I), _rule_beans),
    (re.compile(r"粉料?缺料|powder\s*empty|料盒缺料", re.I), _rule_powder),
    (re.compile(r"缺水|水箱|no\s*water|water\s*(empty|tank)|进水", re.I), _rule_water),
    (re.compile(r"废水满|废水箱|waste\s*water|drip\s*tray", re.I), _rule_waste_water),
    (re.compile(r"渣桶满|渣盒|grounds?\s*(full|bin|container)", re.I), _rule_grounds),
    (re.compile(r"门未关|门开|door\s*open", re.I), _rule_door),
    (re.compile(r"落杯|缺杯|杯架|cup\s*(drop|dispens|empty|error)", re.I), _rule_cup),
    (re.compile(r"超温|加热超时|锅炉|温度|boiler|temperature|heating", re.I), _rule_temp),
    (re.compile(r"冲泡器|冲泡单元|酿造|brew(er|ing)\s*(unit|error|fault|position)|brewerctrl", re.I), _rule_brewer),
    (re.compile(r"研磨超时|磨豆机|grind(er)?\s*(timeout|fault|error|stall)|grinderctrl", re.I), _rule_grinder),
    (re.compile(r"牛奶|奶泡|奶管|milk", re.I), _rule_milk),
    (re.compile(r"清洗提醒|清洗|冲洗|clean(ing)?\s*(remind|required|needed)|rinse", re.I), _rule_cleaning),
    (re.compile(r"支付|刷卡|二维码|payment|card\s*reader|coin", re.I), _rule_payment),
    (re.compile(r"网络|服务器|network|server|UnknownHost|SocketTimeout|ConnectException", re.I), _rule_network),
    (re.compile(r"串口|通信|主控板|serial|communication|mainboard", re.I), _rule_comm),
    (re.compile(r"超时|timeout", re.I), _rule_timeout),
]

_COMPONENT_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]{2,40})\s*[:：]")
_CJK_RE = re.compile(r"[一-鿿]")


def strip_cjk(message: str) -> str:
    """A nyers sor kínai karakterek nélkül (azonosítók, számok maradnak)."""
    out = _CJK_RE.sub("", message)
    out = re.sub(r"(\s*[:：,，]\s*){2,}", lambda m: m.group(0).strip()[0] + " ", out)  # dupla elválasztók
    return re.sub(r"\s{2,}", " ", out).strip(" :：,，")


def diagnose(source: str, message: str) -> dict:
    comp = _COMPONENT_RE.match(message or "")
    component = comp.group(1) if comp else ""
    for rx, fn in RULES:
        if rx.search(message or ""):
            d = fn(message)  # type: ignore[operator]
            d.component = component
            return d.as_dict()
    generic = Diagnosis(
        "unknown", "warn" if source == "exception" else "info",
        "Ismeretlen naplóbejegyzés" if source != "exception" else "Ismeretlen kivétel a kávéfőző appban",
        "Ezt a mintát a tudástár még nem ismeri. A technikai részletben a gép eredeti sora (azonosítók, számok) látható; "
        "a „Magyarázat kérése” gomb AI-val értelmezi, ha be van állítva AI-szolgáltató.",
        ["Ha a gép normálisan működik, zárd le.", "Ha ismétlődik és a gép hibát mutat, kérj magyarázatot vagy jelezd a szerviznek a technikai részlettel."],
        known=False, component=component,
    )
    return generic.as_dict()


async def ai_explain(db: AsyncSession, source: str, message: str, count: int) -> str:
    """AI-magyarázat egy ismeretlen (vagy bármely) naplósorra — magyarul,
    kínai szöveg nélkül a válaszban."""
    from app.services.wfm.ai_service import generate

    prompt = (
        "Egy Jetinno JL30 típusú, Android-vezérlésű automata kávégép naplósorát kapod (a gép szoftvere kínai/angol "
        "üzeneteket ír). Értelmezd egy magyar kávégép-szerviz technikusának. VÁLASZ MAGYARUL, KÍNAI KARAKTER NÉLKÜL, "
        "ebben a szerkezetben: 1) Mi történt (1–2 mondat) 2) Mennyire súlyos (nem hiba / figyelmeztetés / a gép nem szolgál ki) "
        "3) Valószínű ok 4) Teendők lépésenként (max. 5 lépés). Legyél konkrét, ne találj ki nem létező menüpontot.\n\n"
        f"Forrás: {source}\nElőfordulás: {count}×\nNaplósor: {message}"
    )
    text = await generate(db, prompt, max_tokens=700)
    return _CJK_RE.sub("", text).strip()
