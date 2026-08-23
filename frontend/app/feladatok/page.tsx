"use client";

/** Feladatok (vezetői): online munkalap kiállítása dolgozóra
 * (opcionális skill-szűréssel + AI javaslattal), státuszok, kommentek, PDF. */

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import SearchSelect from "@/components/SearchSelect";
import { api, downloadFile, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";
import type { EmployeeOut } from "@/lib/types";

interface Skill {
  id: number;
  name: string;
}

interface CommentOut {
  id: string;
  author_name: string | null;
  text: string;
  created_at: string;
}

interface TaskOut {
  id: string;
  title: string;
  description: string | null;
  employee_id: string;
  employee_name: string | null;
  due_date: string;
  required_skill: Skill | null;
  status: "open" | "done" | "needs_more_work";
  comments: CommentOut[];
  created_at: string;
  worksheet_serial: string | null;
  worksheet_completed: boolean;
  worksheet_external: boolean;
}

const STATUS_COLORS: Record<string, string> = {
  open: "bg-sky-100 text-sky-800",
  done: "bg-emerald-100 text-emerald-800",
  needs_more_work: "bg-amber-100 text-amber-800",
};

const STATUSES = ["open", "done", "needs_more_work"] as const;

function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export default function FeladatokPage() {
  const [tasks, setTasks] = useState<TaskOut[]>([]);
  const [employees, setEmployees] = useState<EmployeeOut[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [kindFilter, setKindFilter] = useState<"all" | "normal" | "external">("all");
  const [externalService, setExternalService] = useState(false);
  // KSZ-munkalap tárgy-gépe: ebből jön a karbantartási díj és a számlázási partner
  const [taskAssetId, setTaskAssetId] = useState("");
  const [assetOptions, setAssetOptions] = useState<{ id: string; name: string; barcode: string; partner_name: string | null }[]>([]);
  useEffect(() => {
    api.get<{ id: string; name: string; barcode: string; partner_name: string | null }[]>("/api/assets")
      .then(setAssetOptions).catch(() => {});
  }, []);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    title: "",
    description: "",
    employee_id: "",
    due_date: todayIso(),
    required_skill_id: 0,
    client_name: "",
    client_location: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiSuggestion, setAiSuggestion] = useState<{
    employee_name: string;
    reason: string;
    open_tasks: number;
  } | null>(null);
  const { t, lang } = useT();
  const { toast, confirm, prompt } = useUI();

  const load = useCallback(() => {
    api
      .get<TaskOut[]>(`/api/tasks${statusFilter ? `?status=${statusFilter}` : ""}`)
      .then(setTasks)
      .catch(() => {});
  }, [statusFilter]);

  useEffect(() => {
    api.get<EmployeeOut[]>("/api/employees").then(setEmployees).catch(() => {});
    api.get<Skill[]>("/api/settings/skills").then(setSkills).catch(() => {});
  }, []);
  useEffect(load, [load]);

  const assignableEmployees = useMemo(() => {
    const active = employees.filter((e) => e.status === "active");
    if (!form.required_skill_id) return active.map((e) => ({ emp: e, hasSkill: true }));
    return active
      .map((e) => ({
        emp: e,
        hasSkill: (e.skills ?? []).some((s) => s.id === form.required_skill_id),
      }))
      .sort((a, b) => Number(b.hasSkill) - Number(a.hasSkill));
  }, [employees, form.required_skill_id]);

  async function askAi() {
    if (!form.title.trim()) {
      setError(t("tasks.aiNeedTitle"));
      return;
    }
    setAiBusy(true);
    setError(null);
    setAiSuggestion(null);
    try {
      const res = await api.post<{
        employee_id: string;
        employee_name: string;
        reason: string;
        open_tasks: number;
      }>("/api/tasks/suggest-assignee", {
        title: form.title,
        description: form.description || null,
        due_date: form.due_date,
        required_skill_id: form.required_skill_id || null,
      });
      setForm((f) => ({ ...f, employee_id: res.employee_id }));
      setAiSuggestion({
        employee_name: res.employee_name,
        reason: res.reason,
        open_tasks: res.open_tasks,
      });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setAiBusy(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.post<TaskOut & { ai_reason?: string | null }>("/api/tasks", {
        title: form.title,
        description: form.description || null,
        // "auto" = az AI jelöli ki a legalkalmasabb dolgozót a szerveren
        employee_id: form.employee_id === "auto" ? null : form.employee_id,
        due_date: form.due_date,
        required_skill_id: form.required_skill_id || null,
        client_name: form.client_name || null,
        client_location: form.client_location || null,
        external_service: externalService,
        asset_id: externalService && taskAssetId ? taskAssetId : null,
      });
      setShowForm(false);
      setExternalService(false);
      setTaskAssetId("");
      setForm({
        title: "", description: "", employee_id: "", due_date: todayIso(),
        required_skill_id: 0, client_name: "", client_location: "",
      });
      const messages: string[] = [];
      if (created.ai_reason) {
        messages.push(
          t("tasks.aiAssignedAlert", {
            name: created.employee_name ?? "?",
            reason: created.ai_reason,
          })
        );
      }
      if (created.worksheet_serial) {
        messages.push(t("tasks.issuedAlert", { serial: created.worksheet_serial }));
      }
      if (messages.length > 0) toast(messages.join("\n\n"), "success");
      load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function setStatus(task: TaskOut, status: string) {
    try {
      await api.patch(`/api/tasks/${task.id}`, { status });
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function emailWorksheet(task: TaskOut) {
    const to = await prompt(t("tasks.emailPrompt"), { type: "email", placeholder: "ugyfel@example.com" });
    if (!to) return;
    try {
      await api.post(`/api/tasks/${task.id}/worksheet/email`, { to });
      toast(t("tasks.emailSent", { to }), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function downloadWorksheet(task: TaskOut) {
    try {
      await downloadFile(
        `/api/tasks/${task.id}/worksheet/pdf`,
        `${task.worksheet_serial ?? "munkalap"}.pdf`
      );
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  // KSZ-munkalap ügyfél-árainak szerkesztése — a képviselő állítja be, nem a
  // szervizes; a -1-es ügyfél-példányra ezek az árak kerülnek.
  interface WsData {
    work_description: string;
    materials: { name: string; qty: string; unit: string; cost_net: number | null; price_net: number | null }[];
    hours_spent: number | null;
    client_name: string | null;
    client_location: string | null;
    maintenance_fee: number | null;
    fee_discount: boolean;
    invoiced: boolean;
  }
  const [priceEdit, setPriceEdit] = useState<{ task: TaskOut; ws: WsData; prices: string[]; fee: string; discount: boolean } | null>(null);

  async function openPriceEdit(task: TaskOut) {
    try {
      const ws = await api.get<WsData>(`/api/tasks/${task.id}/worksheet`);
      setPriceEdit({
        task,
        ws,
        prices: (ws.materials ?? []).map((m) => (m.price_net != null ? String(m.price_net) : "")),
        fee: ws.maintenance_fee != null ? String(ws.maintenance_fee) : "",
        discount: ws.fee_discount,
      });
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function savePrices() {
    if (!priceEdit) return;
    try {
      await api.put(`/api/tasks/${priceEdit.task.id}/worksheet`, {
        work_description: priceEdit.ws.work_description,
        hours_spent: priceEdit.ws.hours_spent,
        client_name: priceEdit.ws.client_name,
        client_location: priceEdit.ws.client_location,
        materials: priceEdit.ws.materials.map((m, i) => ({
          name: m.name, qty: m.qty, unit: m.unit,
          cost_net: m.cost_net,
          price_net: priceEdit.prices[i] ? Number(priceEdit.prices[i]) : null,
        })),
        maintenance_fee: priceEdit.fee ? Number(priceEdit.fee) : null,
        fee_discount: priceEdit.discount,
      });
      setPriceEdit(null);
      toast(t("common.saved"), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  // KSZ-munkalap ügyfél-példánya (-1): a mi szerviz-árainkkal, költségek nélkül
  async function downloadCustomerCopy(task: TaskOut) {
    try {
      await downloadFile(
        `/api/tasks/${task.id}/worksheet/pdf?variant=customer`,
        `${task.worksheet_serial ?? "munkalap"}-1.pdf`
      );
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function remove(task: TaskOut) {
    if (!(await confirm(t("tasks.deleteConfirm", { title: task.title })))) return;
    try {
      await api.delete(`/api/tasks/${task.id}`);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("tasks.title")}</h1>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          <option value="">{t("tasks.allStatuses")}</option>
          {STATUSES.map((status) => (
            <option key={status} value={status}>{t(`tasks.statuses.${status}`)}</option>
          ))}
        </select>
        <div className="flex gap-1.5">
          {([["all", t("contracts.filterAll")], ["normal", t("tasks.kindNormal")], ["external", t("tasks.kindExternal")]] as const).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setKindFilter(key)}
              className={`rounded-full px-3 py-1.5 text-xs font-medium transition ${
                kindFilter === key
                  ? "bg-indigo-600 text-white"
                  : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <button
          onClick={() => setShowForm(true)}
          className="ml-auto rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
        >
          {t("tasks.new")}
        </button>
      </div>

      <div className="space-y-3">
        {tasks
          .filter((task) =>
            kindFilter === "all"
              ? true
              : kindFilter === "external"
                ? task.worksheet_external
                : !task.worksheet_external
          )
          .map((task) => (
          <div key={task.id} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex flex-wrap items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold">{task.title}</span>
                  <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[task.status]}`}>
                    {t(`tasks.statuses.${task.status}`)}
                  </span>
                  {task.required_skill && (
                    <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs text-indigo-700">
                      {task.required_skill.name}
                    </span>
                  )}
                  {task.worksheet_serial && (
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs ${
                        task.worksheet_completed
                          ? "bg-emerald-50 text-emerald-700"
                          : "bg-amber-50 text-amber-700"
                      }`}
                      title={task.worksheet_completed ? t("tasks.worksheetDoneTitle") : t("tasks.worksheetIssuedTitle")}
                    >
                      📝 {task.worksheet_serial} · {task.worksheet_completed ? t("tasks.worksheetDone") : t("tasks.worksheetIssued")}
                    </span>
                  )}
                  {task.worksheet_external && (
                    <span className="rounded-full bg-orange-100 px-2 py-0.5 text-xs font-semibold text-orange-700">
                      🔧 {t("tasks.externalBadge")}
                    </span>
                  )}
                </div>
                <p className="text-sm text-slate-500">
                  {task.employee_name} · {task.due_date}
                  {task.comments.length > 0 && ` · ${t("tasks.comments", { count: task.comments.length })}`}
                </p>
              </div>
              <div className="flex gap-2">
                {task.worksheet_serial && (
                  <>
                    {/* KSZ: alapból az ÜGYFÉL példánya — a belső (költséges) külön gombra */}
                    {task.worksheet_external ? (
                      <>
                        <button
                          onClick={() => downloadCustomerCopy(task)}
                          title={t("tasks.customerCopyTitle")}
                          className="rounded-lg border border-emerald-300 px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-50"
                        >
                          {t("tasks.worksheetPdf")}
                        </button>
                        <button
                          onClick={() => openPriceEdit(task)}
                          title={t("tasks.priceEditHint")}
                          className="rounded-lg border border-emerald-300 px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-50"
                        >
                          💰 {t("tasks.priceEdit")}
                        </button>
                        <button
                          onClick={() => downloadWorksheet(task)}
                          title={t("tasks.internalCopyHint")}
                          className="rounded-lg border border-orange-300 px-3 py-1.5 text-xs font-medium text-orange-700 hover:bg-orange-50"
                        >
                          {t("tasks.worksheetPdfInternal")}
                        </button>
                      </>
                    ) : (
                      <button
                        onClick={() => downloadWorksheet(task)}
                        className="rounded-lg border border-emerald-300 px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-50"
                      >
                        {t("tasks.worksheetPdf")}
                      </button>
                    )}
                    <button
                      onClick={() => emailWorksheet(task)}
                      className="rounded-lg border border-emerald-300 px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-50"
                    >
                      {t("tasks.worksheetEmail")}
                    </button>
                  </>
                )}
                {task.status !== "open" && (
                  <button
                    onClick={() => setStatus(task, "open")}
                    className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs hover:bg-slate-100"
                  >
                    {t("tasks.reopen")}
                  </button>
                )}
                {task.status !== "done" && (
                  <button
                    onClick={() => setStatus(task, "done")}
                    className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700"
                  >
                    {t("tasks.closeTask")}
                  </button>
                )}
                <button
                  onClick={() => setExpanded(expanded === task.id ? null : task.id)}
                  className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs hover:bg-slate-100"
                >
                  {expanded === task.id ? t("common.close") : t("common.details")}
                </button>
                <button
                  onClick={() => remove(task)}
                  className="rounded-lg border border-red-200 px-3 py-1.5 text-xs text-red-600 hover:bg-red-50"
                >
                  {t("common.delete")}
                </button>
              </div>
            </div>
            {expanded === task.id && (
              <div className="mt-3 border-t border-slate-100 pt-3 text-sm">
                {task.description && <p className="mb-2 whitespace-pre-wrap text-slate-600">{task.description}</p>}
                {task.comments.length === 0 ? (
                  <p className="text-slate-400">{t("tasks.noComments")}</p>
                ) : (
                  <ul className="space-y-1">
                    {task.comments.map((c) => (
                      <li key={c.id}>
                        <span className="font-medium">{c.author_name ?? "?"}:</span>{" "}
                        <span className="text-slate-600">{c.text}</span>{" "}
                        <span className="text-xs text-slate-400">
                          {new Date(c.created_at).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { dateStyle: "short", timeStyle: "short" })}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        ))}
        {tasks.length === 0 && (
          <p className="rounded-2xl border border-slate-200 bg-white px-4 py-10 text-center text-slate-400">
            {t("tasks.empty")}
          </p>
        )}
      </div>

      {showForm && (
        <div onMouseDown={(e) => { if (e.target === e.currentTarget) setShowForm(false); }} className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={submit} className="w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{t("tasks.newTitle")}</h2>
            <p className="text-xs text-slate-500">{t("tasks.newHint")}</p>
            <label className="block text-sm">
              {t("tasks.taskTitle")}
              <input required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              {t("tasks.description")}
              <textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={3} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              {t("tasks.requiredSkill")}
              <select
                value={form.required_skill_id}
                onChange={(e) => setForm({ ...form, required_skill_id: Number(e.target.value) })}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              >
                <option value={0}>{t("tasks.noSkill")}</option>
                {skills.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            </label>
            <div className="flex items-center justify-between">
              <button
                type="button"
                onClick={askAi}
                disabled={aiBusy}
                className="rounded-lg bg-violet-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-50"
              >
                {aiBusy ? t("tasks.aiThinking") : t("tasks.aiButton")}
              </button>
            </div>
            {aiSuggestion && (
              <div className="rounded-xl border border-violet-200 bg-violet-50 p-3 text-sm">
                <p className="font-medium text-violet-900">
                  {t("tasks.aiSuggestion", { name: aiSuggestion.employee_name })}
                  <span className="ml-2 font-normal text-violet-600">
                    {t("tasks.aiOpenTasks", { count: aiSuggestion.open_tasks })}
                  </span>
                </p>
                {aiSuggestion.reason && (
                  <p className="mt-1 text-violet-800">{aiSuggestion.reason}</p>
                )}
                <p className="mt-1 text-xs text-violet-500">{t("tasks.aiFieldSet")}</p>
              </div>
            )}
            <label className="block text-sm">
              {t("tasks.employee")}
              <select
                required
                value={form.employee_id}
                onChange={(e) => setForm({ ...form, employee_id: e.target.value })}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              >
                <option value="">{t("tasks.choose")}</option>
                <option value="auto">{t("tasks.autoAssign")}</option>
                {assignableEmployees.map(({ emp, hasSkill }) => (
                  <option key={emp.id} value={emp.id}>
                    {emp.last_name} {emp.first_name}
                    {form.required_skill_id ? (hasSkill ? t("tasks.hasSkill") : t("tasks.noSkillSuffix")) : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              {t("tasks.dueDate")}
              <input required type="date" value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="block text-sm">
                {t("tasks.client")}
                <input value={form.client_name} onChange={(e) => setForm({ ...form, client_name: e.target.value })} placeholder={t("tasks.clientPlaceholder")} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
              <label className="block text-sm">
                {t("tasks.location")}
                <input value={form.client_location} onChange={(e) => setForm({ ...form, client_location: e.target.value })} placeholder={t("tasks.locationPlaceholder")} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
            </div>
            <label className="flex items-start gap-2 rounded-xl border border-orange-200 bg-orange-50 p-3 text-sm text-orange-900">
              <input
                type="checkbox"
                checked={externalService}
                onChange={(e) => setExternalService(e.target.checked)}
                className="mt-0.5 h-4 w-4"
              />
              <span>
                <span className="font-semibold">🔧 {t("tasks.externalCheckbox")}</span>
                <span className="mt-0.5 block text-xs text-orange-700">{t("tasks.externalCheckboxHint")}</span>
              </span>
            </label>
            {externalService && (
              <label className="block text-sm">
                {t("tasks.wsMachine")}
                <SearchSelect
                  items={assetOptions.map((a) => ({ id: a.id, label: a.name, sublabel: a.partner_name, badge: a.barcode }))}
                  value={taskAssetId}
                  onChange={setTaskAssetId}
                  placeholder={t("service.machineSearchPh")}
                  className="mt-1 w-full"
                />
                <span className="mt-0.5 block text-xs text-slate-400">{t("tasks.wsMachineHint")}</span>
              </label>
            )}
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={() => setShowForm(false)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{t("tasks.assign")}</button>
            </div>
          </form>
        </div>
      )}
      {/* KSZ ügyfél-árak szerkesztése (képviselő) */}
      {priceEdit && (
        <div onMouseDown={(e) => { if (e.target === e.currentTarget) setPriceEdit(null); }} className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4">
          <div className="my-8 w-full max-w-lg space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">💰 {t("tasks.priceEditTitle")}</h2>
            <p className="text-xs text-slate-500">
              {priceEdit.task.worksheet_serial} · {t("tasks.priceEditHint")}
            </p>
            {!priceEdit.ws.work_description.trim() && (
              <p className="rounded-xl bg-amber-50 px-3 py-2 text-sm text-amber-800">
                {t("tasks.priceEditNotFilled")}
              </p>
            )}
            {priceEdit.ws.materials.length === 0 ? (
              <p className="rounded-xl bg-amber-50 px-3 py-2 text-sm text-amber-800">
                {t("tasks.priceEditEmpty")}
              </p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-slate-400">
                    <th className="py-1 pr-2">{t("myTasks.wsItemName")}</th>
                    <th className="py-1 pr-2">{t("myTasks.wsQty")}</th>
                    <th className="py-1 pr-2 text-right">{t("myTasks.wsCostNet")}</th>
                    <th className="py-1 text-right">{t("myTasks.wsPriceNet")}</th>
                  </tr>
                </thead>
                <tbody>
                  {priceEdit.ws.materials.map((m, i) => (
                    <tr key={i} className="border-t border-slate-100">
                      <td className="py-1.5 pr-2">{m.name}</td>
                      <td className="py-1.5 pr-2 text-slate-500">{m.qty} {m.unit}</td>
                      <td className="py-1.5 pr-2 text-right text-orange-700">
                        {m.cost_net != null ? `${m.cost_net.toLocaleString("hu-HU")} Ft` : "—"}
                      </td>
                      <td className="py-1.5 text-right">
                        <input
                          type="number" min={0}
                          value={priceEdit.prices[i] ?? ""}
                          onChange={(e) => {
                            const next = [...priceEdit.prices];
                            next[i] = e.target.value;
                            setPriceEdit({ ...priceEdit, prices: next });
                          }}
                          className="w-28 rounded-lg border border-emerald-300 bg-emerald-50 px-2 py-1 text-right text-sm"
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {/* Karbantartási díj: a gépből előtöltve, átírható; kedvezmény = elengedve */}
            <div className="rounded-xl border border-indigo-200 bg-indigo-50 p-3">
              <div className="flex flex-wrap items-center gap-3">
                <label className="text-sm font-medium text-indigo-900">
                  {t("tasks.maintFee")}
                  <input
                    type="number" min={0} step="1"
                    value={priceEdit.fee}
                    onChange={(e) => setPriceEdit({ ...priceEdit, fee: e.target.value })}
                    disabled={priceEdit.ws.invoiced}
                    className="ml-2 w-32 rounded-lg border border-indigo-300 bg-white px-2 py-1 text-right text-sm"
                  /> Ft
                </label>
                <label className="flex items-center gap-2 text-sm text-indigo-900">
                  <input
                    type="checkbox"
                    checked={priceEdit.discount}
                    onChange={(e) => setPriceEdit({ ...priceEdit, discount: e.target.checked })}
                    disabled={priceEdit.ws.invoiced}
                    className="h-4 w-4"
                  />
                  {t("tasks.maintFeeDiscount")}
                </label>
                {priceEdit.ws.invoiced && (
                  <span className="rounded bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800">
                    ✓ {t("tasks.maintFeeInvoiced")}
                  </span>
                )}
              </div>
              <p className="mt-1 text-xs text-indigo-700">{t("tasks.maintFeeHint")}</p>
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button onClick={() => setPriceEdit(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button
                onClick={savePrices}
                disabled={!priceEdit.ws.work_description.trim()}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-40"
              >
                💾 {t("common.save")}
              </button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
