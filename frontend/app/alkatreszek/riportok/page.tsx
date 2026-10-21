"use client";

/** Alkatrész-riportok (ERP 3. kör, 1d553d2e): fogyás (cikk / szerelő / gép /
 * partner), készlet-egészség (forgás, halott készlet, öregedés), készletérték
 * mozgó átlagáron, ABC újraszámolás, lejáró garanciák. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface Cons { key: string; label: string; sub: string | null; quantity: number; lines: number; cost: number; revenue: number }
interface Health { part_id: string; sku: string; name: string; unit: string; quantity: number; value: number; used_90d: number; used_365d: number; turnover: number | null; days_since_receipt: number | null; days_since_use: number | null; dead: boolean; abc_class: string | null }
interface Valuation { total_value: number; total_items: number; rows: { part_id: string; sku: string; name: string; unit: string; quantity: number; avg_cost: number | null; value: number }[]; by_warehouse: { warehouse_id: string; name: string; kind: string; value: number }[] }
interface Warranty { movement_id: string; created_at: string; sku: string; name: string; quantity: number; unit: string; serial_no: string | null; warranty_until: string | null; warranty_active: boolean; worksheet_serial: string | null; asset_name: string | null; asset_barcode: string | null }

type Tab = "consumption" | "health" | "valuation" | "warranty";
type Group = "part" | "employee" | "asset" | "partner";

function iso(d: Date) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }

export default function RiportokPage() {
  const { t, lang } = useT();
  const { toast } = useUI();
  const loc = lang === "hu" ? "hu-HU" : "en-GB";
  const ft = (n: number | null | undefined) => (n == null ? "—" : `${Math.round(n).toLocaleString(loc)} Ft`);
  const [tab, setTab] = useState<Tab>("consumption");
  const [group, setGroup] = useState<Group>("part");
  const [from, setFrom] = useState(() => iso(new Date(new Date().getFullYear(), new Date().getMonth(), 1)));
  const [to, setTo] = useState(() => iso(new Date()));
  const [cons, setCons] = useState<Cons[]>([]);
  const [health, setHealth] = useState<Health[]>([]);
  const [val, setVal] = useState<Valuation | null>(null);
  const [warr, setWarr] = useState<Warranty[]>([]);
  const [days, setDays] = useState(30);
  const [inclExpired, setInclExpired] = useState(false);
  const [busy, setBusy] = useState(false);

  const loadCons = useCallback(() => {
    api.get<Cons[]>(`/api/parts/reports/consumption?from=${from}&to=${to}&group=${group}`).then(setCons).catch((e) => toast(errorMessage(e), "error"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from, to, group]);
  useEffect(() => { loadCons(); }, [loadCons]);
  useEffect(() => {
    api.get<Health[]>("/api/parts/reports/health").then(setHealth).catch(() => {});
    api.get<Valuation>("/api/parts/valuation").then(setVal).catch(() => {});
  }, []);
  useEffect(() => {
    api.get<Warranty[]>(`/api/parts/warranty?days=${days}&include_expired=${inclExpired}`).then(setWarr).catch(() => {});
  }, [days, inclExpired]);

  async function recomputeAbc() {
    setBusy(true);
    try {
      const r = await api.post<{ A: number; B: number; C: number }>("/api/parts/abc/recompute?months=12", {});
      toast(t("preports.abcDone", { a: r.A, b: r.B, c: r.C }), "success");
      setHealth(await api.get<Health[]>("/api/parts/reports/health"));
    } catch (err) { toast(errorMessage(err), "error"); } finally { setBusy(false); }
  }

  const tabBtn = (key: Tab, label: string) => (
    <button key={key} onClick={() => setTab(key)} className={`px-3 py-1.5 text-sm ${tab === key ? "bg-indigo-600 text-white" : "bg-white text-slate-600 hover:bg-slate-100"}`}>{label}</button>
  );
  const daysTxt = (n: number | null) => (n == null ? t("preports.never") : t("preports.days", { n }));
  const abcChip = (c: string | null) => c ? <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${c === "A" ? "bg-emerald-100 text-emerald-800" : c === "B" ? "bg-amber-100 text-amber-800" : "bg-slate-100 text-slate-600"}`}>{c}</span> : null;
  const th = "px-3 py-2";

  return (
    <AppShell>
      <div className="mb-4">
        <h1 className="text-xl font-semibold">📊 {t("preports.title")}</h1>
        <p className="text-sm text-slate-500">{t("preports.subtitle")}</p>
      </div>
      <div className="mb-4 flex overflow-hidden rounded-lg border border-slate-300 text-sm w-fit">
        {tabBtn("consumption", t("preports.tab.consumption"))}{tabBtn("health", t("preports.tab.health"))}{tabBtn("valuation", t("preports.tab.valuation"))}{tabBtn("warranty", `${t("preports.tab.warranty")}${warr.length ? ` (${warr.length})` : ""}`)}
      </div>

      {tab === "consumption" && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm" /><span className="text-slate-400">→</span>
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm" />
            <span className="ml-2 text-xs text-slate-500">{t("preports.group")}:</span>
            <div className="inline-flex rounded-xl bg-indigo-50 p-1">{(["part", "employee", "asset", "partner"] as Group[]).map((g) => <button key={g} onClick={() => setGroup(g)} className={`rounded-lg px-3 py-1 text-sm ${group === g ? "bg-indigo-600 text-white" : "text-indigo-800 hover:bg-indigo-100"}`}>{t(`preports.group.${g}`)}</button>)}</div>
          </div>
          <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
            {cons.length === 0 ? <p className="p-8 text-center text-sm text-slate-400">{t("preports.empty")}</p> : (
              <table className="w-full text-sm">
                <thead><tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500"><th className={th}>{t(`preports.group.${group}`)}</th><th className={`${th} text-right`}>{t("preports.col.qty")}</th><th className={`${th} text-right`}>{t("preports.col.lines")}</th><th className={`${th} text-right`}>{t("preports.col.cost")}</th><th className={`${th} text-right`}>{t("preports.col.revenue")}</th></tr></thead>
                <tbody>{cons.map((r) => (
                  <tr key={r.key} className="border-b border-slate-100"><td className={th}>{r.label}{r.sub && <span className="ml-1 font-mono text-xs text-slate-400">{r.sub}</span>}</td><td className={`${th} text-right tabular-nums`}>{r.quantity}</td><td className={`${th} text-right tabular-nums text-slate-500`}>{r.lines}</td><td className={`${th} text-right tabular-nums`}>{ft(r.cost)}</td><td className={`${th} text-right tabular-nums font-medium`}>{ft(r.revenue)}</td></tr>
                ))}
                <tr className="bg-slate-50 font-semibold"><td className={th}>Σ</td><td className={`${th} text-right tabular-nums`}>{cons.reduce((a, r) => a + r.quantity, 0)}</td><td className={th}></td><td className={`${th} text-right tabular-nums`}>{ft(cons.reduce((a, r) => a + r.cost, 0))}</td><td className={`${th} text-right tabular-nums`}>{ft(cons.reduce((a, r) => a + r.revenue, 0))}</td></tr>
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {tab === "health" && (
        <div className="space-y-3">
          <div className="flex"><button disabled={busy} onClick={() => void recomputeAbc()} className="ml-auto rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-100 disabled:opacity-50">{t("preports.abcRecompute")}</button></div>
          <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
            {health.length === 0 ? <p className="p-8 text-center text-sm text-slate-400">{t("preports.empty")}</p> : (
              <table className="w-full text-sm">
                <thead><tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500"><th className={th}>{t("parts.abc")}</th><th className={th}>{t("parts.sku")}</th><th className={th}>{t("parts.name")}</th><th className={`${th} text-right`}>{t("parts.quantity")}</th><th className={`${th} text-right`}>{t("preports.col.value")}</th><th className={`${th} text-right`}>{t("preports.col.used90")}</th><th className={`${th} text-right`}>{t("preports.col.used365")}</th><th className={`${th} text-right`}>{t("preports.col.turnover")}</th><th className={th}>{t("preports.col.lastReceipt")}</th><th className={th}>{t("preports.col.lastUse")}</th></tr></thead>
                <tbody>{health.map((h) => (
                  <tr key={h.part_id} className={`border-b border-slate-100 ${h.dead ? "bg-rose-50" : ""}`}>
                    <td className={th}>{abcChip(h.abc_class)}</td><td className={`${th} font-mono text-xs`}>{h.sku}</td>
                    <td className={th}>{h.name}{h.dead && <span className="ml-2 rounded bg-rose-100 px-1.5 py-0.5 text-[10px] font-semibold text-rose-800">{t("preports.dead")}</span>}</td>
                    <td className={`${th} text-right tabular-nums`}>{h.quantity} {h.unit}</td><td className={`${th} text-right tabular-nums`}>{ft(h.value)}</td>
                    <td className={`${th} text-right tabular-nums`}>{h.used_90d}</td><td className={`${th} text-right tabular-nums`}>{h.used_365d}</td>
                    <td className={`${th} text-right tabular-nums`}>{h.turnover ?? "—"}</td><td className={`${th} text-xs text-slate-500`}>{daysTxt(h.days_since_receipt)}</td><td className={`${th} text-xs text-slate-500`}>{daysTxt(h.days_since_use)}</td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {tab === "valuation" && val && (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm"><div className="text-[11px] uppercase tracking-wide text-slate-500">{t("preports.totalValue")}</div><div className="text-2xl font-bold tabular-nums">{ft(val.total_value)}</div><div className="text-[11px] text-slate-500">{val.total_items} {t("parts.tab.parts").toLowerCase()}</div></div>
            <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm sm:col-span-2"><div className="text-[11px] uppercase tracking-wide text-slate-500">{t("preports.byWarehouse")}</div>
              <div className="mt-1 flex flex-wrap gap-2">{val.by_warehouse.map((w) => <span key={w.warehouse_id} className="rounded-full bg-slate-100 px-2.5 py-1 text-sm">{w.kind === "vehicle" ? "🚚" : w.kind === "quarantine" ? "🚧" : "🏬"} {w.name}: <b className="tabular-nums">{ft(w.value)}</b></span>)}</div>
            </div>
          </div>
          <div className="flex"><a href="/api/parts/export.csv?what=valuation" className="ml-auto rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-100">{t("parts.export.valuation")}</a></div>
          <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
            <table className="w-full text-sm">
              <thead><tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500"><th className={th}>{t("parts.sku")}</th><th className={th}>{t("parts.name")}</th><th className={`${th} text-right`}>{t("parts.quantity")}</th><th className={`${th} text-right`}>{t("parts.avgCost")}</th><th className={`${th} text-right`}>{t("preports.col.value")}</th></tr></thead>
              <tbody>{val.rows.map((r) => <tr key={r.part_id} className="border-b border-slate-100"><td className={`${th} font-mono text-xs`}>{r.sku}</td><td className={th}>{r.name}</td><td className={`${th} text-right tabular-nums`}>{r.quantity} {r.unit}</td><td className={`${th} text-right tabular-nums`}>{ft(r.avg_cost)}</td><td className={`${th} text-right tabular-nums font-medium`}>{ft(r.value)}</td></tr>)}</tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "warranty" && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <label className="text-sm">{t("preports.warrantyDays")} <input type="number" min={0} max={730} value={days} onChange={(e) => setDays(Number(e.target.value) || 0)} className="ml-1 w-20 rounded-lg border border-slate-300 px-2 py-1 text-sm" /></label>
            <label className="flex items-center gap-1.5 text-xs text-slate-500"><input type="checkbox" checked={inclExpired} onChange={(e) => setInclExpired(e.target.checked)} className="h-3.5 w-3.5" />{t("preports.includeExpired")}</label>
          </div>
          <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
            {warr.length === 0 ? <p className="p-8 text-center text-sm text-slate-400">{t("preports.warrantyEmpty")}</p> : (
              <table className="w-full text-sm">
                <thead><tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500"><th className={th}>{t("parts.warranty")}</th><th className={th}>{t("parts.part")}</th><th className={th}>{t("parts.serial")}</th><th className={th}>{t("preports.group.asset")}</th><th className={th}>{t("worktime.col.worksheet")}</th></tr></thead>
                <tbody>{warr.map((w) => (
                  <tr key={w.movement_id} className={`border-b border-slate-100 ${w.warranty_active ? "" : "bg-rose-50"}`}>
                    <td className={`${th} whitespace-nowrap font-mono text-xs`}>{w.warranty_until} <span className={`ml-1 rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${w.warranty_active ? "bg-emerald-100 text-emerald-800" : "bg-rose-100 text-rose-800"}`}>{w.warranty_active ? t("preports.activeUntil") : t("preports.expired")}</span></td>
                    <td className={th}><span className="font-mono text-xs text-slate-400">{w.sku}</span> {w.name} · {w.quantity} {w.unit}</td>
                    <td className={`${th} font-mono text-xs`}>{w.serial_no ?? "—"}</td>
                    <td className={th}>{w.asset_name ?? "—"}{w.asset_barcode && <span className="ml-1 font-mono text-xs text-slate-400">{w.asset_barcode}</span>}</td>
                    <td className={`${th} font-mono text-xs`}>{w.worksheet_serial ?? "—"}</td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </AppShell>
  );
}
