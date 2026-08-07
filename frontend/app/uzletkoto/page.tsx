"use client";

/** Üzletkötő-elszámolás: ki számoltatott el, mely ügyfeleket, mekkora
 *  összegben, milyen fizetési móddal — a végén fizetési módonkénti összesítés. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface Agent {
  user_id: string | null;
  name: string;
  count: number;
}

interface Settlement {
  id: string;
  partner_name: string | null;
  settled_by_name: string;
  payment_method: "cash" | "card" | "transfer";
  total_net: number;
  total_gross: number;
  invoiced: boolean;
  created_at: string;
}

interface Summary {
  by_payment: Record<string, { count: number; net: number; gross: number }>;
  total_net: number;
  total_gross: number;
  count: number;
}

const PAYMENTS = ["cash", "card", "transfer"] as const;

export default function UzletkotoPage() {
  const { t, lang } = useT();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentId, setAgentId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [rows, setRows] = useState<Settlement[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);

  const fmt = (dt: string) =>
    new Date(dt).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { dateStyle: "short", timeStyle: "short" });
  const ft = (n: number) => `${n.toLocaleString(lang === "hu" ? "hu-HU" : "en-GB")} Ft`;

  useEffect(() => {
    api.get<Agent[]>("/api/settlements/agents").then(setAgents).catch(() => {});
  }, []);

  const load = useCallback(() => {
    const params = new URLSearchParams();
    if (agentId) params.set("settled_by", agentId);
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    api.get<Settlement[]>(`/api/settlements?${params}`).then(setRows).catch(() => {});
    api.get<Summary>(`/api/settlements/summary?${params}`).then(setSummary).catch(() => {});
  }, [agentId, dateFrom, dateTo]);
  useEffect(load, [load]);

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("cons.agentTitle")}</h1>
        <select
          value={agentId}
          onChange={(e) => setAgentId(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          <option value="">{t("cons.allAgents")}</option>
          {agents.map((a) => (
            <option key={a.user_id ?? a.name} value={a.user_id ?? ""}>
              {a.name} ({a.count})
            </option>
          ))}
        </select>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          {t("cons.dateFrom")}
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1.5" />
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          {t("cons.dateTo")}
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1.5" />
        </label>
      </div>

      <div className="mb-6 overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
              <th className="px-4 py-3">{t("cons.date")}</th>
              <th className="px-4 py-3">{t("cons.partner")}</th>
              <th className="px-4 py-3">{t("cons.settledBy")}</th>
              <th className="px-4 py-3">{t("cons.payment")}</th>
              <th className="px-4 py-3">{t("cons.amountNet")}</th>
              <th className="px-4 py-3">{t("cons.amountGross")}</th>
              <th className="px-4 py-3">{t("cons.invoiced")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id} className="border-b border-slate-100 last:border-0">
                <td className="px-4 py-3 whitespace-nowrap">{fmt(s.created_at)}</td>
                <td className="px-4 py-3">{s.partner_name}</td>
                <td className="px-4 py-3 text-slate-500">{s.settled_by_name}</td>
                <td className="px-4 py-3">{t(`cons.payments.${s.payment_method}`)}</td>
                <td className="px-4 py-3">{ft(s.total_net)}</td>
                <td className="px-4 py-3 font-medium">{ft(s.total_gross)}</td>
                <td className="px-4 py-3">
                  <span className={`rounded px-2 py-0.5 text-xs font-medium ${s.invoiced ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"}`}>
                    {s.invoiced ? t("cons.invoiced") : t("cons.notInvoiced")}
                  </span>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={7} className="px-4 py-10 text-center text-slate-400">{t("cons.noSettlements")}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {summary && (
        <>
          <h2 className="mb-2 font-semibold">{t("cons.summaryByPayment")}</h2>
          <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
                  <th className="px-4 py-3">{t("cons.payment")}</th>
                  <th className="px-4 py-3">{t("cons.count")}</th>
                  <th className="px-4 py-3">{t("cons.amountNet")}</th>
                  <th className="px-4 py-3">{t("cons.amountGross")}</th>
                </tr>
              </thead>
              <tbody>
                {PAYMENTS.map((m) => (
                  <tr key={m} className="border-b border-slate-100 last:border-0">
                    <td className="px-4 py-3">{t(`cons.payments.${m}`)}</td>
                    <td className="px-4 py-3">{summary.by_payment[m]?.count ?? 0}</td>
                    <td className="px-4 py-3">{ft(summary.by_payment[m]?.net ?? 0)}</td>
                    <td className="px-4 py-3">{ft(summary.by_payment[m]?.gross ?? 0)}</td>
                  </tr>
                ))}
                <tr className="bg-slate-50 font-semibold">
                  <td className="px-4 py-3">{t("cons.total")}</td>
                  <td className="px-4 py-3">{summary.count}</td>
                  <td className="px-4 py-3">{ft(summary.total_net)}</td>
                  <td className="px-4 py-3">{ft(summary.total_gross)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </>
      )}
    </AppShell>
  );
}
