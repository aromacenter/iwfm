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

interface AdjustmentRow {
  created_at: string;
  warehouse_name: string;
  warehouse_kind: string;
  product_name: string;
  unit: string;
  delta: number;
  actor_name: string | null;
  note: string | null;
}

interface OverrideRow {
  created_at: string;
  actor_name: string | null;
  partner: string | null;
  settlement_id: string | null;
  field: string | null;
  target: string | null;
  product: string | null;
  from: number | null;
  to: number | null;
}

interface Oversight {
  adjustments: AdjustmentRow[];
  overrides: OverrideRow[];
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
  // Felügyeleti riport: leltár-eltérések + elszámoláskor kézzel átírt értékek
  const [view, setView] = useState<"log" | "oversight">("log");
  const [ovDays, setOvDays] = useState(31);
  const [oversight, setOversight] = useState<Oversight | null>(null);

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

  useEffect(() => {
    if (view !== "oversight") return;
    api.get<Oversight>(`/api/audit/oversight?days=${ovDays}`).then(setOversight).catch(() => {});
  }, [view, ovDays]);

  const totalPages = data ? Math.max(Math.ceil(data.total / PAGE_SIZE), 1) : 1;

  const fmtNum = (v: number | null) =>
    v == null ? "—" : v.toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { maximumFractionDigits: 3 });

  if (view === "oversight") {
    return (
      <AppShell>
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-bold">{t("audit.title")}</h1>
          <div className="flex gap-1 rounded-full bg-slate-100 p-1 text-sm">
            <button onClick={() => setView("log")} className="rounded-full px-3 py-1 hover:bg-white">{t("audit.tabLog")}</button>
            <button className="rounded-full bg-white px-3 py-1 font-medium shadow-sm">{t("audit.tabOversight")}</button>
          </div>
          <select
            value={ovDays}
            onChange={(e) => setOvDays(Number(e.target.value))}
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
          >
            {[31, 92, 366].map((d) => (
              <option key={d} value={d}>{t("audit.ovDays", { days: d })}</option>
            ))}
          </select>
        </div>

        <p className="mb-4 max-w-3xl text-sm text-slate-500">{t("audit.ovHint")}</p>

        <div className="mb-6 rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="border-b border-slate-200 px-4 py-3 text-sm font-semibold">
            📋 {t("audit.ovAdjustments")}
            <span className="ml-2 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-normal text-slate-500">{oversight?.adjustments.length ?? 0}</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-slate-500">
                  <th className="border-b border-slate-200 px-4 py-2">{t("audit.when")}</th>
                  <th className="border-b border-slate-200 px-4 py-2">{t("audit.who")}</th>
                  <th className="border-b border-slate-200 px-4 py-2">{t("audit.ovWarehouse")}</th>
                  <th className="border-b border-slate-200 px-4 py-2">{t("audit.ovProduct")}</th>
                  <th className="border-b border-slate-200 px-4 py-2 text-right">{t("audit.ovDelta")}</th>
                  <th className="border-b border-slate-200 px-4 py-2">{t("audit.ovNote")}</th>
                </tr>
              </thead>
              <tbody>
                {(oversight?.adjustments ?? []).map((a, i) => (
                  <tr key={i} className="border-b border-slate-100 last:border-0">
                    <td className="px-4 py-2 whitespace-nowrap text-slate-500">{fmt(a.created_at)}</td>
                    <td className="px-4 py-2 font-medium">{a.actor_name ?? "—"}</td>
                    <td className="px-4 py-2">{a.warehouse_kind === "van" ? "🚐" : "🏬"} {a.warehouse_name}</td>
                    <td className="px-4 py-2">{a.product_name}</td>
                    <td className={`px-4 py-2 text-right font-semibold tabular-nums ${a.delta < 0 ? "text-rose-600" : "text-emerald-600"}`}>
                      {a.delta > 0 ? "+" : ""}{fmtNum(a.delta)} {a.unit}
                    </td>
                    <td className="px-4 py-2 text-xs text-slate-500">{a.note ?? "—"}</td>
                  </tr>
                ))}
                {oversight && oversight.adjustments.length === 0 && (
                  <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-400">{t("audit.ovNoAdjustments")}</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="border-b border-slate-200 px-4 py-3 text-sm font-semibold">
            ✏️ {t("audit.ovOverrides")}
            <span className="ml-2 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-normal text-slate-500">{oversight?.overrides.length ?? 0}</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-slate-500">
                  <th className="border-b border-slate-200 px-4 py-2">{t("audit.when")}</th>
                  <th className="border-b border-slate-200 px-4 py-2">{t("audit.who")}</th>
                  <th className="border-b border-slate-200 px-4 py-2">{t("audit.ovPartner")}</th>
                  <th className="border-b border-slate-200 px-4 py-2">{t("audit.ovField")}</th>
                  <th className="border-b border-slate-200 px-4 py-2">{t("audit.ovProduct")}</th>
                  <th className="border-b border-slate-200 px-4 py-2 text-right">{t("audit.ovChange")}</th>
                </tr>
              </thead>
              <tbody>
                {(oversight?.overrides ?? []).map((o, i) => (
                  <tr key={i} className="border-b border-slate-100 last:border-0">
                    <td className="px-4 py-2 whitespace-nowrap text-slate-500">{fmt(o.created_at)}</td>
                    <td className="px-4 py-2 font-medium">{o.actor_name ?? "—"}</td>
                    <td className="px-4 py-2">{o.partner ?? "—"}</td>
                    <td className="px-4 py-2">
                      <span className="rounded bg-amber-50 px-1.5 py-0.5 text-xs text-amber-800">
                        {o.field ? t(`audit.ovField_${o.field}`) : "—"}
                      </span>
                      {o.target && <span className="ml-1 text-xs text-slate-400">{o.target}</span>}
                    </td>
                    <td className="px-4 py-2">{o.product ?? "—"}</td>
                    <td className="px-4 py-2 text-right tabular-nums">
                      <span className="text-slate-400 line-through">{fmtNum(o.from)}</span>
                      <span className="mx-1 text-slate-400">→</span>
                      <span className="font-semibold text-amber-700">{fmtNum(o.to)}</span>
                    </td>
                  </tr>
                ))}
                {oversight && oversight.overrides.length === 0 && (
                  <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-400">{t("audit.ovNoOverrides")}</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("audit.title")}</h1>
        <div className="flex gap-1 rounded-full bg-slate-100 p-1 text-sm">
          <button className="rounded-full bg-white px-3 py-1 font-medium shadow-sm">{t("audit.tabLog")}</button>
          <button onClick={() => setView("oversight")} className="rounded-full px-3 py-1 hover:bg-white">{t("audit.tabOversight")}</button>
        </div>
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
