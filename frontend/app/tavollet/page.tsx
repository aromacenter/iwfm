"use client";

/** Távollét-kérelmek: lista, felvétel, jóváhagyás/elutasítás. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { EmployeeOut, TimeOffOut } from "@/lib/types";

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-amber-100 text-amber-800",
  approved: "bg-emerald-100 text-emerald-800",
  rejected: "bg-red-100 text-red-700",
  cancelled: "bg-slate-100 text-slate-500",
};

const TYPES = ["annual", "sick", "unpaid", "other"] as const;
const STATUSES = ["pending", "approved", "rejected", "cancelled"] as const;

export default function TavolletPage() {
  const [items, setItems] = useState<TimeOffOut[]>([]);
  const [employees, setEmployees] = useState<EmployeeOut[]>([]);
  const [filter, setFilter] = useState<string>("");
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ employee_id: "", type: "annual", start_date: "", end_date: "", reason: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { t } = useT();

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
        <h1 className="text-xl font-bold">{t("leave.title")}</h1>
        <div className="flex gap-2">
          <select value={filter} onChange={(e) => setFilter(e.target.value)} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm">
            <option value="">{t("leave.allStatuses")}</option>
            {STATUSES.map((status) => (
              <option key={status} value={status}>{t(`leave.statuses.${status}`)}</option>
            ))}
          </select>
          <button onClick={() => setShowForm(true)} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
            {t("leave.new")}
          </button>
        </div>
      </div>

      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
              <th className="px-4 py-3">{t("leave.employee")}</th>
              <th className="px-4 py-3">{t("leave.type")}</th>
              <th className="px-4 py-3">{t("leave.period")}</th>
              <th className="px-4 py-3">{t("leave.reason")}</th>
              <th className="px-4 py-3">{t("leave.status")}</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} className="border-b border-slate-100 last:border-0">
                <td className="px-4 py-3 font-medium">{item.employee_name ?? "—"}</td>
                <td className="px-4 py-3">{t(`leave.types.${item.type}`)}</td>
                <td className="px-4 py-3">{item.start_date} → {item.end_date}</td>
                <td className="px-4 py-3 text-slate-500">{item.reason ?? "—"}</td>
                <td className="px-4 py-3">
                  <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[item.status]}`}>
                    {t(`leave.statuses.${item.status}`)}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  {item.status === "pending" && (
                    <div className="flex justify-end gap-2">
                      <button onClick={() => decide(item.id, "approved")} className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-700">
                        {t("dash.approve")}
                      </button>
                      <button onClick={() => decide(item.id, "rejected")} className="rounded border border-red-300 px-3 py-1 text-xs text-red-600 hover:bg-red-50">
                        {t("dash.reject")}
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-10 text-center text-slate-400">{t("leave.empty")}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {showForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={submit} className="w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{t("leave.newTitle")}</h2>
            <label className="block text-sm">
              {t("leave.employee")}
              <select required value={form.employee_id} onChange={(e) => setForm({ ...form, employee_id: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2">
                <option value="">{t("leave.choose")}</option>
                {employees.filter((e) => e.status === "active").map((emp) => (
                  <option key={emp.id} value={emp.id}>{emp.last_name} {emp.first_name}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              {t("leave.type")}
              <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2">
                {TYPES.map((type) => (
                  <option key={type} value={type}>{t(`leave.types.${type}`)}</option>
                ))}
              </select>
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-sm">
                {t("leave.from")}
                <input required type="date" value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
              <label className="block text-sm">
                {t("leave.to")}
                <input required type="date" value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
            </div>
            <label className="block text-sm">
              {t("leave.reason")}
              <input value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
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
