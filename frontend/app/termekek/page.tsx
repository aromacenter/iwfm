"use client";

/** Termékek (bizomány): a partnerekhez kihelyezhető fogyóeszközök (pl. kávé)
 *  katalógusa — gramm/adag és ár/adag beállítással (elszámolás alapja). */

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import IconLegend from "@/components/IconLegend";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { usePerms } from "@/lib/perms";
import { useUI } from "@/lib/ui";

interface Product {
  id: string;
  name: string;
  category: string | null;
  unit: string;
  grams_per_portion: number;
  price_per_portion: number;
  vat_percent: number;
  is_consignment: boolean;
  low_stock_threshold: number | null;
  purchase_price: number | null;
  is_active: boolean;
  notes: string | null;
}

/** Árrés egy adagon: eladási ár − (beszerzési Ft/kg × gramm/adag). */
function marginPerPortion(p: Product): number | null {
  if (p.purchase_price == null) return null;
  return p.price_per_portion - (p.purchase_price * p.grams_per_portion) / 1000;
}

const DEFAULT_CATEGORIES = ["Kávék", "Kávégépek", "Alkatrészek", "Kellékek"];

const EMPTY = {
  id: "",
  name: "",
  category: "",
  unit: "kg",
  grams_per_portion: "7",
  price_per_portion: "",
  vat_percent: "27",
  is_consignment: false,
  low_stock_threshold: "",
  purchase_price: "",
  is_active: true,
  notes: "",
};

