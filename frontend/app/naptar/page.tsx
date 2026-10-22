"use client";

/** Naptár-főoldal: belépés után mindenki ezt látja. Két nézet (user-kérés):
 *  - HÓNAP-RÁCS (alapértelmezett): klasszikus naptár, esemény-címkékkel,
 *    hónap-léptetéssel; napra kattintva a nap részletei.
 *  - IDŐVONAL: csak a feladatos napok, az üres időszakok egy sorrá
 *    zsugorodnak — telefonon/terepen ez a kényelmes.
 * Vezetői jogosultsággal az összes feladat, dolgozói nézetben a sajátok. */

import { useEffect, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { usePerms } from "@/lib/perms";
import { useUI } from "@/lib/ui";
import { errorMessage } from "@/lib/api";

interface CalTask {
  id: string;
  title: string;
  status: "open" | "done" | "needs_more_work";
  due_date: string | null;
  employee_name?: string | null;
  description?: string | null;
  worksheet_serial?: string | null;
  worksheet_completed?: boolean;
  client_name?: string | null;
  visit_window?: string | null;
  reschedule_note?: string | null;
  asset?: { name?: string | null; barcode?: string | null; partner_name?: string | null; partner_id?: string | null; partner_visit_window?: string | null } | null;
}

interface EditState {
  task: CalTask;
  due: string;
  window: string;
  note: string;
  saveWindow: boolean;
}

const STATUS_CHIP: Record<CalTask["status"], string> = {
  open: "bg-indigo-50 text-indigo-800 border-indigo-200",
  needs_more_work: "bg-amber-50 text-amber-800 border-amber-200",
  done: "bg-emerald-50 text-emerald-700 border-emerald-200 opacity-75",
};

const STATUS_ICON: Record<CalTask["status"], string> = {
  open: "🧾",
  needs_more_work: "🔧",
  done: "✅",
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
  const { toast } = useUI();
  const [tasks, setTasks] = useState<CalTask[]>([]);
  const [reloadKey, setReloadKey] = useState(0);
  // Szabadszavas kereső: cím, partner, ügyfél, gép, dolgozó, munkalap-szám (21d6ddc1)
  const [q, setQ] = useState("");
  // Szerkesztő-modal: határidő (indokkal), időkapu a partnernél (700ab5b2)
  const [edit, setEdit] = useState<EditState | null>(null);
  const [saving, setSaving] = useState(false);
  const canAll = can("tasks");
  const canMine = can("my_tasks");

  // Nézet-váltó: hónap-rács (alap) / idővonal — a választás megmarad.
  const [view, setView] = useState<"month" | "timeline">("month");
  useEffect(() => {
    try {
      const saved = localStorage.getItem("iwfm-cal-view");
      if (saved === "timeline") setView("timeline");
    } catch { /* tárolás nélkül is megy */ }
  }, []);
  function switchView(v: "month" | "timeline") {
    setView(v);
    try { localStorage.setItem("iwfm-cal-view", v); } catch { /* nem kritikus */ }
  }

  // Hónap-léptetés (csak a rács-nézetben): 0 = aktuális hónap
  const [monthOffset, setMonthOffset] = useState(0);
  const today = useMemo(() => new Date(), []);
  const todayIso = iso(today);
  const monthStart = useMemo(
    () => new Date(today.getFullYear(), today.getMonth() + monthOffset, 1),
    [today, monthOffset],
  );
  // A rács első/utolsó látható napja (hétfő-kezdésű teljes hetek)
  const gridStart = useMemo(() => mondayOf(monthStart), [monthStart]);
  const gridEnd = useMemo(() => {
    const lastDay = new Date(monthStart.getFullYear(), monthStart.getMonth() + 1, 0);
    return addDays(mondayOf(lastDay), 6);
  }, [monthStart]);

  // Idővonal: mától 4 hét előre
  const tlFrom = todayIso;
  const tlTo = iso(addDays(today, 27));

  const rangeFrom = view === "month" ? iso(gridStart) : tlFrom;
  const rangeTo = view === "month" ? iso(gridEnd) : tlTo;

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canAll, canMine, rangeFrom, rangeTo, reloadKey]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return tasks;
    return tasks.filter((task) =>
      [task.title, task.description, task.employee_name, task.client_name, task.worksheet_serial,
        task.asset?.name, task.asset?.barcode, task.asset?.partner_name, task.visit_window]
        .some((v) => (v ?? "").toLowerCase().includes(needle)),
    );
  }, [tasks, q]);

  const byDay = useMemo(() => {
    const m = new Map<string, CalTask[]>();
    for (const task of filtered) {
      if (!task.due_date) continue;
      const key = task.due_date.slice(0, 10);
      m.set(key, [...(m.get(key) ?? []), task]);
    }
    return m;
  }, [filtered]);

  const taskHref = canAll ? "/feladatok" : "/feladataim";
  const locale = lang === "en" ? "en-GB" : "hu-HU";

  // Nap-részletező: napra kattintva nagyban mutatja az aznapi teendőket
  const [openDay, setOpenDay] = useState<string | null>(null);

  function openEdit(task: CalTask) {
    setEdit({
      task,
      due: (task.due_date ?? "").slice(0, 10),
      window: task.visit_window ?? task.asset?.partner_visit_window ?? "",
      note: "",
      saveWindow: false,
    });
  }

  async function saveEdit() {
    if (!edit) return;
    const dueChanged = edit.due !== (edit.task.due_date ?? "").slice(0, 10);
    if (dueChanged && edit.note.trim().length < 3) {
      toast(t("cal.rescheduleNoteRequired"), "error");
      return;
    }
    setSaving(true);
    try {
      const body: Record<string, unknown> = {
        visit_window: edit.window.trim() || null,
        save_window_for_partner: edit.saveWindow,
      };
      if (dueChanged) {
        body.due_date = edit.due;
        body.reschedule_note = edit.note.trim();
      }
      await api.patch(`/api/tasks/${edit.task.id}`, body);
      toast(t("cal.saved"), "success");
      setEdit(null);
      setReloadKey((k) => k + 1);
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setSaving(false);
    }
  }

  // Egy feladat-címke: vezetőnek a szerkesztő nyílik, dolgozónak a saját lista
  function taskChip(task: CalTask, className: string, children: ReactNode) {
    if (canAll) {
      return (
        <button
          type="button"
          key={task.id}
          onClick={(e) => { e.stopPropagation(); openEdit(task); }}
          className={`${className} w-full text-left`}
          title={task.title + (task.employee_name ? ` — ${task.employee_name}` : "")}
        >
          {children}
        </button>
      );
    }
    return (
      <Link key={task.id} href={taskHref} onClick={(e) => e.stopPropagation()} className={className} title={task.title}>
        {children}
      </Link>
    );
  }

  // ── HÓNAP-RÁCS nézet ────────────────────────────────────────────────────
  function monthGrid() {
    const days: Date[] = [];
    for (let d = new Date(gridStart); d <= gridEnd; d = addDays(d, 1)) days.push(new Date(d));
    const dayNames = Array.from({ length: 7 }, (_, i) =>
      addDays(gridStart, i).toLocaleDateString(locale, { weekday: "short" }),
    );
    return (
      <section>
        <div className="mb-1 grid grid-cols-7 gap-1.5">
          {dayNames.map((n) => (
            <div key={n} className="px-1 text-center text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              {n}
            </div>
          ))}
        </div>
        <div className="grid grid-cols-7 gap-1.5">
          {days.map((d) => {
            const key = iso(d);
            const isToday = key === todayIso;
            const inMonth = d.getMonth() === monthStart.getMonth();
            const dayTasks = byDay.get(key) ?? [];
            const shown = dayTasks.slice(0, 2);
            return (
              <div
                key={key}
                onClick={() => setOpenDay(key)}
                className={`min-h-[5.5rem] cursor-pointer rounded-xl border p-1.5 transition hover:ring-2 hover:ring-indigo-200 ${
                  isToday
                    ? "border-indigo-400 bg-indigo-50 ring-1 ring-indigo-300"
                    : "border-slate-200 bg-white"
                } ${inMonth ? "" : "opacity-40"}`}
              >
                <div className={`text-xs ${isToday ? "font-bold text-indigo-700" : "font-medium text-slate-500"}`}>
                  {d.getDate()}
                </div>
                <div className="mt-1 space-y-1">
                  {shown.map((task) =>
                    taskChip(
                      task,
                      `block truncate rounded-md border px-1 py-0.5 text-[10.5px] font-medium hover:opacity-80 ${STATUS_CHIP[task.status]}`,
                      <>{STATUS_ICON[task.status]} {task.title}{task.visit_window ? ` ⏰${task.visit_window}` : ""}</>,
                    ),
                  )}
                  {dayTasks.length > 2 && (
                    <p className="px-1 text-[10px] text-slate-400">{t("cal.more", { n: dayTasks.length - 2 })}</p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>
    );
  }

  // ── IDŐVONAL nézet: csak a feladatos napok, az üresek összezsugorodnak ──
  function timeline() {
    const rows: { kind: "day"; key: string; tasks: CalTask[] }[] = [];
    const gaps: { afterIdx: number; from: string; to: string; days: number }[] = [];
    let gapStart: Date | null = null;
    for (let d = new Date(today); iso(d) <= tlTo; d = addDays(d, 1)) {
      const key = iso(d);
      const dayTasks = byDay.get(key) ?? [];
      if (dayTasks.length > 0) {
        if (gapStart) {
          const days = Math.round((d.getTime() - gapStart.getTime()) / 86400000);
          gaps.push({ afterIdx: rows.length - 1, from: iso(gapStart), to: iso(addDays(d, -1)), days });
          gapStart = null;
        }
        rows.push({ kind: "day", key, tasks: dayTasks });
      } else if (!gapStart) {
        gapStart = new Date(d);
      }
    }
    const gapByIdx = new Map(gaps.map((g) => [g.afterIdx, g]));
    const fmtShort = (k: string) =>
      new Date(`${k}T00:00:00`).toLocaleDateString(locale, { month: "short", day: "numeric", weekday: "long" });
    return (
      <section className="relative pl-7">
        {/* függőleges vonal */}
        <div aria-hidden className="absolute bottom-2 left-2 top-2 w-0.5 rounded bg-slate-200" />
        {rows.length === 0 && (
          <p className="rounded-2xl border border-dashed border-slate-300 bg-white px-4 py-8 text-center text-sm text-slate-400">
            {t("cal.tlEmpty")}
          </p>
        )}
        {gapByIdx.has(-1) && (
          <p className="mb-4 text-xs text-slate-400">
            ◌ {t("cal.tlGap", { from: fmtShort(gapByIdx.get(-1)!.from), n: gapByIdx.get(-1)!.days })}
          </p>
        )}
        {rows.map((row, i) => (
          <div key={row.key} className="mb-5">
            <div className="relative">
              <span
                aria-hidden
                className={`absolute -left-[1.42rem] top-1 h-2.5 w-2.5 rounded-full ${
                  row.key === todayIso ? "bg-indigo-500 ring-4 ring-indigo-100" : "bg-slate-300"
                }`}
              />
              <button onClick={() => setOpenDay(row.key)} className="mb-2 text-left">
                <span className="text-sm font-bold capitalize text-slate-700 hover:text-indigo-700">
                  {row.key === todayIso && <span className="mr-1 rounded bg-indigo-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-indigo-700">{t("cal.today")}</span>}
                  {fmtShort(row.key)}
                </span>
                <span className="ml-2 text-xs text-slate-400">{t("cal.taskCount", { n: row.tasks.length })}</span>
              </button>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              {row.tasks.map((task) =>
                taskChip(
                  task,
                  `block rounded-xl border p-2.5 text-sm hover:opacity-85 ${STATUS_CHIP[task.status]}`,
                  <>
                    <span className="font-semibold">{STATUS_ICON[task.status]} {task.title}</span>
                    <span className="mt-0.5 block truncate text-xs opacity-80">
                      {[
                        (task.asset?.partner_name || task.client_name) && `📍 ${task.asset?.partner_name ?? task.client_name}`,
                        task.visit_window && `⏰ ${task.visit_window}`,
                        canAll && task.employee_name && `👤 ${task.employee_name}`,
                      ].filter(Boolean).join(" · ")}
                    </span>
                  </>,
                ),
              )}
            </div>
            {gapByIdx.has(i) && (
              <p className="mt-4 text-xs text-slate-400">
                ◌ {t("cal.tlGap", { from: fmtShort(gapByIdx.get(i)!.from), n: gapByIdx.get(i)!.days })}
              </p>
            )}
          </div>
        ))}
      </section>
    );
  }

  const monthLabel = monthStart.toLocaleDateString(locale, { year: "numeric", month: "long" });

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">📆 {t("cal.title")}</h1>
        {/* nézet-váltó: hónap-rács (alap) / idővonal */}
        <div className="flex overflow-hidden rounded-lg border border-slate-300 bg-white text-sm">
          <button
            onClick={() => switchView("month")}
            className={`px-3 py-1.5 ${view === "month" ? "bg-indigo-600 font-medium text-white" : "text-slate-600 hover:bg-slate-50"}`}
          >
            🗓 {t("cal.viewMonth")}
          </button>
          <button
            onClick={() => switchView("timeline")}
            className={`px-3 py-1.5 ${view === "timeline" ? "bg-indigo-600 font-medium text-white" : "text-slate-600 hover:bg-slate-50"}`}
          >
            📜 {t("cal.viewTimeline")}
          </button>
        </div>
        {view === "month" && (
          <div className="flex items-center gap-1">
            <button onClick={() => setMonthOffset((o) => o - 1)} title={t("cal.prevMonth")} className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm hover:bg-slate-100">‹</button>
            <button onClick={() => setMonthOffset(0)} className={`rounded-lg border px-3 py-1.5 text-sm ${monthOffset === 0 ? "border-indigo-300 bg-indigo-50 font-medium text-indigo-700" : "border-slate-300 bg-white hover:bg-slate-100"}`}>
              {t("cal.today")}
            </button>
            <button onClick={() => setMonthOffset((o) => o + 1)} title={t("cal.nextMonth")} className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm hover:bg-slate-100">›</button>
            <span className="ml-2 text-sm font-semibold capitalize text-slate-700">{monthLabel}</span>
          </div>
        )}
        {(canAll || canMine) && (
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={`🔍 ${t("cal.search")}`}
            className="ml-auto w-56 max-w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
          />
        )}
        {(canAll || canMine) && (
          <Link
            href={taskHref}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm hover:bg-slate-100"
          >
            {t("cal.allTasks")}
          </Link>
        )}
      </div>
      {q.trim() && (
        <p className="mb-2 text-xs text-slate-500">{t("cal.searchHits", { n: filtered.length })}</p>
      )}

      {view === "month" ? monthGrid() : timeline()}

      {/* Nap-részletező: a kiválasztott nap teendői nagyban */}
      {openDay && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="relative max-h-[85vh] w-full max-w-xl overflow-y-auto rounded-2xl bg-white p-5 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => setOpenDay(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
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
                      <span className="flex items-center gap-1.5">
                        {canAll && (
                          <button
                            type="button"
                            onClick={(e) => { e.preventDefault(); e.stopPropagation(); openEdit(task); }}
                            className="rounded-full bg-white px-2 py-0.5 text-xs font-medium text-indigo-700 ring-1 ring-indigo-200 hover:bg-indigo-50"
                          >
                            ✏️ {t("cal.edit")}
                          </button>
                        )}
                        <span className="rounded-full bg-white px-2 py-0.5 text-xs font-medium">
                          {t(`tasks.statuses.${task.status}`)}
                        </span>
                      </span>
                    </div>
                    <div className="mt-1 space-y-0.5 text-sm opacity-90">
                      {task.employee_name && <p>👤 {task.employee_name}</p>}
                      {(task.visit_window || task.asset?.partner_visit_window) && (
                        <p>⏰ {task.visit_window ?? task.asset?.partner_visit_window}</p>
                      )}
                      {task.reschedule_note && <p className="text-xs opacity-80">🔁 {task.reschedule_note}</p>}
                      {task.asset?.name && (
                        <p>☕ {task.asset.name}{task.asset.barcode ? ` (${task.asset.barcode})` : ""}</p>
                      )}
                      {(task.asset?.partner_name || task.client_name) && (
                        <p className="font-medium">📍 {task.asset?.partner_name ?? task.client_name}</p>
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

      {/* Szerkesztő: határidő (indokkal), időkapu — a partnerhez is menthető */}
      {edit && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4">
          <form
            onSubmit={(e) => { e.preventDefault(); void saveEdit(); }}
            className="relative w-full max-w-md space-y-3 rounded-2xl bg-white p-5 shadow-xl"
          >
            <h2 className="text-lg font-bold">✏️ {t("cal.editTitle")}</h2>
            <p className="text-sm font-medium text-slate-700">{edit.task.title}</p>
            {(edit.task.asset?.partner_name || edit.task.client_name) && (
              <p className="text-xs text-slate-500">📍 {edit.task.asset?.partner_name ?? edit.task.client_name}</p>
            )}
            <label className="block text-sm">
              {t("cal.dueDate")}
              <input
                type="date"
                value={edit.due}
                onChange={(e) => setEdit({ ...edit, due: e.target.value })}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            {edit.due !== (edit.task.due_date ?? "").slice(0, 10) && (
              <label className="block text-sm">
                {t("cal.rescheduleNote")} <span className="text-rose-600">*</span>
                <input
                  value={edit.note}
                  onChange={(e) => setEdit({ ...edit, note: e.target.value })}
                  placeholder={t("cal.rescheduleNotePh")}
                  required
                  minLength={3}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                />
              </label>
            )}
            <label className="block text-sm">
              {t("cal.visitWindow")}
              <input
                value={edit.window}
                onChange={(e) => setEdit({ ...edit, window: e.target.value })}
                placeholder={t("cal.visitWindowPh")}
                maxLength={64}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
              {edit.task.asset?.partner_visit_window && (
                <span className="mt-1 block text-xs text-slate-400">{t("cal.partnerWindow", { w: edit.task.asset.partner_visit_window })}</span>
              )}
            </label>
            {edit.task.asset?.partner_id && (
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={edit.saveWindow}
                  onChange={(e) => setEdit({ ...edit, saveWindow: e.target.checked })}
                  className="h-4 w-4"
                />
                {t("cal.saveWindowPartner")}
              </label>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setEdit(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={saving} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{saving ? t("common.saving") : t("common.save")}</button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
