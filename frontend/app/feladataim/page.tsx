"use client";

/** Feladataim (dolgozói, mobilra optimalizálva): a kiosztott feladatok,
 * komment a munkával kapcsolatban, és két státuszgomb:
 * Befejezett / További munkát igényel. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";

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

export default function FeladataimPage() {
  const [tasks, setTasks] = useState<TaskOut[]>([]);
  const [comments, setComments] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [noEmployee, setNoEmployee] = useState(false);

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
      <div className="mx-auto max-w-lg space-y-4">
        <h1 className="text-xl font-bold">Feladataim</h1>
        {tasks.length === 0 && (
          <p className="rounded-2xl border border-slate-200 bg-white px-4 py-10 text-center text-slate-400">
            Most nincs kiosztott feladatod. 🎉
          </p>
        )}
        {tasks.map((t) => (
          <section key={t.id} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <h2 className="font-semibold">{t.title}</h2>
              <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[t.status]}`}>
                {STATUS_LABELS[t.status]}
              </span>
            </div>
            <p className="text-xs text-slate-400">Határidő: {t.due_date}</p>
            {t.description && (
              <p className="mt-2 whitespace-pre-wrap text-sm text-slate-600">{t.description}</p>
            )}

            {t.comments.length > 0 && (
              <ul className="mt-3 space-y-1 border-t border-slate-100 pt-2 text-sm">
                {t.comments.map((c) => (
                  <li key={c.id}>
                    <span className="font-medium">{c.author_name ?? "?"}:</span>{" "}
                    <span className="text-slate-600">{c.text}</span>
                  </li>
                ))}
              </ul>
            )}

            <div className="mt-3 space-y-2">
              <div className="flex gap-2">
                <input
                  value={comments[t.id] ?? ""}
                  onChange={(e) => setComments((c) => ({ ...c, [t.id]: e.target.value }))}
                  placeholder="Komment a munkáról…"
                  className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
                <button
                  onClick={() => sendComment(t)}
                  disabled={busy === t.id || !(comments[t.id] ?? "").trim()}
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm hover:bg-slate-100 disabled:opacity-40"
                >
                  Küldés
                </button>
              </div>
              {t.status !== "done" && (
                <div className="flex gap-2">
                  <button
                    onClick={() => setStatus(t, "done")}
                    disabled={busy === t.id}
                    className="flex-1 rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                  >
                    ✓ Befejezett
                  </button>
                  <button
                    onClick={() => setStatus(t, "needs_more_work")}
                    disabled={busy === t.id}
                    className="flex-1 rounded-xl bg-amber-500 px-4 py-2.5 text-sm font-medium text-white hover:bg-amber-600 disabled:opacity-50"
                  >
                    További munkát igényel
                  </button>
                </div>
              )}
            </div>
          </section>
        ))}
      </div>
    </AppShell>
  );
}
