"use client";

/** Jelenlét: időszaki bejegyzések, kézi felvétel/javítás, bérexport letöltés. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, downloadFile, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";
import type { EmployeeOut, EntryOut } from "@/lib/types";

function isoDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function monthBounds(): { from: string; to: string } {
  const now = new Date();
  return {
    from: isoDate(new Date(now.getFullYear(), now.getMonth(), 1)),
    to: isoDate(new Date(now.getFullYear(), now.getMonth() + 1, 0)),
  };
}

function mondayOf(d: Date): Date {
  const copy = new Date(d);
  copy.setDate(copy.getDate() - ((copy.getDay() + 6) % 7));
  copy.setHours(0, 0, 0, 0);
  return copy;
}

function addDays(d: Date, n: number): Date {
  const copy = new Date(d);
  copy.setDate(copy.getDate() + n);
  return copy;
}

export default function JelenletPage() {
  const [view, setView] = useState<"list" | "timecard" | "punches">("list");
  // Blokkolás-napló: melyik nap eseményeit mutatjuk (alapból ma)
  const [punchDay, setPunchDay] = useState<string>(() => isoDate(new Date()));
  const [{ from, to }, setRange] = useState(monthBounds());
  const [weekStart, setWeekStart] = useState<Date>(() => mondayOf(new Date()));
  const [entries, setEntries] = useState<EntryOut[]>([]);
  const [employees, setEmployees] = useState<EmployeeOut[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ employee_id: "", date: "", in_time: "08:00", out_time: "16:30", break_minutes: 30, note: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { t, lang } = useT();
  const { toast } = useUI();

  const fmt = (dt: string) =>
    new Date(dt).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", {
      dateStyle: "short",
      timeStyle: "short",
    });
  const fmtTime = (dt: string) =>
    new Date(dt).toLocaleTimeString(lang === "hu" ? "hu-HU" : "en-GB", {
      hour: "2-digit",
      minute: "2-digit",
    });

  // Az aktív nézet időszaka: lista = választott tartomány; timecard = a hét;
  // blokkolás-napló = egyetlen nap.
  const effFrom = view === "list" ? from : view === "punches" ? punchDay : isoDate(weekStart);
  const effTo = view === "list" ? to : view === "punches" ? punchDay : isoDate(addDays(weekStart, 6));

  const load = useCallback(() => {
    api
      .get<EntryOut[]>(`/api/time-entries?date_from=${effFrom}&date_to=${effTo}`)
      .then(setEntries)
      .catch(() => {});
  }, [effFrom, effTo]);

  useEffect(() => {
    api.get<EmployeeOut[]>("/api/employees").then(setEmployees).catch(() => {});
  }, []);
  useEffect(load, [load]);

  const weekDays = Array.from({ length: 7 }, (_, i) => addDays(weekStart, i));

  /** Timecard rács: dolgozó × nap → bejegyzések (helyi nap szerint). */
  const entriesByCell = new Map<string, EntryOut[]>();
  for (const entry of entries) {
    const day = isoDate(new Date(entry.clock_in));
    const key = `${entry.employee_id}|${day}`;
    entriesByCell.set(key, [...(entriesByCell.get(key) ?? []), entry]);
  }
  const dayMinutes = (empId: string, day: string) =>
    (entriesByCell.get(`${empId}|${day}`) ?? []).reduce(
      (sum, e) => sum + (e.worked_minutes ?? 0),
      0
    );

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/time-entries", {
        employee_id: form.employee_id,
        clock_in: `${form.date}T${form.in_time}:00Z`,
        clock_out: `${form.date}T${form.out_time}:00Z`,
        break_minutes: form.break_minutes,
        note: form.note || null,
      });
      setShowForm(false);
      load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function exportPayroll(format: "csv" | "xlsx") {
    try {
      await downloadFile(
        `/api/payroll/export?period_start=${effFrom}&period_end=${effTo}&format=${format}`,
        `berexport_${effFrom}_${effTo}.${format}`
      );
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  const totalHours = entries.reduce((sum, e) => sum + (e.worked_minutes ?? 0), 0) / 60;

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("att.title")}</h1>
        <div className="flex overflow-hidden rounded-lg border border-slate-300 text-sm">
          {(["list", "timecard", "punches"] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => setView(mode)}
              className={`px-3 py-1.5 ${
                view === mode ? "bg-indigo-600 text-white" : "bg-white text-slate-600 hover:bg-slate-100"
              }`}
            >
              {mode === "list" ? t("att.viewList") : mode === "timecard" ? t("att.viewTimecard") : t("att.viewPunches")}
            </button>
          ))}
        </div>
        {view === "list" ? (
          <>
            <input type="date" value={from} onChange={(e) => setRange({ from: e.target.value, to })} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm" />
            <span className="text-slate-400">→</span>
            <input type="date" value={to} onChange={(e) => setRange({ from, to: e.target.value })} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm" />
          </>
        ) : view === "punches" ? (
          <div className="flex items-center gap-1 rounded-lg border border-slate-300 bg-white">
            <button onClick={() => setPunchDay(isoDate(addDays(new Date(punchDay), -1)))} className="px-3 py-1.5 hover:bg-slate-100">←</button>
            <input type="date" value={punchDay} onChange={(e) => setPunchDay(e.target.value)} className="border-0 bg-transparent px-1 py-1.5 text-sm font-medium" />
            <button onClick={() => setPunchDay(isoDate(addDays(new Date(punchDay), 1)))} className="px-3 py-1.5 hover:bg-slate-100">→</button>
          </div>
        ) : (
          <div className="flex items-center gap-1 rounded-lg border border-slate-300 bg-white">
            <button onClick={() => setWeekStart(addDays(weekStart, -7))} className="px-3 py-1.5 hover:bg-slate-100">←</button>
            <span className="px-2 text-sm font-medium">
              {isoDate(weekStart)} – {isoDate(addDays(weekStart, 6))}
            </span>
            <button onClick={() => setWeekStart(addDays(weekStart, 7))} className="px-3 py-1.5 hover:bg-slate-100">→</button>
          </div>
        )}
        <div className="ml-auto flex gap-2">
          <button onClick={() => setShowForm(true)} className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm hover:bg-slate-100">
            {t("att.manualEntry")}
          </button>
          <button onClick={() => exportPayroll("csv")} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
            {t("att.exportCsv")}
          </button>
          <button onClick={() => exportPayroll("xlsx")} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
            {t("att.exportXlsx")}
          </button>
        </div>
      </div>

      <p className="mb-3 text-sm text-slate-500">
        {t("att.totalWorked")}{" "}
        <span className="font-semibold">{t("att.totalHours", { hours: totalHours.toFixed(1) })}</span>
      </p>

      {view === "punches" && (
        <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
                <th className="px-4 py-2">{t("att.punchTime")}</th>
                <th className="px-4 py-2">{t("att.employee")}</th>
                <th className="px-4 py-2">{t("att.punchEvent")}</th>
                <th className="px-4 py-2">{t("att.punchSource")}</th>
                <th className="px-4 py-2">{t("att.note")}</th>
              </tr>
            </thead>
            <tbody>
              {entries
                .flatMap((e) => {
                  const events: { time: string; type: "in" | "out"; entry: EntryOut }[] = [
                    { time: e.clock_in, type: "in", entry: e },
                  ];
                  if (e.clock_out) events.push({ time: e.clock_out, type: "out", entry: e });
                  return events;
                })
                .sort((a, b) => (a.time < b.time ? 1 : -1))
                .map((ev, i) => (
                  <tr key={`${ev.entry.id}-${ev.type}-${i}`} className="border-b border-slate-100 last:border-0">
                    <td className="px-4 py-2 font-mono font-semibold tabular-nums">{fmtTime(ev.time)}</td>
                    <td className="px-4 py-2 font-medium">{ev.entry.employee_name ?? "—"}</td>
                    <td className="px-4 py-2">
                      {ev.type === "in" ? (
                        <span className="rounded bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-800">▶ {t("att.punchIn")}</span>
                      ) : (
                        <span className="rounded bg-rose-100 px-2 py-0.5 text-xs font-semibold text-rose-700">■ {t("att.punchOut")}</span>
                      )}
                      {ev.type === "in" && !ev.entry.clock_out && (
                        <span className="ml-2 rounded bg-emerald-50 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700">🟢 {t("att.punchStillIn")}</span>
                      )}
                    </td>
                    <td className="px-4 py-2">
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">
                        {t(`att.sources.${ev.entry.source}`) !== `att.sources.${ev.entry.source}` ? t(`att.sources.${ev.entry.source}`) : ev.entry.source}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-xs text-slate-500">{ev.entry.note ?? "—"}</td>
                  </tr>
                ))}
              {entries.length === 0 && (
                <tr><td colSpan={5} className="px-4 py-10 text-center text-slate-400">{t("att.punchEmpty")}</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {view === "timecard" && (
        <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
                <th className="w-44 px-3 py-2">{t("att.employee")}</th>
                {weekDays.map((d, i) => (
                  <th key={i} className="px-2 py-2 text-center">
                    {t(`sched.days.${i}`)}
                    <div className="font-normal normal-case text-slate-400">{isoDate(d).slice(5)}</div>
                  </th>
                ))}
                <th className="px-3 py-2 text-right">{t("att.weekTotal")}</th>
              </tr>
            </thead>
            <tbody>
              {employees
                .filter((e) => e.status === "active")
                .map((emp) => {
                  const weekTotal = weekDays.reduce(
                    (sum, d) => sum + dayMinutes(emp.id, isoDate(d)),
                    0
                  );
                  return (
                    <tr key={emp.id} className="border-b border-slate-100 last:border-0">
                      <td className="px-3 py-2 font-medium">
                        {emp.last_name} {emp.first_name}
                        <div className="font-mono text-xs font-normal text-indigo-600">
                          {emp.employee_code ?? ""}
                        </div>
                      </td>
                      {weekDays.map((d) => {
                        const day = isoDate(d);
                        const cellEntries = entriesByCell.get(`${emp.id}|${day}`) ?? [];
                        const minutes = dayMinutes(emp.id, day);
                        return (
                          <td key={day} className="border-l border-slate-100 px-1 py-1 text-center align-top">
                            {cellEntries.length === 0 ? (
                              <span className="text-slate-300">{t("att.noEntries")}</span>
                            ) : (
                              <>
                                {cellEntries.map((entry) => (
                                  <div key={entry.id} className="text-xs text-slate-600">
                                    {fmtTime(entry.clock_in)}–
                                    {entry.clock_out ? (
                                      fmtTime(entry.clock_out)
                                    ) : (
                                      <span className="font-medium text-emerald-600">{t("att.open")}</span>
                                    )}
                                  </div>
                                ))}
                                {minutes > 0 && (
                                  <div className="mt-0.5 text-xs font-semibold text-slate-800">
                                    {t("att.dayTotal", { hours: (minutes / 60).toFixed(1) })}
                                  </div>
                                )}
                              </>
                            )}
                          </td>
                        );
                      })}
                      <td className="px-3 py-2 text-right font-semibold">
                        {(weekTotal / 60).toFixed(1)} h
                      </td>
                    </tr>
                  );
                })}
              {employees.filter((e) => e.status === "active").length === 0 && (
                <tr>
                  <td colSpan={9} className="px-4 py-10 text-center text-slate-400">{t("att.empty")}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {view === "list" && (
      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
              <th className="px-4 py-3">{t("att.employee")}</th>
              <th className="px-4 py-3">{t("att.clockIn")}</th>
              <th className="px-4 py-3">{t("att.clockOut")}</th>
              <th className="px-4 py-3">{t("att.break")}</th>
              <th className="px-4 py-3">{t("att.worked")}</th>
              <th className="px-4 py-3">{t("att.source")}</th>
              <th className="px-4 py-3">{t("att.note")}</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.id} className="border-b border-slate-100 last:border-0">
                <td className="px-4 py-3 font-medium">{entry.employee_name ?? "—"}</td>
                <td className="px-4 py-3">{fmt(entry.clock_in)}</td>
                <td className="px-4 py-3">
                  {entry.clock_out ? fmt(entry.clock_out) : <span className="text-emerald-600">{t("att.open")}</span>}
                </td>
                <td className="px-4 py-3">{entry.break_minutes} p</td>
                <td className="px-4 py-3 font-medium">
                  {entry.worked_minutes !== null ? `${(entry.worked_minutes / 60).toFixed(1)} h` : "—"}
                </td>
                <td className="px-4 py-3">
                  <span className={`rounded px-2 py-0.5 text-xs ${entry.source === "self" ? "bg-sky-100 text-sky-800" : "bg-slate-100 text-slate-600"}`}>
                    {entry.source === "self" ? t("att.self") : t("att.manual")}
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-500">{entry.note ?? "—"}</td>
              </tr>
            ))}
            {entries.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-slate-400">{t("att.empty")}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      )}

      {showForm && (
        <div onMouseDown={(e) => { if (e.target === e.currentTarget) setShowForm(false); }} className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={submit} className="w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{t("att.manualTitle")}</h2>
            <p className="text-xs text-slate-500">{t("att.manualHint")}</p>
            <label className="block text-sm">
              {t("att.employee")}
              <select required value={form.employee_id} onChange={(e) => setForm({ ...form, employee_id: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2">
                <option value="">{t("leave.choose")}</option>
                {employees.filter((e) => e.status === "active").map((emp) => (
                  <option key={emp.id} value={emp.id}>{emp.last_name} {emp.first_name}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              {t("att.day")}
              <input required type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <label className="block text-sm">
                {t("att.in")}
                <input required type="time" value={form.in_time} onChange={(e) => setForm({ ...form, in_time: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-2" />
              </label>
              <label className="block text-sm">
                {t("att.out")}
                <input required type="time" value={form.out_time} onChange={(e) => setForm({ ...form, out_time: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-2" />
              </label>
              <label className="block text-sm">
                {t("att.breakMin")}
                <input type="number" min={0} max={240} value={form.break_minutes} onChange={(e) => setForm({ ...form, break_minutes: Number(e.target.value) })} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-2" />
              </label>
            </div>
            <label className="block text-sm">
              {t("att.note")}
              <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={() => setShowForm(false)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{t("common.save")}</button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
