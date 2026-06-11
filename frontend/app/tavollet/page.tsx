"use client";

/** Távollét-kérelmek: lista, felvétel, jóváhagyás/elutasítás. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import type { EmployeeOut, TimeOffOut } from "@/lib/types";
import { TIME_OFF_LABELS, TIME_OFF_STATUS_LABELS } from "@/lib/types";

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-amber-100 text-amber-800",
  approved: "bg-emerald-100 text-emerald-800",
  rejected: "bg-red-100 text-red-700",
  cancelled: "bg-slate-100 text-slate-500",
};

export default function TavolletPage() {
  const [items, setItems] = useState<TimeOffOut[]>([]);
  const [employees, setEmployees] = useState<EmployeeOut[]>([]);
  const [filter, setFilter] = useState<string>("");
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ employee_id: "", type: "annual", start_date: "", end_date: "", reason: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api
      .get<TimeOffOut[]>(`/api/time-off${filter ? `?status=${filter}` : ""}`)
      .then(setItems)
      .catch(() => {});
  }, [filter]);

  useEffect(() => {
    api.get<EmployeeOut[]>("/api/employees").then(setEmployees).catch(() => {});
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

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/time-off", { ...form, reason: form.reason || null });
      setShowForm(false);
      setForm({ employee_id: "", type: "annual", start_date: "", end_date: "", reason: "" });
      load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold">Távollét</h1>
        <div className="flex gap-2">
          <select value={filter} onChange={(e) => setFilter(e.target.value)} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm">
            <option value="">Minden állapot</option>
            {Object.entries(TIME_OFF_STATUS_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
          <button onClick={() => setShowForm(true)} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
            + Új kérelem
          </button>
        </div>
      </div>

      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
              <th className="px-4 py-3">Dolgozó</th>
              <th className="px-4 py-3">Típus</th>
              <th className="px-4 py-3">Időszak</th>
              <th className="px-4 py-3">Indok</th>
              <th className="px-4 py-3">Állapot</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {items.map((t) => (
              <tr key={t.id} className="border-b border-slate-100 last:border-0">
                <td className="px-4 py-3 font-medium">{t.employee_name ?? "—"}</td>
                <td className="px-4 py-3">{TIME_OFF_LABELS[t.type]}</td>
                <td className="px-4 py-3">{t.start_date} → {t.end_date}</td>
                <td className="px-4 py-3 text-slate-500">{t.reason ?? "—"}</td>
                <td className="px-4 py-3">
                  <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[t.status]}`}>
                    {TIME_OFF_STATUS_LABELS[t.status]}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  {t.status === "pending" && (
                    <div className="flex justify-end gap-2">
                      <button onClick={() => decide(t.id, "approved")} className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-700">
                        Jóváhagy
                      </button>
                      <button onClick={() => decide(t.id, "rejected")} className="rounded border border-red-300 px-3 py-1 text-xs text-red-600 hover:bg-red-50">
                        Elutasít
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-10 text-center text-slate-400">Nincs kérelem.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {showForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={submit} className="w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">Új távollét</h2>
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
              Típus
              <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2">
                {Object.entries(TIME_OFF_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-sm">
                Kezdete
                <input required type="date" value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
              <label className="block text-sm">
                Vége
                <input required type="date" value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
            </div>
            <label className="block text-sm">
              Indok
              <input value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
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
