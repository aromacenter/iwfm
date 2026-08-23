"use client";

/** Vezérlőpult — rendszerüzenetek: függő kérelmek (azonnali döntéssel),
 * ma távol lévők, éppen bent lévők, jövő heti beosztás állapota. */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import PartnerInfo from "@/components/PartnerInfo";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface ConsStats {
  monthly_revenue: { month: string; gross: number; count: number }[];
  top_partners: { name: string; gross: number; count: number }[];
  agent_ranking: { name: string; gross: number; count: number }[];
  by_payment: Record<string, { count: number; gross: number }>;
  outstanding_total: number;
  open_service_tickets: number;
  low_stock_count: number;
}

interface DashboardData {
  todays_tasks: { id: string; title: string; employee_name: string; status: string }[];
  pending_time_off: {
    id: string;
    employee_name: string;
    type: string;
    start_date: string;
    end_date: string;
    reason: string | null;
    created_at: string;
  }[];
  on_leave_today: { employee_name: string; type: string; until: string }[];
  clocked_in_now: { employee_name: string; since: string }[];
  next_week: { week_start: string; draft_shifts: number };
  active_employees: number;
}

interface PerfRow {
  partner_id: string;
  name: string;
  partner_code: string | null;
  invoicing_company: string | null;
  gross: number;
  settlements: number;
  machines: number;
  tickets: number;
  service_cost: number;
  per_visit: number;
}

interface CoverageRow {
  city: string;
  partners: number;
  machines: number;
  gross: number;
}

interface PartnerPerformance {
  top: PerfRow[];
  bottom: PerfRow[];
}

interface ReceivablesStats {
  total: number;
  overdue_total: number;
  aging: { b0_30: number; b31_60: number; b61_90: number; b90_plus: number };
  debtor_count: number;
  top_debtors: {
    partner_id: string;
    partner_name: string;
    remaining: number;
    items: number;
    oldest_at: string;
    oldest_days: number;
    overdue_items: number;
  }[];
}

