"use client";

/** Vezérlőpult — rendszerüzenetek: függő kérelmek (azonnali döntéssel),
 * ma távol lévők, éppen bent lévők, jövő heti beosztás állapota. */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

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

export default function VezerlopultPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const { t, lang } = useT();
  const { toast } = useUI();

  const load = useCallback(() => {
    api.get<DashboardData>("/api/dashboard").then(setData).catch(() => {});
  }, []);
  useEffect(load, [load]);

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
    </AppShell>
  );
}
