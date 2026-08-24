"use client";

/** Dolgozói önkiszolgálás (mobilra optimalizálva):
 * saját publikált beosztás, munkaidő-állapot (csak olvasás), távollét-kérelem. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { EntryOut, ShiftOut, TimeOffOut } from "@/lib/types";

const TYPES = ["annual", "sick", "unpaid", "other"] as const;

function hhmm(t: string): string {
  return t.slice(0, 5);
}

export default function BeosztasomPage() {
  const [shifts, setShifts] = useState<ShiftOut[]>([]);
  const [openEntry, setOpenEntry] = useState<EntryOut | null>(null);
  const [timeOff, setTimeOff] = useState<TimeOffOut[]>([]);
  const [profile, setProfile] = useState<{ name: string; employee_code: string | null } | null>(null);
  const [showRequest, setShowRequest] = useState(false);
  const [form, setForm] = useState({ type: "annual", start_date: "", end_date: "", reason: "" });
  const [error, setError] = useState<string | null>(null);
  const [noEmployee, setNoEmployee] = useState(false);
  const [busy, setBusy] = useState(false);
  const { t, lang } = useT();

  const dayLabel = (isoDate: string) => {
    const d = new Date(`${isoDate}T00:00:00`);
    const names = [0, 1, 2, 3, 4, 5, 6].map((i) => t(`mySched.dayNames.${i}`));
    return `${isoDate} (${names[(d.getDay() + 6) % 7]})`;
  };

  const load = useCallback(() => {
    api
      .get<ShiftOut[]>("/api/me/schedule")
      .then(setShifts)
      .catch((err) => {
        if (err?.code === "employee.none_for_user") setNoEmployee(true);
      });
    api.get<{ open: EntryOut | null }>("/api/me/clock").then((r) => setOpenEntry(r.open)).catch(() => {});
    api.get<TimeOffOut[]>("/api/me/time-off").then(setTimeOff).catch(() => {});
    api
      .get<{ name: string; employee_code: string | null }>("/api/me/profile")
      .then(setProfile)
      .catch(() => {});
  }, []);

  useEffect(load, [load]);

  async function requestTimeOff(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/me/time-off", { ...form, reason: form.reason || null });
      setShowRequest(false);
      setForm({ type: "annual", start_date: "", end_date: "", reason: "" });
      load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const byDate = shifts.reduce<Record<string, ShiftOut[]>>((acc, s) => {
    (acc[s.work_date] ??= []).push(s);
    return acc;
  }, {});

  if (noEmployee) {
    return (
      <AppShell>
        <p className="rounded-2xl border border-amber-200 bg-amber-50 p-6 text-amber-800">
          {t("mySched.noEmployeeCard")}
        </p>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-lg space-y-6">
        {profile?.employee_code && (
          <p className="text-center text-sm text-slate-500">
            {t("mySched.codeInfo")}{" "}
            <span className="font-mono text-base font-bold text-indigo-700">
              {profile.employee_code}
            </span>
          </p>
        )}

        {/* Munkaidő — csak állapot; blokkolni a terminálon lehet */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-2 font-semibold">{t("mySched.workTime")}</h2>
          {openEntry ? (
            <p className="text-sm">
              <span className="font-medium text-emerald-700">{t("mySched.clockedIn")}</span>{" "}
              <span className="text-slate-500">
                {t("mySched.since", {
                  time: new Date(openEntry.clock_in).toLocaleTimeString(
                    lang === "hu" ? "hu-HU" : "en-GB",
                    { hour: "2-digit", minute: "2-digit" }
                  ),
                })}
              </span>
            </p>
          ) : (
            <p className="text-sm text-slate-500">{t("mySched.notClockedIn")}</p>
          )}
          <p className="mt-2 text-xs text-slate-400">{t("mySched.terminalHint")}</p>
        </section>

        {/* Beosztás */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 font-semibold">{t("mySched.scheduleTitle")}</h2>
          {Object.keys(byDate).length === 0 ? (
            <p className="text-sm text-slate-400">{t("mySched.noSchedule")}</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {Object.entries(byDate).map(([day, dayShifts]) => (
                <li key={day} className="flex items-baseline justify-between py-2">
                  <span className="text-sm font-medium">{dayLabel(day)}</span>
                  <span className="text-sm text-slate-600">
                    {dayShifts
                      .map(
                        (s) =>
                          `${hhmm(s.start_time)}–${hhmm(s.end_time)}${s.location ? ` · ${s.location}` : ""}${s.role_label ? ` · ${s.role_label}` : ""}`
                      )
                      .join(", ")}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Távollét */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-semibold">{t("mySched.timeOffTitle")}</h2>
            <button
              onClick={() => setShowRequest(true)}
              className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
            >
              {t("mySched.request")}
            </button>
          </div>
          {timeOff.length === 0 ? (
            <p className="text-sm text-slate-400">{t("mySched.noRequests")}</p>
          ) : (
            <ul className="divide-y divide-slate-100 text-sm">
              {timeOff.map((item) => (
                <li key={item.id} className="flex items-center justify-between py-2">
                  <span>
                    {t(`leave.types.${item.type}`)} · {item.start_date} → {item.end_date}
                  </span>
                  <span
                    className={`rounded px-2 py-0.5 text-xs font-medium ${
                      item.status === "approved"
                        ? "bg-emerald-100 text-emerald-800"
                        : item.status === "rejected"
                          ? "bg-red-100 text-red-700"
                          : "bg-amber-100 text-amber-800"
                    }`}
                  >
                    {t(`leave.statuses.${item.status}`)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {showRequest && (
        <div onMouseDown={(e) => { if (e.target === e.currentTarget) setShowRequest(false); }} className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={requestTimeOff} className="max-h-[90vh] w-full max-w-sm space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{t("mySched.requestTitle")}</h2>
            <label className="block text-sm">
              {t("leave.type")}
              <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2">
                {TYPES.map((type) => (
                  <option key={type} value={type}>{t(`leave.types.${type}`)}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              {t("leave.from")}
              <input required type="date" value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              {t("leave.to")}
              <input required type="date" value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              {t("leave.reason")}
              <input value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={() => setShowRequest(false)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{t("common.send")}</button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
