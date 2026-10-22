"use client";

/** Kávégép telemetria-előzmények — rendezett nézet a nyers jelentésekből:
 * 1) pillanatkép a legutóbbi jelentésből (kártyák), 2) események (ügynök-
 * frissítés, újraindulás, hálózat-váltás, hibák), 3) áttekintő táblázat
 * trendsávokkal, soronként lenyitható, szekciókra bontott részletekkel. */

import { useMemo, useState } from "react";
import { useT } from "@/lib/i18n";

export interface TelemetryRow {
  created_at: string;
  data: Record<string, unknown>;
}

const SYSTEM_KEYS = ["agent", "android", "jetinno_app", "uptime_min", "ram_free_mb", "ram_total_mb", "disk_free_mb", "reported_at"];
const NET_KEYS = ["net", "net_kbps", "ip", "ssid", "net_log_at"];
const TRAFFIC_KEYS = ["orders_today", "drinks_ok_today", "counter_total", "counter_days", "order_log_at", "errors_today", "sales_today", "portions_today"];
const LIST_KEYS = ["products_today", "pay_today"];
const HB_KEYS = ["heartbeat"];
const KNOWN = new Set([...SYSTEM_KEYS, ...NET_KEYS, ...TRAFFIC_KEYS, ...HB_KEYS, ...LIST_KEYS]);

