"use client";

/** Vending → Telemetria: a kávégépek nyilvántartása és a beküldött
 * telemetria. A gép a /api/pay/telemetry végpontra jelent a saját
 * API-kulcsával — a kulcsot itt generáljuk és visszük fel a gépre. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface Machine {
  id: string;
  machine_code: string;
  name: string;
  note: string | null;
  api_key: string | null;
  is_active: boolean;
  last_seen_at: string | null;
  telemetry: Record<string, unknown> | null;
  paid_today: number;
  revenue_today: number;
}

interface TelemetryRow {
  created_at: string;
  data: Record<string, unknown>;
}

function onlineState(lastSeen: string | null): "online" | "stale" | "offline" {
  if (!lastSeen) return "offline";
  const age = Date.now() - new Date(lastSeen).getTime();
  if (age < 5 * 60_000) return "online";
  if (age < 60 * 60_000) return "stale";
  return "offline";
}

const STATE_CHIP: Record<string, string> = {
  online: "bg-emerald-100 text-emerald-800",
  stale: "bg-amber-100 text-amber-800",
  offline: "bg-rose-100 text-rose-800",
};

export default function VendingTelemetriaPage() {
  const { t, lang } = useT();
  const { toast, confirm } = useUI();
  const ft = (n: number) => `${Math.round(n).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB")} Ft`;
  const fmt = (dt: string) =>
    new Date(dt).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { dateStyle: "short", timeStyle: "medium" });

  const [machines, setMachines] = useState<Machine[]>([]);
  const [form, setForm] = useState<{
    id: string | null; code: string; name: string; note: string; active: boolean;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<{ machine: Machine; rows: TelemetryRow[] } | null>(null);
  const [keyShown, setKeyShown] = useState<string | null>(null); // machine id

  const load = useCallback(() => {
    api.get<Machine[]>("/api/saferpay/machines").then(setMachines).catch(() => {});
  }, []);
  useEffect(() => {
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, [load]);

  async function saveMachine(e: React.FormEvent) {
    e.preventDefault();
    if (!form || !form.code.trim() || !form.name.trim()) return;
    setBusy(true);
    const body = {
      machine_code: form.code.trim(), name: form.name.trim(),
      note: form.note.trim() || null, is_active: form.active,
    };
    try {
      if (form.id) await api.put(`/api/saferpay/machines/${form.id}`, body);
      else await api.post("/api/saferpay/machines", body);
      toast(t("common.saved"), "success");
      setForm(null);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function regenKey(m: Machine) {
    if (!(await confirm(t("vtel.regenConfirm", { name: m.name })))) return;
    try {
      await api.post(`/api/saferpay/machines/${m.id}/regen-key`, {});
      toast(t("vtel.keyRegenerated"), "success");
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function deleteMachine(m: Machine) {
    if (!(await confirm(t("vtel.deleteConfirm", { name: m.name })))) return;
    try {
      await api.delete(`/api/saferpay/machines/${m.id}`);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function openHistory(m: Machine) {
    try {
      const rows = await api.get<TelemetryRow[]>(`/api/saferpay/machines/${m.id}/telemetry`);
      setHistory({ machine: m, rows });
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function copyKey(m: Machine) {
    if (!m.api_key) return;
    try {
      await navigator.clipboard.writeText(m.api_key);
      toast(t("vtel.keyCopied"), "success");
    } catch {
      setKeyShown(m.id); // ha a vágólap nem megy, mutatjuk kijelöléshez
    }
  }

  const telemetryChips = (data: Record<string, unknown> | null) => {
    if (!data || Object.keys(data).length === 0) return null;
    return (
      <div className="mt-1 flex flex-wrap gap-1.5">
        {Object.entries(data).slice(0, 10).map(([k, v]) => (
          <span key={k} className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">
            {k}: <b>{v === null || v === undefined ? "—" : String(v)}</b>
          </span>
        ))}
      </div>
    );
  };

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-xl font-semibold">📡 {t("vtel.title")}</h1>
          <p className="text-sm text-slate-500">{t("vtel.subtitle")}</p>
        </div>
        <button
          onClick={() => setForm({ id: null, code: "", name: "", note: "", active: true })}
          className="ml-auto rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
        >
          + {t("vtel.addMachine")}
        </button>
      </div>

      {machines.length === 0 ? (
        <div className="rounded-2xl border border-slate-200 bg-white p-10 text-center text-sm text-slate-400 shadow-sm">
          {t("vtel.empty")}
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {machines.map((m) => {
            const state = onlineState(m.last_seen_at);
            return (
              <div key={m.id} className={`rounded-2xl border bg-white p-4 shadow-sm ${m.is_active ? "border-slate-200" : "border-slate-200 opacity-60"}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="font-semibold">🤖 {m.name}</h2>
                  <span className="font-mono text-xs text-slate-400">{m.machine_code}</span>
                  <span className={`ml-auto rounded px-2 py-0.5 text-xs font-medium ${STATE_CHIP[state]}`}>
                    {t(`vtel.state.${state}`)}
                  </span>
                </div>
                <p className="mt-0.5 text-xs text-slate-500">
                  {m.last_seen_at
                    ? t("vtel.lastSeen", { at: fmt(m.last_seen_at) })
                    : t("vtel.neverSeen")}
                  {" · "}✅ {t("vtel.todayStats", { n: m.paid_today })} · {ft(m.revenue_today)}
                </p>
                {m.note && <p className="mt-0.5 text-xs text-slate-400">{m.note}</p>}
                {telemetryChips(m.telemetry)}
                {keyShown === m.id && m.api_key && (
                  <input
                    readOnly
                    value={m.api_key}
                    onFocus={(e) => e.target.select()}
                    className="mt-2 w-full rounded-lg border border-slate-300 bg-slate-50 px-2 py-1 font-mono text-xs"
                  />
                )}
                <div className="mt-3 flex flex-wrap gap-1.5">
                  <button onClick={() => void openHistory(m)} className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs hover:bg-slate-100">
                    📜 {t("vtel.history")}
                  </button>
                  <button onClick={() => void copyKey(m)} className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs hover:bg-slate-100">
                    🔑 {t("vtel.copyKey")}
                  </button>
                  <button onClick={() => void regenKey(m)} className="rounded-lg border border-amber-300 px-2.5 py-1 text-xs text-amber-700 hover:bg-amber-50">
                    ♻️ {t("vtel.regenKey")}
                  </button>
                  <button
                    onClick={() => setForm({ id: m.id, code: m.machine_code, name: m.name, note: m.note ?? "", active: m.is_active })}
                    className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs hover:bg-slate-100"
                  >
                    ✏️ {t("common.edit")}
                  </button>
                  <button onClick={() => void deleteMachine(m)} className="rounded-lg border border-rose-300 px-2.5 py-1 text-xs text-rose-700 hover:bg-rose-50">
                    🗑 {t("common.delete")}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
      <p className="mt-3 text-xs text-slate-400">{t("vtel.apiHint")}</p>

      {/* Gép-űrlap modal */}
      {form && (
        <div onMouseDown={(e) => { if (e.target === e.currentTarget) setForm(null); }} className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={saveMachine} className="w-full max-w-sm space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">🤖 {form.id ? t("vtel.editMachine") : t("vtel.addMachine")}</h2>
            <label className="block text-sm">
              {t("vtel.name")} *
              <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              {t("vtel.code")} *
              <input required value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="pl. 174226" className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              {t("vtel.note")}
              <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.active} onChange={(e) => setForm({ ...form, active: e.target.checked })} className="h-4 w-4" />
              {t("vtel.activeLabel")}
            </label>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
                {busy ? t("common.saving") : t("common.save")}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Telemetria-előzmények modal */}
      {history && (
        <div onMouseDown={(e) => { if (e.target === e.currentTarget) setHistory(null); }} className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="max-h-[80vh] w-full max-w-lg space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">📜 {history.machine.name} — {t("vtel.history")}</h2>
            {history.rows.length === 0 ? (
              <p className="py-6 text-center text-sm text-slate-400">{t("vtel.noHistory")}</p>
            ) : (
              <div className="space-y-2 text-sm">
                {history.rows.map((r, i) => (
                  <div key={i} className="rounded-xl border border-slate-100 p-2">
                    <p className="text-xs text-slate-400">{fmt(r.created_at)}</p>
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {Object.entries(r.data).map(([k, v]) => (
                        <span key={k} className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">
                          {k}: <b>{v === null || v === undefined ? "—" : String(v)}</b>
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
            <div className="flex justify-end">
              <button onClick={() => setHistory(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">
                {t("common.close")}
              </button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
