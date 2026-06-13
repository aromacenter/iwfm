"use client";

/** Partnerek törzsadat: cégadatok (adószám, cégjegyzékszám, bank, fizetési
 *  határidő), kapcsolattartó, székhely/számlázási cím — teljes nyilvántartás. */

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";

type PartnerType = "customer" | "supplier" | "both";

interface Partner {
  id: string;
  name: string;
  partner_type: PartnerType;
  tax_number: string | null;
  eu_tax_number: string | null;
  reg_number: string | null;
  contact_name: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  website: string | null;
  address: string | null;
  billing_address: string | null;
  bank_account: string | null;
  payment_terms_days: number | null;
  notes: string | null;
  is_active: boolean;
  asset_count: number;
}

const EMPTY = {
  id: "",
  name: "",
  partner_type: "customer" as PartnerType,
  tax_number: "",
  eu_tax_number: "",
  reg_number: "",
  contact_name: "",
  contact_email: "",
  contact_phone: "",
  website: "",
  address: "",
  billing_address: "",
  bank_account: "",
  payment_terms_days: "",
  notes: "",
  is_active: true,
};

const TYPE_COLORS: Record<PartnerType, string> = {
  customer: "bg-sky-100 text-sky-800",
  supplier: "bg-violet-100 text-violet-800",
  both: "bg-emerald-100 text-emerald-800",
};