function num(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
function str(v: unknown): string {
  return v === null || v === undefined ? "—" : String(v);
}

export default function TelemetryHistory({ rows }: { rows: TelemetryRow[] }) {
  const { t, lang } = useT();
  const loc = lang === "hu" ? "hu-HU" : "en-GB";
  const [open, setOpen] = useState<number | null>(0);
  const fmt = (dt: string) => new Date(dt).toLocaleString(loc, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  const fmtFull = (dt: string) => new Date(dt).toLocaleString(loc, { dateStyle: "short", timeStyle: "short" });
  const uptime = (min: number | null) => {
    if (min == null) return "—";
    const d = Math.floor(min / 1440); const h = Math.floor((min % 1440) / 60); const m = Math.round(min % 60);
    return [d > 0 ? `${d} n` : "", h > 0 || d > 0 ? `${h} ó` : "", `${m} p`].filter(Boolean).join(" ");
  };
  const gb = (mb: number | null) => (mb == null ? "—" : mb >= 1024 ? `${(mb / 1024).toLocaleString(loc, { maximumFractionDigits: 1 })} GB` : `${Math.round(mb)} MB`);
  const speed = (kbps: number | null) => (kbps == null ? "" : kbps >= 1000000 ? `${(kbps / 1048576).toLocaleString(loc, { maximumFractionDigits: 1 })} Gbit/s` : kbps >= 1000 ? `${Math.round(kbps / 1000)} Mbit/s` : `${kbps} kbit/s`);
  const label = (k: string) => { const v = t(`vtel.k.${k}`); return v === `vtel.k.${k}` ? k : v; };

  // időrend: a legfrissebb elöl (a backend így adja), a trendhez fordítva
  const latest = rows[0];
  const chrono = useMemo(() => [...rows].reverse(), [rows]);

  // Események: két egymást követő jelentés összevetéséből
  const events = useMemo(() => {
    const out: { at: string; icon: string; text: string; tone: string }[] = [];
    for (let i = 1; i < chrono.length; i++) {
      const a = chrono[i - 1].data, b = chrono[i].data, at = chrono[i].created_at;
      if (a.agent !== b.agent && b.agent) out.push({ at, icon: "⬆️", text: t("vtel.hist.evAgent", { from: str(a.agent), to: str(b.agent) }), tone: "text-indigo-700" });
      const ua = num(a.uptime_min), ub = num(b.uptime_min);
      if (ua != null && ub != null && ub < ua) out.push({ at, icon: "🔄", text: t("vtel.hist.evRestart", { up: uptime(ub) }), tone: "text-amber-700" });
      if (a.net !== b.net && b.net) out.push({ at, icon: "🌐", text: t("vtel.hist.evNet", { from: str(a.net), to: str(b.net) }), tone: "text-sky-700" });
      if (a.jetinno_app !== b.jetinno_app && b.jetinno_app) out.push({ at, icon: "☕", text: t("vtel.hist.evApp", { from: str(a.jetinno_app), to: str(b.jetinno_app) }), tone: "text-slate-700" });
      const ea = num(a.errors_today) ?? 0, eb = num(b.errors_today) ?? 0;
      if (eb > ea) out.push({ at, icon: "⚠️", text: t("vtel.hist.evErrors", { n: eb }), tone: "text-rose-700" });
    }
    return out.reverse().slice(0, 12);
  }, [chrono, t]);

  // Trendsávok: RAM szabad %, rendelések ma — a legutóbbi 30 jelentés
  const trend = (key: string, pct?: (d: Record<string, unknown>) => number | null) => {
    const vals = chrono.slice(-30).map((r) => (pct ? pct(r.data) : num(r.data[key])));
    const max = Math.max(1, ...vals.map((v) => v ?? 0));
    return (
      <div className="flex h-6 items-end gap-px">
        {vals.map((v, i) => <div key={i} title={v == null ? "—" : String(Math.round(v))} style={{ height: `${v == null ? 2 : Math.max(2, (v / max) * 100)}%` }} className={`w-1.5 rounded-sm ${v == null ? "bg-slate-200" : "bg-indigo-400"}`} />)}
      </div>
    );
  };
  const ramPct = (d: Record<string, unknown>) => { const f = num(d.ram_free_mb), tot = num(d.ram_total_mb); return f != null && tot ? (f / tot) * 100 : null; };

  if (!latest) return <p className="py-6 text-center text-sm text-slate-400">{t("vtel.noHistory")}</p>;
  const L = latest.data;
  const card = (title: string, value: string, sub?: string, tone = "") => (
    <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 shadow-sm">
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{title}</div>
      <div className={`text-base font-semibold ${tone}`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
  const ramFreePct = ramPct(L);
  const ageMin = Math.round((Date.now() - new Date(latest.created_at).getTime()) / 60000);

  const section = (title: string, keys: string[], d: Record<string, unknown>) => {
    const present = keys.filter((k) => d[k] !== undefined);
    if (present.length === 0) return null;
    return (
      <div>
        <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{title}</div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
          {present.map((k) => (
            <div key={k} className="contents">
              <dt className="text-slate-500">{label(k)}</dt>
              <dd className="font-medium text-slate-800">
                {k === "uptime_min" ? uptime(num(d[k])) : k.endsWith("_mb") ? gb(num(d[k])) : k === "net_kbps" ? speed(num(d[k])) : str(d[k])}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    );
  };

  return (
    <div className="space-y-4 text-sm">
      {/* ── Pillanatkép a legutóbbi jelentésből ── */}
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <span className="font-semibold">{t("vtel.hist.snapshot")}</span>
          <span className={`text-xs ${ageMin > 90 ? "text-rose-600" : "text-slate-500"}`}>{t("vtel.hist.lastReport", { at: fmtFull(latest.created_at), min: ageMin })}</span>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {card(t("vtel.k.agent"), str(L.agent), `Android ${str(L.android)}`)}
          {card(t("vtel.k.jetinno_app"), str(L.jetinno_app).replace("jetinnocoffe ", ""))}
          {card(t("vtel.k.uptime_min"), uptime(num(L.uptime_min)))}
          {card(t("vtel.k.net"), str(L.net), speed(num(L.net_kbps)))}
          {card(t("vtel.k.ram_free_mb"), gb(num(L.ram_free_mb)), ramFreePct != null ? `${Math.round(ramFreePct)} % / ${gb(num(L.ram_total_mb))}` : undefined, ramFreePct != null && ramFreePct < 15 ? "text-rose-700" : "")}
          {card(t("vtel.k.disk_free_mb"), gb(num(L.disk_free_mb)), undefined, (num(L.disk_free_mb) ?? 99999) < 500 ? "text-rose-700" : "")}
          {card(t("vtel.k.orders_today"), str(L.orders_today), L.order_log_at ? `${t("vtel.k.order_log_at")}: ${str(L.order_log_at)}` : undefined)}
          {card(t("vtel.k.errors_today"), str(L.errors_today), undefined, (num(L.errors_today) ?? 0) > 0 ? "text-rose-700" : "text-emerald-700")}
          {L.counter_total !== undefined && card(t("vtel.counter"), (num(L.counter_total) ?? 0).toLocaleString(loc), L.counter_days !== undefined ? t("vtel.counterDays", { n: str(L.counter_days) }) : undefined)}
          {L.drinks_ok_today !== undefined && card(t("vtel.k.drinks_ok_today"), str(L.drinks_ok_today), typeof L.pay_today === "object" && L.pay_today ? Object.entries(L.pay_today as Record<string, number>).map(([k, v]) => `${k}: ${v}`).join(" · ") : undefined)}
        </div>
        {Array.isArray(L.products_today) && (L.products_today as { id: number; key: string; count: number }[]).length > 0 && (
          <div className="mt-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
            <div className="text-[10px] uppercase tracking-wide text-slate-500">{t("vtel.hist.productsToday")}</div>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {(L.products_today as { id: number; key: string; count: number }[]).map((p) => (
                <span key={p.id} className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-xs">{(p.key || String(p.id)).replace(/_/g, " ")} <b>×{p.count}</b></span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ── Trendek ── */}
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-xl border border-slate-200 p-2"><div className="mb-1 text-[11px] text-slate-500">{t("vtel.hist.trendRam")}</div>{trend("ram_free_mb", ramPct)}</div>
        <div className="rounded-xl border border-slate-200 p-2"><div className="mb-1 text-[11px] text-slate-500">{t("vtel.hist.trendOrders")}</div>{trend("orders_today")}</div>
      </div>

      {/* ── Események ── */}
      <div>
        <div className="mb-1 font-semibold">{t("vtel.hist.events")}</div>
        {events.length === 0 ? <p className="text-xs text-slate-400">{t("vtel.hist.noEvents")}</p> : (
          <ul className="space-y-0.5 text-xs">
            {events.map((e, i) => <li key={i} className="flex gap-2"><span className="w-24 shrink-0 text-slate-400">{fmt(e.at)}</span><span>{e.icon}</span><span className={e.tone}>{e.text}</span></li>)}
          </ul>
        )}
      </div>

      {/* ── Áttekintő táblázat, lenyitható részletekkel ── */}
      <div>
        <div className="mb-1 font-semibold">{t("vtel.hist.table")} <span className="text-xs font-normal text-slate-400">({rows.length})</span></div>
        <div className="overflow-x-auto rounded-xl border border-slate-200">
          <table className="w-full text-xs">
            <thead><tr className="border-b border-slate-200 text-left text-[10px] uppercase tracking-wide text-slate-500">
              <th className="px-2 py-1.5">{t("vtel.hist.col.time")}</th><th className="px-2 py-1.5">{t("vtel.k.agent")}</th><th className="px-2 py-1.5">{t("vtel.k.uptime_min")}</th><th className="px-2 py-1.5 text-right">{t("vtel.k.ram_free_mb")}</th><th className="px-2 py-1.5 text-right">{t("vtel.k.disk_free_mb")}</th><th className="px-2 py-1.5">{t("vtel.k.net")}</th><th className="px-2 py-1.5 text-right">{t("vtel.k.orders_today")}</th><th className="px-2 py-1.5 text-right">{t("vtel.k.errors_today")}</th><th className="px-2 py-1.5"></th>
            </tr></thead>
            <tbody>
              {rows.map((r, i) => {
                const d = r.data;
                const prev = rows[i + 1]?.data;
                const restarted = prev && num(d.uptime_min) != null && num(prev.uptime_min) != null && (num(d.uptime_min) as number) < (num(prev.uptime_min) as number);
                const agentChanged = prev && prev.agent !== d.agent;
                const isOpen = open === i;
                return (
                  <>
                    <tr key={i} onClick={() => setOpen(isOpen ? null : i)} className={`cursor-pointer border-b border-slate-100 hover:bg-slate-50 ${isOpen ? "bg-indigo-50" : ""}`}>
                      <td className="whitespace-nowrap px-2 py-1.5 font-mono text-slate-600">{fmt(r.created_at)}</td>
                      <td className="px-2 py-1.5">{str(d.agent)}{agentChanged && <span className="ml-1 text-indigo-600">⬆️</span>}</td>
                      <td className="whitespace-nowrap px-2 py-1.5">{uptime(num(d.uptime_min))}{restarted && <span className="ml-1 text-amber-600" title={t("vtel.hist.evRestart", { up: uptime(num(d.uptime_min)) })}>🔄</span>}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{gb(num(d.ram_free_mb))}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{gb(num(d.disk_free_mb))}</td>
                      <td className="px-2 py-1.5">{str(d.net)}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{str(d.orders_today)}</td>
                      <td className={`px-2 py-1.5 text-right tabular-nums ${(num(d.errors_today) ?? 0) > 0 ? "font-semibold text-rose-700" : ""}`}>{str(d.errors_today)}</td>
                      <td className="px-2 py-1.5 text-slate-400">{isOpen ? "▾" : "▸"}</td>
                    </tr>
                    {isOpen && (
                      <tr key={`${i}-d`} className="border-b border-slate-200 bg-slate-50">
                        <td colSpan={9} className="px-3 py-2">
                          <div className="grid gap-3 sm:grid-cols-3">
                            {section(t("vtel.hist.sys"), SYSTEM_KEYS, d)}
                            {section(t("vtel.hist.net"), NET_KEYS, d)}
                            {section(t("vtel.hist.traffic"), TRAFFIC_KEYS, d)}
                            {section(t("vtel.hist.other"), Object.keys(d).filter((k) => !KNOWN.has(k)), d)}
                          </div>
                          {d.heartbeat !== undefined && (
                            <details className="mt-2">
                              <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-wide text-slate-500">{t("vtel.hist.heartbeat")}</summary>
                              <pre className="mt-1 whitespace-pre-wrap break-all rounded-lg bg-slate-900 p-2 font-mono text-[11px] text-slate-100">{str(d.heartbeat)}</pre>
                            </details>
                          )}
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
