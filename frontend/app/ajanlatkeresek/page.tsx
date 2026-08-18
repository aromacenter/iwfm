"use client";

/** Ajánlatkérések kezelése: a publikus /ajanlat oldalról beérkező kérések.
 *  Itt töltjük ki az adagárat és a dátumokat, generáljuk a szerződés szövegét
 *  (sablonból, szerkeszthető), és küldjük ki az aláíró-linket. Aláírás után a
 *  partner automatikusan létrejön. Emellett itt kezelhető a publikus oldal
 *  gép-katalógusa és a szerződés-sablon is. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import PartnerInfo from "@/components/PartnerInfo";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface Quote {
  id: string;
  serial: string;
  status: string;
  company_name: string;
  contact_name: string;
  contact_email: string;
  contact_phone: string;
  tax_number: string;
  address_zip: string;
  address_city: string;
  address_street: string;
  address_number: string | null;
  billing_zip: string | null;
  billing_city: string | null;
  billing_street: string | null;
  billing_number: string | null;
  bank_account: string | null;
  machine_type_name: string | null;
  message: string | null;
  price_per_portion: number | null;
  min_portions: number | null;
  below_min_price: number | null;
  rent_if_below_min: boolean;
  valid_from: string | null;
  valid_to: string | null;
  admin_note: string | null;
  contract_text: string | null;
  sign_url: string | null;
  sent_at: string | null;
  signed_at: string | null;
  signed_name: string | null;
  partner_id: string | null;
  created_at: string;
}

interface MachineType {
  id: string;
  name: string;
  description: string | null;
  ideal_for: string | null;
  sort_order: number;
  active: boolean;
  has_image: boolean;
}

const STATUS_STYLE: Record<string, string> = {
  new: "bg-sky-100 text-sky-700",
  sent: "bg-amber-100 text-amber-700",
  signed: "bg-emerald-100 text-emerald-700",
  cancelled: "bg-slate-200 text-slate-500",
};

const EMPTY_MT = { name: "", description: "", ideal_for: "", sort_order: "0", active: true };

export default function AjanlatkeresekPage() {
  const { t, lang } = useT();
  const { toast, confirm } = useUI();
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [filter, setFilter] = useState<string>("all");
  const [open, setOpen] = useState<Quote | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [rentBelow, setRentBelow] = useState(false);
  const [contractText, setContractText] = useState("");
  const [busy, setBusy] = useState(false);
  const [infoPartner, setInfoPartner] = useState<string | null>(null);

  // katalógus + sablon modalok
  const [showTypes, setShowTypes] = useState(false);
  const [types, setTypes] = useState<MachineType[]>([]);
  const [mtForm, setMtForm] = useState<{ [k: string]: string | boolean }>({ ...EMPTY_MT });
  const [mtEditId, setMtEditId] = useState<string | null>(null);
  const [mtImage, setMtImage] = useState<string | null>(null); // új data URL
  const [mtRemoveImage, setMtRemoveImage] = useState(false);
  const [mtHasImage, setMtHasImage] = useState(false);

  function readImageFile(file: File | undefined) {
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) {
      toast(t("quote.imageTooBig"), "error");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setMtImage(String(reader.result));
      setMtRemoveImage(false);
    };
    reader.readAsDataURL(file);
  }
  const [showTemplate, setShowTemplate] = useState(false);
  const [template, setTemplate] = useState("");
  const [defaultTemplate, setDefaultTemplate] = useState("");

  const fmt = (dt: string) =>
    new Date(dt).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { dateStyle: "short", timeStyle: "short" });

  const load = useCallback(() => {
    api.get<Quote[]>("/api/quotes").then(setQuotes).catch(() => {});
  }, []);
  useEffect(load, [load]);

  const loadTypes = useCallback(() => {
    api.get<MachineType[]>("/api/quotes/machine-types").then(setTypes).catch(() => {});
  }, []);

  const rows = quotes.filter((q) => filter === "all" || q.status === filter);
  const counts: Record<string, number> = { all: quotes.length };
  for (const q of quotes) counts[q.status] = (counts[q.status] ?? 0) + 1;

  function openQuote(q: Quote) {
    setOpen(q);
    setRentBelow(q.rent_if_below_min);
    setContractText(q.contract_text ?? "");
    setForm({
      company_name: q.company_name,
      contact_name: q.contact_name,
      contact_email: q.contact_email,
      contact_phone: q.contact_phone,
      tax_number: q.tax_number,
      address_zip: q.address_zip,
      address_city: q.address_city,
      address_street: q.address_street,
      address_number: q.address_number ?? "",
      bank_account: q.bank_account ?? "",
      machine_type_name: q.machine_type_name ?? "",
      price_per_portion: q.price_per_portion != null ? String(q.price_per_portion) : "",
      min_portions: q.min_portions != null ? String(q.min_portions) : "",
      below_min_price: q.below_min_price != null ? String(q.below_min_price) : "",
      valid_from: q.valid_from ?? "",
      valid_to: q.valid_to ?? "",
      admin_note: q.admin_note ?? "",
    });
  }

  function patchBody(): Record<string, unknown> {
    const num = (v: string) => (v.trim() === "" ? null : Number(v));
    return {
      company_name: form.company_name,
      contact_name: form.contact_name,
      contact_email: form.contact_email,
      contact_phone: form.contact_phone,
      tax_number: form.tax_number,
      address_zip: form.address_zip,
      address_city: form.address_city,
      address_street: form.address_street,
      address_number: form.address_number || null,
      bank_account: form.bank_account || null,
      machine_type_name: form.machine_type_name || null,
      price_per_portion: num(form.price_per_portion),
      min_portions: num(form.min_portions),
      below_min_price: num(form.below_min_price),
      rent_if_below_min: rentBelow,
      valid_from: form.valid_from || null,
      valid_to: form.valid_to || null,
      admin_note: form.admin_note || null,
      contract_text: contractText || null,
    };
  }

  async function save(): Promise<Quote | null> {
    if (!open) return null;
    try {
      const updated = await api.patch<Quote>(`/api/quotes/${open.id}`, patchBody());
      setOpen(updated);
      load();
      return updated;
    } catch (err) {
      toast(errorMessage(err), "error");
      return null;
    }
  }

  async function prepare() {
    if (!open) return;
    setBusy(true);
    try {
      const saved = await save();
      if (!saved) return;
      const updated = await api.post<Quote>(`/api/quotes/${open.id}/prepare`);
      setOpen(updated);
      setContractText(updated.contract_text ?? "");
      toast(t("quote.prepared"), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    if (!open) return;
    setBusy(true);
    try {
      const saved = await save();
      if (!saved) return;
      const res = await api.post<{ sign_url: string; email_sent: boolean }>(
        `/api/quotes/${open.id}/send`
      );
      load();
      const q = await api.get<Quote>(`/api/quotes/${open.id}`);
      setOpen(q);
      if (res.email_sent) {
        toast(t("quote.emailSent"), "success");
      } else {
        await navigator.clipboard.writeText(res.sign_url).catch(() => {});
        toast(t("quote.linkCopied"), "success");
      }
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function cancelQuote() {
    if (!open) return;
    if (!(await confirm(t("quote.cancelConfirm", { serial: open.serial })))) return;
    try {
      await api.post(`/api/quotes/${open.id}/cancel`);
      load();
      setOpen(null);
      toast(t("quote.cancelled"), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function copyLink() {
    if (!open?.sign_url) return;
    await navigator.clipboard.writeText(open.sign_url).catch(() => {});
    toast(t("quote.linkCopied"), "success");
  }

  // --- katalógus ---
  async function saveMType() {
    const body = {
      name: String(mtForm.name),
      description: String(mtForm.description) || null,
      ideal_for: String(mtForm.ideal_for) || null,
      sort_order: Number(mtForm.sort_order) || 0,
      active: Boolean(mtForm.active),
      image: mtImage,
      remove_image: mtRemoveImage,
    };
    try {
      if (mtEditId) await api.patch(`/api/quotes/machine-types/${mtEditId}`, body);
      else await api.post("/api/quotes/machine-types", body);
      setMtForm({ ...EMPTY_MT });
      setMtEditId(null);
      setMtImage(null);
      setMtRemoveImage(false);
      setMtHasImage(false);
      loadTypes();
      toast(t("common.saved"), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function deleteMType(m: MachineType) {
    if (!(await confirm(t("quote.deleteTypeConfirm", { name: m.name })))) return;
    try {
      await api.delete(`/api/quotes/machine-types/${m.id}`);
      loadTypes();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function openTemplate() {
    try {
      const res = await api.get<{ contract_template: string | null; default_template: string }>(
        "/api/quotes/template"
      );
      setTemplate(res.contract_template ?? "");
      setDefaultTemplate(res.default_template);
      setShowTemplate(true);
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function saveTemplate() {
    try {
      await api.put("/api/quotes/template", { contract_template: template || null });
      setShowTemplate(false);
      toast(t("common.saved"), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  const inputCls =
    "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none";
  const labelCls = "text-sm";
  const spanCls = "mb-1 block text-slate-600 text-xs";
  const editable = open != null && open.status !== "signed" && open.status !== "cancelled";

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("quote.adminTitle")}</h1>
        <div className="flex flex-wrap gap-1.5">
          {["all", "new", "sent", "signed", "cancelled"].map((key) => (
            <button
              key={key}
              onClick={() => setFilter(key)}
              className={`rounded-full px-3 py-1.5 text-xs font-medium transition ${
                filter === key
                  ? "bg-indigo-600 text-white"
                  : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"
              }`}
            >
              {key === "all" ? t("contracts.filterAll") : t(`quote.status_${key}`)} ({counts[key] ?? 0})
            </button>
          ))}
        </div>
        <div className="ml-auto flex gap-2">
          <button
            onClick={() => { loadTypes(); setShowTypes(true); }}
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-50"
          >
            ☕ {t("quote.machineCatalog")}
          </button>
          <button
            onClick={openTemplate}
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-50"
          >
            📄 {t("quote.templateBtn")}
          </button>
          <button
            onClick={async () => {
              const url = `${window.location.origin}/ajanlat`;
              await navigator.clipboard.writeText(url).catch(() => {});
              toast(t("quote.publicLinkCopied"), "success");
            }}
            className="rounded-lg border border-indigo-300 bg-indigo-50 px-3 py-2 text-sm text-indigo-700 hover:bg-indigo-100"
          >
            🔗 {t("quote.publicPage")}
          </button>
        </div>
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-slate-500">
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("quote.request")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("quote.machine")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("quote.terms")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("service.status")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((q) => (
              <tr
                key={q.id}
                onClick={() => openQuote(q)}
                className="cursor-pointer border-b border-slate-100 align-top last:border-0 hover:bg-slate-50"
              >
                <td className="px-4 py-2.5">
                  <div className="font-medium text-slate-900">{q.company_name}</div>
                  <div className="text-xs text-slate-400">
                    <span className="font-mono">{q.serial}</span> · {fmt(q.created_at)}
                  </div>
                  <div className="text-xs text-slate-500">{q.contact_name} · {q.contact_email}</div>
                </td>
                <td className="px-4 py-2.5 text-slate-600">{q.machine_type_name ?? "—"}</td>
                <td className="px-4 py-2.5 text-xs text-slate-500">
                  {q.price_per_portion != null ? (
                    <>
                      {q.price_per_portion} Ft/adag
                      {q.min_portions != null && <> · min. {q.min_portions} adag</>}
                      {q.valid_from && <> · {q.valid_from} →</>}
                    </>
                  ) : (
                    <span className="text-amber-600">{t("quote.termsMissing")}</span>
                  )}
                </td>
                <td className="px-4 py-2.5">
                  <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${STATUS_STYLE[q.status] ?? ""}`}>
                    {t(`quote.status_${q.status}`)}
                  </span>
                  {q.status === "signed" && q.partner_id && (
                    <button
                      onClick={(e) => { e.stopPropagation(); setInfoPartner(q.partner_id); }}
                      className="ml-2 text-xs text-indigo-700 underline decoration-indigo-300 underline-offset-2"
                    >
                      {t("quote.openPartner")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={4} className="px-4 py-10 text-center text-slate-400">{t("quote.empty")}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Részletek modal */}
      {open && (
        <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4">
          <div className="w-full max-w-3xl rounded-2xl bg-white p-5 shadow-xl">
            <div className="mb-4 flex items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-bold text-slate-900">{open.company_name}</h2>
                <div className="text-xs text-slate-400">
                  <span className="font-mono">{open.serial}</span> · {fmt(open.created_at)} ·{" "}
                  <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${STATUS_STYLE[open.status] ?? ""}`}>
                    {t(`quote.status_${open.status}`)}
                  </span>
                </div>
              </div>
              <button onClick={() => setOpen(null)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100">
                ✕ {t("common.close")}
              </button>
            </div>

            {open.message && (
              <div className="mb-4 rounded-xl border border-sky-200 bg-sky-50 px-4 py-2.5 text-sm text-sky-800">
                💬 {open.message}
              </div>
            )}
            {open.status === "signed" && (
              <div className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm text-emerald-800">
                ✅ {t("quote.signedBanner", { name: open.signed_name ?? "?", at: open.signed_at ? fmt(open.signed_at) : "" })}
                {open.partner_id && (
                  <button onClick={() => setInfoPartner(open.partner_id)} className="ml-2 underline">
                    {t("quote.openPartner")}
                  </button>
                )}
              </div>
            )}

            {/* Vevő adatai */}
            <h3 className="mb-2 text-sm font-semibold text-slate-700">{t("quote.companyData")}</h3>
            <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
              {([
                ["company_name", t("quote.companyName")],
                ["tax_number", t("quote.taxNumber")],
                ["bank_account", t("quote.bankAccount")],
                ["contact_name", t("quote.contactName")],
                ["contact_email", t("quote.contactEmail")],
                ["contact_phone", t("quote.contactPhone")],
                ["address_zip", t("partners.zip")],
                ["address_city", t("partners.city")],
                ["address_street", t("partners.street")],
                ["address_number", t("partners.houseNo")],
                ["machine_type_name", t("quote.machine")],
              ] as [string, string][]).map(([key, label]) => (
                <label key={key} className={labelCls}>
                  <span className={spanCls}>{label}</span>
                  <input
                    value={form[key] ?? ""}
                    onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                    disabled={!editable}
                    className={inputCls + " disabled:bg-slate-50 disabled:text-slate-500"}
                  />
                </label>
              ))}
            </div>

            {/* Feltételek */}
            <h3 className="mb-2 text-sm font-semibold text-slate-700">{t("quote.ourTerms")}</h3>
            <div className="mb-2 grid grid-cols-2 gap-3 sm:grid-cols-3">
              {([
                ["price_per_portion", t("quote.pricePerPortion"), "number"],
                ["min_portions", t("quote.minPortions"), "number"],
                ["below_min_price", t("quote.belowMinPrice"), "number"],
                ["valid_from", t("contracts.validFrom"), "date"],
                ["valid_to", t("contracts.validTo"), "date"],
              ] as [string, string, string][]).map(([key, label, type]) => (
                <label key={key} className={labelCls}>
                  <span className={spanCls}>{label}</span>
                  <input
                    type={type}
                    value={form[key] ?? ""}
                    onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                    disabled={!editable}
                    className={inputCls + " disabled:bg-slate-50 disabled:text-slate-500"}
                  />
                </label>
              ))}
              <label className="flex items-end gap-2 pb-2 text-sm text-slate-600">
                <input type="checkbox" checked={rentBelow} disabled={!editable} onChange={(e) => setRentBelow(e.target.checked)} />
                {t("partners.contractRentIfBelowMin")}
              </label>
            </div>
            <label className={labelCls + " mb-4 block"}>
              <span className={spanCls}>{t("quote.adminNote")}</span>
              <input
                value={form.admin_note ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, admin_note: e.target.value }))}
                disabled={!editable}
                className={inputCls + " disabled:bg-slate-50 disabled:text-slate-500"}
              />
            </label>

            {/* Szerződés szövege */}
            <div className="mb-1 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-700">{t("quote.contractText")}</h3>
              {editable && (
                <button onClick={prepare} disabled={busy} className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs hover:bg-slate-100 disabled:opacity-50">
                  ⚙️ {t("quote.generateContract")}
                </button>
              )}
            </div>
            <textarea
              value={contractText}
              onChange={(e) => setContractText(e.target.value)}
              disabled={!editable}
              rows={10}
              className={inputCls + " mb-4 font-mono text-xs disabled:bg-slate-50 disabled:text-slate-500"}
              placeholder={t("quote.contractPlaceholder")}
            />

            {open.sign_url && open.status !== "cancelled" && (
              <div className="mb-4 flex items-center gap-2 rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-2.5 text-sm text-indigo-800">
                <span className="min-w-0 flex-1 truncate">🔗 {open.sign_url}</span>
                <button onClick={copyLink} className="shrink-0 rounded-lg border border-indigo-300 px-2.5 py-1 text-xs hover:bg-indigo-100">
                  {t("quote.copyLink")}
                </button>
              </div>
            )}

            {editable && (
              <div className="flex flex-wrap justify-end gap-2">
                <button onClick={cancelQuote} className="mr-auto rounded-lg border border-red-300 px-4 py-2 text-sm text-red-600 hover:bg-red-50">
                  🚫 {t("quote.cancelBtn")}
                </button>
                <button onClick={() => save().then((s) => s && toast(t("common.saved"), "success"))} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">
                  💾 {t("common.save")}
                </button>
                <button
                  onClick={send}
                  disabled={busy || !contractText.trim()}
                  className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-40"
                >
                  ✉ {open.status === "sent" ? t("quote.resend") : t("quote.sendBtn")}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Gép-katalógus modal */}
      {showTypes && (
        <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4">
          <div className="w-full max-w-2xl rounded-2xl bg-white p-5 shadow-xl">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-bold">☕ {t("quote.machineCatalog")}</h2>
              <button onClick={() => { setShowTypes(false); setMtEditId(null); setMtForm({ ...EMPTY_MT }); setMtImage(null); setMtRemoveImage(false); setMtHasImage(false); }} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100">
                ✕ {t("common.close")}
              </button>
            </div>
            <p className="mb-3 text-sm text-slate-500">{t("quote.catalogHint")}</p>
            <div className="mb-4 space-y-2">
              {types.map((m) => (
                <div key={m.id} className="flex items-start justify-between gap-3 rounded-xl border border-slate-200 p-3">
                  {m.has_image && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={`/api/public/quotes/machine-types/${m.id}/image`}
                      alt={m.name}
                      className="h-14 w-14 shrink-0 rounded-lg border border-slate-200 bg-white object-contain"
                    />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-slate-900">
                      {m.name}
                      {!m.active && (
                        <span className="ml-2 rounded bg-slate-200 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-slate-500">
                          {t("partners.inactive")}
                        </span>
                      )}
                    </div>
                    {m.description && <div className="text-xs text-slate-500">{m.description}</div>}
                    {m.ideal_for && (
                      <div className="text-xs text-emerald-700">{t("quote.idealFor")}: {m.ideal_for}</div>
                    )}
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <button
                      title={t("common.edit")}
                      onClick={() => {
                        setMtEditId(m.id);
                        setMtForm({
                          name: m.name, description: m.description ?? "",
                          ideal_for: m.ideal_for ?? "", sort_order: String(m.sort_order),
                          active: m.active,
                        });
                        setMtImage(null);
                        setMtRemoveImage(false);
                        setMtHasImage(m.has_image);
                      }}
                      className="rounded-lg border border-slate-300 px-2 py-1 text-sm hover:bg-slate-100"
                    >
                      ✏️
                    </button>
                    <button title={t("common.delete")} onClick={() => deleteMType(m)} className="rounded-lg border border-slate-300 px-2 py-1 text-sm hover:bg-red-50">
                      🗑️
                    </button>
                  </div>
                </div>
              ))}
              {types.length === 0 && <div className="py-4 text-center text-sm text-slate-400">{t("quote.noTypes")}</div>}
            </div>
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
              <h3 className="mb-2 text-sm font-semibold text-slate-700">
                {mtEditId ? t("quote.editType") : t("quote.newType")}
              </h3>
              <div className="grid gap-2">
                <input value={String(mtForm.name)} onChange={(e) => setMtForm((f) => ({ ...f, name: e.target.value }))} className={inputCls} placeholder={t("quote.typeName")} />
                <textarea value={String(mtForm.description)} onChange={(e) => setMtForm((f) => ({ ...f, description: e.target.value }))} rows={2} className={inputCls} placeholder={t("quote.typeDescription")} />
                <textarea value={String(mtForm.ideal_for)} onChange={(e) => setMtForm((f) => ({ ...f, ideal_for: e.target.value }))} rows={2} className={inputCls} placeholder={t("quote.typeIdealFor")} />
                <div className="flex items-center gap-3">
                  {(mtImage || (mtHasImage && !mtRemoveImage)) && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={mtImage ?? `/api/public/quotes/machine-types/${mtEditId}/image`}
                      alt=""
                      className="h-16 w-16 rounded-lg border border-slate-200 bg-white object-contain"
                    />
                  )}
                  <label className="cursor-pointer rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-100">
                    📷 {t("quote.typePhoto")}
                    <input
                      type="file"
                      accept="image/png,image/jpeg,image/webp"
                      className="hidden"
                      onChange={(e) => { readImageFile(e.target.files?.[0]); e.target.value = ""; }}
                    />
                  </label>
                  {(mtImage || (mtHasImage && !mtRemoveImage)) && (
                    <button
                      type="button"
                      onClick={() => { setMtImage(null); setMtRemoveImage(true); }}
                      className="text-xs text-slate-400 hover:text-red-600"
                    >
                      ✕ {t("quote.removePhoto")}
                    </button>
                  )}
                </div>
                <div className="flex items-center gap-3">
                  <label className="flex items-center gap-2 text-sm text-slate-600">
                    {t("quote.sortOrder")}
                    <input type="number" value={String(mtForm.sort_order)} onChange={(e) => setMtForm((f) => ({ ...f, sort_order: e.target.value }))} className={inputCls + " w-24"} />
                  </label>
                  <label className="flex items-center gap-2 text-sm text-slate-600">
                    <input type="checkbox" checked={Boolean(mtForm.active)} onChange={(e) => setMtForm((f) => ({ ...f, active: e.target.checked }))} />
                    {t("quote.typeActive")}
                  </label>
                  <button onClick={saveMType} disabled={!String(mtForm.name).trim()} className="ml-auto rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-40">
                    💾 {t("common.save")}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Sablon modal */}
      {showTemplate && (
        <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4">
          <div className="w-full max-w-3xl rounded-2xl bg-white p-5 shadow-xl">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-lg font-bold">📄 {t("quote.templateTitle")}</h2>
              <button onClick={() => setShowTemplate(false)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100">
                ✕ {t("common.close")}
              </button>
            </div>
            <p className="mb-2 text-sm text-slate-500">{t("quote.templateHint")}</p>
            <div className="mb-3 flex flex-wrap gap-1">
              {["szerzodes_szam", "datum", "ceg_nev", "adoszam", "szekhely", "szamlazasi_cim",
                "kapcsolattarto", "email", "telefon", "bankszamla", "gep", "adagar",
                "min_adag", "min_alatti_ar", "ervenyes_tol", "ervenyes_ig"].map((v) => (
                <code key={v} className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">
                  {"{{" + v + "}}"}
                </code>
              ))}
            </div>
            <textarea
              value={template}
              onChange={(e) => setTemplate(e.target.value)}
              rows={16}
              className={inputCls + " mb-3 font-mono text-xs"}
              placeholder={defaultTemplate}
            />
            <div className="flex justify-end gap-2">
              <button onClick={() => setTemplate("")} className="mr-auto rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">
                {t("quote.templateReset")}
              </button>
              <button onClick={saveTemplate} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700">
                💾 {t("common.save")}
              </button>
            </div>
          </div>
        </div>
      )}

      <PartnerInfo partnerId={infoPartner} onClose={() => setInfoPartner(null)} />
    </AppShell>
  );
}