export default function TermekekPage() {
  const { t } = useT();
  const { toast, confirm } = useUI();
  const canDelete = usePerms().can("delete");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [products, setProducts] = useState<Product[]>([]);
  const [form, setForm] = useState<typeof EMPTY | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get<Product[]>("/api/products").then(setProducts).catch(() => {});
  }, []);
  useEffect(load, [load]);

  const [categoryFilter, setCategoryFilter] = useState("");
  const categories = useMemo(() => {
    const set = new Set<string>();
    for (const p of products) if (p.category) set.add(p.category);
    return [...set].sort((a, b) => a.localeCompare(b, "hu"));
  }, [products]);
  const shownProducts = useMemo(
    () =>
      categoryFilter === ""
        ? products
        : products.filter((p) =>
            categoryFilter === "__none__" ? !p.category : p.category === categoryFilter,
          ),
    [products, categoryFilter],
  );

  function edit(p: Product) {
    setError(null);
    setForm({
      id: p.id,
      name: p.name,
      category: p.category ?? "",
      unit: p.unit,
      grams_per_portion: String(p.grams_per_portion),
      price_per_portion: String(p.price_per_portion),
      vat_percent: String(p.vat_percent),
      is_consignment: p.is_consignment,
      low_stock_threshold: p.low_stock_threshold?.toString() ?? "",
      purchase_price: p.purchase_price?.toString() ?? "",
      is_active: p.is_active,
      notes: p.notes ?? "",
    });
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
    if (!(await confirm(t("cons.productDeleteConfirm", { count: selected.size })))) return;
    try {
      const res = await api.post<{ deleted: number; blocked: { name: string; code: string }[] }>(
        "/api/products/bulk-delete",
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
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!form) return;
    setBusy(true);
    setError(null);
    try {
      const body = {
        name: form.name,
        category: form.category.trim() || null,
        unit: form.unit || "kg",
        grams_per_portion: Number(form.grams_per_portion) || 7,
        price_per_portion: Number(form.price_per_portion) || 0,
        vat_percent: Number(form.vat_percent) || 27,
        is_consignment: form.is_consignment,
        low_stock_threshold:
          form.low_stock_threshold === "" ? null : Number(form.low_stock_threshold),
        purchase_price: form.purchase_price === "" ? null : Number(form.purchase_price),
        is_active: form.is_active,
        notes: form.notes || null,
      };
      if (form.id) await api.patch(`/api/products/${form.id}`, body);
      else await api.post("/api/products", body);
      setForm(null);
      load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("cons.productsTitle")}</h1>
        {selected.size > 0 && (
          <button
            onClick={bulkDelete}
            className="ml-auto rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
          >
            {t("bulk.deleteSelected", { count: selected.size })}
          </button>
        )}
        <button
          onClick={() => { setError(null); setForm({ ...EMPTY }); }}
          className={`${selected.size > 0 ? "" : "ml-auto "}rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700`}
        >
          {t("cons.newProduct")}
        </button>
      </div>

      {(categories.length > 0 || categoryFilter !== "") && (
        <div className="mb-3 flex flex-wrap items-center gap-1.5">
          <button
            onClick={() => setCategoryFilter("")}
            className={`rounded-full px-3 py-1 text-xs ${categoryFilter === "" ? "bg-indigo-600 font-medium text-white" : "border border-slate-300 text-slate-600 hover:bg-slate-100"}`}
          >
            {t("cons.categoryAll")}
          </button>
          {categories.map((c) => (
            <button
              key={c}
              onClick={() => setCategoryFilter(c)}
              className={`rounded-full px-3 py-1 text-xs ${categoryFilter === c ? "bg-indigo-600 font-medium text-white" : "border border-slate-300 text-slate-600 hover:bg-slate-100"}`}
            >
              {c}
            </button>
          ))}
          {products.some((p) => !p.category) && (
            <button
              onClick={() => setCategoryFilter("__none__")}
              className={`rounded-full px-3 py-1 text-xs ${categoryFilter === "__none__" ? "bg-indigo-600 font-medium text-white" : "border border-slate-300 text-slate-600 hover:bg-slate-100"}`}
            >
              {t("cons.categoryNone")}
            </button>
          )}
        </div>
      )}

      <IconLegend items={[{ icon: "✏️", label: t("common.edit") }]} />
      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-slate-500">
              {canDelete && (
              <th className="sticky top-0 z-10 w-8 border-b border-slate-200 bg-white px-3 py-3">
                <input
                  type="checkbox"
                  checked={shownProducts.length > 0 && shownProducts.every((p) => selected.has(p.id))}
                  onChange={(e) =>
                    setSelected(e.target.checked ? new Set(shownProducts.map((p) => p.id)) : new Set())
                  }
                  className="h-4 w-4"
                />
              </th>
              )}
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("cons.name")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("cons.gramsPerPortion")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("cons.pricePerPortion")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("cons.purchasePriceShort")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("cons.marginPerPortion")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("cons.vat")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("cons.lowStockThreshold")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {shownProducts.map((p) => (
              <tr key={p.id} className="border-b border-slate-100 last:border-0">
                {canDelete && (
                <td className="px-3 py-3">
                  <input
                    type="checkbox"
                    checked={selected.has(p.id)}
                    onChange={() => toggleSelect(p.id)}
                    className="h-4 w-4"
                  />
                </td>
                )}
                <td className="px-4 py-3">
                  <div className="font-medium">
                    {p.name}
                    {!p.is_active && (
                      <span className="ml-2 rounded bg-slate-200 px-1.5 py-0.5 text-xs font-normal">{t("partners.inactive")}</span>
                    )}
                  </div>
                  <div className="text-xs text-slate-400">
                    {p.unit}
                    {p.category && (
                      <span className="ml-2 rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700">{p.category}</span>
                    )}
                    {p.is_consignment && (
                      <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800" title={t("cons.consignmentFlagHint")}>☕ {t("cons.consignmentBadge")}</span>
                    )}
                  </div>
                </td>
                <td className="px-4 py-3">{p.grams_per_portion} g</td>
                <td className="px-4 py-3 font-medium">{p.price_per_portion.toLocaleString("hu-HU")} Ft</td>
                <td className="px-4 py-3 text-slate-500">
                  {p.purchase_price != null ? `${p.purchase_price.toLocaleString("hu-HU")} Ft/kg` : "—"}
                </td>
                <td className="px-4 py-3">
                  {(() => {
                    const m = marginPerPortion(p);
                    if (m == null) return <span className="text-slate-400">—</span>;
                    return (
                      <span className={m < 0 ? "font-medium text-red-600" : "text-emerald-700"}>
                        {m.toLocaleString("hu-HU", { maximumFractionDigits: 1 })} Ft
                      </span>
                    );
                  })()}
                </td>
                <td className="px-4 py-3 text-slate-500">{p.vat_percent}%</td>
                <td className="px-4 py-3 text-slate-500">
                  {p.low_stock_threshold != null ? `${p.low_stock_threshold} ${p.unit}` : "—"}
                </td>
                <td className="px-4 py-3 text-right">
                  <button onClick={() => edit(p)} title={t("common.edit")} className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100">
                    ✏️
                  </button>
                </td>
              </tr>
            ))}
            {shownProducts.length === 0 && (
              <tr><td colSpan={9} className="px-4 py-10 text-center text-slate-400">{t("cons.noProducts")}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {form && (
        <div
          onMouseDown={(e) => { if (e.target === e.currentTarget) setForm(null); }}
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4"
        >
          <form onSubmit={save} className="my-8 w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{form.id ? t("cons.editProduct") : t("cons.newProduct")}</h2>
            <label className="block text-sm">
              {t("cons.name")} *
              <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              {t("cons.category")}
              <input
                list="category-options"
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value })}
                placeholder={t("cons.categoryHint")}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
              <datalist id="category-options">
                {[...new Set([...DEFAULT_CATEGORIES, ...categories])].map((c) => (
                  <option key={c} value={c} />
                ))}
              </datalist>
            </label>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <label className="block text-sm">
                {t("cons.unit")}
                <input value={form.unit} onChange={(e) => setForm({ ...form, unit: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
              <label className="block text-sm">
                {t("cons.gramsPerPortion")}
                <input type="number" min={1} value={form.grams_per_portion} onChange={(e) => setForm({ ...form, grams_per_portion: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
              <label className="block text-sm">
                {t("cons.vat")}
                <input type="number" min={0} max={100} value={form.vat_percent} onChange={(e) => setForm({ ...form, vat_percent: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="block text-sm">
                {t("cons.pricePerPortion")} *
                <input required type="number" min={0} step="0.01" value={form.price_per_portion} onChange={(e) => setForm({ ...form, price_per_portion: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
              <label className="block text-sm">
                {t("cons.lowStockThreshold")}
                <input type="number" min={0} step="0.1" value={form.low_stock_threshold} onChange={(e) => setForm({ ...form, low_stock_threshold: e.target.value })} placeholder={t("cons.noAlert")} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
            </div>
            <label className="block text-sm">
              {t("cons.purchasePrice")}
              <input type="number" min={0} step="0.01" value={form.purchase_price} onChange={(e) => setForm({ ...form, purchase_price: e.target.value })} placeholder={t("cons.purchasePriceHint")} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              {t("cons.notes")}
              <textarea value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} rows={2} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm">
              <input type="checkbox" checked={form.is_consignment} onChange={(e) => setForm({ ...form, is_consignment: e.target.checked })} className="mt-0.5 h-4 w-4" />
              <span>
                ☕ {t("cons.consignmentFlag")}
                <span className="block text-xs text-amber-700">{t("cons.consignmentFlagHint")}</span>
              </span>
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} className="h-4 w-4" />
              {t("cons.active")}
            </label>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{busy ? t("common.saving") : t("common.save")}</button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
