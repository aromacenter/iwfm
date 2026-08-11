"use client";

/** Üzletkötő-elszámolás: ki számoltatott el, mely ügyfeleket, mekkora
 *  összegben, milyen fizetési móddal — a végén fizetési módonkénti összesítés. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import IconLegend from "@/components/IconLegend";
import { api, errorMessage } from "@/lib/api";
import { COMPANIES, COMPANY_CHIP, COMPANY_SHORT, type CompanyKey } from "@/lib/companies";
import { useT } from "@/lib/i18n";
import { usePerms } from "@/lib/perms";
import { useUI } from "@/lib/ui";

interface Agent {
  user_id: string | null;
  name: string;
  count: number;
}

interface Settlement {
  id: string;
  partner_name: string | null;
  settled_by_name: string;
  invoicing_company: CompanyKey | null;
  payment_method: "cash" | "card" | "transfer";
  total_net: number;
  total_gross: number;
  invoiced: boolean;
  created_at: string;
}

interface Summary {
  by_payment: Record<string, { count: number; net: number; gross: number }>;
  by_company: Record<string, { count: number; net: number; gross: number }>;
  total_net: number;
  total_gross: number;
  count: number;
}

interface Receivable {
  id: string;
  partner_name: string | null;
  payment_method: "cash" | "card" | "transfer";
  total_gross: number;
  billingo_document_id: string | null;
  due_date: string | null;
  days_overdue: number;
  created_at: string;
}

const PAYMENTS = ["cash", "card", "transfer"] as const;

export default function UzletkotoPage() {
  const { t, lang } = useT();
  const { toast, confirm } = useUI();
  const { can } = usePerms();
  const canDelete = can("delete");
  const canInvoice = can("invoicing");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentId, setAgentId] = useState("");
  const [company, setCompany] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [rows, setRows] = useState<Settlement[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [receivables, setReceivables] = useState<Receivable[]>([]);

  const fmt = (dt: string) =>
    new Date(dt).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { dateStyle: "short", timeStyle: "short" });
  const ft = (n: number) => `${n.toLocaleString(lang === "hu" ? "hu-HU" : "en-GB")} Ft`;

  useEffect(() => {
    api.get<Agent[]>("/api/settlements/agents").then(setAgents).catch(() => {});
  }, []);

  const load = useCallback(() => {
    const params = new URLSearchParams();
    if (agentId) params.set("settled_by", agentId);
    if (company) params.set("company", company);
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    api.get<Settlement[]>(`/api/settlements?${params}`).then(setRows).catch(() => {});
    api.get<Summary>(`/api/settlements/summary?${params}`).then(setSummary).catch(() => {});
  }, [agentId, company, dateFrom, dateTo]);
  useEffect(load, [load]);

  const loadReceivables = useCallback(() => {
    if (!canInvoice) return;
    api.get<Receivable[]>("/api/settlements/receivables").then(setReceivables).catch(() => {});
  }, [canInvoice]);
  useEffect(loadReceivables, [loadReceivables]);

  async function markPaid(r: Receivable) {
    if (!(await confirm(t("recv.markPaidConfirm", { name: r.partner_name ?? "?", gross: r.total_gross.toLocaleString("hu-HU") })))) return;
    try {
      await api.post(`/api/settlements/${r.id}/mark-paid`);
      toast(t("recv.markedPaid"), "success");
      loadReceivables();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function syncPayment(r: Receivable) {
    try {
      const res = await api.post<{ billingo_payment_status: string | null; payment_status: string }>(
        `/api/settlements/${r.id}/sync-payment`,
      );
      if (res.payment_status === "paid") {
        toast(t("recv.syncedPaid"), "success");
      } else {
        toast(t("recv.syncedNotPaid", { status: res.billingo_payment_status ?? "?" }), "info");
      }
      loadReceivables();
    } catch (err) {
      toast(errorMessage(err), "error");
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
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

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
        <select
          value={company}
          onChange={(e) => setCompany(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          <option value="">{t("partners.allCompanies")}</option>
          <option value="xp">{COMPANY_SHORT.xp}</option>
          <option value="pc">{COMPANY_SHORT.pc}</option>
        </select>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          {t("cons.dateFrom")}
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1.5" />
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          {t("cons.dateTo")}
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1.5" />
        </label>
        {selected.size > 0 && (
          <button
            onClick={bulkDelete}
            className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
          >
            {t("bulk.deleteSelected", { count: selected.size })}
          </button>
        )}
      </div>

      <div className="mb-6 rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-slate-500">
              {canDelete && (
              <th className="sticky top-0 z-10 w-8 border-b border-slate-200 bg-white px-3 py-3">
                <input
                  type="checkbox"
                  checked={rows.length > 0 && rows.every((s) => selected.has(s.id))}
                  onChange={(e) =>
                    setSelected(e.target.checked ? new Set(rows.map((s) => s.id)) : new Set())
                  }
                  className="h-4 w-4"
                />
              </th>
              )}
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("cons.date")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("cons.partner")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("cons.payment")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3 text-right">{t("cons.amountGross")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("cons.invoiced")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id} className="border-b border-slate-100 last:border-0">
                {canDelete && (
                <td className="px-3 py-3">
                  <input
                    type="checkbox"
                    checked={selected.has(s.id)}
                    onChange={() => toggleSelect(s.id)}
                    className="h-4 w-4"
                  />
                </td>
                )}
                <td className="px-4 py-3 whitespace-nowrap">{fmt(s.created_at)}</td>
                <td className="px-4 py-3">
                  <div className="font-medium">{s.partner_name}</div>
                  <div className="flex items-center gap-1.5 text-xs text-slate-400">
                    <span>{s.settled_by_name}</span>
                    {s.invoicing_company && (
                      <span
                        title={COMPANIES[s.invoicing_company]}
                        className={`rounded px-1 py-0.5 text-[10px] font-medium ${COMPANY_CHIP[s.invoicing_company]}`}
                      >
                        {COMPANY_SHORT[s.invoicing_company]}
                      </span>
                    )}
                  </div>
                </td>
                <td className="px-4 py-3">{t(`cons.payments.${s.payment_method}`)}</td>
                <td className="px-4 py-3 text-right">
                  <div className="font-medium">{ft(s.total_gross)}</div>
                  <div className="text-xs text-slate-400">{t("cons.amountNet")}: {ft(s.total_net)}</div>
                </td>
                <td className="px-4 py-3">
                  <span className={`rounded px-2 py-0.5 text-xs font-medium ${s.invoiced ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"}`}>
                    {s.invoiced ? t("cons.invoiced") : t("cons.notInvoiced")}
                  </span>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-10 text-center text-slate-400">{t("cons.noSettlements")}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {canInvoice && receivables.length > 0 && (
        <div className="mb-6">
          <h2 className="mb-2 font-semibold">{t("recv.title", { count: receivables.length })}</h2>
          <IconLegend
            items={[
              { icon: "✅", label: t("recv.markPaid") },
              { icon: "🔄", label: t("recv.sync") },
            ]}
          />
          <div className="overflow-x-auto rounded-2xl border border-red-200 bg-white shadow-sm">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-red-200 bg-red-50 text-left text-xs uppercase text-red-700">
                  <th className="px-4 py-3">{t("cons.partner")}</th>
                  <th className="px-4 py-3">{t("cons.date")}</th>
                  <th className="px-4 py-3">{t("recv.dueDate")}</th>
                  <th className="px-4 py-3">{t("recv.overdue")}</th>
                  <th className="px-4 py-3">{t("cons.amountGross")}</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody>
                {receivables.map((r) => (
                  <tr key={r.id} className="border-b border-slate-100 last:border-0">
                    <td className="px-4 py-3 font-medium">{r.partner_name}</td>
                    <td className="px-4 py-3 whitespace-nowrap">{fmt(r.created_at)}</td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      {r.due_date ? new Date(r.due_date).toLocaleDateString(lang === "hu" ? "hu-HU" : "en-GB") : "—"}
                    </td>
                    <td className="px-4 py-3">
                      {r.days_overdue > 0 ? (
                        <span className="rounded bg-red-100 px-2 py-0.5 text-xs font-semibold text-red-700">
                          {t("recv.overdueDays", { days: r.days_overdue })}
                        </span>
                      ) : (
                        <span className="text-xs text-slate-400">{t("recv.notYetDue")}</span>
                      )}
                    </td>
                    <td className="px-4 py-3 font-medium">{ft(r.total_gross)}</td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        {r.billingo_document_id && (
                          <button
                            onClick={() => syncPayment(r)}
                            title={t("recv.syncTitle")}
                            className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100"
                          >
                            🔄
                          </button>
                        )}
                        <button
                          onClick={() => markPaid(r)}
                          title={t("recv.markPaid")}
                          className="rounded border border-emerald-300 px-2 py-1 text-sm leading-none hover:bg-emerald-50"
                        >
                          ✅
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                <tr className="bg-red-50 font-semibold text-red-800">
                  <td className="px-4 py-3" colSpan={4}>{t("recv.totalOutstanding")}</td>
                  <td className="px-4 py-3">{ft(receivables.reduce((sum, r) => sum + r.total_gross, 0))}</td>
                  <td />
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}

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
                {(["xp", "pc"] as const).map((c) =>
                  summary.by_company?.[c]?.count ? (
                    <tr key={c} className="border-b border-slate-100 text-slate-600 last:border-0">
                      <td className="px-4 py-3">
                        <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${COMPANY_CHIP[c]}`}>
                          {COMPANIES[c]}
                        </span>
                      </td>
                      <td className="px-4 py-3">{summary.by_company[c].count}</td>
                      <td className="px-4 py-3">{ft(summary.by_company[c].net)}</td>
                      <td className="px-4 py-3">{ft(summary.by_company[c].gross)}</td>
                    </tr>
                  ) : null,
                )}
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
