"use client";

/** Dolgozói önkiszolgálás (telefonra optimalizálva):
 * saját publikált beosztás, be-/kijelentkezés, távollét-kérelem. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import type { EntryOut, ShiftOut, TimeOffOut } from "@/lib/types";
import { TIME_OFF_LABELS, TIME_OFF_STATUS_LABELS } from "@/lib/types";

const DAY_NAMES = ["hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap"];

function hhmm(t: string): string {
  return t.slice(0, 5);
}

function dayLabel(isoDate: string): string {
  const d = new Date(`${isoDate}T00:00:00`);
  return `${isoDate} (${DAY_NAMES[(d.getDay() + 6) % 7]})`;
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

  async function clock(direction: "in" | "out") {
    setBusy(true);
    try {
      await api.post(`/api/me/clock-${direction}`, {});
      load();
    } catch (err) {
      alert(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

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
          Ehhez a fiókhoz még nem tartozik dolgozói adatlap — szólj a vezetődnek.
        </p>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-lg space-y-6">
        {profile?.employee_code && (
          <p className="text-center text-sm text-slate-500">
            Törzsszámod a blokkoló-terminálhoz:{" "}
            <span className="font-mono text-base font-bold text-indigo-700">
              {profile.employee_code}
            </span>
          </p>
        )}
        {/* Óra */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 font-semibold">Munkaidő</h2>
          {openEntry ? (
            <div className="flex items-center justify-between">
              <div className="text-sm">
                <span className="font-medium text-emerald-700">Bejelentkezve</span>
                <div className="text-slate-500">
                  {new Date(openEntry.clock_in).toLocaleTimeString("hu-HU", { hour: "2-digit", minute: "2-digit" })} óta
                </div>
              </div>
              <button
                onClick={() => clock("out")}
                disabled={busy}
                className="rounded-xl bg-red-600 px-6 py-3 font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                Kijelentkezés
              </button>
            </div>
          ) : (
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-500">Most nem vagy bejelentkezve.</span>
              <button
                onClick={() => clock("in")}
                disabled={busy}
                className="rounded-xl bg-emerald-600 px-6 py-3 font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
              >
                Bejelentkezés
              </button>
            </div>
          )}
        </section>

        {/* Beosztás */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 font-semibold">Beosztásom (következő 4 hét)</h2>
          {Object.keys(byDate).length === 0 ? (
            <p className="text-sm text-slate-400">Még nincs közölt beosztásod.</p>
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
            <h2 className="font-semibold">Távolléteim</h2>
            <button
              onClick={() => setShowRequest(true)}
              className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
            >
              + Kérelem
            </button>
          </div>
          {timeOff.length === 0 ? (
            <p className="text-sm text-slate-400">Nincs kérelmed.</p>
          ) : (
            <ul className="divide-y divide-slate-100 text-sm">
              {timeOff.map((t) => (
                <li key={t.id} className="flex items-center justify-between py-2">
                  <span>
                    {TIME_OFF_LABELS[t.type]} · {t.start_date} → {t.end_date}
                  </span>
                  <span
                    className={`rounded px-2 py-0.5 text-xs font-medium ${
                      t.status === "approved"
                        ? "bg-emerald-100 text-emerald-800"
                        : t.status === "rejected"
                          ? "bg-red-100 text-red-700"
                          : "bg-amber-100 text-amber-800"
                    }`}
                  >
                    {TIME_OFF_STATUS_LABELS[t.status]}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {showRequest && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={requestTimeOff} className="w-full max-w-sm space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">Távollét kérése</h2>
            <label className="block text-sm">
              Típus
              <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2">
                {Object.entries(TIME_OFF_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              Kezdete
              <input required type="date" value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              Vége
              <input required type="date" value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              Indok
              <input value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={() => setShowRequest(false)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">Mégsem</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">Küldés</button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
