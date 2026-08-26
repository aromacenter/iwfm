"use client";

/** Elszámolás: partner (külső raktár) kiválasztása → készlet + feltöltés →
 *  fizikai leltár rögzítése → fogyás/összeg számítás → elszámolás mentése →
 *  „Kiszámlázott” gomb (Billingó). Az elszámoló a bejelentkezett user. */

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import IconLegend from "@/components/IconLegend";
import PartnerInfo from "@/components/PartnerInfo";
import PartnerPicker from "@/components/PartnerPicker";
import SearchSelect from "@/components/SearchSelect";
import SignatureCanvas from "@/components/SignatureCanvas";
import { api, ApiError, downloadFile, errorMessage } from "@/lib/api";
import { COMPANIES, COMPANY_CHIP, COMPANY_SHORT } from "@/lib/companies";
import { useT } from "@/lib/i18n";
import { usePerms } from "@/lib/perms";
import { useUI } from "@/lib/ui";

interface Partner {
  id: string;
  name: string;
  partner_code: string | null;
  tax_number: string | null;
  contact_name: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  address_city: string | null;
  is_active: boolean;
}

interface Stock {
  product_id: string;
  product_name: string;
  unit: string;
  quantity: number;
  grams_per_portion: number;
  price_per_portion: number;
  base_price_per_portion: number;
  has_price_override: boolean;
  portions_available: number;
  min_quantity: number | null;
}

interface Product {
  id: string;
  name: string;
  category: string | null;
  is_active: boolean;
  purchase_price: number | null;
  price_per_portion: number;
  grams_per_portion: number;
  unit: string;
  is_consignment: boolean;
}

interface Settlement {
  id: string;
  partner_id: string;
  partner_name: string | null;
  settled_by_name: string;
  invoicing_company: "xp" | "pc" | null;
  payment_method: "cash" | "card" | "transfer" | "cod";
  no_vat: boolean;
  total_net: number;
  total_gross: number;
  invoiced: boolean;
  billingo_status: string | null;
  has_signature: boolean;
  receipt_sent_at: string | null;
  debt_before: number | null;
  paid_amount: number | null;
  previous_at: string | null;
  created_at: string;
}

interface PartnerPrice {
  product_id: string;
  product_name: string;
  base_price_per_portion: number;
  price_per_portion: number | null;
}

interface Order {
  id: string;
  order_no: string;
  partner_label: string | null;
  items: { name: string; quantity: number; unit: string }[];
  note: string | null;
  contact_name: string | null;
  contact_phone: string | null;
  created_at: string;
}

interface LowStock {
  partner_id: string;
  partner_name: string;
  product_id: string;
  product_name: string;
  unit: string;
  quantity: number;
  threshold: number;
}

interface DuePartner {
  partner_id: string;
  partner_code: string | null;
  name: string;
  contact_phone: string | null;
  address: string | null;
  city: string | null;
  last_settlement_at: string | null;
  days_since: number | null;
  stock_products: number;
  avg_daily_kg: number | null;
  days_left: number | null;
  suggested_kg: number | null;
  interval_weeks: number;
  next_due: string | null;
}

const PAYMENTS = ["cash", "card", "transfer", "cod"] as const;

interface CtxMachine {
  asset_id: string;
  barcode: string;
  name: string;
  counter_count: number;
  counters: number[] | null;
  prev_counter: number;
  last_settled_at: string | null;
  default_product_id: string | null;
  product_name: string | null;
}

interface DebtItem {
  settlement_id: string;
  created_at: string;
  total_gross: number;
  paid_amount: number;
  remaining: number;
  due_date: string | null;
  invoiced: boolean;
}

interface SettlementContext {
  machines: CtxMachine[];
  debt: number;
  debt_items: DebtItem[];
  active_contracts: number;
  last_visit: string | null;
  single_product_id: string | null;
  open_deliveries: number;
  open_deliveries_net: number;
  default_payment_method: "cash" | "card" | "transfer" | "cod" | null;
  payment_terms_days: number | null;
  settlement_weeks: number | null;
}

interface MachineInput {
  newCounter: string;
  newCounters: string[];
  service: string;
}

interface MachinePayload {
  asset_id: string;
  new_counter: number;
  new_counters?: number[];
  service_portions: number;
  discount_pct: number;
  product_id?: string | null;
  price_per_portion?: number | null;
}

interface HandoverPayload {
  product_id: string;
  quantity: number;
  unit_cost?: number | null;
  price?: number | null; // eladási egységár nem-bizományos terméknél
}

// Offline-várólista: hálózati hiba esetén ide kerül az elszámolás, és
// újracsatlakozáskor automatikusan beküldjük.
const QUEUE_KEY = "iwfm-pending-settlements";

interface QueuedSettlement {
  partner_id: string;
  partner_name: string;
  payment_method: (typeof PAYMENTS)[number];
  no_vat?: boolean;
  lines: { product_id: string; physical_qty: number; counter_portions?: number | null; price_per_portion?: number | null }[];
  machines?: MachinePayload[];
  handovers?: HandoverPayload[];
  paid_amount?: number | null;
  note: string | null;
  queued_at: string;
}

function readQueue(): QueuedSettlement[] {
  try {
    return JSON.parse(localStorage.getItem(QUEUE_KEY) ?? "[]") as QueuedSettlement[];
  } catch {
    return [];
  }
}

function writeQueue(items: QueuedSettlement[]) {
  localStorage.setItem(QUEUE_KEY, JSON.stringify(items));
}

