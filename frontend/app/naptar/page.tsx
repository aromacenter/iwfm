"use client";

/** Naptár-főoldal: belépés után mindenki ezt látja — az aktuális hét és
 * alatta a következő hét, napokra bontva az esedékes teendőkkel. Vezetői
 * jogosultsággal az összes feladat, dolgozói nézetben a sajátok. */

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { usePerms } from "@/lib/perms";

interface CalTask {
  id: string;
  title: string;
  status: "open" | "done" | "needs_more_work";
  due_date: string | null;
  employee_name?: string | null;
}

const STATUS_CHIP: Record<CalTask["status"], string> = {
  open: "bg-indigo-50 text-indigo-800 border-indigo-200",
  needs_more_work: "bg-amber-50 text-amber-800 border-amber-200",
  done: "bg-emerald-50 text-emerald-700 border-emerald-200 line-through opacity-70",
};

function iso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function mondayOf(d: Date): Date {
  const out = new Date(d);
  out.setDate(d.getDate() - ((d.getDay() + 6) % 7)); // hétfő-kezdés
  out.setHours(0, 0, 0, 0);
  return out;
}

function addDays(d: Date, n: number): Date {
  const out = new Date(d);
  out.setDate(d.getDate() + n);
  return out;
}

export default function NaptarPage() {
  const { t, lang } = useT();
  const { can } = usePerms();
  const [tasks, setTasks] = useState<CalTask[]>([]);
  const canAll = can("tasks");
  const canMine = can("my_tasks");

  const thisMonday = useMemo(() => mondayOf(new Date()), []);
  const todayIso = iso(new Date());
  const rangeFrom = iso(thisMonday);
  const rangeTo = iso(addDays(thisMonday, 13));

  useEffect(() => {
    if (canAll) {
      api
        .get<CalTask[]>(`/api/tasks?date_from=${rangeFrom}&date_to=${rangeTo}`)
        .then(setTasks)
        .catch(() => {});
    } else if (canMine) {
      api
        .get<CalTask[]>("/api/me/tasks")
        .then((rows) =>
          setTasks(rows.filter((r) => r.due_date && r.due_date >= rangeFrom && r.due_date <= rangeTo)),
        )
        .catch(() => {});
    }
  }, [canAll, canMine, rangeFrom, rangeTo]);

  const byDay = useMemo(() => {
    const m = new Map<string, CalTask[]>();
    for (const task of tasks) {
      if (!task.due_date) continue;
      const key = task.due_date.slice(0, 10);
      m.set(key, [...(m.get(key) ?? []), task]);
    }
    return m;
  }, [tasks]);

  const taskHref = canAll ? "/feladatok" : "/feladataim";
  const locale = lang === "en" ? "en-GB" : "hu-HU";

  function week(offsetDays: number, titleKey: string) {
    const days = Array.from({ length: 7 }, (_, i) => addDays(thisMonday, offsetDays + i));
    return (
      <section className="mb-6">
        <h2 className="mb-2 font-semibold text-slate-700">{t(titleKey)}</h2>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-7">
          {days.map((d) => {
            const key = iso(d);
            const isToday = key === todayIso;
            const dayTasks = byDay.get(key) ?? [];
            return (
              <div
                key={key}
                className={`min-h-28 rounded-2xl border p-2 shadow-sm ${
                  isToday
                    ? "border-indigo-400 bg-indigo-50/60 ring-2 ring-indigo-300"
                    : "border-slate-200 bg-white"
                }`}
              >
                <div className="mb-1 flex items-baseline justify-between">
                  <span className={`text-xs uppercase ${isToday ? "font-bold text-indigo-700" : "text-slate-400"}`}>
                    {d.toLocaleDateString(locale, { weekday: "short" })}
                  </span>
                  <span className={`text-sm ${isToday ? "font-bold text-indigo-700" : "font-medium text-slate-600"}`}>
                    {d.toLocaleDateString(locale, { month: "short", day: "numeric" })}
                  </span>
                </div>
                <div className="space-y-1">
                  {dayTasks.map((task) => (
                    <Link
                      key={task.id}
                      href={taskHref}
                      className={`block truncate rounded-lg border px-1.5 py-1 text-xs hover:opacity-80 ${STATUS_CHIP[task.status]}`}
                      title={task.title + (task.employee_name ? ` — ${task.employee_name}` : "")}
                    >
                      {task.title}
                      {canAll && task.employee_name && (
                        <span className="block truncate text-[10px] opacity-70">{task.employee_name}</span>
                      )}
                    </Link>
                  ))}
                  {dayTasks.length === 0 && (
                    <p className="text-[11px] text-slate-300">{t("cal.noTasks")}</p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>
    );
  }

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">📆 {t("cal.title")}</h1>
        {(canAll || canMine) && (
          <Link
            href={taskHref}
            className="ml-auto rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm hover:bg-slate-100"
          >
            {t("cal.allTasks")}
          </Link>
        )}
      </div>
      {week(0, "cal.thisWeek")}
      {week(7, "cal.nextWeek")}
    </AppShell>
  );
}
