"use client";

/** Nyilvános árajánlat-kérő oldal (belépés nélkül): a leendő vevő megadja a
 *  cégadatait, kiválasztja a gépet a katalógusból (leírás + kiknek ajánljuk),
 *  és ajánlatot kér. A folytatás (adagár, dátumok, szerződés) belül történik. */

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface MachineType {
  id: string;
  name: string;
  description: string | null;
  ideal_for: string | null;
  has_image: boolean;
}

const EMPTY = {
  company_name: "",
  contact_name: "",
  contact_email: "",
  contact_phone: "",
  tax_number: "",
  address_zip: "",
  address_city: "",
  address_street: "",
  address_number: "",
  billing_zip: "",
  billing_city: "",
  billing_street: "",
  billing_number: "",
  bank_account: "",
  message: "",
};

export default function AjanlatPage() {
  const { t } = useT();
  const [types, setTypes] = useState<MachineType[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [guideOpen, setGuideOpen] = useState(false);
  const [form, setForm] = useState({ ...EMPTY });
  const [sameBilling, setSameBilling] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [doneSerial, setDoneSerial] = useState<string | null>(null);

  useEffect(() => {
    api.get<MachineType[]>("/api/public/quotes/machine-types").then(setTypes).catch(() => {});
  }, []);

  const set = (key: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  const inputCls =
    "w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm focus:border-indigo-500 focus:outline-none";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        ...form,
        machine_type_id: selected,
      };
      if (sameBilling) {
        body.billing_zip = null;
        body.billing_city = null;
        body.billing_street = null;
        body.billing_number = null;
      }
      for (const k of Object.keys(body)) {
        if (body[k] === "") body[k] = null;
      }
      const res = await api.post<{ serial: string }>("/api/public/quotes", body);
      setDoneSerial(res.serial);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 429
          ? t("quote.rateLimited")
          : t("quote.submitError")
      );
    } finally {
      setBusy(false);
    }
  }

  if (doneSerial) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-100 p-4">
        <div className="w-full max-w-lg rounded-2xl border border-slate-200 bg-white p-8 text-center shadow-sm">
          <div className="mb-3 text-5xl">☕</div>
          <h1 className="mb-2 text-2xl font-bold text-slate-900">{t("quote.doneTitle")}</h1>
          <p className="mb-4 text-slate-600">{t("quote.doneText")}</p>
          <div className="inline-block rounded-lg bg-indigo-50 px-4 py-2 font-mono text-sm font-semibold text-indigo-700">
            {doneSerial}
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-100 pb-16">
      <header className="bg-indigo-700 px-4 py-10 text-center text-white">
        <div className="mb-2 text-4xl">☕</div>
        <h1 className="text-2xl font-bold sm:text-3xl">{t("quote.publicTitle")}</h1>
        <p className="mx-auto mt-2 max-w-xl text-sm text-indigo-100">{t("quote.publicSubtitle")}</p>
      </header>

      <form onSubmit={submit} className="mx-auto mt-8 max-w-3xl space-y-6 px-4">
        {/* Gépválasztó: kompakt legördülő + összecsukható infó-sáv */}
        {types.length > 0 && (
          <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="mb-1 text-lg font-semibold text-slate-900">{t("quote.chooseMachine")}</h2>
            <label className="mt-3 block text-sm">
              <span className="mb-1 block text-slate-600">{t("quote.machineSelectLabel")}</span>
              <select
                value={selected ?? ""}
                onChange={(e) => setSelected(e.target.value || null)}
                className={inputCls}
              >
                <option value="">{t("quote.machineSelectEmpty")}</option>
                {types.map((m) => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))}
              </select>
            </label>

            <button
              type="button"
              onClick={() => setGuideOpen((v) => !v)}
              className="mt-4 flex w-full items-center gap-3 rounded-xl border border-amber-300 bg-gradient-to-r from-amber-50 to-orange-50 px-4 py-3 text-left transition hover:from-amber-100 hover:to-orange-100"
            >
              <span className="text-2xl">💡</span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold text-amber-900">{t("quote.guideTitle")}</span>
                <span className="block text-xs text-amber-700">{t("quote.guideSubtitle")}</span>
              </span>
              <span className={`text-amber-600 transition-transform ${guideOpen ? "rotate-180" : ""}`}>▼</span>
            </button>

            {guideOpen && (
              <div className="mt-3 space-y-2">
                {types.map((m) => (
                  <div
                    key={m.id}
                    className={`rounded-xl border p-4 ${
                      selected === m.id ? "border-indigo-400 bg-indigo-50" : "border-slate-200 bg-slate-50"
                    }`}
                  >
                    <div className="flex gap-3">
                      {m.has_image && (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={`/api/public/quotes/machine-types/${m.id}/image`}
                          alt={m.name}
                          className="h-24 w-24 shrink-0 rounded-lg border border-slate-200 bg-white object-contain"
                        />
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="font-semibold text-slate-900">{m.name}</span>
                          <button
                            type="button"
                            onClick={() => { setSelected(m.id); setGuideOpen(false); }}
                            className={`rounded-lg px-3 py-1 text-xs font-semibold transition ${
                              selected === m.id
                                ? "bg-indigo-600 text-white"
                                : "border border-indigo-300 text-indigo-700 hover:bg-indigo-100"
                            }`}
                          >
                            {selected === m.id ? "✓ " + t("quote.chosen") : t("quote.chooseThis")}
                          </button>
                        </div>
                        {m.description && (
                          <p className="mt-1.5 text-sm text-slate-600">{m.description}</p>
                        )}
                        {m.ideal_for && (
                          <p className="mt-1.5 text-xs text-slate-500">
                            <span className="font-semibold text-emerald-700">{t("quote.idealFor")}:</span>{" "}
                            {m.ideal_for}
                          </p>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {/* Cégadatok */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-1 text-lg font-semibold text-slate-900">{t("quote.companyData")}</h2>
          <p className="mb-4 text-sm text-slate-500">{t("quote.companyDataHint")}</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="sm:col-span-2 text-sm">
              <span className="mb-1 block text-slate-600">{t("quote.companyName")} *</span>
              <input required value={form.company_name} onChange={set("company_name")} className={inputCls} />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-slate-600">{t("quote.taxNumber")} *</span>
              <input required minLength={8} value={form.tax_number} onChange={set("tax_number")} className={inputCls} placeholder="12345678-2-42" />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-slate-600">{t("quote.bankAccount")}</span>
              <input value={form.bank_account} onChange={set("bank_account")} className={inputCls} />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-slate-600">{t("quote.contactName")} *</span>
              <input required value={form.contact_name} onChange={set("contact_name")} className={inputCls} />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-slate-600">{t("quote.contactPhone")} *</span>
              <input required minLength={6} type="tel" value={form.contact_phone} onChange={set("contact_phone")} className={inputCls} placeholder="+36 30 …" />
            </label>
            <label className="sm:col-span-2 text-sm">
              <span className="mb-1 block text-slate-600">{t("quote.contactEmail")} *</span>
              <input required type="email" value={form.contact_email} onChange={set("contact_email")} className={inputCls} />
            </label>
          </div>

          <h3 className="mb-2 mt-5 text-sm font-semibold text-slate-700">{t("quote.address")} *</h3>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-6">
            <input required minLength={4} value={form.address_zip} onChange={set("address_zip")} className={inputCls + " sm:col-span-1"} placeholder={t("partners.zip")} />
            <input required value={form.address_city} onChange={set("address_city")} className={inputCls + " sm:col-span-2"} placeholder={t("partners.city")} />
            <input required value={form.address_street} onChange={set("address_street")} className={inputCls + " col-span-2 sm:col-span-2"} placeholder={t("partners.street")} />
            <input value={form.address_number} onChange={set("address_number")} className={inputCls + " sm:col-span-1"} placeholder={t("partners.houseNo")} />
          </div>

          <label className="mt-4 flex items-center gap-2 text-sm text-slate-600">
            <input type="checkbox" checked={sameBilling} onChange={(e) => setSameBilling(e.target.checked)} />
            {t("quote.sameBilling")}
          </label>
          {!sameBilling && (
            <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-6">
              <input value={form.billing_zip} onChange={set("billing_zip")} className={inputCls + " sm:col-span-1"} placeholder={t("partners.zip")} />
              <input value={form.billing_city} onChange={set("billing_city")} className={inputCls + " sm:col-span-2"} placeholder={t("partners.city")} />
              <input value={form.billing_street} onChange={set("billing_street")} className={inputCls + " col-span-2 sm:col-span-2"} placeholder={t("partners.street")} />
              <input value={form.billing_number} onChange={set("billing_number")} className={inputCls + " sm:col-span-1"} placeholder={t("partners.houseNo")} />
            </div>
          )}

          <label className="mt-4 block text-sm">
            <span className="mb-1 block text-slate-600">{t("quote.message")}</span>
            <textarea value={form.message} onChange={set("message")} rows={3} className={inputCls} placeholder={t("quote.messagePlaceholder")} />
          </label>
        </section>

        {error && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
        )}

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-xl bg-indigo-600 px-6 py-3.5 text-base font-semibold text-white shadow-sm transition hover:bg-indigo-700 disabled:opacity-50"
        >
          {busy ? "…" : t("quote.submit")}
        </button>
        <p className="text-center text-xs text-slate-400">{t("quote.privacyNote")}</p>
      </form>
    </main>
  );
}
