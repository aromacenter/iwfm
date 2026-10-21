"use client";

/** Munkaidő-mérés (worktime modul, 1d553d2e C fázis) — vezetői statisztika:
 * a munkalapra írt órák vs. a timerrel mért aktív idő (szünetek nélkül),
 * szerelőnként és munkalaponként, nap / hét / hónap / szabad időszak. A
 * szerelő a számokat nem látja, csak azt, hogy a mérés fut. */

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface Segment { kind: "work" | "travel" | "wait" | "pause"; start: string; end: string | null }
interface EmployeeStat { employee_id: string | null; name: string; worksheets: number; written_hours: number; active_seconds: number; pause_seconds: number; ratio: number | null; work_seconds: number; travel_seconds: number; wait_seconds: number; correction_seconds: number }
interface Correction { timer_id: string; task_id: string; serial: string; title: string; name: string; seconds: number; note: string | null; status: string; requested_at: string | null; active_seconds: number; written_hours: number | null }
interface Row {
  worksheet_id: string; task_id: string; serial: string; title: string; employee_id: string | null; name: string; state: string;
  started_at: string | null; ended_at: string | null; written_hours: number | null; active_seconds: number; pause_seconds: number; pause_count: number; segments: Segment[];
}
interface Stats { date_from: string; date_to: string; worksheets: number; written_hours: number; active_seconds: number; pause_seconds: number; ratio: number | null; by_employee: EmployeeStat[]; rows: Row[] }

type Period = "day" | "week" | "month" | "range";

