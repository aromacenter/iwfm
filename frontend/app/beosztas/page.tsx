"use client";

/** Heti beosztás-rács (Microsoft Shifts-minta): dolgozók × napok,
 * műszak felvétel/szerkesztés, Mt. megfelelőség-panel, közzététel. */

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, ApiError, errorMessage } from "@/lib/api";
import { useT, translate } from "@/lib/i18n";
import type { EmployeeOut, ShiftOut, ViolationOut, WeekOut } from "@/lib/types";

function mondayOf(d: Date): Date {
  const copy = new Date(d);
  copy.setDate(copy.getDate() - ((copy.getDay() + 6) % 7));
  copy.setHours(0, 0, 0, 0);
  return copy;
}

function iso(d: Date): string {
  // Lokális dátum — a toISOString() UTC-re váltana és elcsúsztatná a napot.
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function addDays(d: Date, n: number): Date {
  const copy = new Date(d);
  copy.setDate(copy.getDate() + n);
  return copy;
}

function hhmm(t: string): string {
  return t.slice(0, 5);
}

/** Szabálysértés szövege: kód+paraméterek a nyelvi fájlból; ha nincs param,
 * a szerver (magyar) szövege a tartalék. */
export function violationText(v: ViolationOut): string {
  if (v.params && Object.keys(v.params).length > 0) {
    const localized = translate(`violations.${v.code}`, v.params);
    if (localized !== `violations.${v.code}`) return localized;
  }
  return v.message;
}

interface ShiftForm {
  id: string | null;
  employee_id: string;
  work_date: string;
  start_time: string;
  end_time: string;
  break_minutes: number;
  role_label: string;
  location: string;
  note: string;
}

export default function BeosztasPage() {
  const [weekStart, setWeekStart] = useState<Date>(() => addDays(mondayOf(new Date()), 7));
  const [week, setWeek] = useState<WeekOut | null>(null);
  const [employees, setEmployees] = useState<EmployeeOut[]>([]);
  const [form, setForm] = useState<ShiftForm | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [publishWarnings, setPublishWarnings] = useState<ViolationOut[] | null>(null);
  const { t } = useT();

  const weekIso = iso(weekStart);

  const load = useCallback(() => {
    api.get<WeekOut>(`/api/shifts?week_start=${weekIso}`).then(setWeek).catch(() => {});
  }, [weekIso]);

  useEffect(() => {
    api.get<EmployeeOut[]>("/api/employees").then(setEmployees).catch(() => {});
  }, []);
  useEffect(load, [load]);

  const days = useMemo(
    () => Array.from({ length: 7 }, (_, i) => addDays(weekStart, i)),
    [weekStart]
  );

  const shiftsByCell = useMemo(() => {
    const map = new Map<string, ShiftOut[]>();
    for (const s of week?.shifts ?? []) {
      const key = `${s.employee_id}|${s.work_date}`;
      map.set(key, [...(map.get(key) ?? []), s]);
    }
    return map;
  }, [week]);

  const timeOffByCell = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of week?.time_off ?? []) {
      for (const day of days) {
        const dayIso = iso(day);
        if (item.start_date <= dayIso && dayIso <= item.end_date) {
          map.set(`${item.employee_id}|${dayIso}`, item.type);
        }
      }
    }
    return map;
  }, [week, days]);

  function openCreate(empId: string, dayIso: string) {
    setError(null);
    setForm({
      id: null,
      employee_id: empId,
      work_date: dayIso,
      start_time: "08:00",
      end_time: "16:00",
      break_minutes: 30,
      role_label: "",
      location: "",
      note: "",
    });
  }

  function openEdit(shift: ShiftOut) {
    setError(null);
    setForm({
      id: shift.id,
      employee_id: shift.employee_id,
      work_date: shift.work_date,
      start_time: hhmm(shift.start_time),
      end_time: hhmm(shift.end_time),
      break_minutes: shift.break_minutes,
      role_label: shift.role_label ?? "",
      location: shift.location ?? "",
      note: shift.note ?? "",
    });
  }

  async function saveShift(e: React.FormEvent, force = false) {
    e.preventDefault();
    if (!form) return;
    setBusy(true);
    setError(null);
    const body = {
      work_date: form.work_date,
      start_time: `${form.start_time}:00`,
      end_time: `${form.end_time}:00`,
      break_minutes: form.break_minutes,
      role_label: form.role_label || null,
      location: form.location || null,
      note: form.note || null,
    };
    try {
      if (form.id) {
        await api.patch(`/api/shifts/${form.id}`, { ...body, force });
      } else {
        await api.post("/api/shifts", { ...body, employee_id: form.employee_id });
      }
      setForm(null);
      load();
    } catch (err) {
      if (err instanceof ApiError && err.code === "shift.modify_notice") {
        if (confirm(t("sched.modifyConfirm"))) {
          await saveShift(e, true);
          return;
        }
      }
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function deleteShift(force = false) {
    if (!form?.id) return;
    setBusy(true);
    try {
      await api.delete(`/api/shifts/${form.id}${force ? "?force=true" : ""}`);
      setForm(null);
      load();
    } catch (err) {
      if (err instanceof ApiError && err.code === "shift.modify_notice") {
        if (confirm(t("sched.deleteConfirm"))) {
          await deleteShift(true);
          return;
        }
      }
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function publish(force = false) {
    setBusy(true);
    setPublishWarnings(null);
    try {
      const res = await api.post<{ published: number }>("/api/shifts/publish", {
        week_start: weekIso,
        force,
      });
      alert(t("sched.publishedOk", { count: res.published }));
      load();
    } catch (err) {
      if (err instanceof ApiError && err.code === "publish.needs_confirmation") {
        const detail = err.detail as { violations: ViolationOut[] };
        setPublishWarnings(detail.violations);
      } else if (err instanceof ApiError && err.code === "publish.blocked") {
        const detail = err.detail as { violations: ViolationOut[] };
        alert(
          `${t("sched.publishBlocked")}\n\n` +
            detail.violations.map((v) => `• ${violationText(v)}`).join("\n")
        );
      } else {
        alert(errorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  }

  const employeeName = (id: string) => {
    const emp = employees.find((e) => e.id === id);
    return emp ? `${emp.last_name} ${emp.first_name}` : "?";
  };

  const draftCount = week?.shifts.filter((s) => s.status === "draft").length ?? 0;
  const errors = week?.violations.filter((v) => v.severity === "error") ?? [];
  const activeEmployees = employees.filter((e) => e.status === "active");

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("sched.title")}</h1>
        <div className="flex items-center gap-1 rounded-lg border border-slate-300 bg-white">
          <button onClick={() => setWeekStart(addDays(weekStart, -7))} className="px-3 py-1.5 hover:bg-slate-100">←</button>
          <span className="px-2 text-sm font-medium">
            {weekIso} – {iso(addDays(weekStart, 6))}
          </span>
          <button onClick={() => setWeekStart(addDays(weekStart, 7))} className="px-3 py-1.5 hover:bg-slate-100">→</button>
        </div>
        <button
          onClick={() => setWeekStart(addDays(mondayOf(new Date()), 7))}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-100"
        >
          {t("sched.nextWeekBtn")}
        </button>
        <div className="ml-auto flex items-center gap-2">
          {draftCount > 0 && (
            <span className="text-sm text-slate-500">{t("sched.drafts", { count: draftCount })}</span>
          )}
          <button
            onClick={() => publish(false)}
            disabled={busy || draftCount === 0}
            className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-40"
            title={t("sched.publishHint")}
          >
            {t("sched.publish")}
          </button>
        </div>
      </div>

      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
              <th className="w-44 px-3 py-2">{t("sched.employee")}</th>
              {days.map((d, i) => (
                <th key={i} className="px-2 py-2 text-center">
                  {t(`sched.days.${i}`)}
                  <div className="font-normal normal-case text-slate-400">{iso(d).slice(5)}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {activeEmployees.map((emp) => (
              <tr key={emp.id} className="border-b border-slate-100 last:border-0">
                <td className="px-3 py-2 font-medium">
                  {emp.last_name} {emp.first_name}
                  <div className="text-xs font-normal text-slate-400">{emp.job_title ?? ""}</div>
                </td>
                {days.map((d) => {
                  const dayIso = iso(d);
                  const cellShifts = shiftsByCell.get(`${emp.id}|${dayIso}`) ?? [];
                  const offType = timeOffByCell.get(`${emp.id}|${dayIso}`);
                  return (
                    <td key={dayIso} className="h-16 border-l border-slate-100 px-1 py-1 align-top">
                      {offType && (
                        <div className="mb-1 rounded bg-amber-100 px-1.5 py-0.5 text-center text-xs text-amber-800">
                          {t(`leave.types.${offType}`)}
                        </div>
                      )}
                      {cellShifts.map((s) => (
                        <button
                          key={s.id}
                          onClick={() => openEdit(s)}
                          className={`mb-1 block w-full rounded px-1.5 py-1 text-left text-xs ${
                            s.status === "published"
                              ? "bg-emerald-100 text-emerald-900 hover:bg-emerald-200"
                              : "bg-indigo-100 text-indigo-900 hover:bg-indigo-200"
                          }`}
                          title={s.status === "published" ? t("sched.publishedTitle") : t("sched.draftTitle")}
                        >
                          {hhmm(s.start_time)}–{hhmm(s.end_time)}
                          {s.role_label && <span className="block text-[10px] opacity-70">{s.role_label}</span>}
                        </button>
                      ))}
                      {!offType && (
                        <button
                          onClick={() => openCreate(emp.id, dayIso)}
                          className="block w-full rounded border border-dashed border-slate-200 px-1 py-0.5 text-center text-xs text-slate-300 hover:border-indigo-300 hover:text-indigo-500"
                        >
                          +
                        </button>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
            {activeEmployees.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center text-slate-400">
                  {t("sched.noEmployees")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {(week?.violations.length ?? 0) > 0 && (
        <div className="mt-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="mb-2 font-semibold">
            {t("sched.complianceTitle")}{" "}
            <span className="text-sm font-normal text-slate-500">
              {t("sched.complianceCount", {
                errors: errors.length,
                warnings: (week?.violations.length ?? 0) - errors.length,
              })}
            </span>
          </h2>
          <ul className="space-y-1 text-sm">
            {week!.violations.map((v, i) => (
              <li key={i} className={v.severity === "error" ? "text-red-700" : "text-amber-700"}>
                {v.severity === "error" ? "⛔" : "⚠️"}{" "}
                <span className="font-medium">{v.employee_id ? employeeName(v.employee_id) : ""}</span>{" "}
                — {violationText(v)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {form && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={(e) => saveShift(e)} className="w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">
              {form.id ? t("sched.editShift") : t("sched.newShift")} — {employeeName(form.employee_id)}
            </h2>
            <p className="text-sm text-slate-500">{form.work_date}</p>
            <div className="grid grid-cols-3 gap-3">
              <label className="block text-sm">
                {t("sched.start")}
                <input type="time" required value={form.start_time} onChange={(e) => setForm({ ...form, start_time: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-2" />
              </label>
              <label className="block text-sm">
                {t("sched.end")}
                <input type="time" required value={form.end_time} onChange={(e) => setForm({ ...form, end_time: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-2" />
              </label>
              <label className="block text-sm">
                {t("sched.breakMin")}
                <input type="number" min={0} max={240} value={form.break_minutes} onChange={(e) => setForm({ ...form, break_minutes: Number(e.target.value) })} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-2" />
              </label>
            </div>
            <label className="block text-sm">
              {t("sched.roleLabel")}
              <input value={form.role_label} onChange={(e) => setForm({ ...form, role_label: e.target.value })} placeholder={t("sched.rolePlaceholder")} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              {t("sched.location")}
              <input value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              {t("sched.note")}
              <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <p className="text-xs text-slate-400">{t("sched.overnightHint")}</p>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-between gap-2 pt-2">
              {form.id ? (
                <button type="button" onClick={() => deleteShift()} disabled={busy} className="rounded-lg border border-red-300 px-4 py-2 text-sm text-red-600 hover:bg-red-50">
                  {t("common.delete")}
                </button>
              ) : (
                <span />
              )}
              <div className="flex gap-2">
                <button type="button" onClick={() => setForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">
                  {t("common.cancel")}
                </button>
                <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
                  {t("common.save")}
                </button>
              </div>
            </div>
          </form>
        </div>
      )}

      {publishWarnings && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-lg space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold text-amber-700">{t("sched.publishWarningsTitle")}</h2>
            <ul className="space-y-1 text-sm text-amber-800">
              {publishWarnings.map((v, i) => (
                <li key={i}>⚠️ {violationText(v)}</li>
              ))}
            </ul>
            <p className="text-xs text-slate-500">{t("sched.publishWarningsHint")}</p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setPublishWarnings(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">
                {t("common.cancel")}
              </button>
              <button
                onClick={() => {
                  setPublishWarnings(null);
                  publish(true);
                }}
                className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700"
              >
                {t("sched.publishForce")}
              </button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
