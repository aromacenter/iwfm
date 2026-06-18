"use client";

/** Feladatok (vezetői): online munkalap kiállítása dolgozóra
 * (opcionális skill-szűréssel + AI javaslattal), státuszok, kommentek, PDF. */

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
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
  const { toast, confirm } = useUI();

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
      });
      setShowForm(false);
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
        <button
          onClick={() => setShowForm(true)}
          className="ml-auto rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
        >
          {t("tasks.new")}
        </button>
      </div>

      <div className="space-y-3">
        {tasks.map((task) => (
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
                </div>
                <p className="text-sm text-slate-500">
                  {task.employee_name} · {task.due_date}
                  {task.comments.length > 0 && ` · ${t("tasks.comments", { count: task.comments.length })}`}
                </p>
              </div>
              <div className="flex gap-2">
                {task.worksheet_serial && (
                  <button
                    onClick={() => downloadWorksheet(task)}
                    className="rounded-lg border border-emerald-300 px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-50"
                  >
                    {t("tasks.worksheetPdf")}
                  </button>
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
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={() => setShowForm(false)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{t("tasks.assign")}</button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