export default function VezerlopultPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [stats, setStats] = useState<ConsStats | null>(null);
  const [performance, setPerformance] = useState<PartnerPerformance | null>(null);
  const [coverage, setCoverage] = useState<CoverageRow[]>([]);
  const [receivables, setReceivables] = useState<ReceivablesStats | null>(null);
  const [infoPartner, setInfoPartner] = useState<string | null>(null);
  const { t, lang } = useT();
  const { toast } = useUI();

  const load = useCallback(() => {
    api.get<DashboardData>("/api/dashboard").then(setData).catch(() => {});
    api.get<ConsStats>("/api/dashboard/consignment-stats").then(setStats).catch(() => {});
    api.get<PartnerPerformance>("/api/dashboard/partner-performance").then(setPerformance).catch(() => {});
    api.get<CoverageRow[]>("/api/dashboard/coverage").then(setCoverage).catch(() => {});
    api.get<ReceivablesStats>("/api/dashboard/receivables-stats").then(setReceivables).catch(() => {});
  }, []);
  useEffect(load, [load]);

  const ft = (n: number) => `${n.toLocaleString(lang === "hu" ? "hu-HU" : "en-GB")} Ft`;

  async function decide(id: string, status: "approved" | "rejected") {
    try {
      await api.patch(`/api/time-off/${id}/decide`, { status });
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  if (!data) {
    return (
      <AppShell>
        <p className="text-slate-500">{t("common.loading")}</p>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <h1 className="mb-4 text-xl font-bold">{t("dash.title")}</h1>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Függő kérelmek */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:col-span-2">
          <h2 className="mb-3 font-semibold">
            {t("dash.pendingRequests")}{" "}
            {data.pending_time_off.length > 0 && (
              <span className="ml-1 rounded-full bg-amber-100 px-2 py-0.5 text-sm font-bold text-amber-800">
                {data.pending_time_off.length}
              </span>
            )}
          </h2>
          {data.pending_time_off.length === 0 ? (
            <p className="text-sm text-slate-400">{t("dash.noPending")}</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {data.pending_time_off.map((item) => (
                <li key={item.id} className="flex flex-wrap items-center gap-3 py-3">
                  <div className="min-w-0 flex-1">
                    <span className="font-medium">{item.employee_name}</span>{" "}
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">
                      {t(`leave.types.${item.type}`)}
                    </span>
                    <div className="text-sm text-slate-500">
                      {item.start_date} → {item.end_date}
                      {item.reason && <span className="text-slate-400"> · {item.reason}</span>}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => decide(item.id, "approved")}
                      className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700"
                    >
                      {t("dash.approve")}
                    </button>
                    <button
                      onClick={() => decide(item.id, "rejected")}
                      className="rounded-lg border border-red-300 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50"
                    >
                      {t("dash.reject")}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Mai feladatok */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:col-span-2">
          <h2 className="mb-3 font-semibold">
            {t("dash.todaysTasks")}{" "}
            {data.todays_tasks.length > 0 && (
              <span className="ml-1 rounded-full bg-sky-100 px-2 py-0.5 text-sm font-bold text-sky-800">
                {data.todays_tasks.length}
              </span>
            )}
          </h2>
          {data.todays_tasks.length === 0 ? (
            <p className="text-sm text-slate-400">
              {t("dash.noTasksToday")}{" "}
              <Link href="/feladatok" className="font-medium text-indigo-600 underline">
                {t("dash.newTaskLink")}
              </Link>
            </p>
          ) : (
            <ul className="divide-y divide-slate-100 text-sm">
              {data.todays_tasks.map((task) => (
                <li key={task.id} className="flex items-center justify-between gap-3 py-2">
                  <span>
                    <span className="font-medium">{task.title}</span>
                    <span className="text-slate-500"> — {task.employee_name}</span>
                  </span>
                  <span
                    className={`rounded px-2 py-0.5 text-xs font-medium ${
                      task.status === "done"
                        ? "bg-emerald-100 text-emerald-800"
                        : task.status === "needs_more_work"
                          ? "bg-amber-100 text-amber-800"
                          : "bg-sky-100 text-sky-800"
                    }`}
                  >
                    {t(`tasks.statuses.${task.status}`)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Ma távol */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 font-semibold">{t("dash.onLeaveToday")}</h2>
          {data.on_leave_today.length === 0 ? (
            <p className="text-sm text-slate-400">{t("dash.everyoneAvailable")}</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {data.on_leave_today.map((item, i) => (
                <li key={i} className="flex items-center justify-between">
                  <span className="font-medium">{item.employee_name}</span>
                  <span className="text-slate-500">
                    {t(`leave.types.${item.type}`)} · {t("dash.until", { date: item.until })}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Éppen bent */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 font-semibold">
            {t("dash.workingNow")}{" "}
            <span className="ml-1 rounded-full bg-emerald-100 px-2 py-0.5 text-sm font-bold text-emerald-800">
              {data.clocked_in_now.length}
            </span>
          </h2>
          {data.clocked_in_now.length === 0 ? (
            <p className="text-sm text-slate-400">{t("dash.nobodyClockedIn")}</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {data.clocked_in_now.map((item, i) => (
                <li key={i} className="flex items-center justify-between">
                  <span className="font-medium">{item.employee_name}</span>
                  <span className="text-slate-500">
                    {t("dash.since", {
                      time: new Date(item.since).toLocaleTimeString(
                        lang === "hu" ? "hu-HU" : "en-GB",
                        { hour: "2-digit", minute: "2-digit" }
                      ),
                    })}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Jövő hét */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:col-span-2">
          <h2 className="mb-2 font-semibold">
            {t("dash.nextWeek", { date: data.next_week.week_start })}
          </h2>
          {data.next_week.draft_shifts > 0 ? (
            <p className="text-sm text-amber-700">
              {t("dash.draftWarning", { count: data.next_week.draft_shifts })}{" "}
              <Link href="/beosztas" className="font-medium text-indigo-600 underline">
                {t("dash.goToSchedule")}
              </Link>
            </p>
          ) : (
            <p className="text-sm text-slate-500">
              {t("dash.noDrafts", { count: data.active_employees })}{" "}
              <Link href="/beosztas" className="font-medium text-indigo-600 underline">
                {t("dash.editSchedule")}
              </Link>
            </p>
          )}
        </section>
      </div>

      {stats && (
        <>
          <h2 className="mb-3 mt-8 text-lg font-bold">{t("dash.consTitle")}</h2>

          <div className="mb-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <p className="text-xs uppercase text-slate-500">{t("dash.statThisMonth")}</p>
              <p className="mt-1 text-2xl font-bold">
                {ft(stats.monthly_revenue[stats.monthly_revenue.length - 1]?.gross ?? 0)}
              </p>
            </div>
            <Link href="/uzletkoto" className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm hover:border-red-300">
              <p className="text-xs uppercase text-slate-500">{t("dash.statOutstanding")}</p>
              <p className={`mt-1 text-2xl font-bold ${stats.outstanding_total > 0 ? "text-red-600" : ""}`}>
                {ft(stats.outstanding_total)}
              </p>
            </Link>
            <Link href="/szerviz" className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm hover:border-blue-300">
              <p className="text-xs uppercase text-slate-500">{t("dash.statOpenTickets")}</p>
              <p className="mt-1 text-2xl font-bold">{stats.open_service_tickets}</p>
            </Link>
            <Link href="/elszamolas" className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm hover:border-rose-300">
              <p className="text-xs uppercase text-slate-500">{t("dash.statLowStock")}</p>
              <p className={`mt-1 text-2xl font-bold ${stats.low_stock_count > 0 ? "text-rose-600" : ""}`}>
                {stats.low_stock_count}
              </p>
            </Link>
          </div>

          {/* Kintlévőség-statisztika: ki, mennyivel, mióta tartozik (korosítva) */}
          {receivables && receivables.total > 0 && (
            <section className="mb-4 rounded-2xl border border-rose-200 bg-white p-5 shadow-sm">
              <div className="mb-3 flex flex-wrap items-center gap-3">
                <h3 className="font-semibold text-rose-800">💸 {t("dash.recvTitle")}</h3>
                <span className="text-sm text-slate-500">
                  {t("dash.recvSummary", {
                    total: ft(Math.round(receivables.total)),
                    count: receivables.debtor_count,
                  })}
                </span>
                {receivables.overdue_total > 0 && (
                  <span className="rounded-full bg-rose-100 px-2.5 py-0.5 text-xs font-semibold text-rose-700">
                    {t("dash.recvOverdue", { amount: ft(Math.round(receivables.overdue_total)) })}
                  </span>
                )}
              </div>
              <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {([
                  ["b0_30", t("dash.recvAge0")],
                  ["b31_60", t("dash.recvAge31")],
                  ["b61_90", t("dash.recvAge61")],
                  ["b90_plus", t("dash.recvAge90")],
                ] as const).map(([key, label], i) => (
                  <div key={key} className={`rounded-xl p-3 ${["bg-amber-50", "bg-orange-50", "bg-rose-50", "bg-rose-100"][i]}`}>
                    <p className="text-xs text-slate-500">{label}</p>
                    <p className="mt-0.5 font-bold tabular-nums">{ft(Math.round(receivables.aging[key]))}</p>
                  </div>
                ))}
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-slate-400">
                    <th className="py-1.5 pr-3">{t("cons.partner")}</th>
                    <th className="py-1.5 pr-3 text-right">{t("dash.recvAmount")}</th>
                    <th className="py-1.5 pr-3 text-right">{t("dash.recvItems")}</th>
                    <th className="py-1.5">{t("dash.recvSince")}</th>
                  </tr>
                </thead>
                <tbody>
                  {receivables.top_debtors.map((d) => (
                    <tr key={d.partner_id} className="border-t border-slate-100">
                      <td className="py-1.5 pr-3">
                        <button
                          onClick={() => setInfoPartner(d.partner_id)}
                          className="text-left font-medium text-indigo-700 underline decoration-indigo-300 underline-offset-2 hover:text-indigo-900"
                        >
                          {d.partner_name}
                        </button>
                      </td>
                      <td className="py-1.5 pr-3 text-right font-semibold text-rose-700 tabular-nums">
                        {ft(Math.round(d.remaining))}
                      </td>
                      <td className="py-1.5 pr-3 text-right text-slate-500">{d.items}</td>
                      <td className="py-1.5 text-slate-500">
                        {d.oldest_at}
                        <span className={`ml-1.5 rounded px-1.5 py-0.5 text-[11px] font-semibold ${d.oldest_days > 60 ? "bg-rose-100 text-rose-700" : "bg-slate-100 text-slate-500"}`}>
                          {t("dash.recvDays", { days: d.oldest_days })}
                        </span>
                        {d.overdue_items > 0 && (
                          <span className="ml-1 rounded bg-rose-200 px-1.5 py-0.5 text-[11px] font-semibold text-rose-800">
                            {t("cons.debtOverdue")}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:col-span-2">
              <h3 className="mb-4 font-semibold">{t("dash.monthlyRevenue")}</h3>
              {(() => {
                const max = Math.max(...stats.monthly_revenue.map((m) => m.gross), 1);
                return (
                  <div className="flex h-40 gap-1.5">
                    {stats.monthly_revenue.map((m) => (
                      <div key={m.month} className="group flex h-full flex-1 flex-col items-center gap-1">
                        <div className="flex w-full flex-1 items-end">
                          <div
                            title={`${m.month}: ${ft(m.gross)} (${m.count})`}
                            className="w-full rounded-t bg-indigo-500 transition-colors group-hover:bg-indigo-600"
                            style={{ height: `${Math.max((m.gross / max) * 100, m.gross > 0 ? 4 : 1)}%` }}
                          />
                        </div>
                        <span className="text-[10px] text-slate-400">{m.month.slice(5)}</span>
                      </div>
                    ))}
                  </div>
                );
              })()}
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <h3 className="mb-3 font-semibold">{t("dash.topPartners")}</h3>
              {stats.top_partners.length === 0 ? (
                <p className="text-sm text-slate-400">{t("dash.noStatsData")}</p>
              ) : (
                <ul className="space-y-2 text-sm">
                  {stats.top_partners.map((p, i) => (
                    <li key={p.name} className="flex items-center justify-between">
                      <span>
                        <span className="mr-2 inline-block w-5 text-center font-bold text-slate-400">{i + 1}.</span>
                        <span className="font-medium">{p.name}</span>
                        <span className="ml-1 text-xs text-slate-400">({p.count})</span>
                      </span>
                      <span className="font-semibold">{ft(p.gross)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <h3 className="mb-3 font-semibold">{t("dash.agentRanking")}</h3>
              {stats.agent_ranking.length === 0 ? (
                <p className="text-sm text-slate-400">{t("dash.noStatsData")}</p>
              ) : (
                <ul className="space-y-2 text-sm">
                  {stats.agent_ranking.map((a, i) => (
                    <li key={a.name} className="flex items-center justify-between">
                      <span>
                        <span className="mr-2 inline-block w-5 text-center font-bold text-slate-400">{i + 1}.</span>
                        <span className="font-medium">{a.name}</span>
                        <span className="ml-1 text-xs text-slate-400">({a.count})</span>
                      </span>
                      <span className="font-semibold">{ft(a.gross)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:col-span-2">
              <h3 className="mb-3 font-semibold">{t("dash.paymentSplit")}</h3>
              <div className="grid grid-cols-3 gap-3 text-sm">
                {(["cash", "card", "transfer"] as const).map((m) => (
                  <div key={m} className="rounded-xl bg-slate-50 p-3 text-center">
                    <p className="text-xs uppercase text-slate-500">{t(`cons.payments.${m}`)}</p>
                    <p className="mt-1 font-bold">{ft(stats.by_payment[m]?.gross ?? 0)}</p>
                    <p className="text-xs text-slate-400">{stats.by_payment[m]?.count ?? 0} db</p>
                  </div>
                ))}
              </div>
            </section>

            {performance && (performance.top.length > 0 || performance.bottom.length > 0) && (
              <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:col-span-2">
                <h3 className="mb-1 font-semibold">{t("dash.perfTitle")}</h3>
                <p className="mb-3 text-xs text-slate-500">{t("dash.perfHint")}</p>
                <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
                  {([["top", performance.top], ["bottom", performance.bottom]] as const).map(
                    ([key, rows]) =>
                      rows.length > 0 && (
                        <div key={key}>
                          <p className="mb-2 text-xs font-semibold uppercase text-slate-400">
                            {t(key === "top" ? "dash.perfTop" : "dash.perfBottom")}
                          </p>
                          <table className="w-full text-sm">
                            <thead>
                              <tr className="text-left text-xs uppercase text-slate-500">
                                <th className="py-1.5">{t("cons.partner")}</th>
                                <th className="py-1.5 text-right">{t("dash.perfGross")}</th>
                                <th className="py-1.5 text-right">{t("dash.perfPerVisit")}</th>
                                <th className="py-1.5 text-right" title={t("dash.perfServiceCostHint")}>{t("dash.perfServiceCost")}</th>
                                <th className="py-1.5 text-right" title={t("dash.perfTicketsHint")}>🔧</th>
                              </tr>
                            </thead>
                            <tbody>
                              {rows.map((r) => (
                                <tr key={r.partner_id} className="border-t border-slate-100">
                                  <td className="py-1.5">
                                    <span className="font-medium">{r.name}</span>
                                    <span className="ml-1 text-xs text-slate-400">
                                      {r.machines > 0 ? `${r.machines} gép` : ""}
                                    </span>
                                  </td>
                                  <td className="py-1.5 text-right font-semibold">{ft(r.gross)}</td>
                                  <td className="py-1.5 text-right text-slate-500">{ft(r.per_visit)}</td>
                                  <td className={`py-1.5 text-right ${r.service_cost > 0 && r.service_cost > r.gross * 0.2 ? "font-semibold text-rose-600" : "text-slate-500"}`}>
                                    {r.service_cost > 0 ? ft(r.service_cost) : "—"}
                                  </td>
                                  <td className={`py-1.5 text-right ${r.tickets >= 3 ? "font-semibold text-rose-600" : "text-slate-500"}`}>
                                    {r.tickets}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      ),
                  )}
                </div>
              </section>
            )}

            {coverage.length > 0 && (
              <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:col-span-2">
                <h3 className="mb-1 font-semibold">{t("dash.coverageTitle")}</h3>
                <p className="mb-3 text-xs text-slate-500">{t("dash.coverageHint")}</p>
                <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3 lg:grid-cols-4">
                  {coverage.slice(0, 24).map((c) => (
                    <div key={c.city} className="flex items-baseline justify-between border-b border-slate-100 py-1">
                      <span className="truncate">
                        <span className="font-medium">{c.city}</span>
                        <span className="ml-1 text-xs text-slate-400">{c.partners}p · {c.machines}g</span>
                      </span>
                      <span className="ml-2 whitespace-nowrap text-xs font-semibold text-slate-600">{ft(c.gross)}</span>
                    </div>
                  ))}
                </div>
              </section>
            )}
          </div>
        </>
      )}
      <PartnerInfo partnerId={infoPartner} onClose={() => setInfoPartner(null)} />
    </AppShell>
  );
}
