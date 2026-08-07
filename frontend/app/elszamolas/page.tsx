"use client";

/** Elszámolás: partner (külső raktár) kiválasztása → készlet + feltöltés →
 *  fizikai leltár rögzítése → fogyás/összeg számítás → elszámolás mentése →
 *  „Kiszámlázott” gomb (Billingó). Az elszámoló a bejelentkezett user. */

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface Partner {
  id: string;
  name: string;
  is_active: boolean;
}

interface Stock {
  product_id: string;
  product_name: string;
  unit: string;
  quantity: number;
  grams_per_portion: number;
  price_per_portion: number;
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
  created_at: string;
}

const PAYMENTS = ["cash", "card", "transfer"] as const;

export default function ElszamolasPage() {
  const { t, lang } = useT();
  const { toast, confirm } = useUI();
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  useEffect(loadStock, [loadStock]);
  useEffect(loadHistory, [loadHistory]);

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
    } catch (err) {
      toast(errorMessage(err), "error");
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

  const activeProducts = products.filter((p) => p.is_active);

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("cons.settlementTitle")}</h1>
        <select
          value={partnerId}
          onChange={(e) => setPartnerId(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          <option value="">{t("cons.choosePartner")}</option>
          {partners.filter((p) => p.is_active).map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        {partnerId && (
          <button
            onClick={() => { setError(null); setReplenish({ product_id: activeProducts[0]?.id ?? "", quantity: "" }); }}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm hover:bg-slate-100"
          >
            {t("cons.replenish")}
          </button>
        )}
      </div>

      {partnerId && (
        <div className="mb-6 overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
                <th className="px-4 py-3">{t("cons.name")}</th>
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
                <tr><td colSpan={7} className="px-4 py-10 text-center text-slate-400">{t("cons.noStock")}</td></tr>
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
      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
              <th className="w-8 px-3 py-3">
                <input
                  type="checkbox"
                  checked={history.length > 0 && history.every((s) => selected.has(s.id))}
                  onChange={(e) =>
                    setSelected(e.target.checked ? new Set(history.map((s) => s.id)) : new Set())
                  }
                  className="h-4 w-4"
                />
              </th>
              <th className="px-4 py-3">{t("cons.date")}</th>
              <th className="px-4 py-3">{t("cons.partner")}</th>
              <th className="px-4 py-3">{t("cons.settledBy")}</th>
              <th className="px-4 py-3">{t("cons.payment")}</th>
              <th className="px-4 py-3">{t("cons.total")}</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {history.map((s) => (
              <tr key={s.id} className="border-b border-slate-100 last:border-0">
                <td className="px-3 py-3">
                  <input
                    type="checkbox"
                    checked={selected.has(s.id)}
                    onChange={() => toggleSelect(s.id)}
                    className="h-4 w-4"
                  />
                </td>
                <td className="px-4 py-3 whitespace-nowrap">{fmt(s.created_at)}</td>
                <td className="px-4 py-3">{s.partner_name}</td>
                <td className="px-4 py-3 text-slate-500">{s.settled_by_name}</td>
                <td className="px-4 py-3">{t(`cons.payments.${s.payment_method}`)}</td>
                <td className="px-4 py-3 font-medium">{ft(s.total_gross)}</td>
                <td className="px-4 py-3 text-right">
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
                </td>
              </tr>
            ))}
            {history.length === 0 && (
              <tr><td colSpan={7} className="px-4 py-10 text-center text-slate-400">{t("cons.noSettlements")}</td></tr>
            )}
          </tbody>
        </table>
      </div>

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
