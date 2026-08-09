"use client";

/** Audit napló (csak admin): ki mit csinált és mikor — szűrés művelet,
 *  szereplő és dátum szerint, lapozással. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface AuditItem {
  id: string;
  actor_name: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  detail: Record<string, unknown> | null;
  ip_address: string | null;
  created_at: string;
}

interface AuditPage {
  total: number;
  items: AuditItem[];
}

interface Actor {
  id: string;
  name: string;
}

const PAGE_SIZE = 50;

export default function NaploPage() {
  const { t, lang } = useT();
  const [page, setPage] = useState(0);
  const [data, setData] = useState<AuditPage | null>(null);
  const [actions, setActions] = useState<string[]>([]);
  const [actors, setActors] = useState<Actor[]>([]);
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const fmt = (dt: string) =>
    new Date(dt).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { dateStyle: "short", timeStyle: "medium" });

  useEffect(() => {
    api.get<string[]>("/api/audit/actions").then(setActions).catch(() => {});
    api.get<Actor[]>("/api/audit/actors").then(setActors).catch(() => {});
  }, []);

  const load = useCallback(() => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) });
    if (action) params.set("action", action);
    if (actor) params.set("actor", actor);
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    api.get<AuditPage>(`/api/audit?${params}`).then(setData).catch(() => {});
  }, [page, action, actor, dateFrom, dateTo]);
  useEffect(load, [load]);

  const totalPages = data ? Math.max(Math.ceil(data.total / PAGE_SIZE), 1) : 1;

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("audit.title")}</h1>
        <select
          value={action}
          onChange={(e) => { setAction(e.target.value); setPage(0); }}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          <option value="">{t("audit.allActions")}</option>
          {actions.map((a) => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>
        <select
          value={actor}
          onChange={(e) => { setActor(e.target.value); setPage(0); }}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          <option value="">{t("audit.allActors")}</option>
          {actors.map((a) => (
            <option key={a.id} value={a.id}>{a.name}</option>
          ))}
        </select>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          {t("cons.dateFrom")}
          <input type="date" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setPage(0); }} className="rounded-lg border border-slate-300 px-2 py-1.5" />
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          {t("cons.dateTo")}
          <input type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); setPage(0); }} className="rounded-lg border border-slate-300 px-2 py-1.5" />
        </label>
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-slate-500">
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("audit.when")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("audit.who")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("audit.action")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("audit.entity")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("audit.detail")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">IP</th>
            </tr>
          </thead>
          <tbody>
            {(data?.items ?? []).map((item) => (
              <tr key={item.id} className="border-b border-slate-100 last:border-0 align-top">
                <td className="px-4 py-2.5 whitespace-nowrap text-slate-500">{fmt(item.created_at)}</td>
                <td className="px-4 py-2.5 font-medium">{item.actor_name ?? "—"}</td>
                <td className="px-4 py-2.5"><code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{item.action}</code></td>
                <td className="px-4 py-2.5 text-slate-500">
                  {item.entity_type}
                  {item.entity_id && <span className="ml-1 text-xs text-slate-400">{item.entity_id.slice(0, 12)}</span>}
                </td>
                <td className="max-w-72 px-4 py-2.5 text-xs text-slate-500">
                  {item.detail ? JSON.stringify(item.detail) : "—"}
                </td>
                <td className="px-4 py-2.5 text-xs text-slate-400">{item.ip_address ?? "—"}</td>
              </tr>
            ))}
            {data && data.items.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-10 text-center text-slate-400">{t("audit.empty")}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {data && data.total > PAGE_SIZE && (
        <div className="mt-3 flex items-center justify-between text-sm text-slate-600">
          <span>{t("audit.total", { count: data.total })}</span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(p - 1, 0))}
              disabled={page === 0}
              className="rounded-lg border border-slate-300 px-3 py-1.5 hover:bg-slate-100 disabled:opacity-40"
            >
              ←
            </button>
            <span>{page + 1} / {totalPages}</span>
            <button
              onClick={() => setPage((p) => Math.min(p + 1, totalPages - 1))}
              disabled={page >= totalPages - 1}
              className="rounded-lg border border-slate-300 px-3 py-1.5 hover:bg-slate-100 disabled:opacity-40"
            >
              →
            </button>
          </div>
        </div>
      )}
    </AppShell>
  );
}