export default function ElszamolasPage() {
  const { t, lang } = useT();
  const { toast, confirm, prompt } = useUI();
  const { can } = usePerms();
  const canDelete = can("delete");
  const canPrices = can("products");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [partners, setPartners] = useState<Partner[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [partnerId, setPartnerId] = useState("");
  const [stock, setStock] = useState<Stock[]>([]);
  const [physical, setPhysical] = useState<Record<string, string>>({});
  const [counters, setCounters] = useState<Record<string, string>>({});
  const [payment, setPayment] = useState<(typeof PAYMENTS)[number]>("cash");
  const [note, setNote] = useState("");
  // Gép-soros elszámolás: kontextus + gépenkénti beviteli mezők
  const [ctx, setCtx] = useState<SettlementContext | null>(null);
  const [machineInputs, setMachineInputs] = useState<Record<string, MachineInput>>({});
  const [machineProducts, setMachineProducts] = useState<Record<string, string>>({});
  // Kézi egységár-átírás (Ft/adag, nettó) — üresen az automatikus ár érvényes;
  // az átírás a mentéskor audit-naplóba kerül.
  const [machinePrices, setMachinePrices] = useState<Record<string, string>>({});
  const [linePrices, setLinePrices] = useState<Record<string, string>>({});
  const [handovers, setHandovers] = useState<Record<string, { qty: string; cost: string; price?: string }>>({});
  const [paidAmount, setPaidAmount] = useState("");
  // Készlet-sor nélküli partnernél is elszámolható: kézzel felvett termék-sorok
  const [extraProducts, setExtraProducts] = useState<string[]>([]);
  const [addProductId, setAddProductId] = useState("");
  const [history, setHistory] = useState<Settlement[]>([]);
  const [infoPartner, setInfoPartner] = useState<string | null>(null);
  const [companyFilter, setCompanyFilter] = useState("");
  const [replenish, setReplenish] = useState<{ product_id: string; quantity: string; unit_cost: string; source_warehouse_id: string } | null>(null);
  const [showDebt, setShowDebt] = useState(false);
  // Készlet-visszavét a partnertől raktárba/autóba: null = zárva;
  // product_id "" = TELJES készlet (szerződés-lezárás).
  const [stockReturn, setStockReturn] = useState<{ product_id: string; quantity: string; warehouse_id: string } | null>(null);
  const [srcWarehouses, setSrcWarehouses] = useState<{ id: string; name: string; kind: string; user_id: string | null; is_active: boolean }[]>([]);
  const [meId, setMeId] = useState<string | null>(null);
  // Alapértelmezett forrás: a bejelentkezett képviselő SAJÁT autója, ha van;
  // különben az első autó, végül bármelyik raktár.
  const defaultVanId = useCallback(
    () =>
      srcWarehouses.find((w) => w.kind === "van" && w.user_id === meId)?.id ??
      srcWarehouses.find((w) => w.kind === "van")?.id ??
      srcWarehouses[0]?.id ??
      "",
    [srcWarehouses, meId],
  );
  const [due, setDue] = useState<DuePartner[]>([]);
  const [showDue, setShowDue] = useState(true);
  const [lowStock, setLowStock] = useState<LowStock[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [signing, setSigning] = useState<Settlement | null>(null);
  const [signature, setSignature] = useState<string | null>(null);
  const [signEmail, setSignEmail] = useState("");
  const [prices, setPrices] = useState<PartnerPrice[] | null>(null); // null = modal zárva
  const [priceEdits, setPriceEdits] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingOffline, setPendingOffline] = useState(0);

  const fmt = (dt: string) =>
    new Date(dt).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { dateStyle: "short", timeStyle: "short" });
  const ft = (n: number) => `${n.toLocaleString(lang === "hu" ? "hu-HU" : "en-GB")} Ft`;

  useEffect(() => {
    api.get<Partner[]>("/api/partners").then(setPartners).catch(() => {});
    api.get<Product[]>("/api/products").then(setProducts).catch(() => {});
  }, []);

  const loadStock = useCallback(() => {
    if (!partnerId) { setStock([]); return; }
    api.get<Stock[]>(`/api/partners/${partnerId}/stock`).then((s) => {
      setStock(s);
      setExtraProducts([]);
      setPhysical(Object.fromEntries(s.map((x) => [x.product_id, ""])));
      setCounters(Object.fromEntries(s.map((x) => [x.product_id, ""])));
      setHandovers(Object.fromEntries(s.map((x) => [x.product_id, { qty: "", cost: "" }])));
    }).catch(() => {});
  }, [partnerId]);

  const loadCtx = useCallback(() => {
    setDiscountAll(false);
    setMachinePrices({});
    setLinePrices({});
    if (!partnerId) { setCtx(null); setMachineInputs({}); setPaidAmount(""); return; }
    api.get<SettlementContext>(`/api/partners/${partnerId}/settlement-context`).then((c) => {
      setCtx(c);
      setMachineInputs(Object.fromEntries(c.machines.map((m) => [
        m.asset_id,
        { newCounter: "", newCounters: Array.from({ length: m.counter_count }, () => ""),
          service: "" },
      ])));
      setMachineProducts(Object.fromEntries(c.machines.map((m) => [
        m.asset_id,
        m.default_product_id ?? c.single_product_id ?? "",
      ])));
      setPaidAmount("");
      // Fizetési mód a szerződésből — itt csak felülírni kell, ha eltér.
      if (c.default_payment_method) setPayment(c.default_payment_method);
      else setPayment("cash");
    }).catch(() => setCtx(null));
  }, [partnerId]);

  // Készlet-sorok + kézzel felvett termékek (0 induló készlettel) — leltár
  // nélküli partnernél is elszámolható.
  const stockRows = useMemo<Stock[]>(() => {
    const extras = extraProducts
      .filter((pid) => !stock.some((s) => s.product_id === pid))
      .map((pid) => {
        const p = products.find((x) => x.id === pid);
        if (!p) return null;
        return {
          product_id: p.id,
          product_name: p.name,
          unit: p.unit || "kg",
          quantity: 0,
          grams_per_portion: p.grams_per_portion,
          price_per_portion: p.price_per_portion,
          base_price_per_portion: p.price_per_portion,
          has_price_override: false,
          portions_available: 0,
          min_quantity: null,
        } as Stock;
      })
      .filter(Boolean) as Stock[];
    return [...stock, ...extras];
  }, [stock, extraProducts, products]);

  const loadHistory = useCallback(() => {
    const params = new URLSearchParams();
    if (partnerId) params.set("partner_id", partnerId);
    if (companyFilter) params.set("company", companyFilter);
    const qs = params.toString();
    api.get<Settlement[]>(`/api/settlements${qs ? `?${qs}` : ""}`).then(setHistory).catch(() => {});
  }, [partnerId, companyFilter]);

  // Körút-tervező: város-szűrő + megállók kijelölése → Google Maps útvonal.
  const [dueCity, setDueCity] = useState("");
  const [routeSelected, setRouteSelected] = useState<Set<string>>(new Set());

  const dueCities = useMemo(() => {
    const counts = new Map<string, number>();
    for (const d of due) {
      if (d.city) counts.set(d.city, (counts.get(d.city) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [due]);

  const dueFiltered = useMemo(
    () => (dueCity ? due.filter((d) => d.city === dueCity) : due),
    [due, dueCity],
  );

  function toggleRoute(id: string) {
    setRouteSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else if (next.size < 60) next.add(id);
      else toast(t("route.maxStops"), "error");
      return next;
    });
    setWeekPlan(null);
  }

  async function openRoute() {
    const selected = due.filter((d) => routeSelected.has(d.partner_id) && d.address);
    if (!selected.length) return;
    let addresses = selected.map((d) => d.address as string);
    if (selected.length >= 2) {
      // Hatékonyság: a megállók legrövidebb sorrendjét a szerver számolja
      // (offline koordináta-adatból, 10 megállóig egzakt megoldással).
      try {
        const res = await api.post<{
          stops: { partner_id: string; address: string | null; located: boolean }[];
          total_km: number | null;
          original_km: number | null;
          total_min: number | null;
          used_roads: boolean;
        }>("/api/geo/optimize-route", { partner_ids: selected.map((d) => d.partner_id) });
        addresses = res.stops
          .map((s) => s.address)
          .filter((a): a is string => !!a);
        if (res.total_km !== null && res.original_km !== null) {
          const saved = Math.max(Math.round(res.original_km - res.total_km), 0);
          if (res.used_roads && res.total_min !== null) {
            toast(
              t("route.optimizedRoad", {
                km: res.total_km, min: Math.round(res.total_min), saved,
              }),
              "success",
            );
          } else {
            toast(
              saved > 0
                ? t("route.optimizedSaved", { km: res.total_km, saved })
                : t("route.optimized", { km: res.total_km }),
              "success",
            );
          }
        }
      } catch {
        // koordináta-adat híján az eredeti sorrend megy
      }
    }
    window.open(
      `https://www.google.com/maps/dir/${addresses.map(encodeURIComponent).join("/")}`,
      "_blank",
    );
  }

  type WeekPlan = {
    days: {
      stops: { partner_id: string; name: string; address: string | null; located: boolean }[];
      total_km: number | null;
    }[];
    total_km: number | null;
    used_roads: boolean;
  };
  const [weekPlan, setWeekPlan] = useState<WeekPlan | null>(null);
  const [weekDays, setWeekDays] = useState(3);

  async function planWeek() {
    const selected = due.filter((d) => routeSelected.has(d.partner_id) && d.address);
    if (selected.length < 2) return;
    try {
      const res = await api.post<WeekPlan>("/api/geo/plan-week", {
        partner_ids: selected.map((d) => d.partner_id),
        days: weekDays,
      });
      setWeekPlan(res);
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  function openDayRoute(day: WeekPlan["days"][number]) {
    const addresses = day.stops.map((s) => s.address).filter((a): a is string => !!a);
    if (!addresses.length) return;
    window.open(
      `https://www.google.com/maps/dir/${addresses.map(encodeURIComponent).join("/")}`,
      "_blank",
    );
  }

  const loadDue = useCallback(() => {
    api.get<DuePartner[]>("/api/settlements/due?days=30").then(setDue).catch(() => {});
    api.get<LowStock[]>("/api/products/low-stock").then(setLowStock).catch(() => {});
    api.get<Order[]>("/api/orders?status=open").then(setOrders).catch(() => {});
    api
      .get<{ id: string; name: string; kind: string; user_id: string | null; is_active: boolean }[]>("/api/warehouses")
      .then((rows) => setSrcWarehouses(rows.filter((w) => w.is_active)))
      .catch(() => {});
    api.get<{ id: string }>("/api/auth/me").then((u) => setMeId(u.id)).catch(() => {});
  }, []);

  async function setOrderStatus(o: Order, status: "done" | "cancelled") {
    try {
      await api.patch(`/api/orders/${o.id}`, { status });
      toast(t(status === "done" ? "orders.markedDone" : "orders.markedCancelled"), "success");
      loadDue();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  useEffect(loadStock, [loadStock]);
  useEffect(loadCtx, [loadCtx]);
  useEffect(loadHistory, [loadHistory]);
  useEffect(loadDue, [loadDue]);

  // Offline-várólista ürítése betöltéskor és újracsatlakozáskor.
  const flushQueue = useCallback(async () => {
    let queue = readQueue();
    setPendingOffline(queue.length);
    if (queue.length === 0) return;
    let sent = 0;
    while (queue.length > 0) {
      const item = queue[0];
      try {
        await api.post("/api/settlements", {
          partner_id: item.partner_id,
          payment_method: item.payment_method,
          no_vat: item.no_vat ?? false,
          lines: item.lines,
          machines: item.machines ?? [],
          handovers: item.handovers ?? [],
          paid_amount: item.paid_amount ?? null,
          note: item.note,
        });
        sent += 1;
        queue = queue.slice(1);
        writeQueue(queue);
      } catch (err) {
        if (err instanceof ApiError) {
          // a szerver elutasította (pl. törölt partner) — eldobjuk, jelezzük
          toast(t("offline.dropped", { name: item.partner_name, reason: errorMessage(err) }), "error");
          queue = queue.slice(1);
          writeQueue(queue);
        } else {
          break; // még mindig nincs hálózat — később újra
        }
      }
    }
    setPendingOffline(queue.length);
    if (sent > 0) {
      toast(t("offline.flushed", { count: sent }), "success");
      loadStock();
      loadHistory();
      loadDue();
    }
  }, [toast, t, loadStock, loadHistory, loadDue]);

  useEffect(() => {
    flushQueue();
    const onOnline = () => flushQueue();
    window.addEventListener("online", onOnline);
    return () => window.removeEventListener("online", onOnline);
  }, [flushQueue]);

  // Gép-soros élő előnézet: lefőzött adag (számláló-különbség) − szerviz,
  // kedvezménnyel árazva; termékenként összegződik a leltár-táblához.
  const machinePreview = useMemo(() => {
    const billedByProduct: Record<string, number> = {};
    const amountByProduct: Record<string, number> = {};
    if (!ctx) return { rows: [], billedByProduct, amountByProduct };
    const rows = ctx.machines.map((m) => {
      const inp = machineInputs[m.asset_id];
      let newCounter: number | null = null;
      if (m.counter_count > 1) {
        const anyFilled = inp?.newCounters.some((v) => v !== "");
        newCounter = anyFilled
          ? inp.newCounters.reduce((a, v) => a + (Number(v) || 0), 0)
          : null;
      } else if (inp && inp.newCounter !== "") {
        newCounter = Number(inp.newCounter);
      }
      const filled = newCounter !== null && Number.isFinite(newCounter);
      const belowPrev = filled && (newCounter as number) < m.prev_counter;
      const brewed = filled ? Math.max((newCounter as number) - m.prev_counter, 0) : 0;
      const service = Number(inp?.service) || 0;
      const billed = Math.max(brewed - service, 0);
      const pid = machineProducts[m.asset_id] || m.default_product_id || ctx.single_product_id;
      const stockRow = pid ? stockRows.find((x) => x.product_id === pid) : undefined;
      const autoPrice = stockRow
        ? stockRow.price_per_portion
        : (pid ? products.find((x) => x.id === pid)?.price_per_portion ?? null : null);
      const manual = machinePrices[m.asset_id] ?? "";
      const price = manual !== "" ? Number(manual) : autoPrice;
      const amount = filled && price !== null ? billed * price : 0;
      if (filled && pid) {
        billedByProduct[pid] = (billedByProduct[pid] ?? 0) + billed;
        amountByProduct[pid] = (amountByProduct[pid] ?? 0) + amount;
      }
      return { ...m, filled, newCounter, brewed, billed, price, autoPrice, amount, belowPrev };
    });
    return { rows, billedByProduct, amountByProduct };
  }, [ctx, machineInputs, machineProducts, machinePrices, stockRows, products]);

  // Kedvezményes elszámolás: mindent-vagy-semmit kapcsoló — az egész
  // elszámolás ÁFA és Billingó-számla nélkül megy (a nettó a fizetendő).
  const [discountAll, setDiscountAll] = useState(false);
  const noVat = discountAll;

  // A következő kitöltendő számláló-mezőre ugrás (gomb + Enter a mezőkben).
  function jumpToNextCounter(fromEl?: HTMLElement | null) {
    const els = Array.from(
      document.querySelectorAll<HTMLInputElement>("input[data-counter-input]"),
    );
    if (!els.length) return;
    let start = 0;
    if (fromEl) {
      const i = els.indexOf(fromEl as HTMLInputElement);
      start = i >= 0 ? i + 1 : 0;
    }
    const next = els.slice(start).find((el) => el.value === "") ?? els[start] ?? null;
    if (next) {
      next.focus();
      next.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }

  // Élő fogyás-előnézet a beírt leltár alapján — a gép-sorok által lefedett
  // termékeknél a számlázott adag/összeg a gépekből jön.
  const preview = useMemo(() => {
    let net = 0;
    const rows = stockRows.map((s) => {
      const phys = physical[s.product_id] ?? "";
      const physNum = phys === "" ? null : Number(phys);
      const consumed = physNum === null ? 0 : Math.max(s.quantity - physNum, 0);
      const machineBilled = machinePreview.billedByProduct[s.product_id];
      // Ha a gép számlálója meg van adva, a számlázott adag AZ — a kg-fogyás
      // keresztellenőrzés (a hiányt a mentés kg-áron külön sorban számolja).
      const counterStr = counters[s.product_id] ?? "";
      const counterNum = counterStr === "" ? null : Number(counterStr);
      const manual = linePrices[s.product_id] ?? "";
      const unitPrice = manual !== "" ? Number(manual) : s.price_per_portion;
      let portions: number;
      let amount: number;
      if (machineBilled !== undefined) {
        portions = machineBilled;
        amount = machinePreview.amountByProduct[s.product_id] ?? 0;
      } else if (counterNum !== null) {
        portions = counterNum;
        amount = portions * unitPrice;
      } else {
        portions = s.grams_per_portion > 0 ? (consumed * 1000) / s.grams_per_portion : 0;
        amount = portions * unitPrice;
      }
      net += amount;
      return {
        ...s, consumed, portions, amount,
        filled: physNum !== null || machineBilled !== undefined,
        fromMachines: machineBilled !== undefined,
      };
    });
    // Gép-termék készlet-sor nélkül (ritka): az összegbe így is beszámít
    for (const [pid, amount] of Object.entries(machinePreview.amountByProduct)) {
      if (!stockRows.some((s) => s.product_id === pid)) net += amount;
    }
    return { rows, net };
  }, [stockRows, physical, counters, machinePreview]);

  // Átadott NEM-bizományos áruk: azonnal fizetendők — csak a kávé bizomány.
  const handoverSale = useMemo(() => {
    let net = 0;
    const items: { product_id: string; name: string; qty: number; unit: string; unitPrice: number; amount: number }[] = [];
    for (const s of stockRows) {
      const h = handovers[s.product_id];
      if (!h || h.qty === "" || Number(h.qty) <= 0) continue;
      const p = products.find((x) => x.id === s.product_id);
      if (!p || p.is_consignment) continue;
      const unitPrice = (h.price ?? "") !== "" ? Number(h.price) : s.price_per_portion;
      const amount = Number(h.qty) * unitPrice;
      net += amount;
      items.push({ product_id: s.product_id, name: s.product_name, qty: Number(h.qty), unit: s.unit, unitPrice, amount });
    }
    return { net, items };
  }, [stockRows, handovers, products]);

  // Becsült fizetendő: nettó × ÁFA + korábbi tartozás (a szerződéses tételeket
  // — bérleti díj, minimum — a mentés számolja hozzá). Az átadott eladási
  // áruk azonnal fizetendők, ezért az összesítőbe beleszámítanak.
  const grossEstimate = Math.round((preview.net + handoverSale.net) * (noVat ? 1 : 1.27));
  const totalPayable = grossEstimate + Math.round(ctx?.debt ?? 0);

  // Partnerenkénti minimum készlet (riasztási küszöb) beállítása egy termékre.
  async function setStockMin(s: Stock) {
    const answer = await prompt(
      t("cons.minStockPrompt", { product: s.product_name }),
      { type: "number", initial: s.min_quantity != null ? String(s.min_quantity) : "" },
    );
    if (answer === null && s.min_quantity == null) return; // mégse, nem volt beállítva
    try {
      await api.patch(`/api/partners/${partnerId}/stock/min`, {
        product_id: s.product_id,
        min_quantity: answer === null || answer === "" ? null : Number(answer),
      });
      toast(t("cons.minStockSaved"), "success");
      loadStock();
      loadDue();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  // ── Kiszállítás (digitális szállítólevél): áru elszámolás nélkül — a
  // következő elszámolás betölti és kiszámlázza.
  const [delivery, setDelivery] = useState<{
    source_warehouse_id: string;
    note: string;
    lines: { product_id: string; quantity: string; unit_price: string }[];
  } | null>(null);
  const [deliveryBusy, setDeliveryBusy] = useState(false);
  const [deliveryError, setDeliveryError] = useState<string | null>(null);

  function openDelivery() {
    setDeliveryError(null);
    setDelivery({
      source_warehouse_id: defaultVanId(),
      note: "",
      lines: [{ product_id: "", quantity: "", unit_price: "" }],
    });
  }

  async function saveDelivery(e: React.FormEvent) {
    e.preventDefault();
    if (!delivery || !partnerId) return;
    const lines = delivery.lines.filter((l) => l.product_id && Number(l.quantity) > 0);
    if (!lines.length) {
      setDeliveryError(t("delivery.needLine"));
      return;
    }
    setDeliveryBusy(true);
    setDeliveryError(null);
    try {
      const res = await api.post<{ serial: string; total_net: number }>("/api/deliveries", {
        partner_id: partnerId,
        source_warehouse_id: delivery.source_warehouse_id || null,
        note: delivery.note || null,
        lines: lines.map((l) => ({
          product_id: l.product_id,
          quantity: Number(l.quantity),
          unit_price: Number(l.unit_price) || 0,
        })),
      });
      toast(t("delivery.created", { serial: res.serial }), "success");
      setDelivery(null);
      loadCtx();
    } catch (err) {
      setDeliveryError(errorMessage(err));
    } finally {
      setDeliveryBusy(false);
    }
  }

  async function doReplenish(e: React.FormEvent) {
    e.preventDefault();
    if (!replenish || !partnerId) return;
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/partners/${partnerId}/stock/replenish`, {
        product_id: replenish.product_id,
        quantity: Number(replenish.quantity),
        unit_cost: replenish.unit_cost === "" ? null : Number(replenish.unit_cost),
        source_warehouse_id: replenish.source_warehouse_id || null,
      });
      setReplenish(null);
      loadStock();
      loadDue(); // a készlet-riasztások frissülnek
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function createSettlement() {
    if (!partnerId) return;
    const lines = stockRows
      .filter((s) => (physical[s.product_id] ?? "") !== "")
      .map((s) => ({
        product_id: s.product_id,
        physical_qty: Number(physical[s.product_id]),
        counter_portions:
          (counters[s.product_id] ?? "") === "" ? null : Number(counters[s.product_id]),
        price_per_portion:
          (linePrices[s.product_id] ?? "") === "" ? null : Number(linePrices[s.product_id]),
      }));
    // Gép-sorok: csak a kitöltött (új számlálós) gépek kerülnek be
    if (machinePreview.rows.some((r) => r.belowPrev)) {
      toast(t("cons.machineBelowPrev"), "error");
      return;
    }
    const machines: MachinePayload[] = machinePreview.rows
      .filter((r) => r.filled)
      .map((r) => ({
        asset_id: r.asset_id,
        new_counter: r.newCounter as number,
        new_counters:
          r.counter_count > 1
            ? machineInputs[r.asset_id].newCounters.map((v) => Number(v) || 0)
            : undefined,
        service_portions: Number(machineInputs[r.asset_id]?.service) || 0,
        discount_pct: 0, // a Kedv. pipa nem árcsökkentés: no_vat-ként érvényesül
        product_id: machineProducts[r.asset_id] || null,
        price_per_portion:
          (machinePrices[r.asset_id] ?? "") === "" ? null : Number(machinePrices[r.asset_id]),
      }));
    const handoverList: HandoverPayload[] = stockRows
      .filter((s) => (handovers[s.product_id]?.qty ?? "") !== "" && Number(handovers[s.product_id].qty) > 0)
      .map((s) => ({
        product_id: s.product_id,
        quantity: Number(handovers[s.product_id].qty),
        unit_cost: handovers[s.product_id].cost === "" ? null : Number(handovers[s.product_id].cost),
        price: (handovers[s.product_id].price ?? "") === "" ? null : Number(handovers[s.product_id].price),
      }));
    if (lines.length === 0 && machines.length === 0) return;
    const paid = paidAmount.trim() === "" ? null : Number(paidAmount);
    const payload = {
      partner_id: partnerId,
      payment_method: payment,
      no_vat: noVat,
      lines,
      machines,
      handovers: handoverList,
      paid_amount: paid,
      note: note || null,
    };
    setBusy(true);
    try {
      const res = await api.post<Settlement & { debt_before?: number; paid_amount?: number | null }>(
        "/api/settlements", payload,
      );
      toast(t("cons.settlementSaved", { gross: res.total_gross.toLocaleString("hu-HU") }), "success");
      setNote("");
      setPaidAmount("");
      loadStock();
      loadCtx();
      loadHistory();
      loadDue();
      setSigning(res); // elszámolás után rögtön aláírathatjuk a partnerrel
      setSignature(null);
    } catch (err) {
      if (!(err instanceof ApiError)) {
        // hálózati hiba (offline): várólistára tesszük, később beküldjük
        const queue = readQueue();
        queue.push({
          partner_id: partnerId,
          partner_name: partners.find((p) => p.id === partnerId)?.name ?? "?",
          payment_method: payment,
          no_vat: noVat,
          lines,
          machines,
          handovers: handoverList,
          paid_amount: paid,
          note: note || null,
          queued_at: new Date().toISOString(),
        });
        writeQueue(queue);
        setPendingOffline(queue.length);
        setNote("");
        setPaidAmount("");
        setPhysical(Object.fromEntries(stockRows.map((x) => [x.product_id, ""])));
        toast(t("offline.queued"), "info");
      } else {
        toast(errorMessage(err), "error");
      }
    } finally {
      setBusy(false);
    }
  }

  function toggleSelect(id: string) {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function bulkDelete() {
    if (selected.size === 0) return;
    if (!(await confirm(t("cons.deleteConfirm", { count: selected.size })))) return;
    try {
      const res = await api.post<{ deleted: number; blocked: { name: string; code: string }[] }>(
        "/api/settlements/bulk-delete",
        { ids: [...selected] },
      );
      toast(t("bulk.deleted", { count: res.deleted }), "success");
      if (res.blocked.length > 0) {
        const reasons = res.blocked
          .map((b) => `${b.name}: ${t(`errors.${b.code}`)}`)
          .join("\n");
        toast(t("bulk.blocked", { count: res.blocked.length, reasons }), "error");
      }
      setSelected(new Set());
      loadHistory();
      loadStock(); // a törlés visszaállítja a készletet
      loadDue();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function invoice(s: Settlement) {
    try {
      const res = await api.post<Settlement>(`/api/settlements/${s.id}/invoice`);
      toast(t("cons.invoiceOk", { mode: res.billingo_status ?? "?" }), "success");
      loadHistory();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function downloadReceipt(s: Settlement) {
    try {
      const day = s.created_at.slice(0, 10).replaceAll("-", "");
      await downloadFile(`/api/settlements/${s.id}/pdf`, `ELSZ-${day}-${s.id.slice(0, 8).toUpperCase()}.pdf`);
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function saveSignature() {
    if (!signing || !signature) return;
    setBusy(true);
    try {
      await api.post(`/api/settlements/${signing.id}/signature`, {
        signature,
        partner_email: signEmail.trim() || null,
      });
      toast(t("cons.signatureSaved"), "success");
      setSigning(null);
      setSignature(null);
      setSignEmail("");
      loadHistory();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function openPrices() {
    if (!partnerId) return;
    try {
      const rows = await api.get<PartnerPrice[]>(`/api/partners/${partnerId}/prices`);
      setPrices(rows);
      setPriceEdits(
        Object.fromEntries(rows.map((r) => [r.product_id, r.price_per_portion?.toString() ?? ""])),
      );
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function savePrices() {
    if (!prices || !partnerId) return;
    setBusy(true);
    try {
      for (const row of prices) {
        const raw = (priceEdits[row.product_id] ?? "").trim();
        const hadOverride = row.price_per_portion !== null;
        if (raw === "") {
          if (hadOverride) await api.delete(`/api/partners/${partnerId}/prices/${row.product_id}`);
        } else {
          const value = Number(raw);
          if (!Number.isFinite(value) || value < 0) continue;
          if (!hadOverride || value !== row.price_per_portion) {
            await api.put(`/api/partners/${partnerId}/prices/${row.product_id}`, {
              price_per_portion: value,
            });
          }
        }
      }
      toast(t("prices.saved"), "success");
      setPrices(null);
      loadStock(); // az érvényes árak frissülnek
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function emailReceipt(s: Settlement) {
    const partnerEmail = partners.find((p) => p.id === s.partner_id)?.contact_email ?? "";
    const to = await prompt(t("cons.emailPrompt"), { type: "email", initial: partnerEmail });
    if (to === null) return;
    try {
      await api.post(`/api/settlements/${s.id}/receipt-email`, { to: to.trim() });
      toast(t("cons.emailSent"), "success");
      loadHistory();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  const activeProducts = products.filter((p) => p.is_active);

  // Kézzel felvett termék-sor eltávolítása (a beírt értékeivel együtt).
  function removeExtraProduct(pid: string) {
    setExtraProducts((xs) => xs.filter((x) => x !== pid));
    setPhysical((p) => ({ ...p, [pid]: "" }));
    setCounters((c) => ({ ...c, [pid]: "" }));
    setHandovers((h) => ({ ...h, [pid]: { qty: "", cost: "" } }));
  }

  // Valódi készlet-sor törlése a partner raktárából (a mennyiség kivezetésével).
  async function deleteStockRow(s: Stock) {
    if (!(await confirm(t("cons.stockRowDeleteConfirm", {
      product: s.product_name, qty: s.quantity, unit: s.unit,
    })))) return;
    try {
      await api.delete(`/api/partners/${partnerId}/stock/${s.product_id}`);
      toast(t("cons.stockRowDeleted"), "success");
      loadStock();
      loadDue();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  // Készlet-visszavét a partnertől a képviselő raktárába/autójába:
  // product_id "" = a TELJES készlet (szerződés-lezárás), különben egy termék.
  async function doStockReturn() {
    if (!stockReturn || !stockReturn.warehouse_id) return;
    const isAll = stockReturn.product_id === "";
    if (isAll) {
      const whName = srcWarehouses.find((w) => w.id === stockReturn.warehouse_id)?.name ?? "?";
      if (!(await confirm(t("cons.returnAllConfirm", { warehouse: whName })))) return;
    }
    setBusy(true);
    try {
      const res = await api.post<{ returned: { product_name: string; quantity: number; unit: string }[]; warehouse_name: string }>(
        `/api/partners/${partnerId}/stock/return`,
        isAll
          ? { target_warehouse_id: stockReturn.warehouse_id, all: true }
          : {
              target_warehouse_id: stockReturn.warehouse_id,
              items: [{ product_id: stockReturn.product_id, quantity: Number(stockReturn.quantity) }],
            }
      );
      toast(
        t("cons.returnDone", {
          items: res.returned.map((r) => `${r.product_name}: ${r.quantity} ${r.unit}`).join(", "),
          warehouse: res.warehouse_name,
        }),
        "success"
      );
      setStockReturn(null);
      loadStock();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  function openStockReturn(productId: string, qty?: number) {
    setStockReturn({
      product_id: productId,
      quantity: qty != null ? String(qty) : "",
      warehouse_id: defaultVanId(),
    });
  }

  // Partner kiválasztásakor az elszámolás-munkaterület azonnal fókuszba kerül:
  // a lista-panelek (riasztás/rendelés/esedékes) eltűnnek, a nézet felugrik.
  useEffect(() => {
    if (partnerId) window.scrollTo({ top: 0, behavior: "smooth" });
  }, [partnerId]);

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("cons.settlementTitle")}</h1>
        {pendingOffline > 0 && (
          <button
            onClick={flushQueue}
            title={t("offline.retryHint")}
            className="rounded-full bg-amber-100 px-3 py-1 text-xs font-medium text-amber-800 hover:bg-amber-200"
          >
            {t("offline.pending", { count: pendingOffline })}
          </button>
        )}
        <PartnerPicker
          partners={partners}
          value={partnerId}
          onChange={setPartnerId}
          className="w-80 max-w-full"
        />
        {partnerId && (
          <button
            onClick={() => { setError(null); setReplenish({ product_id: activeProducts[0]?.id ?? "", quantity: "", unit_cost: activeProducts[0]?.purchase_price?.toString() ?? "", source_warehouse_id: defaultVanId() }); }}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm hover:bg-slate-100"
          >
            {t("cons.replenish")}
          </button>
        )}
        {partnerId && (
          <button
            onClick={openDelivery}
            className="rounded-lg border border-sky-300 bg-sky-50 px-4 py-2 text-sm font-medium text-sky-700 hover:bg-sky-100"
          >
            🚚 {t("delivery.new")}
          </button>
        )}
        {partnerId && stock.length > 0 && (
          <button
            onClick={() => openStockReturn("")}
            title={t("cons.returnBtnHint")}
            className="rounded-lg border border-amber-300 bg-white px-4 py-2 text-sm text-amber-700 hover:bg-amber-50"
          >
            ↩ {t("cons.returnBtn")}
          </button>
        )}
        {partnerId && canPrices && (
          <button
            onClick={openPrices}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm hover:bg-slate-100"
          >
            {t("prices.button")}
          </button>
        )}
      </div>

      {!partnerId && lowStock.length > 0 && (
        <div className="mb-6 rounded-2xl border border-rose-200 bg-rose-50 p-4 shadow-sm">
          <p className="mb-2 font-semibold text-rose-900">{t("lowStock.title", { count: lowStock.length })}</p>
          <div className="flex flex-wrap gap-2">
            {lowStock.map((r) => (
              <button
                key={`${r.partner_id}-${r.product_id}`}
                onClick={() => setPartnerId(r.partner_id)}
                title={t("lowStock.hint", { threshold: r.threshold, unit: r.unit })}
                className="rounded-full border border-rose-300 bg-white px-3 py-1 text-xs text-rose-800 hover:bg-rose-100"
              >
                {r.partner_name} · {r.product_name}: <span className="font-semibold">{r.quantity} {r.unit}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {!partnerId && orders.length > 0 && (
        <div className="mb-6 rounded-2xl border border-emerald-200 bg-emerald-50 shadow-sm">
          <p className="px-4 py-3 font-semibold text-emerald-900">{t("orders.title", { count: orders.length })}</p>
          <div className="overflow-x-auto border-t border-emerald-200">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-emerald-700">
                  <th className="px-4 py-2">{t("orders.orderNo")}</th>
                  <th className="px-4 py-2">{t("cons.partner")}</th>
                  <th className="px-4 py-2">{t("orders.items")}</th>
                  <th className="px-4 py-2">{t("orders.contact")}</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.id} className="border-t border-emerald-100 align-top">
                    <td className="px-4 py-2 font-mono text-xs text-emerald-900">
                      {o.order_no}
                      <div className="font-sans text-emerald-700">{fmt(o.created_at)}</div>
                    </td>
                    <td className="px-4 py-2 font-medium text-emerald-950">{o.partner_label ?? "—"}</td>
                    <td className="px-4 py-2 text-emerald-900">
                      {o.items.map((it, i) => (
                        <div key={i}>{it.name}: <span className="font-semibold">{it.quantity} {it.unit}</span></div>
                      ))}
                      {o.note && <div className="text-xs text-emerald-700">„{o.note}”</div>}
                    </td>
                    <td className="px-4 py-2 text-emerald-800">
                      {o.contact_name ?? "—"}
                      {o.contact_phone && <div className="text-xs">{o.contact_phone}</div>}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        <button
                          onClick={() => setOrderStatus(o, "done")}
                          title={t("orders.markDone")}
                          className="rounded border border-emerald-400 px-2 py-1 text-sm leading-none hover:bg-emerald-100"
                        >
                          ✅
                        </button>
                        <button
                          onClick={() => setOrderStatus(o, "cancelled")}
                          title={t("orders.cancel")}
                          className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100"
                        >
                          🚫
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!partnerId && due.length > 0 && (
        <div className="mb-6 rounded-2xl border border-amber-200 bg-amber-50 shadow-sm">
          <button
            onClick={() => setShowDue((v) => !v)}
            className="flex w-full items-center justify-between px-4 py-3 text-left"
          >
            <span className="font-semibold text-amber-900">
              {t("cons.dueTitle", { count: due.length })}
            </span>
            <span className="text-amber-700">{showDue ? "▾" : "▸"}</span>
          </button>
          {showDue && (
            <div className="border-t border-amber-200">
              <div className="flex flex-wrap items-center gap-2 px-4 py-2">
                <select
                  value={dueCity}
                  onChange={(e) => setDueCity(e.target.value)}
                  className="rounded-lg border border-amber-300 bg-white px-2 py-1.5 text-xs"
                >
                  <option value="">{t("route.allCities")}</option>
                  {dueCities.map(([city, count]) => (
                    <option key={city} value={city}>{city} ({count})</option>
                  ))}
                </select>
                {routeSelected.size > 0 && (
                  <>
                    {routeSelected.size <= 10 && (
                      <button
                        onClick={openRoute}
                        className="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-700"
                      >
                        🗺 {t("route.open", { count: routeSelected.size })}
                      </button>
                    )}
                    {routeSelected.size >= 2 && (
                      <span className="flex items-center gap-1">
                        <select
                          value={weekDays}
                          onChange={(e) => setWeekDays(Number(e.target.value))}
                          className="rounded-lg border border-amber-300 bg-white px-2 py-1.5 text-xs"
                          title={t("route.weekDays")}
                        >
                          {[2, 3, 4, 5, 6].map((n) => (
                            <option key={n} value={n}>{t("route.weekDaysOpt", { n })}</option>
                          ))}
                        </select>
                        <button
                          onClick={planWeek}
                          className="rounded-lg bg-amber-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-800"
                        >
                          📅 {t("route.weekPlan")}
                        </button>
                      </span>
                    )}
                    <button
                      onClick={() => { setRouteSelected(new Set()); setWeekPlan(null); }}
                      className="rounded-lg border border-amber-300 px-2 py-1.5 text-xs text-amber-800 hover:bg-amber-100"
                    >
                      ✕
                    </button>
                  </>
                )}
                <span className="text-xs text-amber-700">{t("route.hint")}</span>
              </div>
              {weekPlan && (
                <div className="border-t border-amber-200 px-4 py-3">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-sm font-semibold text-amber-900">
                      {t("route.weekPlanTitle", { km: weekPlan.total_km ?? 0 })}
                      {weekPlan.used_roads && (
                        <span className="ml-2 rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-800">
                          {t("route.roadBased")}
                        </span>
                      )}
                    </span>
                    <button
                      onClick={() => setWeekPlan(null)}
                      className="text-xs text-amber-700 hover:underline"
                    >
                      ✕ {t("common.close")}
                    </button>
                  </div>
                  <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                    {weekPlan.days.map((day, i) => (
                      <div key={i} className="rounded-xl border border-amber-200 bg-white p-3">
                        <div className="mb-2 flex items-center justify-between">
                          <span className="text-sm font-semibold text-amber-900">
                            {t("route.dayN", { n: i + 1 })}
                          </span>
                          <span className="text-xs text-amber-700">
                            {day.total_km !== null ? `${day.total_km} km` : ""}
                          </span>
                        </div>
                        <ol className="mb-2 list-inside list-decimal space-y-0.5 text-xs text-slate-700">
                          {day.stops.map((s) => (
                            <li key={s.partner_id}>
                              {s.name}
                              {!s.located && (
                                <span className="ml-1 text-amber-600">{t("route.noCoords")}</span>
                              )}
                            </li>
                          ))}
                        </ol>
                        {day.stops.some((s) => s.address) && (
                          <button
                            onClick={() => openDayRoute(day)}
                            className="rounded-lg bg-amber-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-amber-700"
                          >
                            🗺 {t("route.openDay")}
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-amber-700">
                    <th className="px-2 py-2"></th>
                    <th className="px-4 py-2">{t("cons.partner")}</th>
                    <th className="px-4 py-2">{t("cons.dueLast")}</th>
                    <th className="px-4 py-2">{t("cons.dueDays")}</th>
                    <th className="px-4 py-2">{t("route.forecast")}</th>
                    <th className="px-4 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {dueFiltered.map((d) => (
                    <tr key={d.partner_id} className="border-t border-amber-100">
                      <td className="px-2 py-2">
                        {d.address && (
                          <input
                            type="checkbox"
                            checked={routeSelected.has(d.partner_id)}
                            onChange={() => toggleRoute(d.partner_id)}
                            title={t("route.addStop")}
                            className="h-4 w-4"
                          />
                        )}
                      </td>
                      <td className="px-4 py-2 font-medium text-amber-950">
                        <button
                          onClick={() => setInfoPartner(d.partner_id)}
                          className="text-left hover:underline"
                        >
                          {d.name}
                        </button>
                        {d.partner_code && <span className="ml-2 text-xs text-amber-600">{d.partner_code}</span>}
                        {d.city && <div className="text-xs font-normal text-amber-700">{d.city}</div>}
                      </td>
                      <td className="px-4 py-2 text-amber-800">
                        {d.last_settlement_at ? fmt(d.last_settlement_at) : t("cons.dueNever")}
                      </td>
                      <td className="px-4 py-2 text-amber-800">
                        {d.days_since !== null ? t("cons.dueDaysAgo", { days: d.days_since }) : "—"}
                        <div className="text-xs text-amber-600">
                          {t("cons.dueEveryWeeks", { weeks: d.interval_weeks })}
                          {d.next_due && <> · {new Date(d.next_due).toLocaleDateString(lang === "hu" ? "hu-HU" : "en-GB", { month: "short", day: "numeric", weekday: "short" })}</>}
                        </div>
                      </td>
                      <td className="px-4 py-2 text-amber-800">
                        {d.days_left !== null && d.days_left !== undefined ? (
                          <>
                            <span className={d.days_left <= 7 ? "font-semibold text-rose-800" : ""}>
                              {t("route.daysLeft", { days: d.days_left })}
                            </span>
                            {d.suggested_kg != null && d.suggested_kg > 0 && (
                              <div className="text-xs">{t("route.bring", { kg: d.suggested_kg })}</div>
                            )}
                          </>
                        ) : (
                          <span className="text-xs text-amber-600">{d.stock_products > 0 ? `${d.stock_products} ${t("route.stockShort")}` : "—"}</span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-right">
                        <button
                          onClick={() => setPartnerId(d.partner_id)}
                          className="rounded bg-amber-600 px-3 py-1 text-xs font-medium text-white hover:bg-amber-700"
                        >
                          {t("cons.dueSettle")}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            </div>
          )}
        </div>
      )}

      {partnerId && ctx && (
        <div className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-1 rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-sm shadow-sm">
          <span className="text-slate-500">
            {t("cons.lastVisit")}:{" "}
            <span className="font-medium text-slate-700">
              {ctx.last_visit ? fmt(ctx.last_visit) : t("cons.dueNever")}
            </span>
          </span>
          <span className="text-slate-500">
            {t("cons.activeContracts")}:{" "}
            <span className="font-medium text-slate-700">{ctx.active_contracts}</span>
          </span>
          <span className="text-slate-500">
            {t("cons.debt")}:{" "}
            {ctx.debt > 0.5 ? (
              <button
                type="button"
                onClick={() => setShowDebt((v) => !v)}
                title={t("cons.debtDetailHint")}
                className="font-semibold text-rose-600 underline decoration-rose-300 underline-offset-2 hover:text-rose-800"
              >
                {ft(Math.round(ctx.debt))} {showDebt ? "▲" : "▼"}
              </button>
            ) : (
              <span className="font-semibold text-emerald-600">{ft(Math.round(ctx.debt))}</span>
            )}
          </span>
          <button
            type="button"
            onClick={openDelivery}
            className="ml-auto rounded-lg border border-sky-300 px-3 py-1.5 text-xs font-medium text-sky-700 hover:bg-sky-50"
          >
            🚚 {t("delivery.new")}
          </button>
        </div>
      )}

      {/* Tartozás tételesen: melyik elszámolásból, mikor, mennyi maradt */}
      {partnerId && ctx && showDebt && ctx.debt_items.length > 0 && (
        <div className="mb-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm shadow-sm">
          <div className="mb-1.5 font-semibold text-rose-800">{t("cons.debtDetailTitle")}</div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left uppercase text-rose-400">
                <th className="py-1 pr-3">{t("cons.debtDate")}</th>
                <th className="py-1 pr-3 text-right">{t("cons.totalGross")}</th>
                <th className="py-1 pr-3 text-right">{t("cons.paid")}</th>
                <th className="py-1 pr-3 text-right">{t("cons.debtRemaining")}</th>
                <th className="py-1">{t("cons.debtDue")}</th>
              </tr>
            </thead>
            <tbody>
              {ctx.debt_items.map((d) => (
                <tr key={d.settlement_id} className="border-t border-rose-100 text-rose-900">
                  <td className="py-1 pr-3">{fmt(d.created_at)}</td>
                  <td className="py-1 pr-3 text-right">{ft(Math.round(d.total_gross))}</td>
                  <td className="py-1 pr-3 text-right">{ft(Math.round(d.paid_amount))}</td>
                  <td className="py-1 pr-3 text-right font-semibold">{ft(Math.round(d.remaining))}</td>
                  <td className="py-1">
                    {d.due_date ?? "—"}
                    {d.due_date && new Date(d.due_date) < new Date() && (
                      <span className="ml-1 rounded bg-rose-200 px-1 py-0.5 text-[10px] font-semibold">
                        {t("cons.debtOverdue")}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {partnerId && ctx && ctx.open_deliveries > 0 && (
        <div className="mb-4 rounded-2xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900">
          🚚 {t("delivery.openBanner", {
            count: ctx.open_deliveries,
            net: Math.round(ctx.open_deliveries_net).toLocaleString("hu-HU"),
          })}
        </div>
      )}

      {partnerId && ctx && ctx.machines.length > 0 && (
        <div className="mb-6 overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="flex flex-wrap items-center gap-3 px-4 pt-3">
            <p className="text-sm font-semibold">{t("cons.machinesTitle")}</p>
            <button
              type="button"
              onClick={() => jumpToNextCounter()}
              className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700"
            >
              ⏭ {t("cons.nextCounter")}
            </button>
            <label className="ml-auto flex cursor-pointer items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={discountAll}
                onChange={(e) => setDiscountAll(e.target.checked)}
                className="h-4 w-4"
              />
              {t("cons.discountAll")}
            </label>
          </div>
          <p className="px-4 pb-2 text-xs text-slate-400">{t("cons.machinesHint")}</p>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
                <th className="px-4 py-2.5">{t("cons.machineBarcode")}</th>
                <th className="px-4 py-2.5">{t("cons.machineName")}</th>
                <th className="px-4 py-2.5">{t("cons.machinePrevAt")}</th>
                <th className="px-4 py-2.5">{t("cons.machinePrevCounter")}</th>
                <th className="px-4 py-2.5">{t("cons.machineNewCounter")}</th>
                <th className="px-4 py-2.5">{t("cons.machineBrewed")}</th>
                <th className="px-4 py-2.5">{t("cons.machineService")}</th>
                <th className="px-4 py-2.5 text-right">{t("cons.portionPrice")}</th>
                <th className="px-4 py-2.5 text-right">{t("cons.amountNet")}</th>
              </tr>
            </thead>
            <tbody>
              {machinePreview.rows.map((m) => (
                <tr key={m.asset_id} className="border-b border-slate-100 last:border-0">
                  <td className="px-4 py-2.5 font-mono text-xs">{m.barcode}</td>
                  <td className="px-4 py-2.5">
                    <div className="font-medium">{m.name}</div>
                    <SearchSelect
                      items={[
                        ...stock.map((s) => ({
                          id: s.product_id, label: s.product_name,
                          sublabel: t("cons.partnerStockGroup"),
                        })),
                        ...activeProducts
                          .filter((p) => !stock.some((s) => s.product_id === p.id))
                          .map((p) => ({ id: p.id, label: p.name, sublabel: p.category })),
                      ]}
                      value={machineProducts[m.asset_id] ?? ""}
                      onChange={(id) =>
                        setMachineProducts({ ...machineProducts, [m.asset_id]: id })
                      }
                      placeholder={t("cons.machineProductChoose")}
                      className="mt-0.5 max-w-52 text-xs"
                    />
                  </td>
                  <td className="px-4 py-2.5 text-xs text-slate-500">
                    {m.last_settled_at ? fmt(m.last_settled_at) : "—"}
                  </td>
                  <td className="px-4 py-2.5 tabular-nums text-slate-600">{m.prev_counter}</td>
                  <td className="px-4 py-2.5">
                    {m.counter_count > 1 ? (
                      <div className="flex gap-1">
                        {machineInputs[m.asset_id]?.newCounters.map((v, i) => (
                          <input
                            key={i}
                            type="number" min={0}
                            data-counter-input={`${m.asset_id}-${i}`}
                            value={v}
                            onChange={(e) => {
                              const inp = machineInputs[m.asset_id];
                              const next = [...inp.newCounters];
                              next[i] = e.target.value;
                              setMachineInputs({ ...machineInputs, [m.asset_id]: { ...inp, newCounters: next } });
                            }}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") { e.preventDefault(); jumpToNextCounter(e.currentTarget); }
                            }}
                            placeholder={String(m.counters?.[i] ?? "")}
                            className="w-20 rounded-lg border border-slate-300 px-2 py-1.5"
                          />
                        ))}
                      </div>
                    ) : (
                      <input
                        type="number" min={0}
                        data-counter-input={m.asset_id}
                        value={machineInputs[m.asset_id]?.newCounter ?? ""}
                        onChange={(e) =>
                          setMachineInputs({
                            ...machineInputs,
                            [m.asset_id]: { ...machineInputs[m.asset_id], newCounter: e.target.value },
                          })
                        }
                        onKeyDown={(e) => {
                          if (e.key === "Enter") { e.preventDefault(); jumpToNextCounter(e.currentTarget); }
                        }}
                        placeholder={String(m.prev_counter)}
                        className={`w-24 rounded-lg border px-2 py-1.5 ${m.belowPrev ? "border-rose-400 bg-rose-50" : "border-slate-300"}`}
                      />
                    )}
                    {m.belowPrev && (
                      <div className="mt-0.5 text-xs text-rose-600">{t("cons.machineBelowPrev")}</div>
                    )}
                  </td>
                  <td className="px-4 py-2.5 tabular-nums">
                    {m.filled ? m.brewed : <span className="text-slate-300">—</span>}
                  </td>
                  <td className="px-4 py-2.5">
                    <input
                      type="number" min={0}
                      value={machineInputs[m.asset_id]?.service ?? ""}
                      onChange={(e) =>
                        setMachineInputs({
                          ...machineInputs,
                          [m.asset_id]: { ...machineInputs[m.asset_id], service: e.target.value },
                        })
                      }
                      placeholder="0"
                      title={t("cons.machineServiceHint")}
                      className="w-16 rounded-lg border border-slate-300 px-2 py-1.5"
                    />
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <input
                      type="number" min={0} step="0.01"
                      value={machinePrices[m.asset_id] ?? ""}
                      onChange={(e) =>
                        setMachinePrices({ ...machinePrices, [m.asset_id]: e.target.value })
                      }
                      placeholder={m.autoPrice !== null ? String(m.autoPrice) : "?"}
                      title={t("cons.priceOverrideHint")}
                      className={`w-20 rounded-lg border px-2 py-1.5 text-right tabular-nums ${(machinePrices[m.asset_id] ?? "") !== "" ? "border-amber-400 bg-amber-50" : "border-slate-300"}`}
                    />
                  </td>
                  <td className="px-4 py-2.5 text-right font-medium tabular-nums">
                    {m.filled ? (m.price !== null ? ft(Math.round(m.amount)) : "?") : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {partnerId && (
        <div className="mb-6 overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
                <th className="px-4 py-3">{t("cons.name")}</th>
                <th className="px-4 py-3">{t("prices.unitPrice")}</th>
                <th className="px-4 py-3">{t("cons.bookQty")}</th>
                <th className="px-4 py-3">{t("cons.portionsAvail")}</th>
                <th className="px-4 py-3">{t("cons.physicalQty")}</th>
                <th className="px-4 py-3">{t("cons.counterPortions")}</th>
                <th className="px-4 py-3">{t("cons.consumed")}</th>
                <th className="px-4 py-3">{t("cons.portions")}</th>
                <th className="px-4 py-3">{t("cons.amountNet")}</th>
                <th className="px-4 py-3">{t("cons.handover")}</th>
              </tr>
            </thead>
            <tbody>
              {preview.rows.map((s) => (
                <tr key={s.product_id} className="border-b border-slate-100 last:border-0">
                  <td className="px-4 py-3 font-medium">
                    {s.product_name}
                    <button
                      onClick={() =>
                        extraProducts.includes(s.product_id)
                          ? removeExtraProduct(s.product_id)
                          : deleteStockRow(s)
                      }
                      title={t("common.delete")}
                      className="ml-2 rounded px-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                    >
                      ✕
                    </button>
                  </td>
                  <td className="px-4 py-3">
                    <input
                      type="number" min={0} step="0.01"
                      value={linePrices[s.product_id] ?? ""}
                      onChange={(e) =>
                        setLinePrices({ ...linePrices, [s.product_id]: e.target.value })
                      }
                      placeholder={String(s.price_per_portion)}
                      title={t("cons.priceOverrideHint")}
                      className={`w-24 rounded-lg border px-2 py-1.5 tabular-nums ${(linePrices[s.product_id] ?? "") !== "" ? "border-amber-400 bg-amber-50" : "border-slate-300"}`}
                    />
                    {s.has_price_override && (
                      <span title={t("prices.overrideHint", { base: ft(s.base_price_per_portion) })} className="ml-1 text-xs text-indigo-600">*</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {s.quantity} {s.unit}
                    {s.quantity > 0 && !extraProducts.includes(s.product_id) && (
                      <button
                        onClick={() => openStockReturn(s.product_id, s.quantity)}
                        title={t("cons.returnRowHint")}
                        className="ml-2 rounded border border-amber-300 px-1.5 py-0.5 text-xs leading-none text-amber-700 hover:bg-amber-50"
                      >
                        ↩
                      </button>
                    )}
                    <button
                      onClick={() => setStockMin(s)}
                      title={t("cons.minStockHint")}
                      className={`ml-2 rounded border px-1.5 py-0.5 text-xs leading-none hover:bg-slate-100 ${s.min_quantity != null ? "border-amber-300 text-amber-700" : "border-slate-300 text-slate-400"}`}
                    >
                      ⚠ {s.min_quantity != null ? `${s.min_quantity} ${s.unit}` : "—"}
                    </button>
                  </td>
                  <td className="px-4 py-3 text-slate-500">{s.portions_available}</td>
                  <td className="px-4 py-3">
                    <input
                      type="number"
                      min={0}
                      step="0.01"
                      value={physical[s.product_id] ?? ""}
                      onChange={(e) => setPhysical({ ...physical, [s.product_id]: e.target.value })}
                      placeholder={`${s.quantity} ${s.unit}`}
                      className="w-28 rounded-lg border border-slate-300 px-2 py-1.5"
                    />
                  </td>
                  <td className="px-4 py-3">
                    {s.fromMachines ? (
                      <span
                        title={t("cons.fromMachinesHint")}
                        className="rounded bg-indigo-50 px-2 py-1 text-xs font-medium text-indigo-700"
                      >
                        ⚙ {s.portions.toFixed(0)}
                      </span>
                    ) : (
                      <input
                        type="number"
                        min={0}
                        step="1"
                        value={counters[s.product_id] ?? ""}
                        onChange={(e) => setCounters({ ...counters, [s.product_id]: e.target.value })}
                        placeholder="—"
                        title={t("cons.counterHint")}
                        className="w-24 rounded-lg border border-slate-300 px-2 py-1.5"
                      />
                    )}
                  </td>
                  <td className="px-4 py-3">{(physical[s.product_id] ?? "") !== "" ? `${s.consumed.toFixed(2)} ${s.unit}` : "—"}</td>
                  <td className="px-4 py-3">{s.filled ? s.portions.toFixed(0) : "—"}</td>
                  <td className="px-4 py-3 font-medium">{s.filled ? ft(Math.round(s.amount)) : "—"}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1">
                      <input
                        type="number"
                        min={0}
                        step="0.01"
                        value={handovers[s.product_id]?.qty ?? ""}
                        onChange={(e) =>
                          setHandovers({
                            ...handovers,
                            [s.product_id]: { ...(handovers[s.product_id] ?? { cost: "" }), qty: e.target.value },
                          })
                        }
                        placeholder="0"
                        title={t("cons.handoverHint")}
                        className="w-20 rounded-lg border border-slate-300 px-2 py-1.5"
                      />
                      <span className="text-xs text-slate-400">{s.unit}</span>
                    </div>
                    {/* Nem-bizományos termék: az átadás azonnal fizetendő —
                        egységár (felülírható) + sor-összeg */}
                    {(() => {
                      const p = products.find((x) => x.id === s.product_id);
                      const h = handovers[s.product_id];
                      if (!p || p.is_consignment || !h || h.qty === "" || Number(h.qty) <= 0) return null;
                      const unitPrice = (h.price ?? "") !== "" ? Number(h.price) : s.price_per_portion;
                      return (
                        <div className="mt-1 flex items-center gap-1 text-xs">
                          <span className="rounded bg-amber-100 px-1.5 py-0.5 font-medium text-amber-800">💰 {t("cons.handoverPayable")}</span>
                          <input
                            type="number"
                            min={0}
                            step="0.01"
                            value={h.price ?? ""}
                            onChange={(e) =>
                              setHandovers({
                                ...handovers,
                                [s.product_id]: { ...h, price: e.target.value },
                              })
                            }
                            placeholder={String(s.price_per_portion)}
                            title={t("cons.handoverPriceHint")}
                            className="w-20 rounded border border-amber-300 px-1.5 py-1"
                          />
                          <span className="font-semibold text-amber-900">= {ft(Math.round(Number(h.qty) * unitPrice))}</span>
                        </div>
                      );
                    })()}
                  </td>
                </tr>
              ))}
              {stockRows.length === 0 && (
                <tr><td colSpan={10} className="px-4 py-6 text-center text-slate-400">{t("cons.noStockAddBelow")}</td></tr>
              )}
            </tbody>
          </table>
          {/* Termék felvétele a leltárba — készlet-sor nélküli partnernél is */}
          <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 px-4 py-2.5">
            <SearchSelect
              items={activeProducts
                .filter((p) => !stockRows.some((s) => s.product_id === p.id))
                .map((p) => ({ id: p.id, label: p.name, sublabel: p.category, badge: p.unit }))}
              value={addProductId}
              onChange={setAddProductId}
              placeholder={t("cons.addProductChoose")}
              className="w-72 text-sm"
            />
            <button
              type="button"
              disabled={!addProductId}
              onClick={() => {
                if (!addProductId) return;
                setExtraProducts((xs) => [...xs, addProductId]);
                setAddProductId("");
              }}
              className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100 disabled:opacity-40"
            >
              {t("cons.addProduct")}
            </button>
            <span className="text-xs text-slate-400">{t("cons.addProductHint")}</span>
          </div>
          {(stockRows.length > 0 || (ctx?.machines.length ?? 0) > 0) && (
            <div className="space-y-3 border-t border-slate-200 px-4 py-3">
              {/* Összesítő: elszámolás + tartozás = összes fizetendő, fizetve */}
              {handoverSale.items.length > 0 && (
                <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-1 text-sm">
                  <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">💰 {t("cons.handoverSaleTitle")}</span>
                  {handoverSale.items.map((it) => (
                    <span key={it.product_id} className="text-amber-800">
                      {it.name}: {it.qty} {it.unit} × {ft(Math.round(it.unitPrice))} = <span className="font-semibold">{ft(Math.round(it.amount))}</span>
                    </span>
                  ))}
                </div>
              )}
              <div className="flex flex-wrap items-center justify-end gap-x-6 gap-y-1 text-sm">
                <span className="text-slate-500">
                  {t("cons.amountNet")}:{" "}
                  <span className="font-semibold text-slate-800">{ft(Math.round(preview.net + handoverSale.net))}</span>
                </span>
                <span className="text-slate-500">
                  {t("cons.grossEstimate")}:{" "}
                  <span className="font-semibold text-slate-800">{ft(grossEstimate)}</span>
                </span>
                {(ctx?.debt ?? 0) > 0.5 && (
                  <span className="text-slate-500">
                    {t("cons.debt")}:{" "}
                    <span className="font-semibold text-rose-600">{ft(Math.round(ctx!.debt))}</span>
                  </span>
                )}
                <span className="text-slate-600">
                  {t("cons.totalPayable")}:{" "}
                  <span className="text-base font-bold text-indigo-700">{ft(totalPayable)}</span>
                </span>
                <label className="flex items-center gap-1.5 text-slate-600">
                  {t("cons.paidAmount")}:
                  <input
                    type="number"
                    min={0}
                    value={paidAmount}
                    onChange={(e) => setPaidAmount(e.target.value)}
                    placeholder={t("cons.paidAmountPh")}
                    title={t("cons.paidAmountHint")}
                    className="w-32 rounded-lg border border-slate-300 px-2 py-1.5"
                  />
                  <span className="text-xs text-slate-400">Ft</span>
                </label>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <select
                  value={payment}
                  onChange={(e) => setPayment(e.target.value as (typeof PAYMENTS)[number])}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                >
                  {PAYMENTS.map((m) => (
                    <option key={m} value={m}>{t(`cons.payments.${m}`)}</option>
                  ))}
                </select>
                {noVat && (
                  <span className="rounded bg-amber-100 px-2 py-1 text-xs font-medium text-amber-800">
                    {t("cons.noVatBadge")}
                  </span>
                )}
                <input
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder={t("cons.notes")}
                  className="min-w-40 flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
                <button
                  onClick={createSettlement}
                  disabled={
                    busy ||
                    (stockRows.every((s) => physical[s.product_id] === "" || physical[s.product_id] === undefined) &&
                      machinePreview.rows.every((r) => !r.filled))
                  }
                  className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
                >
                  {busy ? t("common.saving") : t("cons.createSettlement")}
                </button>
              </div>
              <p className="text-xs text-slate-400">{t("cons.summaryHint")}</p>
            </div>
          )}
        </div>
      )}

      <div className="mb-2 flex items-center gap-3">
        <h2 className="font-semibold">{t("cons.history")}</h2>
        <select
          value={companyFilter}
          onChange={(e) => setCompanyFilter(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs"
        >
          <option value="">{t("partners.allCompanies")}</option>
          <option value="xp">{COMPANY_SHORT.xp}</option>
          <option value="pc">{COMPANY_SHORT.pc}</option>
        </select>
        {selected.size > 0 && (
          <button
            onClick={bulkDelete}
            className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700"
          >
            {t("bulk.deleteSelected", { count: selected.size })}
          </button>
        )}
      </div>
      <IconLegend
        items={[
          { icon: "📄", label: t("cons.receiptPdf") },
          { icon: "✍", label: t("cons.signBtn") },
          { icon: "✉", label: t("cons.emailBtn") },
          { icon: "✓", label: t("cons.legendDone") },
        ]}
      />
      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-slate-500">
              {canDelete && (
              <th className="sticky top-0 z-10 w-8 border-b border-slate-200 bg-white px-3 py-3">
                <input
                  type="checkbox"
                  checked={history.length > 0 && history.every((s) => selected.has(s.id))}
                  onChange={(e) =>
                    setSelected(e.target.checked ? new Set(history.map((s) => s.id)) : new Set())
                  }
                  className="h-4 w-4"
                />
              </th>
              )}
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("cons.date")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("cons.partner")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("cons.payment")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3 text-right">{t("cons.total")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {history.map((s) => (
              <tr key={s.id} className="border-b border-slate-100 last:border-0">
                {canDelete && (
                <td className="px-3 py-3">
                  <input
                    type="checkbox"
                    checked={selected.has(s.id)}
                    onChange={() => toggleSelect(s.id)}
                    className="h-4 w-4"
                  />
                </td>
                )}
                <td className="px-4 py-3 whitespace-nowrap">
                  {fmt(s.created_at)}
                  <div className="text-xs text-slate-400">
                    {s.previous_at
                      ? t("cons.prevSettlementAt", { date: fmt(s.previous_at) })
                      : t("cons.prevSettlementFirst")}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <button
                    onClick={() => setInfoPartner(s.partner_id)}
                    className="text-left font-medium text-indigo-700 hover:underline"
                  >
                    {s.partner_name}
                  </button>
                  <div className="flex items-center gap-1.5 text-xs text-slate-400">
                    <span>{s.settled_by_name}</span>
                    {s.invoicing_company && (
                      <span
                        title={COMPANIES[s.invoicing_company]}
                        className={`rounded px-1 py-0.5 text-[10px] font-medium ${COMPANY_CHIP[s.invoicing_company]}`}
                      >
                        {COMPANY_SHORT[s.invoicing_company]}
                      </span>
                    )}
                  </div>
                </td>
                <td className="px-4 py-3">{t(`cons.payments.${s.payment_method}`)}</td>
                <td className="px-4 py-3 text-right font-medium">
                  {ft(s.total_gross)}
                  {s.paid_amount !== null &&
                    s.total_gross + (s.debt_before ?? 0) - s.paid_amount > 0.5 && (
                    <div
                      title={t("cons.debtLeftHint")}
                      className="mt-0.5 text-xs font-semibold text-rose-600"
                    >
                      {t("cons.debtLeft", {
                        amount: Math.round(
                          s.total_gross + (s.debt_before ?? 0) - s.paid_amount,
                        ).toLocaleString("hu-HU"),
                      })}
                    </div>
                  )}
                </td>
                <td className="px-4 py-3 text-right">
                  <div className="flex items-center justify-end gap-1.5">
                    <button
                      onClick={() => downloadReceipt(s)}
                      title={t("cons.receiptPdf")}
                      className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100"
                    >
                      📄
                    </button>
                    {s.has_signature ? (
                      <span title={t("cons.signed")} className="rounded bg-slate-100 px-2 py-1 text-sm leading-none text-slate-600">✍✓</span>
                    ) : (
                      <button
                        onClick={() => { setSigning(s); setSignature(null); }}
                        title={t("cons.signBtn")}
                        className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100"
                      >
                        ✍
                      </button>
                    )}
                    <button
                      onClick={() => emailReceipt(s)}
                      title={s.receipt_sent_at ? t("cons.emailSentAt", { at: fmt(s.receipt_sent_at) }) : t("cons.emailBtn")}
                      className={`rounded border px-2 py-1 text-sm leading-none hover:bg-slate-100 ${s.receipt_sent_at ? "border-emerald-300 text-emerald-700" : "border-slate-300"}`}
                    >
                      ✉{s.receipt_sent_at ? "✓" : ""}
                    </button>
                    {s.no_vat ? (
                      <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                        {t("cons.noVatBadge")}
                      </span>
                    ) : s.invoiced ? (
                      <span className="rounded bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800">
                        {t("cons.invoiced")}{s.billingo_status ? ` (${s.billingo_status})` : ""}
                      </span>
                    ) : (
                      <button
                        onClick={() => invoice(s)}
                        className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-700"
                      >
                        {t("cons.invoiceBtn")}
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {history.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-10 text-center text-slate-400">{t("cons.noSettlements")}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {prices && (
        <div
          onMouseDown={(e) => { if (e.target === e.currentTarget) setPrices(null); }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
        >
          <div className="max-h-[90vh] w-full max-w-md space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{t("prices.title")}</h2>
            <p className="text-sm text-slate-500">{t("prices.hint")}</p>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-slate-500">
                  <th className="py-2">{t("cons.name")}</th>
                  <th className="py-2">{t("prices.basePrice")}</th>
                  <th className="py-2">{t("prices.partnerPrice")}</th>
                </tr>
              </thead>
              <tbody>
                {prices.map((row) => (
                  <tr key={row.product_id} className="border-t border-slate-100">
                    <td className="py-2 pr-2 font-medium">{row.product_name}</td>
                    <td className="py-2 pr-2 text-slate-500">{ft(row.base_price_per_portion)}</td>
                    <td className="py-2">
                      <input
                        type="number"
                        min={0}
                        step="0.01"
                        value={priceEdits[row.product_id] ?? ""}
                        onChange={(e) =>
                          setPriceEdits({ ...priceEdits, [row.product_id]: e.target.value })
                        }
                        placeholder={t("prices.defaultPh")}
                        className="w-28 rounded-lg border border-slate-300 px-2 py-1.5"
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setPrices(null)}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100"
              >
                {t("common.cancel")}
              </button>
              <button
                onClick={savePrices}
                disabled={busy}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {busy ? t("common.saving") : t("common.save")}
              </button>
            </div>
          </div>
        </div>
      )}

      {signing && (
        <div
          onMouseDown={(e) => { if (e.target === e.currentTarget) { setSigning(null); setSignature(null); } }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
        >
          <div className="max-h-[90vh] w-full max-w-md space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{t("cons.signTitle")}</h2>
            <p className="text-sm text-slate-600">
              {signing.partner_name} · {ft(signing.total_gross)} · {fmt(signing.created_at)}
            </p>
            <SignatureCanvas label={t("cons.signLabel")} onChange={setSignature} />
            {!partners.find((p) => p.id === signing.partner_id)?.contact_email && (
              <label className="block text-sm">
                {t("cons.signEmail")}
                <input
                  type="email"
                  value={signEmail}
                  onChange={(e) => setSignEmail(e.target.value)}
                  placeholder="pl. partner@ceg.hu"
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                />
              </label>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => { setSigning(null); setSignature(null); }}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100"
              >
                {t("common.cancel")}
              </button>
              <button
                onClick={saveSignature}
                disabled={busy || !signature}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {busy ? t("common.saving") : t("cons.signSave")}
              </button>
            </div>
          </div>
        </div>
      )}

      {delivery && (
        <div
          onMouseDown={(e) => { if (e.target === e.currentTarget) setDelivery(null); }}
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4"
        >
          <form onSubmit={saveDelivery} className="my-8 w-full max-w-lg space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">🚚 {t("delivery.title")}</h2>
            <p className="text-xs text-slate-500">{t("delivery.hint")}</p>
            {delivery.lines.map((line, i) => (
              <div key={i} className="flex items-center gap-2">
                <SearchSelect
                  items={activeProducts.map((p) => ({
                    id: p.id, label: p.name, sublabel: p.category, badge: p.unit,
                  }))}
                  value={line.product_id}
                  onChange={(id) => {
                    const prod = activeProducts.find((p) => p.id === id);
                    const lines = [...delivery.lines];
                    lines[i] = {
                      ...line, product_id: id,
                      unit_price: line.unit_price || (prod ? String(prod.price_per_portion) : ""),
                    };
                    setDelivery({ ...delivery, lines });
                  }}
                  placeholder={t("cons.addProductChoose")}
                  className="flex-1"
                />
                <input
                  type="number" min={0.001} step="0.001"
                  placeholder={t("cons.quantity")}
                  value={line.quantity}
                  onChange={(e) => {
                    const lines = [...delivery.lines];
                    lines[i] = { ...line, quantity: e.target.value };
                    setDelivery({ ...delivery, lines });
                  }}
                  className="w-24 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                />
                <input
                  type="number" min={0} step="0.01"
                  placeholder={t("delivery.unitPrice")}
                  title={t("delivery.unitPrice")}
                  value={line.unit_price}
                  onChange={(e) => {
                    const lines = [...delivery.lines];
                    lines[i] = { ...line, unit_price: e.target.value };
                    setDelivery({ ...delivery, lines });
                  }}
                  className="w-28 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                />
                {delivery.lines.length > 1 && (
                  <button
                    type="button"
                    onClick={() => setDelivery({ ...delivery, lines: delivery.lines.filter((_, j) => j !== i) })}
                    className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100"
                  >
                    ✕
                  </button>
                )}
              </div>
            ))}
            <button
              type="button"
              onClick={() => setDelivery({ ...delivery, lines: [...delivery.lines, { product_id: "", quantity: "", unit_price: "" }] })}
              className="text-sm text-indigo-600 hover:underline"
            >
              + {t("po.addLine")}
            </button>
            {srcWarehouses.length > 0 && (
              <label className="block text-sm">
                {t("cons.sourceWarehouse")}
                <SearchSelect
                  items={srcWarehouses.map((w) => ({
                    id: w.id, label: `${w.kind === "van" ? "🚐" : "🏬"} ${w.name}`,
                  }))}
                  value={delivery.source_warehouse_id}
                  onChange={(id) => setDelivery({ ...delivery, source_warehouse_id: id })}
                  allowEmpty
                  emptyLabel={t("cons.sourceWarehouseNone")}
                  className="mt-1"
                />
              </label>
            )}
            <label className="block text-sm">
              {t("cons.notes")}
              <input value={delivery.note} onChange={(e) => setDelivery({ ...delivery, note: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            {deliveryError && <p className="text-sm text-red-600">{deliveryError}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setDelivery(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={deliveryBusy} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-700 disabled:opacity-50">{deliveryBusy ? t("common.saving") : t("delivery.save")}</button>
            </div>
          </form>
        </div>
      )}

      {replenish && (
        <div
          onMouseDown={(e) => { if (e.target === e.currentTarget) setReplenish(null); }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
        >
          <form onSubmit={doReplenish} className="max-h-[90vh] w-full max-w-sm space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{t("cons.replenishTitle")}</h2>
            <label className="block text-sm">
              {t("cons.name")}
              <SearchSelect
                required
                items={activeProducts.map((p) => ({
                  id: p.id, label: p.name, sublabel: p.category, badge: p.unit,
                }))}
                value={replenish.product_id}
                onChange={(id) => {
                  const prod = activeProducts.find((p) => p.id === id);
                  setReplenish({
                    ...replenish,
                    product_id: id,
                    unit_cost: prod?.purchase_price?.toString() ?? "",
                  });
                }}
                className="mt-1"
              />
            </label>
            <label className="block text-sm">
              {t("cons.quantity")} ({activeProducts.find((p) => p.id === replenish.product_id)?.unit ?? "kg"})
              <input
                required
                type="number"
                min={0.01}
                step="0.01"
                value={replenish.quantity}
                onChange={(e) => setReplenish({ ...replenish, quantity: e.target.value })}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="block text-sm">
              {t("cons.purchasePrice")}
              <input
                type="number"
                min={0}
                step="0.01"
                value={replenish.unit_cost}
                onChange={(e) => setReplenish({ ...replenish, unit_cost: e.target.value })}
                placeholder={t("cons.purchasePriceHint")}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
              <span className="mt-1 block text-xs text-slate-400">{t("cons.purchasePriceNote")}</span>
            </label>
            {srcWarehouses.length > 0 && (
              <label className="block text-sm">
                {t("cons.sourceWarehouse")}
                <SearchSelect
                  items={srcWarehouses.map((w) => ({
                    id: w.id,
                    label: `${w.kind === "van" ? "🚐" : "🏬"} ${w.name}`,
                  }))}
                  value={replenish.source_warehouse_id}
                  onChange={(id) => setReplenish({ ...replenish, source_warehouse_id: id })}
                  allowEmpty
                  emptyLabel={t("cons.sourceWarehouseNone")}
                  className="mt-1"
                />
                <span className="mt-1 block text-xs text-slate-400">{t("cons.sourceWarehouseNote")}</span>
              </label>
            )}
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setReplenish(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{busy ? t("common.saving") : t("common.save")}</button>
            </div>
          </form>
        </div>
      )}

      {/* Készlet-visszavét modal: partnertől a képviselő raktárába/autójába */}
      {stockReturn && (
        <div onMouseDown={(e) => { if (e.target === e.currentTarget) setStockReturn(null); }} className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="max-h-[90vh] w-full max-w-sm space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">↩ {t("cons.returnTitle")}</h2>
            <p className="text-xs text-slate-500">{t("cons.returnHint")}</p>
            <label className="block text-sm">
              {t("cons.returnWhat")}
              <SearchSelect
                items={stock.filter((s) => s.quantity > 0).map((s) => ({
                  id: s.product_id,
                  label: `${s.product_name} (${s.quantity} ${s.unit})`,
                }))}
                value={stockReturn.product_id}
                onChange={(id) => {
                  const row = stock.find((s) => s.product_id === id);
                  setStockReturn({
                    ...stockReturn,
                    product_id: id,
                    quantity: row ? String(row.quantity) : "",
                  });
                }}
                allowEmpty
                emptyLabel={t("cons.returnAllOption")}
                className="mt-1"
              />
            </label>
            {stockReturn.product_id !== "" && (
              <label className="block text-sm">
                {t("cons.returnQty")}
                <input
                  type="number" min={0} step="0.01"
                  value={stockReturn.quantity}
                  onChange={(e) => setStockReturn({ ...stockReturn, quantity: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                />
              </label>
            )}
            <label className="block text-sm">
              {t("cons.returnTarget")}
              <SearchSelect
                items={srcWarehouses.map((w) => ({
                  id: w.id,
                  label: `${w.kind === "van" ? "🚐" : "🏬"} ${w.name}`,
                }))}
                value={stockReturn.warehouse_id}
                onChange={(id) => setStockReturn({ ...stockReturn, warehouse_id: id })}
                className="mt-1"
              />
            </label>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setStockReturn(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button
                onClick={doStockReturn}
                disabled={busy || !stockReturn.warehouse_id || (stockReturn.product_id !== "" && !(Number(stockReturn.quantity) > 0))}
                className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50"
              >
                {busy ? t("common.saving") : t("cons.returnDo")}
              </button>
            </div>
          </div>
        </div>
      )}

      <PartnerInfo partnerId={infoPartner} onClose={() => setInfoPartner(null)} />
    </AppShell>
  );
}
