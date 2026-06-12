"use client";

/** Feladatok (vezetői): kiosztás dolgozóra (opcionális skill-szűréssel),
 * státuszok követése, kommentek. A skill-mező a későbbi AI-alapú
 * kiosztás előkészítése. */

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, downloadFile, errorMessage } from "@/lib/api";
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

const STATUS_LABELS: Record<string, string> = {
  open: "Nyitott",
  done: "Befejezett",
  needs_more_work: "További munkát igényel",
};

const STATUS_COLORS: Record<string, string> = {
  open: "bg-sky-100 text-sky-800",
  done: "bg-emerald-100 text-emerald-800",
  needs_more_work: "bg-amber-100 text-amber-800",
};

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

  // Ha skill van választva, a hozzáértő dolgozók előre sorolva
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
      setError("Előbb add meg a feladat címét — abból dolgozik az AI.");
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
      const created = await api.post<TaskOut>("/api/tasks", {
        title: form.title,
        description: form.description || null,
        employee_id: form.employee_id,
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
      if (created.worksheet_serial) {
        alert(`Munkalap kiállítva: ${created.worksheet_serial}\nA dolgozó a telefonján tölti ki a munkavégzés után.`);
      }
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
      alert(errorMessage(err));
    }
  }

  async function downloadWorksheet(task: TaskOut) {
    try {
      await downloadFile(
        `/api/tasks/${task.id}/worksheet/pdf`,
        `${task.worksheet_serial ?? "munkalap"}.pdf`
      );
    } catch (err) {
      alert(errorMessage(err));
    }
  }

  async function remove(task: TaskOut) {
    if (!confirm(`Törlöd a(z) „${task.title}" feladatot?`)) return;
    try {
      await api.delete(`/api/tasks/${task.id}`);
      load();
    } catch (err) {
      alert(errorMessage(err));
    }
  }

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">Feladatok</h1>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          <option value="">Minden státusz</option>
          {Object.entries(STATUS_LABELS).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        <button
          onClick={() => setShowForm(true)}
          className="ml-auto rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
        >
          + Új munkalap / feladat
        </button>
      </div>

      <div className="space-y-3">
        {tasks.map((t) => (
          <div key={t.id} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex flex-wrap items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold">{t.title}</span>
                  <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[t.status]}`}>
                    {STATUS_LABELS[t.status]}
                  </span>
                  {t.required_skill && (
                    <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs text-indigo-700">
                      {t.required_skill.name}
                    </span>
                  )}
                  {t.worksheet_serial && (
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs ${
                        t.worksheet_completed
                          ? "bg-emerald-50 text-emerald-700"
                          : "bg-amber-50 text-amber-700"
                      }`}
                      title={t.worksheet_completed ? "A dolgozó kitöltötte" : "Kiállítva — kitöltésre vár"}
                    >
                      📝 {t.worksheet_serial} · {t.worksheet_completed ? "kitöltve" : "kiállítva"}
                    </span>
                  )}
                </div>
                <p className="text-sm text-slate-500">
                  {t.employee_name} · {t.due_date}
                  {t.comments.length > 0 && ` · ${t.comments.length} komment`}
                </p>
              </div>
              <div className="flex gap-2">
                {t.worksheet_serial && (
                  <button
                    onClick={() => downloadWorksheet(t)}
                    className="rounded-lg border border-emerald-300 px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-50"
                  >
                    Munkalap PDF
                  </button>
                )}
                {t.status !== "open" && (
                  <button
                    onClick={() => setStatus(t, "open")}
                    className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs hover:bg-slate-100"
                  >
                    Újranyitás
                  </button>
                )}
                {t.status !== "done" && (
                  <button
                    onClick={() => setStatus(t, "done")}
                    className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700"
                  >
                    Lezárás
                  </button>
                )}
                <button
                  onClick={() => setExpanded(expanded === t.id ? null : t.id)}
                  className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs hover:bg-slate-100"
                >
                  {expanded === t.id ? "Bezár" : "Részletek"}
                </button>
                <button
                  onClick={() => remove(t)}
                  className="rounded-lg border border-red-200 px-3 py-1.5 text-xs text-red-600 hover:bg-red-50"
                >
                  Törlés
                </button>
              </div>
            </div>
            {expanded === t.id && (
              <div className="mt-3 border-t border-slate-100 pt-3 text-sm">
                {t.description && <p className="mb-2 whitespace-pre-wrap text-slate-600">{t.description}</p>}
                {t.comments.length === 0 ? (
                  <p className="text-slate-400">Nincs komment.</p>
                ) : (
                  <ul className="space-y-1">
                    {t.comments.map((c) => (
                      <li key={c.id}>
                        <span className="font-medium">{c.author_name ?? "?"}:</span>{" "}
                        <span className="text-slate-600">{c.text}</span>{" "}
                        <span className="text-xs text-slate-400">
                          {new Date(c.created_at).toLocaleString("hu-HU", { dateStyle: "short", timeStyle: "short" })}
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
            Nincs feladat.
          </p>
        )}
      </div>

      {showForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={submit} className="w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">📝 Új munkalap kiállítása</h2>
            <p className="text-xs text-slate-500">
              A feladat online munkalapként áll ki (automatikus ML-sorszámmal) — az elvégzett
              munkát, anyagokat és aláírásokat a dolgozó tölti ki a telefonján.
            </p>
            <label className="block text-sm">
              Feladat címe *
              <input required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              Leírás
              <textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={3} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              Szükséges skill (opcionális)
              <select
                value={form.required_skill_id}
                onChange={(e) => setForm({ ...form, required_skill_id: Number(e.target.value) })}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              >
                <option value={0}>— nincs —</option>
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
                {aiBusy ? "AI gondolkodik…" : "✨ AI javaslat: kit bízzunk meg?"}
              </button>
            </div>
            {aiSuggestion && (
              <div className="rounded-xl border border-violet-200 bg-violet-50 p-3 text-sm">
                <p className="font-medium text-violet-900">
                  ✨ Javaslat: {aiSuggestion.employee_name}
                  <span className="ml-2 font-normal text-violet-600">
                    ({aiSuggestion.open_tasks} nyitott feladata van)
                  </span>
                </p>
                {aiSuggestion.reason && (
                  <p className="mt-1 text-violet-800">{aiSuggestion.reason}</p>
                )}
                <p className="mt-1 text-xs text-violet-500">
                  A dolgozó-mezőt beállítottam — szabadon átírhatod.
                </p>
              </div>
            )}
            <label className="block text-sm">
              Dolgozó *
              <select
                required
                value={form.employee_id}
                onChange={(e) => setForm({ ...form, employee_id: e.target.value })}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              >
                <option value="">Válassz…</option>
                {assignableEmployees.map(({ emp, hasSkill }) => (
                  <option key={emp.id} value={emp.id}>
                    {emp.last_name} {emp.first_name}
                    {form.required_skill_id ? (hasSkill ? " ✓ (van skillje)" : " — nincs skillje") : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              Határidő (nap) *
              <input required type="date" value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-sm">
                Ügyfél (opcionális)
                <input value={form.client_name} onChange={(e) => setForm({ ...form, client_name: e.target.value })} placeholder="pl. Minta Kft." className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
              <label className="block text-sm">
                Helyszín (opcionális)
                <input value={form.client_location} onChange={(e) => setForm({ ...form, client_location: e.target.value })} placeholder="cím / telephely" className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
            </div>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={() => setShowForm(false)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">Mégsem</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">Kiosztás</button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
