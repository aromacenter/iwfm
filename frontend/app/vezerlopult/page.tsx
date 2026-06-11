"use client";

/** Vezérlőpult — rendszerüzenetek: függő kérelmek (azonnali döntéssel),
 * ma távol lévők, éppen bent lévők, jövő heti beosztás állapota. */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { TIME_OFF_LABELS } from "@/lib/types";

interface DashboardData {
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

  const load = useCallback(() => {
    api.get<DashboardData>("/api/dashboard").then(setData).catch(() => {});
  }, []);
  useEffect(load, [load]);

  async function decide(id: string, status: "approved" | "rejected") {
    try {
      await api.patch(`/api/time-off/${id}/decide`, { status });
      load();
    } catch (err) {
      alert(errorMessage(err));
    }
  }

  if (!data) {
    return (
      <AppShell>
        <p className="text-slate-500">Betöltés…</p>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <h1 className="mb-4 text-xl font-bold">Vezérlőpult</h1>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Függő kérelmek — a legfontosabb rendszerüzenetek */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:col-span-2">
          <h2 className="mb-3 font-semibold">
            Elbírálásra váró kérelmek{" "}
            {data.pending_time_off.length > 0 && (
              <span className="ml-1 rounded-full bg-amber-100 px-2 py-0.5 text-sm font-bold text-amber-800">
                {data.pending_time_off.length}
              </span>
            )}
          </h2>
          {data.pending_time_off.length === 0 ? (
            <p className="text-sm text-slate-400">Nincs függő kérelem. ✓</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {data.pending_time_off.map((t) => (
                <li key={t.id} className="flex flex-wrap items-center gap-3 py-3">
                  <div className="min-w-0 flex-1">
                    <span className="font-medium">{t.employee_name}</span>{" "}
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">
                      {TIME_OFF_LABELS[t.type] ?? t.type}
                    </span>
                    <div className="text-sm text-slate-500">
                      {t.start_date} → {t.end_date}
                      {t.reason && <span className="text-slate-400"> · {t.reason}</span>}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => decide(t.id, "approved")}
                      className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700"
                    >
                      Jóváhagy
                    </button>
                    <button
                      onClick={() => decide(t.id, "rejected")}
                      className="rounded-lg border border-red-300 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50"
                    >
                      Elutasít
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Ma távol */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 font-semibold">Ma távol</h2>
          {data.on_leave_today.length === 0 ? (
            <p className="text-sm text-slate-400">Ma mindenki elérhető.</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {data.on_leave_today.map((t, i) => (
                <li key={i} className="flex items-center justify-between">
                  <span className="font-medium">{t.employee_name}</span>
                  <span className="text-slate-500">
                    {TIME_OFF_LABELS[t.type] ?? t.type} · {t.until}-ig
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Éppen bent */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 font-semibold">
            Éppen dolgozik{" "}
            <span className="ml-1 rounded-full bg-emerald-100 px-2 py-0.5 text-sm font-bold text-emerald-800">
              {data.clocked_in_now.length}
            </span>
          </h2>
          {data.clocked_in_now.length === 0 ? (
            <p className="text-sm text-slate-400">Most senki sincs beblokkolva.</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {data.clocked_in_now.map((c, i) => (
                <li key={i} className="flex items-center justify-between">
                  <span className="font-medium">{c.employee_name}</span>
                  <span className="text-slate-500">
                    {new Date(c.since).toLocaleTimeString("hu-HU", {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}{" "}
                    óta
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Jövő hét */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:col-span-2">
          <h2 className="mb-2 font-semibold">Jövő heti beosztás ({data.next_week.week_start})</h2>
          {data.next_week.draft_shifts > 0 ? (
            <p className="text-sm text-amber-700">
              ⚠️ {data.next_week.draft_shifts} műszak még piszkozatban — a beosztást legkésőbb 7
              nappal a hét kezdete előtt közölni kell (Mt. 97.§ (4)).{" "}
              <Link href="/beosztas" className="font-medium text-indigo-600 underline">
                Ugrás a beosztáshoz →
              </Link>
            </p>
          ) : (
            <p className="text-sm text-slate-500">
              Nincs közlésre váró piszkozat. Aktív dolgozók: {data.active_employees}.{" "}
              <Link href="/beosztas" className="font-medium text-indigo-600 underline">
                Beosztás szerkesztése →
              </Link>
            </p>
          )}
        </section>
      </div>
    </AppShell>
  );
}
