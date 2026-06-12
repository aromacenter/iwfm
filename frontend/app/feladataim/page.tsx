"use client";

/** Feladataim (dolgozói, mobilra optimalizálva): kiosztott feladatok,
 * komment, státuszgombok, munkalap kitöltése aláírással. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import SignatureCanvas from "@/components/SignatureCanvas";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";

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
  due_date: string;
  required_skill: { id: number; name: string } | null;
  status: "open" | "done" | "needs_more_work";
  comments: CommentOut[];
  worksheet_serial: string | null;
  worksheet_completed: boolean;
}

interface MaterialRow {
  name: string;
  qty: string;
  unit: string;
}

interface WorksheetForm {
  work_description: string;
  hours_spent: string;
  materials: MaterialRow[];
  client_name: string;
  client_location: string;
  employee_signature: string | null;
  client_signature: string | null;
}

const EMPTY_WS: WorksheetForm = {
  work_description: "",
  hours_spent: "",
  materials: [],
  client_name: "",
  client_location: "",
  employee_signature: null,
  client_signature: null,
};

const STATUS_COLORS: Record<string, string> = {
  open: "bg-sky-100 text-sky-800",
  done: "bg-emerald-100 text-emerald-800",
  needs_more_work: "bg-amber-100 text-amber-800",
};

export default function FeladataimPage() {
  const [tasks, setTasks] = useState<TaskOut[]>([]);
  const [comments, setComments] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [noEmployee, setNoEmployee] = useState(false);
  const [wsTask, setWsTask] = useState<TaskOut | null>(null);
  const [ws, setWs] = useState<WorksheetForm>(EMPTY_WS);
  const [wsError, setWsError] = useState<string | null>(null);
  const [wsBusy, setWsBusy] = useState(false);
  const { t } = useT();

  const load = useCallback(() => {
    api
      .get<TaskOut[]>("/api/me/tasks")
      .then(setTasks)
      .catch((err) => {
        if (err?.code === "employee.none_for_user") setNoEmployee(true);
      });
  }, []);
  useEffect(load, [load]);

  async function sendComment(task: TaskOut) {
    const text = (comments[task.id] ?? "").trim();
    if (!text) return;
    setBusy(task.id);
    try {
      await api.post(`/api/me/tasks/${task.id}/comment`, { text });
      setComments((c) => ({ ...c, [task.id]: "" }));
      load();
    } catch (err) {
      alert(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function setStatus(task: TaskOut, status: "done" | "needs_more_work") {
    setBusy(task.id);
    try {
      const comment = (comments[task.id] ?? "").trim();
      await api.post(`/api/me/tasks/${task.id}/status`, {
        status,
        comment: comment || null,
      });
      setComments((c) => ({ ...c, [task.id]: "" }));
      load();
    } catch (err) {
      alert(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function openWorksheet(task: TaskOut) {
    setWsError(null);
    setWs(EMPTY_WS);
    setWsTask(task);
    if (task.worksheet_serial) {
      try {
        const existing = await api.get<{
          work_description: string;
          materials: MaterialRow[];
          hours_spent: number | null;
          client_name: string | null;
          client_location: string | null;
        }>(`/api/me/tasks/${task.id}/worksheet`);
        setWs({
          work_description: existing.work_description,
          hours_spent: existing.hours_spent != null ? String(existing.hours_spent) : "",
          materials: existing.materials ?? [],
          client_name: existing.client_name ?? "",
          client_location: existing.client_location ?? "",
          employee_signature: null,
          client_signature: null,
        });
      } catch {
        /* friss űrlap marad */
      }
    }
  }

  async function saveWorksheet() {
    if (!wsTask) return;
    if (!ws.work_description.trim()) {
      setWsError(t("myTasks.wsWorkRequired"));
      return;
    }
    setWsBusy(true);
    setWsError(null);
    try {
      await api.put(`/api/me/tasks/${wsTask.id}/worksheet`, {
        work_description: ws.work_description,
        hours_spent: ws.hours_spent ? Number(ws.hours_spent) : null,
        materials: ws.materials.filter((m) => m.name.trim()),
        client_name: ws.client_name || null,
        client_location: ws.client_location || null,
        employee_signature: ws.employee_signature,
        client_signature: ws.client_signature,
      });
      setWsTask(null);
      load();
    } catch (err) {
      setWsError(errorMessage(err));
    } finally {
      setWsBusy(false);
    }
  }

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
      <div className="mx-auto max-w-lg space-y-4">
        <h1 className="text-xl font-bold">{t("myTasks.title")}</h1>
        {tasks.length === 0 && (
          <p className="rounded-2xl border border-slate-200 bg-white px-4 py-10 text-center text-slate-400">
            {t("myTasks.empty")}
          </p>
        )}
        {tasks.map((task) => (
          <section key={task.id} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <h2 className="font-semibold">{task.title}</h2>
              <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[task.status]}`}>
                {t(`tasks.statuses.${task.status}`)}
              </span>
            </div>
            <p className="text-xs text-slate-400">{t("myTasks.due", { date: task.due_date })}</p>
            {task.description && (
              <p className="mt-2 whitespace-pre-wrap text-sm text-slate-600">{task.description}</p>
            )}

            {task.comments.length > 0 && (
              <ul className="mt-3 space-y-1 border-t border-slate-100 pt-2 text-sm">
                {task.comments.map((c) => (
                  <li key={c.id}>
                    <span className="font-medium">{c.author_name ?? "?"}:</span>{" "}
                    <span className="text-slate-600">{c.text}</span>
                  </li>
                ))}
              </ul>
            )}

            <div className="mt-3 space-y-2">
              <button
                onClick={() => openWorksheet(task)}
                className={`w-full rounded-xl border px-4 py-2.5 text-sm font-medium ${
                  task.worksheet_completed
                    ? "border-emerald-300 bg-emerald-50 text-emerald-800 hover:bg-emerald-100"
                    : "border-indigo-300 bg-indigo-50 text-indigo-800 hover:bg-indigo-100"
                }`}
              >
                {task.worksheet_completed
                  ? t("myTasks.worksheetDone", { serial: task.worksheet_serial ?? "" })
                  : task.worksheet_serial
                    ? t("myTasks.fillWorksheetSerial", { serial: task.worksheet_serial })
                    : t("myTasks.fillWorksheet")}
              </button>
              <div className="flex gap-2">
                <input
                  value={comments[task.id] ?? ""}
                  onChange={(e) => setComments((c) => ({ ...c, [task.id]: e.target.value }))}
                  placeholder={t("myTasks.commentPlaceholder")}
                  className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
                <button
                  onClick={() => sendComment(task)}
                  disabled={busy === task.id || !(comments[task.id] ?? "").trim()}
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm hover:bg-slate-100 disabled:opacity-40"
                >
                  {t("common.send")}
                </button>
              </div>
              {task.status !== "done" && (
                <div className="flex gap-2">
                  <button
                    onClick={() => setStatus(task, "done")}
                    disabled={busy === task.id}
                    className="flex-1 rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                  >
                    {t("myTasks.done")}
                  </button>
                  <button
                    onClick={() => setStatus(task, "needs_more_work")}
                    disabled={busy === task.id}
                    className="flex-1 rounded-xl bg-amber-500 px-4 py-2.5 text-sm font-medium text-white hover:bg-amber-600 disabled:opacity-50"
                  >
                    {t("myTasks.needsMoreWork")}
                  </button>
                </div>
              )}
            </div>
          </section>
        ))}
      </div>

      {wsTask && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4">
          <div className="my-6 w-full max-w-lg space-y-3 rounded-2xl bg-white p-5 shadow-xl">
            <h2 className="text-lg font-semibold">{t("myTasks.wsTitle", { title: wsTask.title })}</h2>
            {wsTask.worksheet_serial && (
              <p className="text-xs text-slate-500">{t("myTasks.wsSerial", { serial: wsTask.worksheet_serial })}</p>
            )}
            <label className="block text-sm">
              {t("myTasks.wsWork")}
              <textarea
                value={ws.work_description}
                onChange={(e) => setWs({ ...ws, work_description: e.target.value })}
                rows={4}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                placeholder={t("myTasks.wsWorkPlaceholder")}
              />
            </label>
            <label className="block text-sm">
              {t("myTasks.wsHours")}
              <input
                type="number"
                min={0}
                step={0.5}
                value={ws.hours_spent}
                onChange={(e) => setWs({ ...ws, hours_spent: e.target.value })}
                className="mt-1 w-32 rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>

            <div>
              <div className="mb-1 flex items-center justify-between">
                <span className="text-sm text-slate-600">{t("myTasks.wsMaterials")}</span>
                <button
                  type="button"
                  onClick={() =>
                    setWs({ ...ws, materials: [...ws.materials, { name: "", qty: "1", unit: t("myTasks.wsUnit") }] })
                  }
                  className="text-sm font-medium text-indigo-600 hover:text-indigo-800"
                >
                  {t("myTasks.wsAddItem")}
                </button>
              </div>
              {ws.materials.map((m, i) => (
                <div key={i} className="mb-1 flex gap-1">
                  <input
                    value={m.name}
                    onChange={(e) => {
                      const next = [...ws.materials];
                      next[i] = { ...m, name: e.target.value };
                      setWs({ ...ws, materials: next });
                    }}
                    placeholder={t("myTasks.wsItemName")}
                    className="flex-1 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                  />
                  <input
                    value={m.qty}
                    onChange={(e) => {
                      const next = [...ws.materials];
                      next[i] = { ...m, qty: e.target.value };
                      setWs({ ...ws, materials: next });
                    }}
                    placeholder={t("myTasks.wsQty")}
                    className="w-16 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                  />
                  <input
                    value={m.unit}
                    onChange={(e) => {
                      const next = [...ws.materials];
                      next[i] = { ...m, unit: e.target.value };
                      setWs({ ...ws, materials: next });
                    }}
                    placeholder={t("myTasks.wsUnit")}
                    className="w-14 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                  />
                  <button
                    type="button"
                    onClick={() =>
                      setWs({ ...ws, materials: ws.materials.filter((_, idx) => idx !== i) })
                    }
                    className="px-1 text-slate-400 hover:text-red-600"
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>

            <div className="grid grid-cols-2 gap-2">
              <label className="block text-sm">
                {t("myTasks.wsClient")}
                <input
                  value={ws.client_name}
                  onChange={(e) => setWs({ ...ws, client_name: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                />
              </label>
              <label className="block text-sm">
                {t("myTasks.wsLocation")}
                <input
                  value={ws.client_location}
                  onChange={(e) => setWs({ ...ws, client_location: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                />
              </label>
            </div>

            <SignatureCanvas
              label={wsTask.worksheet_completed ? t("myTasks.wsEmpSigKeep") : t("myTasks.wsEmpSig")}
              onChange={(d) => setWs((w) => ({ ...w, employee_signature: d }))}
            />
            <SignatureCanvas
              label={wsTask.worksheet_completed ? t("myTasks.wsClientSigKeep") : t("myTasks.wsClientSig")}
              onChange={(d) => setWs((w) => ({ ...w, client_signature: d }))}
            />

            {wsError && <p className="text-sm text-red-600">{wsError}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button
                onClick={() => setWsTask(null)}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100"
              >
                {t("common.cancel")}
              </button>
              <button
                onClick={saveWorksheet}
                disabled={wsBusy}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {wsBusy ? t("common.saving") : t("myTasks.wsSave")}
              </button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
