"use client";

/** Partnerek törzsadat: cégadatok (adószám, cégjegyzékszám, bank, fizetési
 *  határidő), kapcsolattartó, székhely/számlázási cím — teljes nyilvántartás. */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AppShell from "@/components/AppShell";
import PartnerInfo from "@/components/PartnerInfo";
import IconLegend from "@/components/IconLegend";
import { api, errorMessage } from "@/lib/api";
import { COMPANIES, COMPANY_CHIP, COMPANY_SHORT, type CompanyKey } from "@/lib/companies";
import { useT } from "@/lib/i18n";
import { usePerms } from "@/lib/perms";
import { useUI } from "@/lib/ui";

type PartnerType = "customer" | "supplier" | "both";

interface Partner {
  id: string;
  partner_code: string | null;
  name: string;
  company_name: string | null;
  invoicing_company: CompanyKey | null;
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
  address_zip: string | null;
  address_city: string | null;
  address_street: string | null;
  address_number: string | null;
  billing_zip: string | null;
  billing_city: string | null;
  billing_street: string | null;
  billing_number: string | null;
  bank_account: string | null;
  payment_terms_days: number | null;
  contract_min_portions: number | null;
  contract_below_min_price: number | null;
  contract_min_kg: number | null;
  contract_below_min_price_kg: number | null;
  contract_no_min_until: string | null;
  contract_rent_if_below_min: boolean;
  notes: string | null;
  is_active: boolean;
  asset_count: number;
}

