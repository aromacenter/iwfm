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
  description?: string | null;
  worksheet_serial?: string | null;
  worksheet_completed?: boolean;
  asset?: { name?: string | null; barcode?: string | null; partner_name?: string | null } | null;
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

  // Nap-részletező: napra kattintva nagyban mutatja az aznapi teendőket
  const [openDay, setOpenDay] = useState<string | null>(null);

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
                onClick={() => setOpenDay(key)}
                className={`min-h-28 cursor-pointer rounded-2xl border p-2 shadow-sm transition hover:ring-2 hover:ring-indigo-200 ${
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
                      onClick={(e) => e.stopPropagation()}
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

      {/* Nap-részletező: a kiválasztott nap teendői nagyban */}
      {openDay && (
        <div
          onMouseDown={(e) => { if (e.target === e.currentTarget) setOpenDay(null); }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
        >
          <div className="max-h-[85vh] w-full max-w-xl overflow-y-auto rounded-2xl bg-white p-5 shadow-xl">
            <div className="mb-3 flex items-center justify-between gap-3">
              <h2 className="text-lg font-bold capitalize">
                {new Date(`${openDay}T00:00:00`).toLocaleDateString(locale, {
                  year: "numeric", month: "long", day: "numeric", weekday: "long",
                })}
                {openDay === todayIso && (
                  <span className="ml-2 rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-700">
                    {t("cal.today")}
                  </span>
                )}
              </h2>
              <button
                onClick={() => setOpenDay(null)}
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100"
              >
                {t("common.close")}
              </button>
            </div>
            {(byDay.get(openDay) ?? []).length === 0 ? (
              <p className="rounded-xl bg-slate-50 px-4 py-6 text-center text-sm text-slate-400">
                {t("cal.dayEmpty")}
              </p>
            ) : (
              <div className="space-y-3">
                {(byDay.get(openDay) ?? []).map((task) => (
                  <Link
                    key={task.id}
                    href={taskHref}
                    className={`block rounded-xl border p-3 hover:opacity-90 ${STATUS_CHIP[task.status]}`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-semibold">{task.title}</span>
                      <span className="rounded-full bg-white/70 px-2 py-0.5 text-xs font-medium">
                        {t(`tasks.statuses.${task.status}`)}
                      </span>
                    </div>
                    <div className="mt-1 space-y-0.5 text-sm opacity-90">
                      {task.employee_name && <p>👤 {task.employee_name}</p>}
                      {task.asset?.name && (
                        <p>☕ {task.asset.name}{task.asset.barcode ? ` (${task.asset.barcode})` : ""}</p>
                      )}
                      {task.worksheet_serial && (
                        <p>🧾 {task.worksheet_serial}{task.worksheet_completed ? " ✓" : ""}</p>
                      )}
                      {task.description && (
                        <p className="whitespace-pre-line text-xs opacity-80">
                          {task.description.length > 300
                            ? `${task.description.slice(0, 300)}…`
                            : task.description}
                        </p>
                      )}
                    </div>
                  </Link>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </AppShell>
  );
}
