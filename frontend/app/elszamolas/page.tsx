"use client";

/** Elszámolás: partner (külső raktár) kiválasztása → készlet + feltöltés →
 *  fizikai leltár rögzítése → fogyás/összeg számítás → elszámolás mentése →
 *  „Kiszámlázott” gomb (Billingó). Az elszámoló a bejelentkezett user. */

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import IconLegend from "@/components/IconLegend";
import PartnerPicker from "@/components/PartnerPicker";
import SignatureCanvas from "@/components/SignatureCanvas";
import { api, ApiError, downloadFile, errorMessage } from "@/lib/api";
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
}

interface Product {
  id: string;
  name: string;
  is_active: boolean;
}

interface Settlement {
  id: string;
  partner_id: string;
  partner_name: string | null;
  settled_by_name: string;
  payment_method: "cash" | "card" | "transfer";
  total_net: number;
  total_gross: number;
  invoiced: boolean;
  billingo_status: string | null;
  has_signature: boolean;
  receipt_sent_at: string | null;
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
  last_settlement_at: string | null;
  days_since: number | null;
  stock_products: number;
}

const PAYMENTS = ["cash", "card", "transfer"] as const;

// Offline-várólista: hálózati hiba esetén ide kerül az elszámolás, és
// újracsatlakozáskor automatikusan beküldjük.
const QUEUE_KEY = "iwfm-pending-settlements";

