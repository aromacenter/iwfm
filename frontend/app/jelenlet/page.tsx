"use client";

/** Jelenlét: időszaki bejegyzések, kézi felvétel/javítás, bérexport letöltés. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, downloadFile, errorMessage } from "@/lib/api";
import type { EmployeeOut, EntryOut } from "@/lib/types";

function monthBounds(): { from: string; to: string } {
  const now = new Date();
  const from = new Date(now.getFullYear(), now.getMonth(), 1);
  const to = new Date(now.getFullYear(), now.getMonth() + 1, 0);
  const iso = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return { from: iso(from), to: iso(to) };
}

function fmt(dt: string): string {
  return new Date(dt).toLocaleString("hu-HU", { dateStyle: "short", timeStyle: "short" });
}

export default function JelenletPage() {
  const [{ from, to }, setRange] = useState(monthBounds());
  const [entries, setEntries] = useState<EntryOut[]>([]);
  const [employees, setEmployees] = useState<EmployeeOut[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ employee_id: "", date: "", in_time: "08:00", out_time: "16:30", break_minutes: 30, note: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api
      .get<EntryOut[]>(`/api/time-entries?date_from=${from}&date_to=${to}`)
      .then(setEntries)
      .catch(() => {});
  }, [from, to]);

  useEffect(() => {
    api.get<EmployeeOut[]>("/api/employees").then(setEmployees).catch(() => {});
  }, []);
  useEffect(load, [load]);

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
        `/api/payroll/export?period_start=${from}&period_end=${to}&format=${format}`,
        `berexport_${from}_${to}.${format}`
      );
    } catch (err) {
      alert(errorMessage(err));
    }
  }

  const totalHours = entries.reduce((sum, e) => sum + (e.worked_minutes ?? 0), 0) / 60;

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">Jelenlét</h1>
        <input type="date" value={from} onChange={(e) => setRange({ from: e.target.value, to })} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm" />
        <span className="text-slate-400">→</span>
        <input type="date" value={to} onChange={(e) => setRange({ from, to: e.target.value })} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm" />
        <div className="ml-auto flex gap-2">
          <button onClick={() => setShowForm(true)} className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm hover:bg-slate-100">
            + Kézi bejegyzés
          </button>
          <button onClick={() => exportPayroll("csv")} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
            Bérexport CSV
          </button>
          <button onClick={() => exportPayroll("xlsx")} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
            Bérexport XLSX
          </button>
        </div>
      </div>

      <p className="mb-3 text-sm text-slate-500">
        Összes ledolgozott idő az időszakban: <span className="font-semibold">{totalHours.toFixed(1)} óra</span>
      </p>

      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
              <th className="px-4 py-3">Dolgozó</th>
              <th className="px-4 py-3">Bejelentkezés</th>
              <th className="px-4 py-3">Kijelentkezés</th>
              <th className="px-4 py-3">Szünet</th>
              <th className="px-4 py-3">Ledolgozott</th>
              <th className="px-4 py-3">Forrás</th>
              <th className="px-4 py-3">Megjegyzés</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id} className="border-b border-slate-100 last:border-0">
                <td className="px-4 py-3 font-medium">{e.employee_name ?? "—"}</td>
                <td className="px-4 py-3">{fmt(e.clock_in)}</td>
                <td className="px-4 py-3">{e.clock_out ? fmt(e.clock_out) : <span className="text-emerald-600">nyitva</span>}</td>
                <td className="px-4 py-3">{e.break_minutes} p</td>
                <td className="px-4 py-3 font-medium">
                  {e.worked_minutes !== null ? `${(e.worked_minutes / 60).toFixed(1)} h` : "—"}
                </td>
                <td className="px-4 py-3">
                  <span className={`rounded px-2 py-0.5 text-xs ${e.source === "self" ? "bg-sky-100 text-sky-800" : "bg-slate-100 text-slate-600"}`}>
                    {e.source === "self" ? "saját" : "kézi"}
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-500">{e.note ?? "—"}</td>
              </tr>
            ))}
            {entries.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-slate-400">Nincs bejegyzés az időszakban.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {showForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={submit} className="w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">Kézi jelenléti bejegyzés</h2>
            <p className="text-xs text-slate-500">Minden kézi bejegyzés auditálásra kerül.</p>
            <label className="block text-sm">
              Dolgozó
              <select required value={form.employee_id} onChange={(e) => setForm({ ...form, employee_id: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2">
                <option value="">Válassz…</option>
                {employees.filter((e) => e.status === "active").map((emp) => (
                  <option key={emp.id} value={emp.id}>{emp.last_name} {emp.first_name}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              Nap
              <input required type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <div className="grid grid-cols-3 gap-3">
              <label className="block text-sm">
                Be
                <input required type="time" value={form.in_time} onChange={(e) => setForm({ ...form, in_time: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-2" />
              </label>
              <label className="block text-sm">
                Ki
                <input required type="time" value={form.out_time} onChange={(e) => setForm({ ...form, out_time: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-2" />
              </label>
              <label className="block text-sm">
                Szünet (p)
                <input type="number" min={0} max={240} value={form.break_minutes} onChange={(e) => setForm({ ...form, break_minutes: Number(e.target.value) })} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-2" />
              </label>
            </div>
            <label className="block text-sm">
              Megjegyzés
              <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={() => setShowForm(false)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">Mégsem</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">Mentés</button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
