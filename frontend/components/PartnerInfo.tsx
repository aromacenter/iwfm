"use client";

/** Egységes partner-adatlap felugró: bármely listából a partner nevére
 *  kattintva nyílik — törzsadatok, szerződések, kihelyezett gépek, készlet,
 *  tartozás, utolsó elszámolások, nyitott szállítólevelek egy nézetben. */

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface Overview {
  partner: {
    id: string;
    partner_code: string | null;
    name: string;
    company_name: string | null;
    invoicing_company: string | null;
    partner_type: string;
    tax_number: string | null;
    eu_tax_number: string | null;
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
  };
  debt: number;
  contracts: {
    id: string; valid_from: string; valid_to: string | null; status: string;
    min_portions: number | null; below_min_price: number | null;
    min_kg: number | null; below_min_price_kg: number | null;
    rent_if_below_min: boolean; note: string | null;
  }[];
  machines: { id: string; barcode: string; name: string; counter: number | null; customer_owned: boolean }[];
  stock: { product_name: string; quantity: number; unit: string }[];
  recent_settlements: {
    id: string; created_at: string; total_gross: number;
    paid_amount: number | null; invoiced: boolean; payment_method: string;
  }[];
  open_deliveries: number;
  open_deliveries_net: number;
  open_tickets: number;
}

const ft = (n: number) => `${Math.round(n).toLocaleString("hu-HU")} Ft`;
const dt = (s: string) => new Date(s).toLocaleString("hu-HU", { dateStyle: "short", timeStyle: "short" });

