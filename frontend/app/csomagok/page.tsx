"use client";

/** GLS-csomagfeladás: címke rendelésből (?order=<id> előtöltéssel) vagy
 * kézzel, utánvéttel; a címke-PDF nyomtatási ablakban nyílik és bármikor
 * újranyomtatható; csomagkövetés státusz-frissítéssel. */

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import SearchSelect from "@/components/SearchSelect";
import { api, errorMessage, printFile } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface PartnerLite {
  id: string;
  name: string;
  company_name: string | null;
  contact_name: string | null;
  contact_phone: string | null;
  contact_email: string | null;
  address: string | null;
  address_zip: string | null;
  address_city: string | null;
  address_street: string | null;
  address_number: string | null;
  is_active: boolean;
}

interface OrderLite {
  id: string;
  order_no: string;
  partner_id: string | null;
  partner_label: string | null;
  items: { name: string; quantity: number; unit: string }[];
  contact_name: string | null;
  contact_phone: string | null;
}

interface Parcel {
  id: string;
  parcel_number: string | null;
  recipient_name: string;
  recipient_city: string;
  recipient_zip: string;
  content: string | null;
  count: number;
  cod_amount: number | null;
  status_key: "created" | "handed_over" | "in_transit" | "delivered" | "returned";
  last_status: string | null;
  last_status_at: string | null;
  history: { date: string; description: string; depot: string }[];
  can_delete: boolean;
  test_mode: boolean;
  partner_name: string | null;
  order_no: string | null;
  created_at: string;
}

const STATUS_BADGE: Record<Parcel["status_key"], string> = {
  created: "bg-slate-100 text-slate-700",
  handed_over: "bg-sky-100 text-sky-800",
  in_transit: "bg-amber-100 text-amber-800",
  delivered: "bg-emerald-100 text-emerald-800",
  returned: "bg-rose-100 text-rose-800",
};

const EMPTY_FORM = {
  partner_id: "",
  order_id: "",
  recipient_name: "",
  recipient_zip: "",
  recipient_city: "",
  recipient_street: "",
  recipient_house: "",
  recipient_phone: "",
  recipient_email: "",
  content: "",
  count: "1",
  cod: false,
  cod_amount: "",
};

