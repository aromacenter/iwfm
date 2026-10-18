"use client";

/** QR-fizetések (Saferpay modul) — ADMIN NÉZET: a kávégépeken indított
 * QR-fizetések listája és állapota. A QR maga a gépek kijelzőjén jelenik
 * meg (gép-oldali API-bekötés), itt csak látjuk és adminisztráljuk. */

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface SpProduct {
  id: string;
  name: string;
  price_huf: number;
  machine_code: string | null;
  is_active: boolean;
  sort_order: number;
}

interface Order {
  id: string;
  order_no: string;
  product_name: string;
  amount_huf: number;
  status: "created" | "paid" | "failed" | "expired" | "cancelled";
  machine_code: string | null;
  payment_means: string | null;
  created_at: string;
  paid_at: string | null;
}

const STATUS_CHIP: Record<string, string> = {
  created: "bg-amber-100 text-amber-800",
  paid: "bg-emerald-100 text-emerald-800",
  failed: "bg-rose-100 text-rose-800",
  expired: "bg-slate-100 text-slate-500",
  cancelled: "bg-slate-100 text-slate-500",
};

export default function KaveFizetesPage() {
  const { t, lang } = useT();
  const { toast, confirm } = useUI();
  const ft = (n: number) => `${Math.round(n).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB")} Ft`;
  const fmt = (dt: string) =>
    new Date(dt).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { dateStyle: "short", timeStyle: "short" });

  const [orders, setOrders] = useState<Order[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [machineFilter, setMachineFilter] = useState("");
  // Termékek és árak (a gép menüje) — itt menedzselhető
  const [products, setProducts] = useState<SpProduct[]>([]);
  const [prodForm, setProdForm] = useState<{
    id: string | null; name: string; price: string; machine: string; active: boolean;
  } | null>(null);
  const [prodBusy, setProdBusy] = useState(false);

  const load = useCallback(() => {
    api.get<Order[]>("/api/saferpay/orders").then(setOrders).catch(() => {});
    api.get<SpProduct[]>("/api/saferpay/products").then(setProducts).catch(() => {});
  }, []);
  useEffect(() => {
    load();
    // frissítés félpercenként — a gépeken közben is pöröghet a forgalom
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, [load]);

  async function saveProduct(e: React.FormEvent) {
    e.preventDefault();
    if (!prodForm || !prodForm.name.trim() || !prodForm.price) return;
    setProdBusy(true);
    const body = {
      name: prodForm.name.trim(),
      price_huf: Number(prodForm.price),
      machine_code: prodForm.machine.trim() || null,
      is_active: prodForm.active,
      sort_order: prodForm.id
        ? products.find((p) => p.id === prodForm.id)?.sort_order ?? 0
        : products.length,
    };
    try {
      if (prodForm.id) await api.put(`/api/saferpay/products/${prodForm.id}`, body);
      else await api.post("/api/saferpay/products", body);
      toast(t("common.saved"), "success");
      setProdForm(null);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setProdBusy(false);
    }
  }

  async function deleteProduct(p: SpProduct) {
    if (!(await confirm(t("spay.deleteConfirm", { name: p.name })))) return;
    try {
      await api.delete(`/api/saferpay/products/${p.id}`);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  const machines = useMemo(
    () => Array.from(new Set(orders.map((o) => o.machine_code).filter(Boolean))) as string[],
    [orders],
  );
  const filtered = orders.filter((o) => {
    if (statusFilter && o.status !== statusFilter) return false;
    if (machineFilter && o.machine_code !== machineFilter) return false;
    return true;
  });

  // Mai fizetett forgalom
  const today = new Date().toDateString();
  const paidToday = orders.filter((o) => o.status === "paid" && new Date(o.created_at).toDateString() === today);

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-xl font-semibold">📲 {t("spay.title")}</h1>
          <p className="text-sm text-slate-500">{t("spay.subtitle")}</p>
        </div>
        <div className="ml-auto flex flex-wrap gap-2 text-sm">
          <span className="rounded-lg bg-emerald-50 px-3 py-1.5 font-medium text-emerald-800">
            ✅ {t("spay.todayPaid", { n: paidToday.length })} · {ft(paidToday.reduce((a, o) => a + o.amount_huf, 0))}
          </span>
        </div>
      </div>

      {/* Termékek és árak — a gép a /api/pay/menu-ból ezt olvassa */}
      <div className="mb-4 rounded-2xl border border-emerald-200 bg-white p-4 shadow-sm">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <h2 className="font-semibold">☕ {t("spay.productsTitle")}</h2>
          <button
            onClick={() => setProdForm({ id: null, name: "", price: "", machine: "", active: true })}
            className="ml-auto rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700"
          >
            + {t("spay.addProduct")}
          </button>
        </div>
        <p className="mb-2 text-xs text-slate-500">{t("spay.productsHint")}</p>
        {products.length === 0 ? (
          <p className="py-4 text-center text-sm text-slate-400">{t("spay.noProducts")}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-slate-500">
                  <th className="py-1.5 pr-3">{t("spay.colProduct")}</th>
                  <th className="py-1.5 pr-3 text-right">{t("spay.colAmount")}</th>
                  <th className="py-1.5 pr-3">{t("spay.colMachine")}</th>
                  <th className="py-1.5 pr-3">{t("spay.colStatus")}</th>
                  <th className="py-1.5"></th>
                </tr>
              </thead>
              <tbody>
                {products.map((p) => (
                  <tr key={p.id} className={`border-t border-slate-100 ${p.is_active ? "" : "opacity-50"}`}>
                    <td className="py-1.5 pr-3 font-medium">{p.name}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{ft(p.price_huf)}</td>
                    <td className="py-1.5 pr-3 text-xs">{p.machine_code ? `🤖 ${p.machine_code}` : t("spay.allMachinesShort")}</td>
                    <td className="py-1.5 pr-3">
                      <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${p.is_active ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-500"}`}>
                        {p.is_active ? t("spay.active") : t("spay.inactive")}
                      </span>
                    </td>
                    <td className="py-1.5 text-right">
                      <button
                        onClick={() => setProdForm({ id: p.id, name: p.name, price: String(Math.round(p.price_huf)), machine: p.machine_code ?? "", active: p.is_active })}
                        title={t("common.edit")}
                        className="rounded border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-100"
                      >
                        ✏️
                      </button>
                      <button
                        onClick={() => void deleteProduct(p)}
                        title={t("common.delete")}
                        className="ml-1 rounded border border-rose-300 px-2 py-0.5 text-xs text-rose-700 hover:bg-rose-50"
                      >
                        🗑
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="mb-3 flex flex-wrap gap-2">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm"
        >
          <option value="">{t("spay.allStatuses")}</option>
          {(["paid", "created", "failed", "expired"] as const).map((s) => (
            <option key={s} value={s}>{t(`spay.status.${s}`)}</option>
          ))}
        </select>
        {machines.length > 0 && (
          <select
            value={machineFilter}
            onChange={(e) => setMachineFilter(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm"
          >
            <option value="">{t("spay.allMachines")}</option>
            {machines.map((m) => (
              <option key={m} value={m}>🤖 {m}</option>
            ))}
          </select>
        )}
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        {filtered.length === 0 ? (
          <p className="py-10 text-center text-sm text-slate-400">{t("spay.empty")}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-slate-500">
                  <th className="px-4 py-2.5">{t("spay.colWhen")}</th>
                  <th className="px-4 py-2.5">{t("spay.colOrder")}</th>
                  <th className="px-4 py-2.5">{t("spay.colProduct")}</th>
                  <th className="px-4 py-2.5 text-right">{t("spay.colAmount")}</th>
                  <th className="px-4 py-2.5">{t("spay.colMachine")}</th>
                  <th className="px-4 py-2.5">{t("spay.colMeans")}</th>
                  <th className="px-4 py-2.5">{t("spay.colStatus")}</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((o) => (
                  <tr key={o.id} className="border-t border-slate-100">
                    <td className="whitespace-nowrap px-4 py-2 text-xs text-slate-500">{fmt(o.created_at)}</td>
                    <td className="px-4 py-2 font-mono text-xs">{o.order_no}</td>
                    <td className="px-4 py-2 font-medium">{o.product_name}</td>
                    <td className="px-4 py-2 text-right tabular-nums">{ft(o.amount_huf)}</td>
                    <td className="px-4 py-2 text-xs">{o.machine_code ? `🤖 ${o.machine_code}` : "—"}</td>
                    <td className="px-4 py-2 text-xs text-slate-500">{o.payment_means ?? "—"}</td>
                    <td className="px-4 py-2">
                      <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${STATUS_CHIP[o.status]}`}>
                        {t(`spay.status.${o.status}`)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <p className="mt-3 text-xs text-slate-400">{t("spay.machineHint")}</p>

      {/* Termék-űrlap modal */}
      {prodForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={saveProduct} className="relative w-full max-w-sm space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => setProdForm(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">
              ☕ {prodForm.id ? t("spay.editProduct") : t("spay.addProduct")}
            </h2>
            <label className="block text-sm">
              {t("spay.colProduct")} *
              <input required value={prodForm.name} onChange={(e) => setProdForm({ ...prodForm, name: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              {t("spay.priceLabel")} *
              <input required type="number" min={1} value={prodForm.price} onChange={(e) => setProdForm({ ...prodForm, price: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              {t("spay.machineLabel")}
              <input value={prodForm.machine} onChange={(e) => setProdForm({ ...prodForm, machine: e.target.value })} placeholder={t("spay.machineLabelPh")} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={prodForm.active} onChange={(e) => setProdForm({ ...prodForm, active: e.target.checked })} className="h-4 w-4" />
              {t("spay.active")}
            </label>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setProdForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={prodBusy} className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50">
                {prodBusy ? t("common.saving") : t("common.save")}
              </button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