export default function PartnerInfo({
  partnerId,
  onClose,
}: {
  partnerId: string | null;
  onClose: () => void;
}) {
  const { t } = useT();
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    setData(null);
    setError(false);
    if (!partnerId) return;
    api.get<Overview>(`/api/partners/${partnerId}/overview`).then(setData).catch(() => setError(true));
  }, [partnerId]);

  if (!partnerId) return null;
  const p = data?.partner;

  function row(label: string, value: React.ReactNode) {
    if (value === null || value === undefined || value === "") return null;
    return (
      <div className="flex gap-2 text-sm">
        <span className="w-36 shrink-0 text-slate-400">{label}</span>
        <span className="min-w-0 break-words">{value}</span>
      </div>
    );
  }

  return (
    <div
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
      className="fixed inset-0 z-[60] flex items-start justify-center overflow-y-auto bg-black/40 p-4"
    >
      <div className="my-8 w-full max-w-2xl space-y-4 rounded-2xl bg-white p-6 shadow-xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">
              {p ? p.name : t("common.loading")}
              {p?.partner_code && <span className="ml-2 font-mono text-sm text-slate-400">{p.partner_code}</span>}
              {p && !p.is_active && (
                <span className="ml-2 rounded bg-slate-200 px-1.5 py-0.5 text-xs font-normal">{t("partners.inactive")}</span>
              )}
            </h2>
            {p?.company_name && <p className="text-sm text-slate-500">{p.company_name}</p>}
          </div>
          <button onClick={onClose} className="text-sm text-slate-500 hover:underline">✕ {t("common.close")}</button>
        </div>

        {error && <p className="text-sm text-red-600">{t("errors.network")}</p>}

        {data && p && (
          <>
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="rounded-xl border border-slate-200 p-3 text-center">
                <div className={`text-lg font-bold ${data.debt > 0.5 ? "text-rose-600" : "text-emerald-600"}`}>{ft(data.debt)}</div>
                <div className="text-xs text-slate-500">{t("cons.debt")}</div>
              </div>
              <div className="rounded-xl border border-slate-200 p-3 text-center">
                <div className="text-lg font-bold">{data.machines.length}</div>
                <div className="text-xs text-slate-500">{t("pinfo.machines")}</div>
              </div>
              <div className="rounded-xl border border-slate-200 p-3 text-center">
                <div className="text-lg font-bold">{data.open_tickets}</div>
                <div className="text-xs text-slate-500">{t("pinfo.openTickets")}</div>
              </div>
            </div>

            {data.open_deliveries > 0 && (
              <p className="rounded-xl border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900">
                🚚 {t("delivery.openBanner", { count: data.open_deliveries, net: Math.round(data.open_deliveries_net).toLocaleString("hu-HU") })}
              </p>
            )}

            <div className="space-y-1 rounded-xl border border-slate-200 p-3">
              <p className="mb-1 text-xs font-semibold uppercase text-slate-400">{t("pinfo.master")}</p>
              {row(t("partners.taxNumber"), p.tax_number)}
              {row(t("partners.contactName"), p.contact_name)}
              {row(t("partners.contactEmail"), p.contact_email)}
              {row(t("partners.contactPhone"), p.contact_phone)}
              {row(t("partners.address"), p.address)}
              {row(t("partners.billingAddress"), p.billing_address)}
              {row(t("partners.bankAccount"), p.bank_account)}
              {row(t("partners.paymentTerms"), p.payment_terms_days != null ? `${p.payment_terms_days} nap` : null)}
              {row(t("partners.notes"), p.notes)}
            </div>

            {data.contracts.length > 0 && (
              <div className="rounded-xl border border-slate-200 p-3">
                <p className="mb-2 text-xs font-semibold uppercase text-slate-400">{t("contracts.title")}</p>
                <div className="space-y-1">
                  {data.contracts.map((c) => (
                    <div key={c.id} className="flex flex-wrap items-center gap-2 text-sm">
                      <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                        c.status === "active" ? "bg-emerald-100 text-emerald-800"
                        : c.status === "future" ? "bg-sky-100 text-sky-800"
                        : "bg-slate-200 text-slate-500"
                      }`}>{t(`contracts.status_${c.status}`)}</span>
                      <span>{c.valid_from} → {c.valid_to ?? t("contracts.openEnded")}</span>
                      <span className="text-xs text-slate-500">
                        {[
                          c.min_portions != null ? t("contracts.sumMinPortions", { n: c.min_portions, price: c.below_min_price ?? "—" }) : null,
                          c.min_kg != null ? t("contracts.sumMinKg", { n: c.min_kg, price: c.below_min_price_kg ?? "—" }) : null,
                          c.rent_if_below_min ? t("contracts.sumRent") : null,
                        ].filter(Boolean).join(" · ")}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {data.machines.length > 0 && (
              <div className="rounded-xl border border-slate-200 p-3">
                <p className="mb-2 text-xs font-semibold uppercase text-slate-400">{t("pinfo.machines")}</p>
                <div className="space-y-0.5 text-sm">
                  {data.machines.map((m) => (
                    <div key={m.id} className="flex flex-wrap gap-2">
                      <span className="font-mono text-xs text-slate-400">{m.barcode}</span>
                      <span>{m.name}</span>
                      {m.counter != null && <span className="text-slate-500">({t("pinfo.counter")}: {m.counter.toLocaleString("hu-HU")})</span>}
                      {m.customer_owned && <span className="rounded bg-amber-100 px-1.5 text-xs text-amber-800">{t("inv.customerOwnedShort")}</span>}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {data.stock.length > 0 && (
              <div className="rounded-xl border border-slate-200 p-3">
                <p className="mb-2 text-xs font-semibold uppercase text-slate-400">{t("pinfo.stock")}</p>
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
                  {data.stock.map((s, i) => (
                    <span key={i}>{s.product_name}: <b>{s.quantity} {s.unit}</b></span>
                  ))}
                </div>
              </div>
            )}

            {data.recent_settlements.length > 0 && (
              <div className="rounded-xl border border-slate-200 p-3">
                <p className="mb-2 text-xs font-semibold uppercase text-slate-400">{t("pinfo.recentSettlements")}</p>
                <div className="space-y-0.5 text-sm">
                  {data.recent_settlements.map((s) => (
                    <div key={s.id} className="flex flex-wrap gap-2">
                      <span className="text-slate-500">{dt(s.created_at)}</span>
                      <span className="font-medium">{ft(s.total_gross)}</span>
                      <span className="text-xs text-slate-400">{t(`cons.payments.${s.payment_method}`)}</span>
                      {s.invoiced && <span className="rounded bg-emerald-100 px-1.5 text-xs text-emerald-800">{t("pinfo.invoiced")}</span>}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
