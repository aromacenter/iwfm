"use client";

/** Szerszám-nyilvántartás (137. kör): a dolgozó a nála lévő szerszámokat látja;
 *  a kezelő (szerviz/feladat/alkatrész jog) a teljes listát kezeli — felvétel,
 *  átadás-átvétel, ellenőrzés/kalibrálás rögzítése, állapot, napló. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { usePerms } from "@/lib/perms";
import { useUI } from "@/lib/ui";

interface Tool {
  id: string;
  name: string;
  serial_no: string | null;
  category: string | null;
  holder_employee_id: string | null;
  holder_name: string | null;
  status: "ok" | "repair" | "lost" | "retired";
  next_check: string | null;
  last_check: string | null;
  check_interval_months: number | null;
  note: string | null;
  check_state: "ok" | "soon" | "overdue" | "none";
  created_at: string;
}
interface ToolEvent { id: string; kind: string; from_name: string | null; to_name: string | null; note: string | null; actor_name: string | null; created_at: string }
interface EmployeeLite { id: string; last_name: string; first_name: string; status?: string }

interface FormState {
  id: string | null;
  name: string;
  serial_no: string;
  category: string;
  holder_employee_id: string;
  status: Tool["status"];
  next_check: string;
  check_interval_months: string;
  note: string;
}
const EMPTY: FormState = { id: null, name: "", serial_no: "", category: "", holder_employee_id: "", status: "ok", next_check: "", check_interval_months: "", note: "" };
const inputCls = "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm";
const STATUS_CLS: Record<Tool["status"], string> = {
  ok: "bg-emerald-100 text-emerald-800", repair: "bg-amber-100 text-amber-800", lost: "bg-rose-100 text-rose-800", retired: "bg-slate-200 text-slate-600",
};

export default function SzerszamokPage() {
  const { t, lang } = useT();
  const { toast, confirm } = useUI();
  const perms = usePerms();
  const canManage = perms.can("service") || perms.can("tasks") || perms.can("parts");
  const canDelete = perms.can("delete");
  const [mine, setMine] = useState<Tool[]>([]);
  const [tools, setTools] = useState<Tool[]>([]);
  const [employees, setEmployees] = useState<EmployeeLite[]>([]);
  const [search, setSearch] = useState("");
  const [dueOnly, setDueOnly] = useState(false);
  const [form, setForm] = useState<FormState | null>(null);
  const [busy, setBusy] = useState(false);
  const [handover, setHandover] = useState<{ tool: Tool; employee_id: string; note: string } | null>(null);
  const [check, setCheck] = useState<{ tool: Tool; next_check: string; note: string } | null>(null);
  const [events, setEvents] = useState<{ tool: Tool; rows: ToolEvent[] } | null>(null);

  const fd = (d: string | null) => (d ? new Date(d).toLocaleDateString(lang === "hu" ? "hu-HU" : "en-GB") : "—");
  const fmt = (dt: string) => new Date(dt).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { dateStyle: "short", timeStyle: "short" });

  const load = useCallback(() => {
    api.get<Tool[]>("/api/tools/my").then(setMine).catch(() => setMine([]));
    if (!canManage) return;
    const params = new URLSearchParams();
    if (search.trim()) params.set("q", search.trim());
    if (dueOnly) params.set("due", "true");
    const qs = params.toString();
    api.get<Tool[]>(`/api/tools${qs ? `?${qs}` : ""}`).then(setTools).catch((err) => toast(errorMessage(err), "error"));
  }, [canManage, search, dueOnly, toast]);
  useEffect(load, [load]);
  useEffect(() => {
    if (!canManage) return;
    api.get<EmployeeLite[]>("/api/employees").then((rows) => setEmployees(rows.filter((e) => !e.status || e.status === "active"))).catch(() => {});
  }, [canManage]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!form) return;
    setBusy(true);
    const body = {
      name: form.name.trim(), serial_no: form.serial_no.trim() || null, category: form.category.trim() || null,
      status: form.status, next_check: form.next_check || null,
      check_interval_months: form.check_interval_months ? Number(form.check_interval_months) : null,
      note: form.note.trim() || null,
    };
    try {
      if (form.id) await api.patch(`/api/tools/${form.id}`, body);
      else await api.post("/api/tools", { ...body, holder_employee_id: form.holder_employee_id || null });
      toast(t("common.saved"), "success");
      setForm(null);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function doHandover() {
    if (!handover) return;
    setBusy(true);
    try {
      await api.post(`/api/tools/${handover.tool.id}/handover`, { employee_id: handover.employee_id || null, note: handover.note || null });
      toast(t("tools.handoverDone"), "success");
      setHandover(null);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function doCheck() {
    if (!check) return;
    setBusy(true);
    try {
      await api.post(`/api/tools/${check.tool.id}/check`, { next_check: check.next_check || null, note: check.note || null });
      toast(t("tools.checkDone"), "success");
      setCheck(null);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function openEvents(tool: Tool) {
    try {
      const rows = await api.get<ToolEvent[]>(`/api/tools/${tool.id}/events`);
      setEvents({ tool, rows });
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function remove(tool: Tool) {
    if (!(await confirm(t("tools.deleteConfirm", { name: tool.name })))) return;
    try {
      await api.delete(`/api/tools/${tool.id}`);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  const checkBadge = (tool: Tool) =>
    tool.check_state === "overdue" ? (
      <span className="ml-1 rounded bg-rose-100 px-1.5 py-0.5 text-[11px] font-medium text-rose-800">⚠️ {t("tools.checkOverdue")}</span>
    ) : tool.check_state === "soon" ? (
      <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium text-amber-800">⏳ {t("tools.checkSoon")}</span>
    ) : null;

  return (
    <AppShell>
      <h1 className="mb-1 text-xl font-bold">🧰 {t("tools.title")}</h1>
      <p className="mb-4 text-sm text-slate-500">{t("tools.hint")}</p>

      {/* Saját szerszámok */}
      <section className="mb-6 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="mb-2 font-semibold">{t("tools.myTitle")}</h2>
        {mine.length === 0 ? (
          <p className="text-sm text-slate-400">{t("tools.myEmpty")}</p>
        ) : (
          <ul className="divide-y divide-slate-100 text-sm">
            {mine.map((tool) => (
              <li key={tool.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span>
                  <span className="font-medium">{tool.name}</span>
                  {tool.serial_no && <span className="ml-2 font-mono text-xs text-slate-400">{tool.serial_no}</span>}
                  {tool.category && <span className="ml-2 text-xs text-slate-500">{tool.category}</span>}
                  {checkBadge(tool)}
                </span>
                <span className="text-xs text-slate-500">
                  <span className={`rounded px-1.5 py-0.5 ${STATUS_CLS[tool.status]}`}>{t(`tools.statuses.${tool.status}`)}</span>
                  {tool.next_check && <> · {t("tools.nextCheck")}: {fd(tool.next_check)}</>}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {canManage && (
        <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t("tools.search")} className="w-72 max-w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <button onClick={() => setDueOnly((v) => !v)} className={`rounded-lg border px-3 py-2 text-sm ${dueOnly ? "border-amber-400 bg-amber-50 text-amber-900" : "border-slate-300 text-slate-600"}`}>
              {dueOnly ? t("tools.filterAll") : t("tools.filterDue")}
            </button>
            <button onClick={() => setForm(EMPTY)} className="ml-auto rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
              {t("tools.new")}
            </button>
          </div>

          {form && (
            <form onSubmit={save} className="mb-4 grid gap-3 rounded-xl border border-indigo-200 bg-indigo-50/40 p-4 sm:grid-cols-2">
              <h3 className="font-semibold sm:col-span-2">{form.id ? t("tools.editTitle") : t("tools.newTitle")}</h3>
              <label className="text-sm">{t("tools.name")}<input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={inputCls} /></label>
              <label className="text-sm">{t("tools.serial")}<input value={form.serial_no} onChange={(e) => setForm({ ...form, serial_no: e.target.value })} className={inputCls} /></label>
              <label className="text-sm">{t("tools.category")}<input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} placeholder={t("tools.categoryPh")} className={inputCls} /></label>
              {!form.id && (
                <label className="text-sm">{t("tools.holder")}
                  <select value={form.holder_employee_id} onChange={(e) => setForm({ ...form, holder_employee_id: e.target.value })} className={inputCls}>
                    <option value="">{t("tools.unassigned")}</option>
                    {employees.map((e) => <option key={e.id} value={e.id}>{e.last_name} {e.first_name}</option>)}
                  </select>
                </label>
              )}
              <label className="text-sm">{t("tools.status")}
                <select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value as Tool["status"] })} className={inputCls}>
                  {(["ok", "repair", "lost", "retired"] as const).map((s) => <option key={s} value={s}>{t(`tools.statuses.${s}`)}</option>)}
                </select>
              </label>
              <label className="text-sm">{t("tools.nextCheck")}<input type="date" value={form.next_check} onChange={(e) => setForm({ ...form, next_check: e.target.value })} className={inputCls} /></label>
              <label className="text-sm">{t("tools.interval")}<input type="number" min={1} max={120} value={form.check_interval_months} onChange={(e) => setForm({ ...form, check_interval_months: e.target.value })} className={inputCls} /></label>
              <label className="text-sm sm:col-span-2">{t("tools.note")}<textarea rows={2} value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} className={inputCls} /></label>
              <div className="flex gap-2 sm:col-span-2">
                <button type="submit" disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{t("common.save")}</button>
                <button type="button" onClick={() => setForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm">{t("common.cancel")}</button>
              </div>
            </form>
          )}

          {handover && (
            <div className="mb-4 flex flex-wrap items-end gap-2 rounded-xl border border-sky-200 bg-sky-50 p-3 text-sm">
              <span className="font-medium">🤝 {t("tools.handover")}: {handover.tool.name}</span>
              <label>{t("tools.handoverTo")}
                <select value={handover.employee_id} onChange={(e) => setHandover({ ...handover, employee_id: e.target.value })} className={inputCls}>
                  <option value="">{t("tools.unassigned")}</option>
                  {employees.map((e) => <option key={e.id} value={e.id}>{e.last_name} {e.first_name}</option>)}
                </select>
              </label>
              <input value={handover.note} onChange={(e) => setHandover({ ...handover, note: e.target.value })} placeholder={t("tools.handoverNote")} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              <button disabled={busy} onClick={() => void doHandover()} className="rounded-lg bg-sky-600 px-3 py-2 text-sm font-medium text-white">{t("common.save")}</button>
              <button onClick={() => setHandover(null)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">{t("common.cancel")}</button>
            </div>
          )}

          {check && (
            <div className="mb-4 flex flex-wrap items-end gap-2 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm">
              <span className="font-medium">✅ {t("tools.check")}: {check.tool.name}</span>
              <label>{t("tools.nextCheck")}<input type="date" value={check.next_check} onChange={(e) => setCheck({ ...check, next_check: e.target.value })} className={inputCls} title={t("tools.checkNextPh")} /></label>
              <input value={check.note} onChange={(e) => setCheck({ ...check, note: e.target.value })} placeholder={t("tools.note")} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              <button disabled={busy} onClick={() => void doCheck()} className="rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white">{t("common.save")}</button>
              <button onClick={() => setCheck(null)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">{t("common.cancel")}</button>
            </div>
          )}

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-slate-500">
                  <th className="py-2 pr-2">{t("tools.name")}</th>
                  <th className="py-2 pr-2">{t("tools.holder")}</th>
                  <th className="py-2 pr-2">{t("tools.status")}</th>
                  <th className="py-2 pr-2">{t("tools.nextCheck")}</th>
                  <th className="py-2"></th>
                </tr>
              </thead>
              <tbody>
                {tools.map((tool) => (
                  <tr key={tool.id} className={`border-t border-slate-100 ${tool.check_state === "overdue" ? "bg-rose-50/60" : ""}`}>
                    <td className="py-2 pr-2">
                      <div className="font-medium">{tool.name}{checkBadge(tool)}</div>
                      <div className="text-xs text-slate-400">{tool.serial_no ?? ""}{tool.category ? ` · ${tool.category}` : ""}</div>
                    </td>
                    <td className="py-2 pr-2">{tool.holder_name ?? <span className="text-slate-400">{t("tools.unassigned")}</span>}</td>
                    <td className="py-2 pr-2"><span className={`rounded px-1.5 py-0.5 text-xs ${STATUS_CLS[tool.status]}`}>{t(`tools.statuses.${tool.status}`)}</span></td>
                    <td className="py-2 pr-2 whitespace-nowrap">{fd(tool.next_check)}{tool.last_check && <div className="text-xs text-slate-400">{t("tools.lastCheck")}: {fd(tool.last_check)}</div>}</td>
                    <td className="py-2 text-right whitespace-nowrap">
                      <button onClick={() => setHandover({ tool, employee_id: "", note: "" })} className="mr-1 rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100" title={t("tools.handover")}>🤝</button>
                      <button onClick={() => setCheck({ tool, next_check: "", note: "" })} className="mr-1 rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100" title={t("tools.check")}>✅</button>
                      <button onClick={() => void openEvents(tool)} className="mr-1 rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100" title={t("tools.events")}>📜</button>
                      <button
                        onClick={() => setForm({
                          id: tool.id, name: tool.name, serial_no: tool.serial_no ?? "", category: tool.category ?? "", holder_employee_id: tool.holder_employee_id ?? "",
                          status: tool.status, next_check: tool.next_check ?? "", check_interval_months: tool.check_interval_months ? String(tool.check_interval_months) : "", note: tool.note ?? "",
                        })}
                        className="mr-1 rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100"
                        title={t("common.edit")}
                      >✏️</button>
                      {canDelete && <button onClick={() => void remove(tool)} className="rounded border border-rose-200 px-2 py-1 text-xs text-rose-700 hover:bg-rose-50" title={t("common.delete")}>🗑</button>}
                    </td>
                  </tr>
                ))}
                {tools.length === 0 && <tr><td colSpan={5} className="py-6 text-center text-slate-400">{t("tools.empty")}</td></tr>}
              </tbody>
            </table>
          </div>

          {events && (
            <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm">
              <div className="mb-1 flex items-center justify-between">
                <span className="font-medium">📜 {t("tools.events")}: {events.tool.name}</span>
                <button onClick={() => setEvents(null)} className="text-xs text-slate-500 hover:underline">{t("common.close")}</button>
              </div>
              <ul className="divide-y divide-slate-200 text-xs">
                {events.rows.map((e) => (
                  <li key={e.id} className="py-1">
                    <span className="text-slate-400">{fmt(e.created_at)}</span> · <b>{t(`tools.eventKinds.${e.kind}`)}</b>
                    {e.kind === "handover" && <> {e.from_name ?? t("tools.unassigned")} → {e.to_name ?? t("tools.unassigned")}</>}
                    {e.note && <> · {e.note}</>}
                    {e.actor_name && <span className="text-slate-400"> ({e.actor_name})</span>}
                  </li>
                ))}
                {events.rows.length === 0 && <li className="py-1 text-slate-400">—</li>}
              </ul>
            </div>
          )}
        </section>
      )}
    </AppShell>
  );
}