export default function CsomagokPage() {
  const { t } = useT();
  const { toast, confirm } = useUI();
  const [partners, setPartners] = useState<PartnerLite[]>([]);
  const [parcels, setParcels] = useState<Parcel[]>([]);
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadParcels = useCallback(() => {
    api.get<Parcel[]>("/api/gls").then(setParcels).catch(() => {});
  }, []);

  useEffect(() => {
    api.get<PartnerLite[]>("/api/partners").then(setPartners).catch(() => {});
    loadParcels();
  }, [loadParcels]);

  // Rendelésből érkező előtöltés (?order=<id>)
  useEffect(() => {
    const orderId = new URLSearchParams(window.location.search).get("order");
    if (!orderId) return;
    api.get<OrderLite[]>("/api/orders").then((rows) => {
      const o = rows.find((x) => x.id === orderId);
      if (!o) return;
      setForm((f) => ({
        ...f,
        order_id: o.id,
        partner_id: o.partner_id ?? "",
        recipient_name: o.contact_name || o.partner_label || "",
        recipient_phone: o.contact_phone ?? "",
        content: o.items.map((it) => `${it.name} ${it.quantity} ${it.unit}`).join(", ").slice(0, 250),
      }));
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Partner kiválasztásakor a cím/elérhetőség automatikusan kitöltődik
  function selectPartner(id: string) {
    const p = partners.find((x) => x.id === id);
    setForm((f) => {
      if (!p) return { ...f, partner_id: id };
      const street = [p.address_street].filter(Boolean).join(" ");
      return {
        ...f,
        partner_id: id,
        recipient_name: f.recipient_name || p.company_name || p.name,
        recipient_zip: p.address_zip ?? f.recipient_zip,
        recipient_city: p.address_city ?? f.recipient_city,
        recipient_street: street || p.address || f.recipient_street,
        recipient_house: p.address_number ?? f.recipient_house,
        recipient_phone: f.recipient_phone || p.contact_phone || "",
        recipient_email: f.recipient_email || p.contact_email || "",
      };
    });
  }

  const partnerItems = useMemo(
    () =>
      partners
        .filter((p) => p.is_active)
        .map((p) => ({
          id: p.id,
          label: p.name,
          sublabel: p.address_city,
          keywords: [p.company_name, p.address_city, p.contact_email].filter(Boolean).join(" "),
        })),
    [partners],
  );

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const codAmount = form.cod ? Number(form.cod_amount) : null;
    if (form.cod && (!codAmount || codAmount <= 0)) {
      setError(t("gls.codAmountRequired"));
      return;
    }
    if (!(await confirm(t("gls.createConfirm", { name: form.recipient_name })))) return;
    setBusy(true);
    try {
      const parcel = await api.post<Parcel>("/api/gls", {
        order_id: form.order_id || null,
        partner_id: form.partner_id || null,
        recipient_name: form.recipient_name,
        recipient_zip: form.recipient_zip,
        recipient_city: form.recipient_city,
        recipient_street: form.recipient_street,
        recipient_house: form.recipient_house || null,
        recipient_phone: form.recipient_phone || null,
        recipient_email: form.recipient_email || null,
        content: form.content || null,
        count: Number(form.count) || 1,
        cod_amount: codAmount,
      });
      toast(t("gls.created", { number: parcel.parcel_number ?? "?" }), "success");
      setForm({ ...EMPTY_FORM });
      loadParcels();
      try {
        await printFile(`/api/gls/${parcel.id}/label`);
      } catch (err) {
        toast(errorMessage(err), "error");
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const [openTimeline, setOpenTimeline] = useState<string | null>(null);

  async function refreshStatus(p: Parcel) {
    try {
      await api.post(`/api/gls/${p.id}/refresh-status`, {});
      loadParcels();
      setOpenTimeline(p.id);
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function deleteParcel(p: Parcel) {
    if (!(await confirm(t("gls.deleteConfirm", { name: p.recipient_name })))) return;
    try {
      await api.delete(`/api/gls/${p.id}`);
      toast(t("gls.deleted"), "success");
      loadParcels();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  const inputCls = "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm";

  return (
    <AppShell>
      <div className="mb-4 flex items-center gap-3">
        <h1 className="text-xl font-bold">📦 {t("gls.title")}</h1>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Új csomag */}
        <form onSubmit={submit} className="space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="font-semibold">{t("gls.newParcel")}</h2>
          {form.order_id && (
            <p className="rounded-lg bg-sky-50 px-3 py-1.5 text-xs text-sky-800">
              {t("gls.fromOrder")}
            </p>
          )}
          <label className="block text-sm">
            {t("gls.partner")}
            <SearchSelect
              items={partnerItems}
              value={form.partner_id}
              onChange={selectPartner}
              placeholder={t("gls.partnerPh")}
            />
          </label>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="block text-sm sm:col-span-2">
              {t("gls.recipientName")} *
              <input required value={form.recipient_name} onChange={(e) => setForm({ ...form, recipient_name: e.target.value })} className={inputCls} />
            </label>
            <label className="block text-sm">
              {t("gls.zip")} *
              <input required value={form.recipient_zip} onChange={(e) => setForm({ ...form, recipient_zip: e.target.value })} className={inputCls} />
            </label>
            <label className="block text-sm">
              {t("gls.city")} *
              <input required value={form.recipient_city} onChange={(e) => setForm({ ...form, recipient_city: e.target.value })} className={inputCls} />
            </label>
            <label className="block text-sm">
              {t("gls.street")} *
              <input required value={form.recipient_street} onChange={(e) => setForm({ ...form, recipient_street: e.target.value })} className={inputCls} />
            </label>
            <label className="block text-sm">
              {t("gls.house")}
              <input value={form.recipient_house} onChange={(e) => setForm({ ...form, recipient_house: e.target.value })} className={inputCls} />
            </label>
            <label className="block text-sm">
              {t("gls.phone")}
              <input value={form.recipient_phone} onChange={(e) => setForm({ ...form, recipient_phone: e.target.value })} className={inputCls} />
            </label>
            <label className="block text-sm">
              {t("gls.email")}
              <input type="email" value={form.recipient_email} onChange={(e) => setForm({ ...form, recipient_email: e.target.value })} className={inputCls} />
            </label>
            <label className="block text-sm">
              {t("gls.content")}
              <input value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} className={inputCls} />
            </label>
            <label className="block text-sm">
              {t("gls.count")}
              <input type="number" min={1} max={20} value={form.count} onChange={(e) => setForm({ ...form, count: e.target.value })} className={inputCls} />
            </label>
          </div>
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-3">
            <label className="flex items-center gap-2 text-sm font-medium">
              <input type="checkbox" checked={form.cod} onChange={(e) => setForm({ ...form, cod: e.target.checked })} className="h-4 w-4" />
              {t("gls.cod")}
            </label>
            {form.cod && (
              <label className="mt-2 block text-sm">
                {t("gls.codAmount")} *
                <input type="number" min={1} value={form.cod_amount} onChange={(e) => setForm({ ...form, cod_amount: e.target.value })} className={inputCls} />
              </label>
            )}
          </div>
          {error && <p className="text-sm text-red-600">{error}</p>}
          <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
            {busy ? t("common.saving") : `🏷️ ${t("gls.create")}`}
          </button>
        </form>

        {/* Feladott csomagok */}
        <div className="space-y-2 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="font-semibold">{t("gls.recent")}</h2>
          {parcels.length === 0 && <p className="text-sm text-slate-400">{t("gls.empty")}</p>}
          {parcels.map((p) => (
            <div key={p.id} className="rounded-xl border border-slate-200 p-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono text-xs font-semibold">{p.parcel_number ?? "—"}</span>
                <span className="flex gap-1.5">
                  <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${STATUS_BADGE[p.status_key]}`}>
                    {t(`gls.status.${p.status_key}`)}
                  </span>
                  {p.test_mode && (
                    <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800">TESZT</span>
                  )}
                  {p.cod_amount != null && (
                    <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-800">
                      COD {p.cod_amount.toLocaleString("hu-HU")} Ft
                    </span>
                  )}
                </span>
              </div>
              <p className="mt-0.5">
                <b>{p.recipient_name}</b> · {p.recipient_zip} {p.recipient_city}
                {p.order_no && <span className="text-slate-500"> · {p.order_no}</span>}
              </p>
              {p.last_status && (
                <p className="text-xs text-slate-500">
                  {p.last_status} {p.last_status_at && `(${p.last_status_at.slice(0, 16).replace("T", " ")})`}
                </p>
              )}
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                <button
                  onClick={() => printFile(`/api/gls/${p.id}/label`).catch((err) => toast(errorMessage(err), "error"))}
                  className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100"
                >
                  🖨 {t("gls.printLabel")}
                </button>
                <button
                  onClick={() => refreshStatus(p)}
                  className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100"
                >
                  🔄 {t("gls.track")}
                </button>
                {p.history.length > 0 && (
                  <button
                    onClick={() => setOpenTimeline(openTimeline === p.id ? null : p.id)}
                    className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100"
                  >
                    {openTimeline === p.id ? "▲" : "▼"} {t("gls.timeline")} ({p.history.length})
                  </button>
                )}
                {p.can_delete && (
                  <button
                    onClick={() => deleteParcel(p)}
                    className="rounded border border-rose-300 px-2 py-1 text-xs text-rose-700 hover:bg-rose-50"
                  >
                    🗑 {t("gls.delete")}
                  </button>
                )}
              </div>
              {/* Nyomkövetési idővonal — a GLS összes eseménye */}
              {openTimeline === p.id && p.history.length > 0 && (
                <ol className="mt-2 space-y-1 border-l-2 border-indigo-200 pl-3">
                  {p.history.map((ev, i) => (
                    <li key={i} className="text-xs">
                      <span className="text-slate-400">{ev.date.slice(0, 16).replace("T", " ")}</span>{" "}
                      <span className={i === 0 ? "font-semibold" : ""}>{ev.description}</span>
                      {ev.depot && <span className="text-slate-400"> · {ev.depot}</span>}
                    </li>
                  ))}
                </ol>
              )}
            </div>
          ))}
        </div>
      </div>
    </AppShell>
  );
}