interface QueuedSettlement {
  partner_id: string;
  partner_name: string;
  payment_method: (typeof PAYMENTS)[number];
  lines: { product_id: string; physical_qty: number }[];
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
  const [payment, setPayment] = useState<(typeof PAYMENTS)[number]>("cash");
  const [note, setNote] = useState("");
  const [history, setHistory] = useState<Settlement[]>([]);
  const [replenish, setReplenish] = useState<{ product_id: string; quantity: string } | null>(null);
  const [due, setDue] = useState<DuePartner[]>([]);
  const [showDue, setShowDue] = useState(true);
  const [lowStock, setLowStock] = useState<LowStock[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [signing, setSigning] = useState<Settlement | null>(null);
  const [signature, setSignature] = useState<string | null>(null);
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
      setPhysical(Object.fromEntries(s.map((x) => [x.product_id, ""])));
    }).catch(() => {});
  }, [partnerId]);

  const loadHistory = useCallback(() => {
    const params = partnerId ? `?partner_id=${partnerId}` : "";
    api.get<Settlement[]>(`/api/settlements${params}`).then(setHistory).catch(() => {});
  }, [partnerId]);

  const loadDue = useCallback(() => {
    api.get<DuePartner[]>("/api/settlements/due?days=30").then(setDue).catch(() => {});
    api.get<LowStock[]>("/api/products/low-stock").then(setLowStock).catch(() => {});
    api.get<Order[]>("/api/orders?status=open").then(setOrders).catch(() => {});
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
          lines: item.lines,
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

  // Élő fogyás-előnézet a beírt leltár alapján
  const preview = useMemo(() => {
    let net = 0;
    const rows = stock.map((s) => {
      const phys = physical[s.product_id];
      const physNum = phys === "" ? null : Number(phys);
      const consumed = physNum === null ? 0 : Math.max(s.quantity - physNum, 0);
      const portions = s.grams_per_portion > 0 ? (consumed * 1000) / s.grams_per_portion : 0;
      const amount = portions * s.price_per_portion;
      net += amount;
      return { ...s, consumed, portions, amount, filled: physNum !== null };
    });
    return { rows, net };
  }, [stock, physical]);

  async function doReplenish(e: React.FormEvent) {
    e.preventDefault();
    if (!replenish || !partnerId) return;
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/partners/${partnerId}/stock/replenish`, {
        product_id: replenish.product_id,
        quantity: Number(replenish.quantity),
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
    const lines = stock
      .filter((s) => physical[s.product_id] !== "")
      .map((s) => ({ product_id: s.product_id, physical_qty: Number(physical[s.product_id]) }));
    if (lines.length === 0) return;
    setBusy(true);
    try {
      const res = await api.post<Settlement>("/api/settlements", {
        partner_id: partnerId,
        payment_method: payment,
        lines,
        note: note || null,
      });
      toast(t("cons.settlementSaved", { gross: res.total_gross.toLocaleString("hu-HU") }), "success");
      setNote("");
      loadStock();
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
          lines,
          note: note || null,
          queued_at: new Date().toISOString(),
        });
        writeQueue(queue);
        setPendingOffline(queue.length);
        setNote("");
        setPhysical(Object.fromEntries(stock.map((x) => [x.product_id, ""])));
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
      await api.post(`/api/settlements/${signing.id}/signature`, { signature });
      toast(t("cons.signatureSaved"), "success");
      setSigning(null);
      setSignature(null);
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
            onClick={() => { setError(null); setReplenish({ product_id: activeProducts[0]?.id ?? "", quantity: "" }); }}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm hover:bg-slate-100"
          >
            {t("cons.replenish")}
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

      {lowStock.length > 0 && (
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

      {orders.length > 0 && (
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

      {due.length > 0 && (
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
            <div className="overflow-x-auto border-t border-amber-200">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-amber-700">
                    <th className="px-4 py-2">{t("cons.partner")}</th>
                    <th className="px-4 py-2">{t("cons.dueLast")}</th>
                    <th className="px-4 py-2">{t("cons.dueDays")}</th>
                    <th className="px-4 py-2">{t("cons.dueStock")}</th>
                    <th className="px-4 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {due.map((d) => (
                    <tr key={d.partner_id} className="border-t border-amber-100">
                      <td className="px-4 py-2 font-medium text-amber-950">
                        {d.name}
                        {d.partner_code && <span className="ml-2 text-xs text-amber-600">{d.partner_code}</span>}
                      </td>
                      <td className="px-4 py-2 text-amber-800">
                        {d.last_settlement_at ? fmt(d.last_settlement_at) : t("cons.dueNever")}
                      </td>
                      <td className="px-4 py-2 text-amber-800">
                        {d.days_since !== null ? t("cons.dueDaysAgo", { days: d.days_since }) : "—"}
                      </td>
                      <td className="px-4 py-2 text-amber-800">{d.stock_products}</td>
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
          )}
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
                <th className="px-4 py-3">{t("cons.consumed")}</th>
                <th className="px-4 py-3">{t("cons.portions")}</th>
                <th className="px-4 py-3">{t("cons.amountNet")}</th>
              </tr>
            </thead>
            <tbody>
              {preview.rows.map((s) => (
                <tr key={s.product_id} className="border-b border-slate-100 last:border-0">
                  <td className="px-4 py-3 font-medium">{s.product_name}</td>
                  <td className="px-4 py-3">
                    {ft(s.price_per_portion)}
                    {s.has_price_override && (
                      <span title={t("prices.overrideHint", { base: ft(s.base_price_per_portion) })} className="ml-1 text-xs text-indigo-600">*</span>
                    )}
                  </td>
                  <td className="px-4 py-3">{s.quantity} {s.unit}</td>
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
                  <td className="px-4 py-3">{s.filled ? `${s.consumed.toFixed(2)} ${s.unit}` : "—"}</td>
                  <td className="px-4 py-3">{s.filled ? s.portions.toFixed(0) : "—"}</td>
                  <td className="px-4 py-3 font-medium">{s.filled ? ft(Math.round(s.amount)) : "—"}</td>
                </tr>
              ))}
              {stock.length === 0 && (
                <tr><td colSpan={8} className="px-4 py-10 text-center text-slate-400">{t("cons.noStock")}</td></tr>
              )}
            </tbody>
          </table>
          {stock.length > 0 && (
            <div className="flex flex-wrap items-center gap-3 border-t border-slate-200 px-4 py-3">
              <select
                value={payment}
                onChange={(e) => setPayment(e.target.value as (typeof PAYMENTS)[number])}
                className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
              >
                {PAYMENTS.map((m) => (
                  <option key={m} value={m}>{t(`cons.payments.${m}`)}</option>
                ))}
              </select>
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder={t("cons.notes")}
                className="min-w-40 flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <span className="text-sm text-slate-600">
                {t("cons.amountNet")}: <span className="font-semibold">{ft(Math.round(preview.net))}</span>
              </span>
              <button
                onClick={createSettlement}
                disabled={busy || preview.rows.every((r) => !r.filled)}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {busy ? t("common.saving") : t("cons.createSettlement")}
              </button>
            </div>
          )}
        </div>
      )}

      <div className="mb-2 flex items-center gap-3">
        <h2 className="font-semibold">{t("cons.history")}</h2>
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
                <td className="px-4 py-3 whitespace-nowrap">{fmt(s.created_at)}</td>
                <td className="px-4 py-3">
                  <div className="font-medium">{s.partner_name}</div>
                  <div className="text-xs text-slate-400">{s.settled_by_name}</div>
                </td>
                <td className="px-4 py-3">{t(`cons.payments.${s.payment_method}`)}</td>
                <td className="px-4 py-3 text-right font-medium">{ft(s.total_gross)}</td>
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
                    {s.invoiced ? (
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
          <div className="w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{t("cons.signTitle")}</h2>
            <p className="text-sm text-slate-600">
              {signing.partner_name} · {ft(signing.total_gross)} · {fmt(signing.created_at)}
            </p>
            <SignatureCanvas label={t("cons.signLabel")} onChange={setSignature} />
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

      {replenish && (
        <div
          onMouseDown={(e) => { if (e.target === e.currentTarget) setReplenish(null); }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
        >
          <form onSubmit={doReplenish} className="w-full max-w-sm space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{t("cons.replenishTitle")}</h2>
            <label className="block text-sm">
              {t("cons.name")}
              <select
                required
                value={replenish.product_id}
                onChange={(e) => setReplenish({ ...replenish, product_id: e.target.value })}
                className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2"
              >
                {activeProducts.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              {t("cons.quantity")} (kg)
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
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setReplenish(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{busy ? t("common.saving") : t("common.save")}</button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