export default function PartnerekPage() {
  const { t } = useT();
  const [partners, setPartners] = useState<Partner[]>([]);
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [form, setForm] = useState<typeof EMPTY | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get<Partner[]>("/api/partners").then(setPartners).catch(() => {});
  }, []);

  useEffect(load, [load]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return partners.filter((p) => {
      if (typeFilter && p.partner_type !== typeFilter) return false;
      if (!q) return true;
      return (
        p.name.toLowerCase().includes(q) ||
        (p.tax_number ?? "").toLowerCase().includes(q) ||
        (p.contact_name ?? "").toLowerCase().includes(q) ||
        (p.contact_email ?? "").toLowerCase().includes(q)
      );
    });
  }, [partners, search, typeFilter]);

  function edit(p: Partner) {
    setError(null);
    setForm({
      id: p.id,
      name: p.name,
      partner_type: p.partner_type,
      tax_number: p.tax_number ?? "",
      eu_tax_number: p.eu_tax_number ?? "",
      reg_number: p.reg_number ?? "",
      contact_name: p.contact_name ?? "",
      contact_email: p.contact_email ?? "",
      contact_phone: p.contact_phone ?? "",
      website: p.website ?? "",
      address: p.address ?? "",
      billing_address: p.billing_address ?? "",
      bank_account: p.bank_account ?? "",
      payment_terms_days: p.payment_terms_days != null ? String(p.payment_terms_days) : "",
      notes: p.notes ?? "",
      is_active: p.is_active,
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
        partner_type: form.partner_type,
        tax_number: form.tax_number || null,
        eu_tax_number: form.eu_tax_number || null,
        reg_number: form.reg_number || null,
        contact_name: form.contact_name || null,
        contact_email: form.contact_email || null,
        contact_phone: form.contact_phone || null,
        website: form.website || null,
        address: form.address || null,
        billing_address: form.billing_address || null,
        bank_account: form.bank_account || null,
        payment_terms_days: form.payment_terms_days ? Number(form.payment_terms_days) : null,
        notes: form.notes || null,
        is_active: form.is_active,
      };
      if (form.id) await api.patch(`/api/partners/${form.id}`, body);
      else await api.post("/api/partners", body);
      setForm(null);
      load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const field = (
    label: string,
    key: keyof typeof EMPTY,
    opts: { type?: string; mono?: boolean } = {},
  ) => (
    <label className="block text-sm">
      {label}
      <input
        type={opts.type ?? "text"}
        value={form ? String(form[key] ?? "") : ""}
        onChange={(e) => form && setForm({ ...form, [key]: e.target.value })}
        className={`mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 ${opts.mono ? "font-mono" : ""}`}
      />
    </label>
  );

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("partners.title")}</h1>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("partners.search")}
          className="min-w-48 flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
        />
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          <option value="">{t("partners.allTypes")}</option>
          {(["customer", "supplier", "both"] as const).map((tp) => (
            <option key={tp} value={tp}>{t(`partners.types.${tp}`)}</option>
          ))}
        </select>
        <button
          onClick={() => { setError(null); setForm({ ...EMPTY }); }}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
        >
          {t("partners.new")}
        </button>
      </div>

      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
              <th className="px-4 py-3">{t("partners.name")}</th>
              <th className="px-4 py-3">{t("partners.type")}</th>
              <th className="px-4 py-3">{t("partners.taxNumber")}</th>
              <th className="px-4 py-3">{t("partners.contact")}</th>
              <th className="px-4 py-3">{t("partners.assets")}</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((p) => (
              <tr key={p.id} className="border-b border-slate-100 last:border-0">
                <td className="px-4 py-3">
                  <div className="font-medium">{p.name}</div>
                  {!p.is_active && (
                    <span className="mt-0.5 inline-block rounded bg-slate-200 px-1.5 py-0.5 text-xs">
                      {t("partners.inactive")}
                    </span>
                  )}
                </td>
                <td className="px-4 py-3">
                  <span className={`rounded px-2 py-0.5 text-xs font-medium ${TYPE_COLORS[p.partner_type]}`}>
                    {t(`partners.types.${p.partner_type}`)}
                  </span>
                </td>
                <td className="px-4 py-3 font-mono text-xs text-slate-500">{p.tax_number ?? "—"}</td>
                <td className="px-4 py-3 text-slate-500">
                  {p.contact_name ?? "—"}
                  {p.contact_phone && <div className="text-xs text-slate-400">{p.contact_phone}</div>}
                </td>
                <td className="px-4 py-3 text-slate-500">{p.asset_count}</td>
                <td className="px-4 py-3 text-right">
                  <button
                    onClick={() => edit(p)}
                    className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100"
                  >
                    {t("common.edit")}
                  </button>
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-10 text-center text-slate-400">{t("partners.empty")}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {form && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4">
          <form onSubmit={save} className="my-8 w-full max-w-2xl space-y-4 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">
              {form.id ? t("partners.editTitle") : t("partners.newTitle")}
            </h2>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="block text-sm sm:col-span-2">
                {t("partners.name")} *
                <input
                  required
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                />
              </label>
              <label className="block text-sm">
                {t("partners.type")}
                <select
                  value={form.partner_type}
                  onChange={(e) => setForm({ ...form, partner_type: e.target.value as PartnerType })}
                  className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2"
                >
                  {(["customer", "supplier", "both"] as const).map((tp) => (
                    <option key={tp} value={tp}>{t(`partners.types.${tp}`)}</option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-2 text-sm sm:mt-6">
                <input
                  type="checkbox"
                  checked={form.is_active}
                  onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
                  className="h-4 w-4"
                />
                {t("partners.active")}
              </label>
            </div>

            <fieldset className="space-y-3 rounded-xl border border-slate-200 p-3">
              <legend className="px-1 text-xs font-semibold uppercase text-slate-400">{t("partners.legalSection")}</legend>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                {field(t("partners.taxNumber"), "tax_number", { mono: true })}
                {field(t("partners.euTaxNumber"), "eu_tax_number", { mono: true })}
                {field(t("partners.regNumber"), "reg_number", { mono: true })}
              </div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {field(t("partners.bankAccount"), "bank_account", { mono: true })}
                {field(t("partners.paymentTerms"), "payment_terms_days", { type: "number" })}
              </div>
            </fieldset>

            <fieldset className="space-y-3 rounded-xl border border-slate-200 p-3">
              <legend className="px-1 text-xs font-semibold uppercase text-slate-400">{t("partners.contactSection")}</legend>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                {field(t("partners.contactName"), "contact_name")}
                {field(t("partners.contactPhone"), "contact_phone")}
                {field(t("partners.contactEmail"), "contact_email", { type: "email" })}
              </div>
              {field(t("partners.website"), "website")}
            </fieldset>

            <fieldset className="space-y-3 rounded-xl border border-slate-200 p-3">
              <legend className="px-1 text-xs font-semibold uppercase text-slate-400">{t("partners.addressSection")}</legend>
              {field(t("partners.address"), "address")}
              {field(t("partners.billingAddress"), "billing_address")}
            </fieldset>

            <label className="block text-sm">
              {t("partners.notes")}
              <textarea
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
                rows={2}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>

            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">
                {t("common.cancel")}
              </button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
                {busy ? t("common.saving") : t("common.save")}
              </button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