const EMPTY = {
  id: "",
  partner_code: "",
  name: "",
  company_name: "",
  invoicing_company: "",
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
  address_zip: "",
  address_city: "",
  address_street: "",
  address_number: "",
  billing_zip: "",
  billing_city: "",
  billing_street: "",
  billing_number: "",
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
  const { toast, confirm } = useUI();
  const canDelete = usePerms().can("delete");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [partners, setPartners] = useState<Partner[]>([]);
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [companyFilter, setCompanyFilter] = useState("");
  const [form, setForm] = useState<typeof EMPTY | null>(null);
  const [infoPartner, setInfoPartner] = useState<string | null>(null);
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
      if (companyFilter === "none" && p.invoicing_company) return false;
      if (companyFilter && companyFilter !== "none" && p.invoicing_company !== companyFilter) return false;
      if (!q) return true;
      return (
        p.name.toLowerCase().includes(q) ||
        (p.company_name ?? "").toLowerCase().includes(q) ||
        (p.partner_code ?? "").toLowerCase().includes(q) ||
        (p.tax_number ?? "").toLowerCase().includes(q) ||
        (p.contact_name ?? "").toLowerCase().includes(q) ||
        (p.contact_email ?? "").toLowerCase().includes(q)
      );
    });
  }, [partners, search, typeFilter, companyFilter]);

  async function copyPortalLink(p: Partner) {
    try {
      const res = await api.post<{ path: string }>(`/api/partners/${p.id}/portal-link`);
      const url = `${window.location.origin}${res.path}`;
      try {
        await navigator.clipboard.writeText(url);
        toast(t("portal.linkCopied"), "success");
      } catch {
        toast(url, "info"); // vágólap-hozzáférés híján legalább mutassuk
      }
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  function edit(p: Partner) {
    setError(null);
    setForm({
      id: p.id,
      partner_code: p.partner_code ?? "",
      name: p.name,
      company_name: p.company_name ?? "",
      invoicing_company: p.invoicing_company ?? "",
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
      address_zip: p.address_zip ?? "",
      address_city: p.address_city ?? "",
      address_street: p.address_street ?? "",
      address_number: p.address_number ?? "",
      billing_zip: p.billing_zip ?? "",
      billing_city: p.billing_city ?? "",
      billing_street: p.billing_street ?? "",
      billing_number: p.billing_number ?? "",
      bank_account: p.bank_account ?? "",
      payment_terms_days: p.payment_terms_days != null ? String(p.payment_terms_days) : "",
      notes: p.notes ?? "",
      is_active: p.is_active,
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
    if (!(await confirm(t("bulk.confirm", { count: selected.size })))) return;
    try {
      const res = await api.post<{ deleted: number; blocked: { name: string; code: string }[] }>(
        "/api/partners/bulk-delete",
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
        company_name: form.company_name || null,
        invoicing_company: form.invoicing_company || null,
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
        address_zip: form.address_zip || null,
        address_city: form.address_city || null,
        address_street: form.address_street || null,
        address_number: form.address_number || null,
        billing_zip: form.billing_zip || null,
        billing_city: form.billing_city || null,
        billing_street: form.billing_street || null,
        billing_number: form.billing_number || null,
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

  // ── Szerződések (külön entitás, érvényességgel) ──
  interface Contract {
    id: string;
    valid_from: string;
    valid_to: string | null;
    min_portions: number | null;
    below_min_price: number | null;
    min_kg: number | null;
    below_min_price_kg: number | null;
    rent_if_below_min: boolean;
    note: string | null;
    status: "active" | "future" | "expired";
  }
  const [contractsFor, setContractsFor] = useState<{ id: string; name: string } | null>(null);
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [cForm, setCForm] = useState<{
    id: string; valid_from: string; valid_to: string; min_portions: string;
    below_min_price: string; min_kg: string; below_min_price_kg: string;
    rent_if_below_min: boolean; note: string;
  } | null>(null);
  const [cError, setCError] = useState<string | null>(null);
  const [cBusy, setCBusy] = useState(false);

  async function openContracts(p: { id: string; name: string }) {
    setContractsFor(p);
    setCForm(null);
    setCError(null);
    try {
      setContracts(await api.get<Contract[]>(`/api/partners/${p.id}/contracts`));
    } catch {
      setContracts([]);
    }
  }

  function newContractForm() {
    setCError(null);
    setCForm({
      id: "", valid_from: new Date().toISOString().slice(0, 10), valid_to: "",
      min_portions: "", below_min_price: "", min_kg: "", below_min_price_kg: "",
      rent_if_below_min: false, note: "",
    });
  }

  function editContractForm(c: Contract) {
    setCError(null);
    setCForm({
      id: c.id, valid_from: c.valid_from, valid_to: c.valid_to ?? "",
      min_portions: c.min_portions != null ? String(c.min_portions) : "",
      below_min_price: c.below_min_price != null ? String(c.below_min_price) : "",
      min_kg: c.min_kg != null ? String(c.min_kg) : "",
      below_min_price_kg: c.below_min_price_kg != null ? String(c.below_min_price_kg) : "",
      rent_if_below_min: c.rent_if_below_min, note: c.note ?? "",
    });
  }

  async function saveContract(e: React.FormEvent) {
    e.preventDefault();
    if (!cForm || !contractsFor) return;
    setCBusy(true);
    setCError(null);
    try {
      const body = {
        valid_from: cForm.valid_from,
        valid_to: cForm.valid_to || null,
        min_portions: cForm.min_portions ? Number(cForm.min_portions) : null,
        below_min_price: cForm.below_min_price ? Number(cForm.below_min_price) : null,
        min_kg: cForm.min_kg ? Number(cForm.min_kg) : null,
        below_min_price_kg: cForm.below_min_price_kg ? Number(cForm.below_min_price_kg) : null,
        rent_if_below_min: cForm.rent_if_below_min,
        note: cForm.note || null,
      };
      if (cForm.id) await api.patch(`/api/partners/${contractsFor.id}/contracts/${cForm.id}`, body);
      else await api.post(`/api/partners/${contractsFor.id}/contracts`, body);
      toast(t("contracts.saved"), "success");
      setCForm(null);
      await openContracts(contractsFor);
      load();
    } catch (err) {
      setCError(errorMessage(err));
    } finally {
      setCBusy(false);
    }
  }

  async function deleteContract(c: Contract) {
    if (!contractsFor) return;
    if (!(await confirm(t("contracts.deleteConfirm", { from: c.valid_from })))) return;
    try {
      await api.delete(`/api/partners/${contractsFor.id}/contracts/${c.id}`);
      toast(t("contracts.deleted"), "success");
      await openContracts(contractsFor);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  // Közhiteles cégadat-lekérés adószámból (EU VIES): hivatalos cégnév +
  // székhely. Csak az üres mezőket tölti ki, kézi értéket nem ír felül.
  const [lookupBusy, setLookupBusy] = useState(false);

  async function taxLookup() {
    if (!form || !form.tax_number.trim()) return;
    setLookupBusy(true);
    try {
      const res = await api.get<{
        found: boolean;
        company_name?: string | null;
        address_zip?: string | null;
        address_city?: string | null;
        address_street?: string | null;
      }>(`/api/partners/tax-lookup?tax_number=${encodeURIComponent(form.tax_number.trim())}`);
      if (!res.found) {
        toast(t("partners.lookupNotFound"), "error");
        return;
      }
      setForm((f) =>
        f
          ? {
              ...f,
              company_name: f.company_name || res.company_name || "",
              address_zip: f.address_zip || res.address_zip || "",
              address_city: f.address_city || res.address_city || "",
              address_street: f.address_street || res.address_street || "",
            }
          : f,
      );
      toast(t("partners.lookupDone"), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setLookupBusy(false);
    }
  }

  // Irányítószám → város automatikus kitöltés (4 számjegynél, ha a város üres
  // vagy egy korábbi auto-kitöltés eredménye).
  const lastAutoCity = useRef<Record<string, string>>({});

  async function zipLookup(zipKey: "address_zip" | "billing_zip", zip: string) {
    const cityKey = zipKey === "address_zip" ? "address_city" : "billing_city";
    if (!/^\d{4}$/.test(zip)) return;
    try {
      const res = await api.get<{ city: string }>(`/api/geo/zip/${zip}`);
      setForm((f) => {
        if (!f) return f;
        const current = String(f[cityKey] ?? "").trim();
        if (current && current !== lastAutoCity.current[cityKey]) return f; // kézi értéket nem írunk felül
        lastAutoCity.current[cityKey] = res.city;
        return { ...f, [cityKey]: res.city };
      });
    } catch {
      /* ismeretlen irányítószám — nem töltünk ki semmit */
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
        onChange={(e) => {
          if (!form) return;
          const value = e.target.value;
          setForm({ ...form, [key]: value });
          if (key === "address_zip" || key === "billing_zip") zipLookup(key, value.trim());
        }}
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
        <select
          value={companyFilter}
          onChange={(e) => setCompanyFilter(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          <option value="">{t("partners.allCompanies")}</option>
          <option value="xp">{COMPANY_SHORT.xp}</option>
          <option value="pc">{COMPANY_SHORT.pc}</option>
          <option value="none">{t("partners.noCompany")}</option>
        </select>
        {selected.size > 0 && (
          <button
            onClick={bulkDelete}
            className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
          >
            {t("bulk.deleteSelected", { count: selected.size })}
          </button>
        )}
        <button
          onClick={() => { setError(null); setForm({ ...EMPTY }); }}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
        >
          {t("partners.new")}
        </button>
      </div>

      <IconLegend
        items={[
          { icon: "🔗", label: t("portal.linkBtn") },
          { icon: "📜", label: t("contracts.title") },
          { icon: "✏️", label: t("common.edit") },
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
                  checked={filtered.length > 0 && filtered.every((p) => selected.has(p.id))}
                  onChange={(e) =>
                    setSelected(e.target.checked ? new Set(filtered.map((p) => p.id)) : new Set())
                  }
                  className="h-4 w-4"
                />
              </th>
              )}
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("partners.name")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("partners.taxNumber")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("partners.contact")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3 text-right">{t("partners.assets")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((p) => (
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
                  <button
                    onClick={() => setInfoPartner(p.id)}
                    className="text-left font-medium text-indigo-700 hover:underline"
                  >
                    {p.name}
                  </button>
                  {p.company_name && p.company_name !== p.name && (
                    <div className="text-xs text-slate-400">{p.company_name}</div>
                  )}
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-mono text-xs font-semibold text-indigo-700">{p.partner_code ?? "—"}</span>
                    <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${TYPE_COLORS[p.partner_type]}`}>
                      {t(`partners.types.${p.partner_type}`)}
                    </span>
                    {p.invoicing_company && (
                      <span
                        title={COMPANIES[p.invoicing_company]}
                        className={`rounded px-1.5 py-0.5 text-xs font-medium ${COMPANY_CHIP[p.invoicing_company]}`}
                      >
                        {COMPANY_SHORT[p.invoicing_company]}
                      </span>
                    )}
                    {!p.is_active && (
                      <span className="rounded bg-slate-200 px-1.5 py-0.5 text-xs">{t("partners.inactive")}</span>
                    )}
                  </div>
                </td>
                <td className="px-4 py-3 font-mono text-xs text-slate-500">{p.tax_number ?? "—"}</td>
                <td className="px-4 py-3 text-slate-500">
                  {p.contact_name ?? "—"}
                  {p.contact_phone && <div className="text-xs text-slate-400">{p.contact_phone}</div>}
                </td>
                <td className="px-4 py-3 text-right text-slate-500">{p.asset_count}</td>
                <td className="px-4 py-3 text-right">
                  <div className="flex items-center justify-end gap-1.5">
                    <button
                      onClick={() => copyPortalLink(p)}
                      title={t("portal.linkBtn")}
                      className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100"
                    >
                      🔗
                    </button>
                    <button
                      onClick={() => openContracts({ id: p.id, name: p.name })}
                      title={t("contracts.title")}
                      className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100"
                    >
                      📜
                    </button>
                    <button
                      onClick={() => edit(p)}
                      title={t("common.edit")}
                      className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100"
                    >
                      ✏️
                    </button>
                  </div>
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
        <div onMouseDown={(e) => { if (e.target === e.currentTarget) setForm(null); }} className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4">
          <form onSubmit={save} className="my-8 w-full max-w-2xl space-y-4 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">
              {form.id ? t("partners.editTitle") : t("partners.newTitle")}
              {form.partner_code && (
                <span className="ml-2 font-mono text-sm font-semibold text-indigo-700">{form.partner_code}</span>
              )}
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
              <label className="block text-sm">
                {t("partners.invoicingCompany")}
                <select
                  value={form.invoicing_company}
                  onChange={(e) => setForm({ ...form, invoicing_company: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2"
                >
                  <option value="">{t("partners.noCompany")}</option>
                  <option value="xp">{COMPANIES.xp}</option>
                  <option value="pc">{COMPANIES.pc}</option>
                </select>
              </label>
              <div className="flex flex-wrap items-end gap-3">
                <div className="min-w-56 flex-1">{field(t("partners.companyName"), "company_name")}</div>
                <button
                  type="button"
                  onClick={taxLookup}
                  disabled={lookupBusy || !form.tax_number.trim()}
                  title={t("partners.lookupHint")}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-100 disabled:opacity-50"
                >
                  {lookupBusy ? t("common.loading") : t("partners.lookupBtn")}
                </button>
              </div>
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
              <div>
                <p className="mb-1 text-sm font-medium text-slate-600">{t("partners.address")}</p>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  {field(t("partners.zip"), "address_zip")}
                  {field(t("partners.city"), "address_city")}
                  {field(t("partners.street"), "address_street")}
                  {field(t("partners.houseNo"), "address_number")}
                </div>
                {form.address && !form.address_zip && !form.address_city && !form.address_street && (
                  <p className="mt-1 text-xs text-slate-400">{t("partners.legacyAddress", { address: form.address })}</p>
                )}
              </div>
              <div>
                <p className="mb-1 text-sm font-medium text-slate-600">{t("partners.billingAddress")}</p>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  {field(t("partners.zip"), "billing_zip")}
                  {field(t("partners.city"), "billing_city")}
                  {field(t("partners.street"), "billing_street")}
                  {field(t("partners.houseNo"), "billing_number")}
                </div>
                {form.billing_address && !form.billing_zip && !form.billing_city && !form.billing_street && (
                  <p className="mt-1 text-xs text-slate-400">{t("partners.legacyAddress", { address: form.billing_address })}</p>
                )}
              </div>
            </fieldset>

            <fieldset className="space-y-2 rounded-xl border border-slate-200 p-3">
              <legend className="px-1 text-xs font-semibold uppercase text-slate-400">{t("partners.contractSection")}</legend>
              <p className="text-xs text-slate-400">{t("contracts.movedHint")}</p>
              {form.id ? (
                <button
                  type="button"
                  onClick={() => openContracts({ id: form.id, name: form.name })}
                  className="rounded-lg border border-indigo-300 px-3 py-2 text-sm font-medium text-indigo-700 hover:bg-indigo-50"
                >
                  📜 {t("contracts.manage")}
                </button>
              ) : (
                <p className="text-xs text-slate-400">{t("contracts.afterSaveHint")}</p>
              )}
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

      {contractsFor && (
        <div
          onMouseDown={(e) => { if (e.target === e.currentTarget) setContractsFor(null); }}
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4"
        >
          <div className="my-8 w-full max-w-2xl space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">📜 {t("contracts.titleFor", { name: contractsFor.name })}</h2>
              <button onClick={() => setContractsFor(null)} className="text-sm text-slate-500 hover:underline">
                ✕ {t("common.close")}
              </button>
            </div>
            <p className="text-xs text-slate-400">{t("contracts.hint")}</p>

            {!cForm && (
              <>
                <button
                  onClick={newContractForm}
                  className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
                >
                  {t("contracts.new")}
                </button>
                <div className="space-y-2">
                  {contracts.map((c) => (
                    <div key={c.id} className="rounded-xl border border-slate-200 p-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                          c.status === "active" ? "bg-emerald-100 text-emerald-800"
                          : c.status === "future" ? "bg-sky-100 text-sky-800"
                          : "bg-slate-200 text-slate-500"
                        }`}>
                          {t(`contracts.status_${c.status}`)}
                        </span>
                        <span className="text-sm font-medium">
                          {c.valid_from} → {c.valid_to ?? t("contracts.openEnded")}
                        </span>
                        <span className="ml-auto flex gap-1">
                          <button onClick={() => editContractForm(c)} title={t("common.edit")} className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100">✏️</button>
                          <button onClick={() => deleteContract(c)} title={t("contracts.delete")} className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-red-50">🗑️</button>
                        </span>
                      </div>
                      <div className="mt-1 text-xs text-slate-600">
                        {[
                          c.min_portions != null ? t("contracts.sumMinPortions", { n: c.min_portions, price: c.below_min_price ?? "—" }) : null,
                          c.min_kg != null ? t("contracts.sumMinKg", { n: c.min_kg, price: c.below_min_price_kg ?? "—" }) : null,
                          c.rent_if_below_min ? t("contracts.sumRent") : null,
                          c.note,
                        ].filter(Boolean).join(" · ") || t("contracts.noTerms")}
                      </div>
                    </div>
                  ))}
                  {contracts.length === 0 && (
                    <p className="rounded-xl border border-dashed border-slate-300 px-3 py-6 text-center text-sm text-slate-400">
                      {t("contracts.empty")}
                    </p>
                  )}
                </div>
              </>
            )}

            {cForm && (
              <form onSubmit={saveContract} className="space-y-3">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <label className="block text-sm">
                    {t("contracts.validFrom")} *
                    <input required type="date" value={cForm.valid_from} onChange={(e) => setCForm({ ...cForm, valid_from: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
                  </label>
                  <label className="block text-sm">
                    {t("contracts.validTo")}
                    <input type="date" value={cForm.valid_to} onChange={(e) => setCForm({ ...cForm, valid_to: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
                    <span className="mt-0.5 block text-xs text-slate-400">{t("contracts.validToHint")}</span>
                  </label>
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <label className="block text-sm">
                    {t("partners.contractMinPortions")}
                    <input type="number" min={0} value={cForm.min_portions} onChange={(e) => setCForm({ ...cForm, min_portions: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
                  </label>
                  <label className="block text-sm">
                    {t("partners.contractBelowMinPrice")}
                    <input type="number" min={0} step="0.01" value={cForm.below_min_price} onChange={(e) => setCForm({ ...cForm, below_min_price: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
                  </label>
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <label className="block text-sm">
                    {t("partners.contractMinKg")}
                    <input type="number" min={0} step="0.1" value={cForm.min_kg} onChange={(e) => setCForm({ ...cForm, min_kg: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
                  </label>
                  <label className="block text-sm">
                    {t("partners.contractBelowMinPriceKg")}
                    <input type="number" min={0} step="0.01" value={cForm.below_min_price_kg} onChange={(e) => setCForm({ ...cForm, below_min_price_kg: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
                  </label>
                </div>
                <label className="flex items-start gap-2 text-sm">
                  <input type="checkbox" checked={cForm.rent_if_below_min} onChange={(e) => setCForm({ ...cForm, rent_if_below_min: e.target.checked })} className="mt-0.5 h-4 w-4" />
                  <span>
                    {t("partners.contractRentIfBelowMin")}
                    <span className="block text-xs text-slate-400">{t("partners.contractRentIfBelowMinHint")}</span>
                  </span>
                </label>
                <label className="block text-sm">
                  {t("partners.notes")}
                  <input value={cForm.note} onChange={(e) => setCForm({ ...cForm, note: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
                </label>
                {cError && <p className="text-sm text-red-600">{cError}</p>}
                <div className="flex justify-end gap-2 pt-1">
                  <button type="button" onClick={() => setCForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
                  <button disabled={cBusy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{cBusy ? t("common.saving") : t("common.save")}</button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}

      <PartnerInfo partnerId={infoPartner} onClose={() => setInfoPartner(null)} />
    </AppShell>
  );
}
