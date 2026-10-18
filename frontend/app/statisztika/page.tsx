"use client";

/** Üzleti statisztika: bevételek és költségek bontása kategóriára (kávé,
 * egyéb áru, szerviz, üzemeltetés), dolgozóra és gépre — napi/heti/havi/éves
 * vagy szabadon választott időszakra. Ebből látszik, mely gépeket,
 * partnereket, termékeket érdemes tartani. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

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
  // Összeg-részletező (84452fba): bevételre kattintva a mögötte lévő
  // elszámolások, kiadásra kattintva a kassza-tételek.
  interface DrillRevenue { id: string; created_at: string; partner_name: string; total_net: number; total_gross: number; payment_method: string; invoiced: boolean }
  interface DrillExpense { expense_date: string; amount_gross: number; entry_type: string; note: string | null; supplier: string | null; receipt_no: string | null }
  const { t, lang } = useT();
  const { toast } = useUI();
  const [drill, setDrill] = useState<{ kind: "revenue" | "expense"; name: string; rows: (DrillRevenue | DrillExpense)[] } | null>(null);
  async function openDrill(kind: "revenue" | "expense", name: string) {
    try {
      const params = new URLSearchParams({ kind, name });
      if (archiveMode) {
        params.set("archive", "true");
      } else {
        const [from, to] = range();
        if (from) params.set("date_from", from);
        if (to) params.set("date_to", to);
      }
      const rows = await api.get<(DrillRevenue | DrillExpense)[]>(`/api/stats/business/drill?${params}`);
      setDrill({ kind, name, rows });
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  const ft = (n: number) => `${Math.round(n).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB")} Ft`;

  // Gép teljes története (5404e465): elszámolások + szerviz-jegyek
  interface MachineHistory {
    barcode: string;
    name: string | null;
    status: string | null;
    settlements: { date: string; partner: string | null; prev_counter: number; new_counter: number; portions: number; net: number }[];
    service: { ticket_no: string; title: string; status: string; created_at: string; partner: string | null }[];
  }
  const [machineHist, setMachineHist] = useState<MachineHistory | null>(null);
  async function openMachine(barcode: string) {
    try {
      const h = await api.get<MachineHistory>(`/api/stats/machine-history?barcode=${encodeURIComponent(barcode)}`);
      setMachineHist(h);
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  const [period, setPeriod] = useState<"day" | "week" | "month" | "year" | "custom">("month");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [stats, setStats] = useState<Stats | null>(null);
  // Archívum (30ea42b2): a statisztika is a 2026-10-12-i újraindítástól
  // számol — a régi világ csak adminnak, külön nézetben.
  const [archiveMode, setArchiveMode] = useState(false);
  const [meRole, setMeRole] = useState<string | null>(null);
  useEffect(() => {
    api.get<{ role?: string }>("/api/auth/me").then((m) => setMeRole(m.role ?? null)).catch(() => {});
  }, []);

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
    const p = new URLSearchParams();
    if (archiveMode) {
      p.set("archive", "true");
    } else {
      const [from, to] = range();
      if (from) p.set("date_from", from);
      if (to) p.set("date_to", to);
    }
    api.get<Stats>(`/api/stats/business?${p}`).then(setStats).catch(() => {});
  }, [range, archiveMode]);

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
              onClick={() => { setPeriod(key); setArchiveMode(false); }}
              className={`rounded-full px-3 py-1.5 text-xs font-medium transition ${
                period === key && !archiveMode ? "bg-indigo-600 text-white" : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"
              }`}
            >
              {label}
            </button>
          ))}
          {meRole === "admin" && (
            <button
              onClick={() => setArchiveMode((a) => !a)}
              title={t("cash.archiveHint")}
              className={`rounded-full px-3 py-1.5 text-xs font-medium transition ${
                archiveMode ? "bg-slate-700 text-white" : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"
              }`}
            >
              📦 {t("cash.archiveBtn")}
            </button>
          )}
        </div>
        {archiveMode && (
          <p className="w-full rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            📦 {t("cash.archiveBanner")}
          </p>
        )}
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
                      <td className="py-1.5 pr-2 text-right tabular-nums">
                        <button onClick={() => void openDrill("revenue", r.name)} title={t("stats.drillRevenueHint")} className="text-indigo-700 hover:underline">{ft(r.net)}</button>
                      </td>
                      <td className="py-1.5 pr-2 text-right tabular-nums text-slate-500">
                        <button onClick={() => void openDrill("revenue", r.name)} title={t("stats.drillRevenueHint")} className="hover:underline">{ft(r.gross)}</button>
                      </td>
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
                      <td className="py-1.5 pr-2 text-right tabular-nums">
                        <button onClick={() => r.name && void openDrill("expense", r.name)} title={t("stats.drillExpenseHint")} className="text-rose-700 hover:underline">{ft(r.expense)}</button>
                      </td>
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
                      {/* kattintható gép-sorszám → teljes történet (5404e465) */}
                      <td className="py-1.5 pr-2 font-mono text-xs">
                        <button
                          onClick={() => void openMachine(r.barcode)}
                          className="text-indigo-700 hover:underline"
                        >
                          {r.barcode}
                        </button>
                      </td>
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
      {/* Gép-történet modal (5404e465) */}
      {machineHist && (
        <div
          className="fixed inset-0 z-[60] flex items-start justify-center overflow-y-auto bg-black/40 p-4"
        >
          <div className="relative my-8 w-full max-w-2xl space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => setMachineHist(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">
              ☕ {machineHist.name ?? "?"} <span className="font-mono text-sm text-slate-400">{machineHist.barcode}</span>
            </h2>
            <div>
              <p className="mb-1 text-sm font-semibold text-slate-700">🧾 {t("stats.mhSettlements", { n: machineHist.settlements.length })}</p>
              {machineHist.settlements.length === 0 ? (
                <p className="py-2 text-sm text-slate-400">{t("stats.empty")}</p>
              ) : (
                <div className="max-h-64 overflow-y-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs uppercase text-slate-500">
                        <th className="py-1 pr-2">{t("stats.mhDate")}</th>
                        <th className="py-1 pr-2">{t("stats.mhPartner")}</th>
                        <th className="py-1 pr-2 text-right">{t("stats.mhCounter")}</th>
                        <th className="py-1 pr-2 text-right">{t("stats.portions")}</th>
                        <th className="py-1 text-right">{t("stats.net")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {machineHist.settlements.map((s, i) => (
                        <tr key={i} className="border-t border-slate-100">
                          <td className="whitespace-nowrap py-1 pr-2 text-xs">{new Date(s.date).toLocaleDateString("hu-HU")}</td>
                          <td className="py-1 pr-2">{s.partner ?? "—"}</td>
                          <td className="py-1 pr-2 text-right tabular-nums text-xs">{s.prev_counter.toLocaleString("hu-HU")} → {s.new_counter.toLocaleString("hu-HU")}</td>
                          <td className="py-1 pr-2 text-right tabular-nums">{Math.round(s.portions).toLocaleString("hu-HU")}</td>
                          <td className="py-1 text-right tabular-nums">{ft(s.net)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
            <div>
              <p className="mb-1 text-sm font-semibold text-slate-700">🔧 {t("stats.mhService", { n: machineHist.service.length })}</p>
              {machineHist.service.length === 0 ? (
                <p className="py-2 text-sm text-slate-400">{t("stats.mhNoService")}</p>
              ) : (
                <div className="max-h-48 space-y-1 overflow-y-auto text-sm">
                  {machineHist.service.map((tk) => (
                    <div key={tk.ticket_no} className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-1">
                      <span className="font-mono text-xs text-slate-400">{tk.ticket_no}</span>
                      <span>{tk.title}</span>
                      {tk.partner && <span className="text-xs text-slate-400">@ {tk.partner}</span>}
                      <span className="ml-auto whitespace-nowrap text-xs text-slate-400">
                        {new Date(tk.created_at).toLocaleDateString("hu-HU")} · {tk.status}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="flex justify-end">
              <button onClick={() => setMachineHist(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">
                {t("common.close")}
              </button>
            </div>
          </div>
        </div>
      )}
      {/* Összeg-részletező modal (84452fba) */}
      {drill && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4"
        >
          <div className="relative max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-white p-5 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => setDrill(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <div className="mb-3 flex items-center justify-between gap-3">
              <h2 className="font-semibold">
                {drill.kind === "revenue" ? "💰" : "💸"} {drill.name} —{" "}
                {drill.kind === "revenue" ? t("stats.drillRevenueTitle") : t("stats.drillExpenseTitle")}
              </h2>
              <button onClick={() => setDrill(null)} className="rounded px-2 py-1 text-lg leading-none text-slate-400 hover:text-slate-700">✕</button>
            </div>
            {drill.rows.length === 0 ? (
              <p className="py-6 text-center text-sm text-slate-400">{t("stats.empty")}</p>
            ) : drill.kind === "revenue" ? (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-slate-500">
                    <th className="py-1.5 pr-2">{t("cons.debtDate")}</th>
                    <th className="py-1.5 pr-2">{t("cons.partner")}</th>
                    <th className="py-1.5 pr-2 text-right">{t("stats.net")}</th>
                    <th className="py-1.5 pr-2 text-right">{t("stats.gross")}</th>
                    <th className="py-1.5">{t("cons.payment")}</th>
                  </tr>
                </thead>
                <tbody>
                  {(drill.rows as { id: string; created_at: string; partner_name: string; total_net: number; total_gross: number; payment_method: string; invoiced: boolean }[]).map((r) => (
                    <tr key={r.id} className="border-t border-slate-100">
                      <td className="py-1.5 pr-2 whitespace-nowrap text-slate-500">{new Date(r.created_at).toLocaleDateString("hu-HU")}</td>
                      <td className="py-1.5 pr-2 font-medium">{r.partner_name}</td>
                      <td className="py-1.5 pr-2 text-right tabular-nums">{ft(r.total_net)}</td>
                      <td className="py-1.5 pr-2 text-right tabular-nums text-slate-500">{ft(r.total_gross)}</td>
                      <td className="py-1.5 text-xs text-slate-500">
                        {t(`cons.payments.${r.payment_method}`)}
                        {r.invoiced && <span className="ml-1 rounded bg-emerald-100 px-1 text-emerald-800">✓</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-slate-500">
                    <th className="py-1.5 pr-2">{t("cash.date")}</th>
                    <th className="py-1.5 pr-2">{t("cash.type")}</th>
                    <th className="py-1.5 pr-2 text-right">{t("cash.amount")}</th>
                    <th className="py-1.5">{t("cash.details")}</th>
                  </tr>
                </thead>
                <tbody>
                  {(drill.rows as { expense_date: string; amount_gross: number; entry_type: string; note: string | null; supplier: string | null; receipt_no: string | null }[]).map((r, i) => (
                    <tr key={i} className="border-t border-slate-100">
                      <td className="py-1.5 pr-2 whitespace-nowrap text-slate-500">{r.expense_date}</td>
                      <td className="py-1.5 pr-2 text-xs">{t(`cash.types.${r.entry_type}`)}</td>
                      <td className="py-1.5 pr-2 text-right tabular-nums">{ft(r.amount_gross)}</td>
                      <td className="py-1.5 text-xs text-slate-500">
                        {[r.note, r.supplier, r.receipt_no && `#${r.receipt_no}`].filter(Boolean).join(" · ")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </AppShell>
  );
}