function iso(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function presetRange(p: Period): [string, string] {
  const now = new Date();
  if (p === "day") return [iso(now), iso(now)];
  if (p === "week") {
    const d = new Date(now); const dow = (d.getDay() + 6) % 7; d.setDate(d.getDate() - dow);
    const e = new Date(d); e.setDate(d.getDate() + 6);
    return [iso(d), iso(e)];
  }
  const d = new Date(now.getFullYear(), now.getMonth(), 1); const e = new Date(now.getFullYear(), now.getMonth() + 1, 0);
  return [iso(d), iso(e)];
}

export default function MunkaidoPage() {
  const { t, lang } = useT();
  const { toast } = useUI();
  const loc = lang === "hu" ? "hu-HU" : "en-GB";
  const [period, setPeriod] = useState<Period>("month");
  const [[from, to], setRange] = useState<[string, string]>(() => presetRange("month"));
  const [employee, setEmployee] = useState("");
  const [employees, setEmployees] = useState<EmployeeStat[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [now, setNow] = useState(0);  // a betöltés pillanata (a futó timer idővonalához)
  const [busy, setBusy] = useState(false);
  const [corrections, setCorrections] = useState<Correction[]>([]);
  const loadCorrections = useCallback(() => { api.get<Correction[]>("/api/worktime/corrections/list?status=pending").then(setCorrections).catch(() => setCorrections([])); }, []);
  useEffect(() => { loadCorrections(); }, [loadCorrections]);
  async function decide(c: Correction, d: "approve" | "reject") {
    try { await api.post(`/api/worktime/corrections/${c.timer_id}/${d}`, {}); toast(t("worktime.corrDecided"), "success"); loadCorrections(); load(); }
    catch (e) { toast(errorMessage(e), "error"); }
  }

  const dur = (sec: number) => {
    const h = Math.floor(sec / 3600); const m = Math.round((sec % 3600) / 60);
    return h > 0 ? `${h} ó ${String(m).padStart(2, "0")} p` : `${m} p`;
  };
  const hrs = (h: number) => `${h.toLocaleString(loc, { maximumFractionDigits: 1 })} ó`;
  const pct = (r: number | null) => (r == null ? "—" : `${Math.round(r * 100)} %`);
  const pill = (r: number | null) => {
    if (r == null) return "bg-slate-100 text-slate-500";
    return r >= 0.85 ? "bg-emerald-100 text-emerald-800" : r >= 0.7 ? "bg-amber-100 text-amber-800" : "bg-rose-100 text-rose-800";
  };
  const ts = (s: string | null) => (s ? new Date(s).toLocaleString(loc, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
  const tm = (s: string | null) => (s ? new Date(s).toLocaleTimeString(loc, { hour: "2-digit", minute: "2-digit" }) : "…");

  const load = useCallback(() => {
    setBusy(true);
    const q = `from=${from}&to=${to}${employee ? `&employee_id=${employee}` : ""}`;
    api.get<Stats>(`/api/worktime/stats/summary?${q}`)
      .then((s) => { setStats(s); setNow(Date.now()); if (!employee) setEmployees(s.by_employee); })
      .catch((e) => toast(errorMessage(e), "error"))
      .finally(() => setBusy(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from, to, employee]);
  useEffect(() => { load(); }, [load]);

  const choosePeriod = (p: Period) => { setPeriod(p); if (p !== "range") setRange(presetRange(p)); };
  const avgPauseMin = useMemo(() => (stats && stats.worksheets > 0 ? Math.round(stats.pause_seconds / 60 / stats.worksheets * 10) / 10 : 0), [stats]);

  // Idővonal-sáv: a szegmensek aránya a munkalap első kezdésétől az utolsó zárásig (vagy mostanáig)
  const timeline = (r: Row) => {
    if (!r.started_at || r.segments.length === 0) return null;
    const t0 = new Date(r.started_at).getTime();
    const t1 = r.ended_at ? new Date(r.ended_at).getTime() : now;
    const total = Math.max(1, t1 - t0);
    return (
      <div className="flex h-5 w-44 overflow-hidden rounded-md bg-slate-200" title={`${tm(r.started_at)} → ${tm(r.ended_at)}`}>
        {r.segments.map((s, i) => {
          const a = new Date(s.start).getTime(); const b = s.end ? new Date(s.end).getTime() : t1;
          const w = Math.max(1, ((b - a) / total) * 100);
          return <div key={i} style={{ width: `${w}%` }} className={s.kind === "pause" ? "bg-amber-400" : s.kind === "travel" ? "bg-sky-400" : s.kind === "wait" ? "bg-slate-400" : "bg-indigo-600"} />;
        })}
      </div>
    );
  };

  const seg = (p: Period, label: string) => (
    <button key={p} onClick={() => choosePeriod(p)} className={`rounded-lg px-3 py-1.5 text-sm ${period === p ? "bg-indigo-600 text-white" : "text-indigo-800 hover:bg-indigo-100"}`}>{label}</button>
  );
  const kpi = (label: string, value: string, sub?: React.ReactNode, cls = "") => (
    <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-0.5 text-2xl font-bold tabular-nums ${cls}`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500">{sub}</div>}
    </div>
  );

  return (
    <AppShell>
      <div className="mb-4">
        <h1 className="text-xl font-semibold">⏱ {t("worktime.title")} <span className="ml-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-normal text-slate-600">admin / manager</span></h1>
        <p className="text-sm text-slate-500">{t("worktime.subtitle")}</p>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-xl bg-indigo-50 p-1">{seg("day", t("worktime.day"))}{seg("week", t("worktime.week"))}{seg("month", t("worktime.month"))}{seg("range", t("worktime.range"))}</div>
        <input type="date" value={from} onChange={(e) => { setPeriod("range"); setRange([e.target.value, to]); }} className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm" />
        <span className="text-slate-400">→</span>
        <input type="date" value={to} onChange={(e) => { setPeriod("range"); setRange([from, e.target.value]); }} className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm" />
        <select value={employee} onChange={(e) => setEmployee(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm">
          <option value="">👤 {t("worktime.allEmployees")} ({employees.length})</option>
          {employees.filter((e) => e.employee_id).map((e) => <option key={e.employee_id!} value={e.employee_id!}>{e.name}</option>)}
        </select>
        {busy && <span className="text-xs text-slate-400">…</span>}
        <a href={`/api/worktime/stats/payroll.csv?from=${from}&to=${to}`} className="ml-auto rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-100">{t("worktime.payrollCsv")}</a>
      </div>

      {corrections.length > 0 && (
        <section className="mb-4 rounded-2xl border border-amber-200 bg-amber-50 p-4">
          <h2 className="mb-2 font-semibold text-amber-900">{t("worktime.corrections")} ({corrections.length})</h2>
          <div className="space-y-2">
            {corrections.map((c) => (
              <div key={c.timer_id} className="flex flex-wrap items-center gap-3 rounded-xl bg-white px-3 py-2 text-sm">
                <span className="font-mono text-xs">{c.serial}</span><span className="font-medium">{c.name}</span><span className="text-slate-500">{c.title}</span>
                <span className={`font-semibold tabular-nums ${c.seconds >= 0 ? "text-emerald-700" : "text-rose-700"}`}>{c.seconds >= 0 ? "+" : ""}{Math.round(c.seconds / 60)} p</span>
                <span className="text-xs text-slate-500">„{c.note}” · {t("worktime.col.active")}: {dur(c.active_seconds)}{c.written_hours != null && ` · ${t("worktime.col.written")}: ${hrs(c.written_hours)}`}</span>
                <span className="ml-auto flex gap-1.5"><button onClick={() => void decide(c, "approve")} className="rounded-lg bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-emerald-700">{t("worktime.approve")}</button><button onClick={() => void decide(c, "reject")} className="rounded-lg border border-rose-300 px-2.5 py-1 text-xs text-rose-700 hover:bg-rose-50">{t("worktime.reject")}</button></span>
              </div>
            ))}
          </div>
        </section>
      )}

      {stats && (
        <>
          <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {kpi(t("worktime.kpi.written"), hrs(stats.written_hours), t("worktime.kpi.worksheets", { n: stats.worksheets }))}
            {kpi(t("worktime.kpi.active"), dur(stats.active_seconds), t("worktime.kpi.noBreaks"))}
            {kpi(t("worktime.kpi.ratio"), pct(stats.ratio), (
              <div className="mt-1 h-2 overflow-hidden rounded bg-slate-200"><div style={{ width: `${Math.min(100, Math.round((stats.ratio ?? 0) * 100))}%` }} className="h-full bg-indigo-600" /></div>
            ), stats.ratio != null && stats.ratio < 0.7 ? "text-rose-700" : stats.ratio != null && stats.ratio < 0.85 ? "text-amber-700" : "")}
            {kpi(t("worktime.kpi.pauses"), dur(stats.pause_seconds), t("worktime.kpi.avgPause", { m: avgPauseMin }))}
          </div>

          <div className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
            <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <h2 className="mb-2 font-semibold">{t("worktime.byEmployee")}</h2>
              {stats.by_employee.length === 0 ? <p className="py-6 text-center text-sm text-slate-400">{t("worktime.empty")}</p> : (
                <table className="w-full text-sm">
                  <thead><tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                    <th className="px-2 py-1.5">{t("worktime.col.employee")}</th><th className="px-2 py-1.5 text-right">{t("worktime.col.worksheets")}</th><th className="px-2 py-1.5 text-right">{t("worktime.col.written")}</th><th className="px-2 py-1.5 text-right">{t("worktime.col.active")}</th><th className="px-2 py-1.5 text-right">{t("worktime.col.travel")}</th><th className="px-2 py-1.5 text-right">{t("worktime.col.ratio")}</th><th className="px-2 py-1.5 text-right">{t("worktime.col.pause")}</th>
                  </tr></thead>
                  <tbody>
                    {stats.by_employee.map((e) => (
                      <tr key={e.employee_id ?? e.name} className="border-b border-slate-100 hover:bg-slate-50">
                        <td className="px-2 py-1.5 font-medium">{e.name}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{e.worksheets}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{hrs(e.written_hours)}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{dur(e.active_seconds)}{e.correction_seconds !== 0 && <span className="ml-1 text-[10px] text-slate-400">({t("worktime.corrected")} {e.correction_seconds >= 0 ? "+" : ""}{Math.round(e.correction_seconds / 60)} p)</span>}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{dur(e.travel_seconds)}{e.wait_seconds > 0 && <span className="block text-[10px]">⏳ {dur(e.wait_seconds)}</span>}</td>
                        <td className="px-2 py-1.5 text-right"><span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${pill(e.ratio)}`}>{pct(e.ratio)}</span></td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{dur(e.pause_seconds)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <h2 className="mb-2 font-semibold">{t("worktime.worksheets")}{employee && ` — ${employees.find((e) => e.employee_id === employee)?.name ?? ""}`}</h2>
              {stats.rows.length === 0 ? <p className="py-6 text-center text-sm text-slate-400">{t("worktime.empty")}</p> : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead><tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                      <th className="px-2 py-1.5">{t("worktime.col.worksheet")}</th><th className="px-2 py-1.5">{t("worktime.col.startEnd")}</th><th className="px-2 py-1.5 text-right">{t("worktime.col.written")}</th><th className="px-2 py-1.5 text-right">{t("worktime.col.active")}</th><th className="px-2 py-1.5">{t("worktime.col.timeline")}</th>
                    </tr></thead>
                    <tbody>
                      {stats.rows.map((r) => (
                        <tr key={r.worksheet_id} className="border-b border-slate-100 hover:bg-slate-50">
                          <td className="px-2 py-1.5"><span className="font-mono text-xs">{r.serial}</span><span className="block text-[11px] text-slate-500">{r.title}{!employee && ` · ${r.name}`}</span></td>
                          <td className="px-2 py-1.5 whitespace-nowrap font-mono text-xs text-slate-600">{ts(r.started_at)} → {r.ended_at ? tm(r.ended_at) : <span className="text-emerald-700">{t("worktime.legend.running")}</span>}{r.pause_count > 0 && <span className="ml-1 text-slate-400">· {r.pause_count}× ☕</span>}</td>
                          <td className="px-2 py-1.5 text-right tabular-nums">{r.written_hours != null ? hrs(r.written_hours) : t("worktime.noWritten")}</td>
                          <td className="px-2 py-1.5 text-right tabular-nums">{dur(r.active_seconds)}</td>
                          <td className="px-2 py-1.5">{timeline(r)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="mt-2 flex flex-wrap gap-2 text-[11px] text-slate-500">
                <span className="rounded-full bg-indigo-600 px-2 py-0.5 text-white">{t("worktime.legend.work")}</span>
                <span className="rounded-full bg-amber-400 px-2 py-0.5 text-black">{t("worktime.legend.pause")}</span>
                <span className="rounded-full bg-sky-400 px-2 py-0.5 text-black">{t("worktime.legend.travel")}</span>
                <span className="rounded-full bg-slate-400 px-2 py-0.5 text-white">{t("worktime.legend.wait")}</span>
              </p>
            </section>
          </div>
        </>
      )}
    </AppShell>
  );
}
