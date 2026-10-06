"use client";

/** Üzleti statisztika: bevételek és költségek bontása kategóriára (kávé,
 * egyéb áru, szerviz, üzemeltetés), dolgozóra és gépre — napi/heti/havi/éves
 * vagy szabadon választott időszakra. Ebből látszik, mely gépeket,
 * partnereket, termékeket érdemes tartani. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface Stats {
  categories: Record<string, { net: number; count: number }>;
  by_employee: { name: string; net: number; gross: number; count: number }[];
  by_machine: { barcode: string; name: string | null; net: number; portions: number; count: number }[];
  costs_by_user: { name: string | null; expense: number; deposit: number; withdrawal: number }[];
  expenses_total: number;
  revenue_total_net: number;
}

const CAT_META: Record<string, { icon: string }> = {
  coffee: { icon: "☕" },
  goods: { icon: "🧃" },
  service: { icon: "🔧" },
  operations: { icon: "🏢" },
};

function iso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export default function StatisztikaPage() {
  const { t, lang } = useT();
  const ft = (n: number) => `${Math.round(n).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB")} Ft`;

  const [period, setPeriod] = useState<"day" | "week" | "month" | "year" | "custom">("month");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [stats, setStats] = useState<Stats | null>(null);

  const range = useCallback((): [string, string] => {
    const now = new Date();
    if (period === "day") return [iso(now), iso(now)];
    if (period === "week") {
      const d = new Date(now);
      const day = (d.getDay() + 6) % 7; // hétfő = 0
      d.setDate(d.getDate() - day);
      return [iso(d), iso(now)];
    }
    if (period === "month") return [iso(new Date(now.getFullYear(), now.getMonth(), 1)), iso(now)];
    if (period === "year") return [iso(new Date(now.getFullYear(), 0, 1)), iso(now)];
    return [dateFrom, dateTo];
  }, [period, dateFrom, dateTo]);

  useEffect(() => {
    const [from, to] = range();
    const p = new URLSearchParams();
    if (from) p.set("date_from", from);
    if (to) p.set("date_to", to);
    api.get<Stats>(`/api/stats/business?${p}`).then(setStats).catch(() => {});
  }, [range]);

  const maxCat = stats
    ? Math.max(1, ...Object.values(stats.categories).map((c) => c.net))
    : 1;

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">📈 {t("stats.title")}</h1>
        <a
          href="/terkep"
          target="_blank"
          rel="noopener"
          className="rounded-lg border border-indigo-300 px-3 py-1.5 text-sm font-medium text-indigo-700 hover:bg-indigo-50"
        >
          🗺️ {t("stats.mapBtn")}
        </a>
        <div className="flex gap-1.5">
          {([["day", t("stats.day")], ["week", t("stats.week")], ["month", t("stats.month")], ["year", t("stats.year")], ["custom", t("stats.custom")]] as const).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setPeriod(key)}
              className={`rounded-full px-3 py-1.5 text-xs font-medium transition ${
                period === key ? "bg-indigo-600 text-white" : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        {period === "custom" && (
          <>
            <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <span className="text-slate-400">–</span>
            <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          </>
        )}
      </div>

      {stats && (
        <div className="space-y-4">
          {/* Kategória-bontás vizuális sávokkal */}
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-semibold">{t("stats.byCategory")}</h2>
              <div className="flex gap-3 text-sm">
                <span className="rounded-lg bg-emerald-50 px-3 py-1 font-semibold text-emerald-700">
                  {t("stats.revenueTotal")}: {ft(stats.revenue_total_net)} {t("cons.netSuffix")}
                </span>
                <span className="rounded-lg bg-rose-50 px-3 py-1 font-semibold text-rose-700">
                  {t("stats.expensesTotal")}: {ft(stats.expenses_total)}
                </span>
              </div>
            </div>
            <div className="space-y-2">
              {Object.entries(stats.categories).map(([key, c]) => (
                <div key={key} className="flex items-center gap-3">
                  <span className="w-44 shrink-0 text-sm font-medium text-slate-700">
                    {CAT_META[key]?.icon} {t(`stats.cats.${key}`)}
                  </span>
                  <div className="h-5 flex-1 overflow-hidden rounded-full bg-slate-100">
                    <div
                      className="h-full rounded-full bg-indigo-500"
                      style={{ width: `${Math.max(2, (c.net / maxCat) * 100)}%` }}
                    />
                  </div>
                  <span className="w-32 shrink-0 text-right text-sm font-semibold tabular-nums">{ft(c.net)}</span>
                  <span className="w-16 shrink-0 text-right text-xs text-slate-400">{c.count} {t("stats.items")}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <h2 className="mb-2 font-semibold">👥 {t("stats.byEmployee")}</h2>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-slate-500">
                    <th className="py-1.5 pr-2">{t("cash.worker")}</th>
                    <th className="py-1.5 pr-2 text-right">{t("stats.net")}</th>
                    <th className="py-1.5 pr-2 text-right">{t("stats.gross")}</th>
                    <th className="py-1.5 text-right">{t("stats.count")}</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.by_employee.map((r, i) => (
                    <tr key={i} className="border-t border-slate-100">
                      <td className="py-1.5 pr-2 font-medium">{r.name}</td>
                      <td className="py-1.5 pr-2 text-right tabular-nums">{ft(r.net)}</td>
                      <td className="py-1.5 pr-2 text-right tabular-nums text-slate-500">{ft(r.gross)}</td>
                      <td className="py-1.5 text-right tabular-nums text-slate-400">{r.count}</td>
                    </tr>
                  ))}
                  {stats.by_employee.length === 0 && (
                    <tr><td colSpan={4} className="py-6 text-center text-slate-400">{t("stats.empty")}</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <h2 className="mb-2 font-semibold">💸 {t("stats.costs")}</h2>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-slate-500">
                    <th className="py-1.5 pr-2">{t("cash.worker")}</th>
                    <th className="py-1.5 pr-2 text-right">{t("cash.expenses")}</th>
                    <th className="py-1.5 pr-2 text-right">{t("cash.deposits")}</th>
                    <th className="py-1.5 text-right">{t("cash.withdrawals")}</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.costs_by_user.map((r, i) => (
                    <tr key={i} className="border-t border-slate-100">
                      <td className="py-1.5 pr-2 font-medium">{r.name ?? "?"}</td>
                      <td className="py-1.5 pr-2 text-right tabular-nums text-rose-700">{ft(r.expense)}</td>
                      <td className="py-1.5 pr-2 text-right tabular-nums text-emerald-700">{ft(r.deposit)}</td>
                      <td className="py-1.5 text-right tabular-nums text-amber-700">{ft(r.withdrawal)}</td>
                    </tr>
                  ))}
                  {stats.costs_by_user.length === 0 && (
                    <tr><td colSpan={4} className="py-6 text-center text-slate-400">{t("stats.empty")}</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="mb-2 font-semibold">☕ {t("stats.byMachine")}</h2>
            <p className="mb-2 text-xs text-slate-400">{t("stats.byMachineHint")}</p>
            <div className="max-h-[480px] overflow-y-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="sticky top-0 bg-white text-left text-xs uppercase text-slate-500">
                    <th className="py-1.5 pr-2">{t("cons.machineBarcode")}</th>
                    <th className="py-1.5 pr-2">{t("cons.machineName")}</th>
                    <th className="py-1.5 pr-2 text-right">{t("stats.portions")}</th>
                    <th className="py-1.5 pr-2 text-right">{t("stats.net")}</th>
                    <th className="py-1.5 text-right">{t("stats.count")}</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.by_machine.map((r) => (
                    <tr key={r.barcode} className="border-t border-slate-100">
                      <td className="py-1.5 pr-2 font-mono text-xs">{r.barcode}</td>
                      <td className="py-1.5 pr-2">{r.name}</td>
                      <td className="py-1.5 pr-2 text-right tabular-nums">{r.portions.toLocaleString("hu-HU")}</td>
                      <td className="py-1.5 pr-2 text-right font-medium tabular-nums">{ft(r.net)}</td>
                      <td className="py-1.5 text-right tabular-nums text-slate-400">{r.count}</td>
                    </tr>
                  ))}
                  {stats.by_machine.length === 0 && (
                    <tr><td colSpan={5} className="py-6 text-center text-slate-400">{t("stats.empty")}</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
