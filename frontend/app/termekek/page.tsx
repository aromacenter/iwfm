"use client";

/** Termékek (bizomány): a partnerekhez kihelyezhető fogyóeszközök (pl. kávé)
 *  katalógusa — gramm/adag és ár/adag beállítással (elszámolás alapja). */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface Product {
  id: string;
  name: string;
  unit: string;
  grams_per_portion: number;
  price_per_portion: number;
  vat_percent: number;
  is_active: boolean;
  notes: string | null;
}

const EMPTY = {
  id: "",
  name: "",
  unit: "kg",
  grams_per_portion: "7",
  price_per_portion: "",
  vat_percent: "27",
  is_active: true,
  notes: "",
};

export default function TermekekPage() {
  const { t } = useT();
  const [products, setProducts] = useState<Product[]>([]);
  const [form, setForm] = useState<typeof EMPTY | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get<Product[]>("/api/products").then(setProducts).catch(() => {});
  }, []);
  useEffect(load, [load]);

  function edit(p: Product) {
    setError(null);
    setForm({
      id: p.id,
      name: p.name,
      unit: p.unit,
      grams_per_portion: String(p.grams_per_portion),
      price_per_portion: String(p.price_per_portion),
      vat_percent: String(p.vat_percent),
      is_active: p.is_active,
      notes: p.notes ?? "",
    });
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!form) return;
    setBusy(true);
    setError(null);
    try {
      const body = {
        name: form.name,
        unit: form.unit || "kg",
        grams_per_portion: Number(form.grams_per_portion) || 7,
        price_per_portion: Number(form.price_per_portion) || 0,
        vat_percent: Number(form.vat_percent) || 27,
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
        <button
          onClick={() => { setError(null); setForm({ ...EMPTY }); }}
          className="ml-auto rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
        >
          {t("cons.newProduct")}
        </button>
      </div>

      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
              <th className="px-4 py-3">{t("cons.name")}</th>
              <th className="px-4 py-3">{t("cons.unit")}</th>
              <th className="px-4 py-3">{t("cons.gramsPerPortion")}</th>
              <th className="px-4 py-3">{t("cons.pricePerPortion")}</th>
              <th className="px-4 py-3">{t("cons.vat")}</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {products.map((p) => (
              <tr key={p.id} className="border-b border-slate-100 last:border-0">
                <td className="px-4 py-3">
                  <span className="font-medium">{p.name}</span>
                  {!p.is_active && (
                    <span className="ml-2 rounded bg-slate-200 px-1.5 py-0.5 text-xs">{t("partners.inactive")}</span>
                  )}
                </td>
                <td className="px-4 py-3 text-slate-500">{p.unit}</td>
                <td className="px-4 py-3">{p.grams_per_portion} g</td>
                <td className="px-4 py-3">{p.price_per_portion.toLocaleString("hu-HU")} Ft</td>
                <td className="px-4 py-3 text-slate-500">{p.vat_percent}%</td>
                <td className="px-4 py-3 text-right">
                  <button onClick={() => edit(p)} className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100">
                    {t("common.edit")}
                  </button>
                </td>
              </tr>
            ))}
            {products.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-10 text-center text-slate-400">{t("cons.noProducts")}</td></tr>
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
            <label className="block text-sm">
              {t("cons.pricePerPortion")} *
              <input required type="number" min={0} step="0.01" value={form.price_per_portion} onChange={(e) => setForm({ ...form, price_per_portion: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              {t("cons.notes")}
              <textarea value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} rows={2} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
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
