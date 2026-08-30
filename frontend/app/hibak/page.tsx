"use client";

/** Admin hibasor (triázs): szűrhető lista súlyosság/oldal/bejelentő/státusz
 * szerint, a képernyőkép kattintásra nagyban. A reggeli triázs-menet: az új
 * bejelentések megerősítése / duplikálása / elutasítása, kötegelés javítási
 * feladatba, majd resolved-dal zárul a fejlesztői kör. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface Bug {
  id: string;
  page_url: string;
  description: string;
  severity: string;
  status: string;
  reporter_name: string;
  user_agent: string | null;
  has_screenshot: boolean;
  fix_group: string | null;
  resolution_note: string | null;
  created_at: string;
}

const SEVERITIES = ["blocker", "major", "minor", "cosmetic"];
const STATUSES = ["new", "confirmed", "duplicate", "rejected", "resolved", "closed", "reopened"];

const SEVERITY_CHIP: Record<string, string> = {
  blocker: "bg-rose-100 text-rose-800",
  major: "bg-amber-100 text-amber-800",
  minor: "bg-sky-100 text-sky-800",
  cosmetic: "bg-slate-100 text-slate-600",
};

export default function HibakPage() {
  const { t } = useT();
  const { toast, prompt } = useUI();
  const [bugs, setBugs] = useState<Bug[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [severityFilter, setSeverityFilter] = useState("");
  const [shot, setShot] = useState<Bug | null>(null);

  const load = useCallback(() => {
    const params = new URLSearchParams();
    if (statusFilter) params.set("status", statusFilter);
    if (severityFilter) params.set("severity", severityFilter);
    api.get<Bug[]>(`/api/bugs?${params.toString()}`).then(setBugs).catch(() => {});
  }, [statusFilter, severityFilter]);
  useEffect(load, [load]);

  async function patch(id: string, body: Record<string, unknown>) {
    try {
      const upd = await api.patch<Bug>(`/api/bugs/${id}`, body);
      setBugs((rows) => rows.map((b) => (b.id === id ? upd : b)));
      toast(t("common.saved"), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function setGroup(b: Bug) {
    const value = await prompt(t("bugs.groupPrompt"), { initial: b.fix_group ?? "" });
    if (value === null) return;
    await patch(b.id, { fix_group: value || null });
  }

  async function resolve(b: Bug) {
    const note = await prompt(t("bugs.resolveNotePrompt"), { initial: b.resolution_note ?? "" });
    if (note === null) return;
    await patch(b.id, { status: "resolved", resolution_note: note || null });
  }

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">🐞 {t("bugs.adminTitle")}</h1>
        <div className="ml-auto flex gap-2">
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm">
            <option value="">{t("bugs.allStatuses")}</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>{t(`bugs.status.${s}`)}</option>
            ))}
          </select>
          <select value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm">
            <option value="">{t("bugs.allSeverities")}</option>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>{t(`bugs.sev.${s}`)}</option>
            ))}
          </select>
        </div>
      </div>

      {bugs.length === 0 && (
        <p className="rounded-2xl border border-slate-200 bg-white px-4 py-10 text-center text-slate-400">
          {t("bugs.adminEmpty")}
        </p>
      )}

      <div className="space-y-2">
        {bugs.map((b) => (
          <div key={b.id} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex flex-wrap items-start gap-3">
              {b.has_screenshot && (
                <button onClick={() => setShot(b)} title={t("bugs.viewShot")} className="shrink-0">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={`/api/bugs/${b.id}/screenshot`}
                    alt=""
                    className="h-16 w-24 rounded-lg border border-slate-200 object-cover hover:ring-2 hover:ring-indigo-400"
                  />
                </button>
              )}
              <div className="min-w-0 flex-1">
                <div className="mb-1 flex flex-wrap items-center gap-1.5">
                  <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${SEVERITY_CHIP[b.severity]}`}>
                    {t(`bugs.sev.${b.severity}`)}
                  </span>
                  <span className="text-xs text-slate-500">
                    {b.reporter_name} · {b.created_at.slice(0, 16).replace("T", " ")}
                  </span>
                  {b.fix_group && (
                    <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-700">
                      📦 {b.fix_group}
                    </span>
                  )}
                </div>
                <p className="whitespace-pre-line text-sm">{b.description}</p>
                <p className="mt-1 truncate text-xs text-slate-400">
                  {b.page_url}
                  {b.user_agent && ` · ${b.user_agent.slice(0, 60)}`}
                </p>
                {b.resolution_note && (
                  <p className="mt-1 text-xs text-emerald-700">💬 {b.resolution_note}</p>
                )}
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1.5">
                <select
                  value={b.status}
                  onChange={(e) =>
                    e.target.value === "resolved" ? resolve(b) : patch(b.id, { status: e.target.value })
                  }
                  className="rounded-lg border border-slate-300 px-2 py-1 text-xs font-medium"
                >
                  {STATUSES.map((s) => (
                    <option key={s} value={s}>{t(`bugs.status.${s}`)}</option>
                  ))}
                </select>
                <button onClick={() => setGroup(b)} className="text-xs font-medium text-indigo-600 hover:underline">
                  📦 {t("bugs.group")}
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      {shot && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6" onClick={() => setShot(null)}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={`/api/bugs/${shot.id}/screenshot`}
            alt=""
            className="max-h-full max-w-full rounded-xl shadow-2xl"
          />
        </div>
      )}
    </AppShell>
  );
}
