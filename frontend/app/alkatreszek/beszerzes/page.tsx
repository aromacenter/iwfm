"use client";

/** Alkatrész-beszerzés (ERP 2. kör, 1d553d2e): újrarendelési javaslat →
 * rendelés-piszkozatok, beszerzési rendelések (megrendelés, vételezés
 * rendelésre helykódra), beszállítói törzs + árlista, visszáru /
 * garanciaigény, szerviz-szettek (kitek). */

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import SearchSelect from "@/components/SearchSelect";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface Part { id: string; sku: string; name: string; unit: string; total_qty: number; min_stock: number; is_active: boolean; pack_qty: number | null }
interface Warehouse { id: string; name: string; kind: string; is_active: boolean; columns: string[]; rows: number }
interface SupplierPrice { part_id: string; sku: string; name: string; price_net: number; supplier_sku: string | null; moq: number; lead_days: number | null }
interface Supplier { id: string; name: string; email: string | null; phone: string | null; contact: string | null; lead_days: number; notes: string | null; is_active: boolean; prices: SupplierPrice[] }
interface POItem { id: string; part_id: string; sku: string; name: string; unit: string; qty_ordered: number; qty_received: number; unit_cost: number | null }
interface PO { id: string; number: string; supplier_id: string | null; supplier_name: string | null; status: "draft" | "ordered" | "partial" | "received" | "cancelled"; expected_date: string | null; note: string | null; created_at: string; total_net: number; items: POItem[] }
interface Reorder { part_id: string; sku: string; name: string; unit: string; total_qty: number; min_stock: number; reorder_qty: number | null; on_order: number; suggested_qty: number; supplier_id: string | null; supplier_name: string | null; unit_cost: number | null; lead_days: number | null }
interface Ret { id: string; part_id: string; sku: string; name: string; unit: string; quantity: number; serial_no: string | null; reason: string | null; status: string; outcome_note: string | null; supplier_name: string | null; warehouse_name: string | null; location: string | null; created_at: string }
interface Kit { id: string; sku: string; name: string; notes: string | null; is_active: boolean; items: { part_id: string; sku: string; name: string; unit: string; quantity: number }[] }

type Tab = "reorder" | "orders" | "suppliers" | "returns" | "kits";
const PO_CHIP: Record<PO["status"], string> = { draft: "bg-slate-100 text-slate-600", ordered: "bg-sky-100 text-sky-800", partial: "bg-amber-100 text-amber-800", received: "bg-emerald-100 text-emerald-800", cancelled: "bg-rose-100 text-rose-700" };
const RET_CHIP: Record<string, string> = { open: "bg-amber-100 text-amber-800", sent: "bg-sky-100 text-sky-800", credited: "bg-emerald-100 text-emerald-800", replaced: "bg-emerald-100 text-emerald-800", rejected: "bg-rose-100 text-rose-700", scrapped: "bg-slate-100 text-slate-600" };

export default function BeszerzesPage() {
  const { t, lang } = useT();
  const { toast, confirm, prompt } = useUI();
  const loc = lang === "hu" ? "hu-HU" : "en-GB";
  const ft = (n: number | null | undefined) => (n == null ? "—" : `${Math.round(n).toLocaleString(loc)} Ft`);
  const fmt = (dt: string) => new Date(dt).toLocaleString(loc, { dateStyle: "short", timeStyle: "short" });
  const num = (s: string) => (s.trim() === "" ? null : Number(String(s).replace(",", ".")));
  const input = "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2";

  const [tab, setTab] = useState<Tab>("reorder");
  const [parts, setParts] = useState<Part[]>([]);
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [orders, setOrders] = useState<PO[]>([]);
  const [reorder, setReorder] = useState<Reorder[]>([]);
  const [returns, setReturns] = useState<Ret[]>([]);
  const [kits, setKits] = useState<Kit[]>([]);
  const [onlyOpen, setOnlyOpen] = useState(true);
  const [busy, setBusy] = useState(false);

  const [poForm, setPoForm] = useState<{ id?: string; supplier_id: string; expected_date: string; note: string; items: { part_id: string; qty: string; cost: string }[] } | null>(null);
  const [receive, setReceive] = useState<{ po: PO; warehouse_id: string; document_no: string; lines: Record<string, { qty: string; location: string }> } | null>(null);
  const [supForm, setSupForm] = useState<{ id?: string; name: string; email: string; phone: string; contact: string; lead_days: string; notes: string; is_active: boolean; prices: { part_id: string; price: string; sku: string; moq: string }[] } | null>(null);
  const [retForm, setRetForm] = useState<{ part_id: string; warehouse_id: string; location: string; quantity: string; supplier_id: string; serial_no: string; reason: string } | null>(null);
  const [kitForm, setKitForm] = useState<{ id?: string; sku: string; name: string; notes: string; is_active: boolean; items: { part_id: string; qty: string }[] } | null>(null);

  const load = useCallback(() => {
    api.get<Part[]>("/api/parts?include_inactive=false").then(setParts).catch(() => {});
    api.get<Warehouse[]>("/api/parts/warehouses").then(setWarehouses).catch(() => {});
    api.get<Supplier[]>("/api/parts/suppliers").then(setSuppliers).catch((e) => toast(errorMessage(e), "error"));
    api.get<PO[]>("/api/parts/purchase-orders").then(setOrders).catch(() => {});
    api.get<Reorder[]>("/api/parts/reorder").then(setReorder).catch(() => {});
    api.get<Ret[]>("/api/parts/returns").then(setReturns).catch(() => {});
    api.get<Kit[]>("/api/parts/kits").then(setKits).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { load(); }, [load]);

  const partItems = useMemo(() => parts.map((p) => ({ id: p.id, label: p.name, badge: p.sku, sublabel: `${p.total_qty} ${p.unit}` })), [parts]);
  const whItems = useMemo(() => warehouses.filter((w) => w.is_active).map((w) => ({ id: w.id, label: w.name, badge: w.kind === "vehicle" ? "🚚" : w.kind === "quarantine" ? "🚧" : "🏬" })), [warehouses]);
  const supItems = useMemo(() => suppliers.filter((s) => s.is_active).map((s) => ({ id: s.id, label: s.name })), [suppliers]);
  const partById = (id: string) => parts.find((p) => p.id === id);
  const locationHint = (whId: string) => { const w = warehouses.find((x) => x.id === whId); return !w || w.columns.length === 0 ? "" : `${w.columns.join("/")} × 1–${w.rows}`; };

  async function run<T>(fn: () => Promise<T>, ok?: string): Promise<T | null> {
    setBusy(true);
    try { const r = await fn(); if (ok) toast(ok, "success"); load(); return r; }
    catch (err) { toast(errorMessage(err), "error"); return null; }
    finally { setBusy(false); }
  }

  // ── rendelések ──
  async function savePo(e: React.FormEvent) {
    e.preventDefault();
    if (!poForm) return;
    const body = { supplier_id: poForm.supplier_id || null, expected_date: poForm.expected_date || null, note: poForm.note || null, items: poForm.items.filter((i) => i.part_id).map((i) => ({ part_id: i.part_id, qty_ordered: num(i.qty) ?? 0, unit_cost: num(i.cost) })) };
    const r = await run(() => (poForm.id ? api.patch(`/api/parts/purchase-orders/${poForm.id}`, body) : api.post("/api/parts/purchase-orders", body)), t("proc.saved"));
    if (r !== null) setPoForm(null);
  }
  async function submitReceive(e: React.FormEvent) {
    e.preventDefault();
    if (!receive) return;
    const lines = Object.entries(receive.lines).filter(([, v]) => (num(v.qty) ?? 0) > 0).map(([item_id, v]) => ({ item_id, quantity: num(v.qty) ?? 0, location: v.location }));
    const r = await run(() => api.post(`/api/parts/purchase-orders/${receive.po.id}/receive`, { warehouse_id: receive.warehouse_id, document_no: receive.document_no || null, lines }), t("proc.received"));
    if (r !== null) setReceive(null);
  }
  // ── beszállítók ──
  async function saveSupplier(e: React.FormEvent) {
    e.preventDefault();
    if (!supForm) return;
    const body = { name: supForm.name, email: supForm.email || null, phone: supForm.phone || null, contact: supForm.contact || null, lead_days: Number(supForm.lead_days) || 0, notes: supForm.notes || null, is_active: supForm.is_active };
    const r = await run(async () => {
      const s = supForm.id ? await api.patch<Supplier>(`/api/parts/suppliers/${supForm.id}`, body) : await api.post<Supplier>("/api/parts/suppliers", body);
      const prices = supForm.prices.filter((p) => p.part_id && num(p.price) != null).map((p) => ({ part_id: p.part_id, price_net: num(p.price) ?? 0, supplier_sku: p.sku || null, moq: num(p.moq) ?? 1 }));
      if (prices.length) await api.put(`/api/parts/suppliers/${s.id}/prices`, prices);
      return s;
    }, t("proc.saved"));
    if (r !== null) setSupForm(null);
  }
  // ── visszáru ──
  async function saveReturn(e: React.FormEvent) {
    e.preventDefault();
    if (!retForm) return;
    const r = await run(() => api.post("/api/parts/returns", { part_id: retForm.part_id, warehouse_id: retForm.warehouse_id, location: retForm.location, quantity: num(retForm.quantity) ?? 0, supplier_id: retForm.supplier_id || null, serial_no: retForm.serial_no || null, reason: retForm.reason || null }), t("proc.saved"));
    if (r !== null) setRetForm(null);
  }
  async function setRetStatus(r: Ret, status: string) {
    let warehouse_id: string | null = null; let location = "";
    if (status === "replaced") {
      const central = warehouses.find((w) => w.kind === "central" && w.is_active);
      const where = await prompt(t("proc.ret.replaceInto"), { initial: central ? `${central.name} / A-1` : "" });
      if (where == null) return;
      const [whName, locPart] = where.split("/").map((s) => s.trim());
      warehouse_id = warehouses.find((w) => w.name === whName)?.id ?? central?.id ?? null;
      location = locPart ?? "";
    }
    await run(() => api.post(`/api/parts/returns/${r.id}/status`, { status, warehouse_id, location }), t("proc.saved"));
  }
  // ── kitek ──
  async function saveKit(e: React.FormEvent) {
    e.preventDefault();
    if (!kitForm) return;
    const body = { sku: kitForm.sku, name: kitForm.name, notes: kitForm.notes || null, is_active: kitForm.is_active, items: kitForm.items.filter((i) => i.part_id).map((i) => ({ part_id: i.part_id, quantity: num(i.qty) ?? 1 })) };
    const r = await run(() => (kitForm.id ? api.patch(`/api/parts/kits/${kitForm.id}`, body) : api.post("/api/parts/kits", body)), t("proc.saved"));
    if (r !== null) setKitForm(null);
  }

  const tabBtn = (key: Tab, label: string) => (
    <button key={key} onClick={() => setTab(key)} className={`px-3 py-1.5 text-sm ${tab === key ? "bg-indigo-600 text-white" : "bg-white text-slate-600 hover:bg-slate-100"}`}>{label}</button>
  );
  const shownOrders = onlyOpen ? orders.filter((o) => o.status === "draft" || o.status === "ordered" || o.status === "partial") : orders;
  const closeX = (fn: () => void) => <button type="button" onClick={fn} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>;

  return (
    <AppShell>
      <div className="mb-4">
        <h1 className="text-xl font-semibold">🧾 {t("proc.title")}</h1>
        <p className="text-sm text-slate-500">{t("proc.subtitle")}</p>
      </div>
      <div className="mb-4 flex overflow-hidden rounded-lg border border-slate-300 text-sm w-fit">
        {tabBtn("reorder", `${t("proc.tab.reorder")}${reorder.length ? ` (${reorder.length})` : ""}`)}{tabBtn("orders", t("proc.tab.orders"))}{tabBtn("suppliers", t("proc.tab.suppliers"))}{tabBtn("returns", t("proc.tab.returns"))}{tabBtn("kits", t("proc.tab.kits"))}
      </div>

      {tab === "reorder" && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-xs text-slate-500">{t("proc.reorderHint")}</p>
            <button disabled={busy || reorder.every((r) => r.suggested_qty <= 0)} onClick={() => void run(async () => { const r = await api.post<PO[]>("/api/parts/reorder/create-orders", {}); toast(t("proc.ordersCreated", { n: r.length }), "success"); setTab("orders"); })} className="ml-auto rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{t("proc.createOrders")}</button>
          </div>
          <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
            {reorder.length === 0 ? <p className="p-8 text-center text-sm text-slate-400">{t("proc.reorderEmpty")}</p> : (
              <table className="w-full text-sm">
                <thead><tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="px-3 py-2">{t("parts.sku")}</th><th className="px-3 py-2">{t("parts.name")}</th><th className="px-3 py-2 text-right">{t("proc.col.stock")}</th><th className="px-3 py-2 text-right">{t("proc.col.min")}</th><th className="px-3 py-2 text-right">{t("proc.col.onOrder")}</th><th className="px-3 py-2 text-right">{t("proc.col.suggested")}</th><th className="px-3 py-2">{t("proc.col.supplier")}</th><th className="px-3 py-2 text-right">{t("proc.col.unitCost")}</th><th className="px-3 py-2 text-right">{t("proc.col.lead")}</th>
                </tr></thead>
                <tbody>
                  {reorder.map((r) => (
                    <tr key={r.part_id} className="border-b border-slate-100">
                      <td className="px-3 py-2 font-mono text-xs">{r.sku}</td><td className="px-3 py-2">{r.name}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-rose-700">{r.total_qty} {r.unit}</td><td className="px-3 py-2 text-right tabular-nums text-slate-500">{r.min_stock}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-sky-700">{r.on_order}</td><td className="px-3 py-2 text-right tabular-nums font-semibold">{r.suggested_qty} {r.unit}</td>
                      <td className="px-3 py-2">{r.supplier_name ?? "—"}</td><td className="px-3 py-2 text-right tabular-nums">{ft(r.unit_cost)}</td><td className="px-3 py-2 text-right text-slate-500">{r.lead_days != null ? `${r.lead_days} nap` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {tab === "orders" && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-1.5 text-xs text-slate-500"><input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} className="h-3.5 w-3.5" />{t("proc.onlyOpenOrders")}</label>
            <button onClick={() => setPoForm({ supplier_id: "", expected_date: "", note: "", items: [{ part_id: "", qty: "1", cost: "" }] })} className="ml-auto rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700">{t("proc.newOrder")}</button>
          </div>
          {shownOrders.length === 0 ? <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-400 shadow-sm">{t("proc.ordersEmpty")}</div> : (
            <div className="space-y-2">
              {shownOrders.map((o) => (
                <div key={o.id} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm font-semibold">{o.number}</span>
                    <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${PO_CHIP[o.status]}`}>{t(`proc.status.${o.status}`)}</span>
                    <span className="text-sm text-slate-600">{o.supplier_name ?? "—"}</span>
                    <span className="text-xs text-slate-400">{fmt(o.created_at)}{o.expected_date && ` · ${t("proc.expected")}: ${o.expected_date}`}</span>
                    <span className="ml-auto text-sm font-semibold tabular-nums">{ft(o.total_net)}</span>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="mt-2 w-full text-sm">
                      <tbody>
                        {o.items.map((it) => (
                          <tr key={it.id} className="border-t border-slate-100">
                            <td className="py-1 pr-2"><span className="font-mono text-xs text-slate-400">{it.sku}</span> {it.name}</td>
                            <td className="py-1 pr-2 text-right tabular-nums">{t("proc.qtyOrdered")}: {it.qty_ordered} {it.unit}</td>
                            <td className={`py-1 pr-2 text-right tabular-nums ${it.qty_received >= it.qty_ordered ? "text-emerald-700" : "text-amber-700"}`}>{t("proc.qtyReceived")}: {it.qty_received}</td>
                            <td className="py-1 text-right tabular-nums text-slate-500">{ft(it.unit_cost)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {o.note && <p className="mt-1 text-xs text-slate-400">{o.note}</p>}
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {o.status === "draft" && <>
                      <button onClick={() => setPoForm({ id: o.id, supplier_id: o.supplier_id ?? "", expected_date: o.expected_date ?? "", note: o.note ?? "", items: o.items.map((i) => ({ part_id: i.part_id, qty: String(i.qty_ordered), cost: i.unit_cost == null ? "" : String(i.unit_cost) })) })} className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs hover:bg-slate-100">✏️ {t("proc.editBtn")}</button>
                      <button onClick={() => void run(() => api.post(`/api/parts/purchase-orders/${o.id}/order`, {}), t("proc.saved"))} className="rounded-lg bg-sky-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-sky-700">{t("proc.orderBtn")}</button>
                    </>}
                    {(o.status === "ordered" || o.status === "partial") && (
                      <button onClick={() => setReceive({ po: o, warehouse_id: warehouses.find((w) => w.kind === "central" && w.is_active)?.id ?? "", document_no: "", lines: Object.fromEntries(o.items.map((i) => [i.id, { qty: String(Math.max(0, i.qty_ordered - i.qty_received)), location: "" }])) })} className="rounded-lg bg-emerald-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-emerald-700">{t("proc.receiveBtn")}</button>
                    )}
                    {o.status !== "received" && o.status !== "cancelled" && (
                      <button onClick={async () => { if (await confirm(t("proc.cancelBtn") + "?")) void run(() => api.post(`/api/parts/purchase-orders/${o.id}/cancel`, {})); }} className="rounded-lg border border-rose-300 px-2.5 py-1 text-xs text-rose-700 hover:bg-rose-50">{t("proc.cancelBtn")}</button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "suppliers" && (
        <div className="space-y-3">
          <div className="flex"><button onClick={() => setSupForm({ name: "", email: "", phone: "", contact: "", lead_days: "3", notes: "", is_active: true, prices: [] })} className="ml-auto rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700">{t("proc.newSupplier")}</button></div>
          {suppliers.length === 0 ? <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-400 shadow-sm">{t("proc.suppliersEmpty")}</div> : (
            <div className="grid gap-3 md:grid-cols-2">
              {suppliers.map((s) => (
                <div key={s.id} className={`rounded-2xl border border-slate-200 bg-white p-4 shadow-sm ${s.is_active ? "" : "opacity-60"}`}>
                  <div className="flex items-center gap-2"><h2 className="font-semibold">🏭 {s.name}</h2><span className="text-xs text-slate-500">· {s.lead_days} nap</span>
                    <button onClick={() => setSupForm({ id: s.id, name: s.name, email: s.email ?? "", phone: s.phone ?? "", contact: s.contact ?? "", lead_days: String(s.lead_days), notes: s.notes ?? "", is_active: s.is_active, prices: s.prices.map((p) => ({ part_id: p.part_id, price: String(p.price_net), sku: p.supplier_sku ?? "", moq: String(p.moq) })) })} className="ml-auto rounded-lg border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-100">✏️</button>
                  </div>
                  <p className="text-xs text-slate-500">{[s.contact, s.email, s.phone].filter(Boolean).join(" · ") || "—"}</p>
                  {s.prices.length > 0 && (
                    <div className="overflow-x-auto">
                      <table className="mt-2 w-full text-xs">
                        <tbody>{s.prices.map((p) => (
                          <tr key={p.part_id} className="border-t border-slate-100"><td className="py-1 pr-2"><span className="font-mono text-slate-400">{p.sku}</span> {p.name}</td><td className="py-1 pr-2 text-right tabular-nums font-medium">{ft(p.price_net)}</td><td className="py-1 text-right text-slate-500">MOQ {p.moq}{p.supplier_sku && ` · ${p.supplier_sku}`}</td></tr>
                        ))}</tbody>
                      </table>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "returns" && (
        <div className="space-y-3">
          <div className="flex"><button onClick={() => setRetForm({ part_id: "", warehouse_id: warehouses.find((w) => w.kind === "quarantine")?.id ?? "", location: "", quantity: "1", supplier_id: "", serial_no: "", reason: "" })} className="ml-auto rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700">{t("proc.newReturn")}</button></div>
          <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
            {returns.length === 0 ? <p className="p-8 text-center text-sm text-slate-400">{t("proc.returnsEmpty")}</p> : (
              <table className="w-full text-sm">
                <thead><tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500"><th className="px-3 py-2">{t("parts.when")}</th><th className="px-3 py-2">{t("parts.part")}</th><th className="px-3 py-2 text-right">{t("parts.quantity")}</th><th className="px-3 py-2">{t("proc.col.supplier")}</th><th className="px-3 py-2">{t("proc.returnReason")}</th><th className="px-3 py-2">{t("proc.ret.setStatus")}</th><th className="px-3 py-2"></th></tr></thead>
                <tbody>{returns.map((r) => (
                  <tr key={r.id} className="border-b border-slate-100">
                    <td className="px-3 py-2 whitespace-nowrap text-xs text-slate-500">{fmt(r.created_at)}</td>
                    <td className="px-3 py-2"><span className="font-mono text-xs text-slate-400">{r.sku}</span> {r.name}{r.serial_no && <span className="ml-1 text-xs text-slate-400">· {r.serial_no}</span>}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{r.quantity} {r.unit}</td>
                    <td className="px-3 py-2">{r.supplier_name ?? "—"}</td>
                    <td className="px-3 py-2 text-xs text-slate-500">{r.reason ?? "—"}{r.outcome_note && ` → ${r.outcome_note}`}</td>
                    <td className="px-3 py-2"><span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${RET_CHIP[r.status] ?? ""}`}>{t(`proc.ret.${r.status}`)}</span></td>
                    <td className="px-3 py-2 text-right whitespace-nowrap">
                      {r.status === "open" && <button onClick={() => void setRetStatus(r, "sent")} className="mr-1 rounded-lg border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-100">📨 {t("proc.ret.sent")}</button>}
                      {(r.status === "open" || r.status === "sent") && <>
                        <button onClick={() => void setRetStatus(r, "credited")} className="mr-1 rounded-lg border border-emerald-300 px-2 py-0.5 text-xs text-emerald-700 hover:bg-emerald-50">{t("proc.ret.credited")}</button>
                        <button onClick={() => void setRetStatus(r, "replaced")} className="mr-1 rounded-lg border border-emerald-300 px-2 py-0.5 text-xs text-emerald-700 hover:bg-emerald-50">{t("proc.ret.replaced")}</button>
                        <button onClick={() => void setRetStatus(r, "rejected")} className="mr-1 rounded-lg border border-rose-300 px-2 py-0.5 text-xs text-rose-700 hover:bg-rose-50">{t("proc.ret.rejected")}</button>
                        <button onClick={() => void setRetStatus(r, "scrapped")} className="rounded-lg border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-100">{t("proc.ret.scrapped")}</button>
                      </>}
                    </td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {tab === "kits" && (
        <div className="space-y-3">
          <div className="flex"><button onClick={() => setKitForm({ sku: "", name: "", notes: "", is_active: true, items: [{ part_id: "", qty: "1" }] })} className="ml-auto rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700">{t("proc.newKit")}</button></div>
          {kits.length === 0 ? <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-400 shadow-sm">{t("proc.kitsEmpty")}</div> : (
            <div className="grid gap-3 md:grid-cols-2">
              {kits.map((k) => (
                <div key={k.id} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                  <div className="flex items-center gap-2"><h2 className="font-semibold">🧰 {k.name}</h2><span className="font-mono text-xs text-slate-400">{k.sku}</span>
                    <button onClick={() => setKitForm({ id: k.id, sku: k.sku, name: k.name, notes: k.notes ?? "", is_active: k.is_active, items: k.items.map((i) => ({ part_id: i.part_id, qty: String(i.quantity) })) })} className="ml-auto rounded-lg border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-100">✏️</button>
                  </div>
                  <ul className="mt-2 text-sm">{k.items.map((i) => <li key={i.part_id} className="flex justify-between border-t border-slate-100 py-1"><span><span className="font-mono text-xs text-slate-400">{i.sku}</span> {i.name}</span><span className="tabular-nums">{i.quantity} {i.unit}</span></li>)}</ul>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Rendelés modal ── */}
      {poForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={savePo} className="relative max-h-[90vh] w-full max-w-2xl space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            {closeX(() => setPoForm(null))}
            <h2 className="text-lg font-semibold">🧾 {t("proc.order")}</h2>
            <div className="grid grid-cols-2 gap-3">
              <div className="text-sm"><p className="mb-1 text-xs text-slate-500">{t("proc.col.supplier")}</p><SearchSelect items={supItems} value={poForm.supplier_id} onChange={(id) => setPoForm({ ...poForm, supplier_id: id })} placeholder={t("proc.col.supplier")} /></div>
              <label className="block text-sm">{t("proc.expected")}<input type="date" value={poForm.expected_date} onChange={(e) => setPoForm({ ...poForm, expected_date: e.target.value })} className={input} /></label>
            </div>
            <div>
              <div className="mb-1 flex items-center justify-between"><span className="text-sm text-slate-600">{t("proc.items")}</span><button type="button" onClick={() => setPoForm({ ...poForm, items: [...poForm.items, { part_id: "", qty: "1", cost: "" }] })} className="text-sm font-medium text-indigo-600">{t("proc.addItem")}</button></div>
              {poForm.items.map((it, i) => (
                <div key={i} className="mb-1 flex gap-1">
                  <div className="flex-1 text-sm"><SearchSelect items={partItems} value={it.part_id} onChange={(id) => { const n = [...poForm.items]; n[i] = { ...it, part_id: id }; setPoForm({ ...poForm, items: n }); }} placeholder={t("parts.search")} /></div>
                  <input type="number" min={0} step="any" value={it.qty} onChange={(e) => { const n = [...poForm.items]; n[i] = { ...it, qty: e.target.value }; setPoForm({ ...poForm, items: n }); }} placeholder={t("proc.qtyOrdered")} className="w-20 rounded-lg border border-slate-300 px-2 py-1.5 text-sm" />
                  <input type="number" min={0} value={it.cost} onChange={(e) => { const n = [...poForm.items]; n[i] = { ...it, cost: e.target.value }; setPoForm({ ...poForm, items: n }); }} placeholder={t("proc.col.unitCost")} className="w-28 rounded-lg border border-slate-300 px-2 py-1.5 text-sm" />
                  <button type="button" onClick={() => setPoForm({ ...poForm, items: poForm.items.filter((_, idx) => idx !== i) })} className="px-1 text-slate-400 hover:text-red-600">✕</button>
                </div>
              ))}
            </div>
            <label className="block text-sm">{t("parts.notes")}<input value={poForm.note} onChange={(e) => setPoForm({ ...poForm, note: e.target.value })} className={input} /></label>
            <div className="flex justify-end gap-2 pt-1"><button type="button" onClick={() => setPoForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button><button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{t("common.save")}</button></div>
          </form>
        </div>
      )}

      {/* ── Vételezés rendelésre ── */}
      {receive && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={submitReceive} className="relative max-h-[90vh] w-full max-w-2xl space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            {closeX(() => setReceive(null))}
            <h2 className="text-lg font-semibold">📥 {t("proc.receiveTitle", { number: receive.po.number })}</h2>
            <div className="grid grid-cols-2 gap-3">
              <div className="text-sm"><p className="mb-1 text-xs text-slate-500">{t("proc.receiveInto")} *</p><SearchSelect items={whItems} value={receive.warehouse_id} onChange={(id) => setReceive({ ...receive, warehouse_id: id })} placeholder={t("parts.warehouse")} /></div>
              <label className="block text-sm">{t("proc.documentNo")}<input value={receive.document_no} onChange={(e) => setReceive({ ...receive, document_no: e.target.value })} className={input} /></label>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead><tr className="text-left text-xs uppercase text-slate-400"><th className="py-1">{t("parts.part")}</th><th className="py-1 text-right">{t("proc.qtyOrdered")}</th><th className="py-1 text-right">{t("proc.receiveQty")}</th><th className="py-1">{t("parts.location")}</th></tr></thead>
                <tbody>{receive.po.items.map((it) => (
                  <tr key={it.id} className="border-t border-slate-100">
                    <td className="py-1 pr-2"><span className="font-mono text-xs text-slate-400">{it.sku}</span> {it.name}</td>
                    <td className="py-1 text-right tabular-nums text-slate-500">{it.qty_received} / {it.qty_ordered}</td>
                    <td className="py-1 text-right"><input type="number" min={0} step="any" value={receive.lines[it.id]?.qty ?? ""} onChange={(e) => setReceive({ ...receive, lines: { ...receive.lines, [it.id]: { ...receive.lines[it.id], qty: e.target.value } } })} className="w-20 rounded-lg border border-slate-300 px-2 py-1 text-right" /></td>
                    <td className="py-1 pl-2"><input value={receive.lines[it.id]?.location ?? ""} onChange={(e) => setReceive({ ...receive, lines: { ...receive.lines, [it.id]: { ...receive.lines[it.id], location: e.target.value.toUpperCase() } } })} placeholder={locationHint(receive.warehouse_id)} className="w-24 rounded-lg border border-slate-300 px-2 py-1" /></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <div className="flex justify-end gap-2 pt-1"><button type="button" onClick={() => setReceive(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button><button disabled={busy || !receive.warehouse_id} className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50">{t("proc.receiveBtn")}</button></div>
          </form>
        </div>
      )}

      {/* ── Beszállító modal ── */}
      {supForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={saveSupplier} className="relative max-h-[90vh] w-full max-w-2xl space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            {closeX(() => setSupForm(null))}
            <h2 className="text-lg font-semibold">🏭 {supForm.id ? t("common.edit") : t("proc.newSupplier")}</h2>
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-sm">{t("proc.supplierName")} *<input required value={supForm.name} onChange={(e) => setSupForm({ ...supForm, name: e.target.value })} className={input} /></label>
              <label className="block text-sm">{t("proc.leadDays")}<input type="number" min={0} value={supForm.lead_days} onChange={(e) => setSupForm({ ...supForm, lead_days: e.target.value })} className={input} /></label>
              <label className="block text-sm">{t("proc.contact")}<input value={supForm.contact} onChange={(e) => setSupForm({ ...supForm, contact: e.target.value })} className={input} /></label>
              <label className="block text-sm">{t("proc.email")}<input value={supForm.email} onChange={(e) => setSupForm({ ...supForm, email: e.target.value })} className={input} /></label>
              <label className="block text-sm">{t("proc.phone")}<input value={supForm.phone} onChange={(e) => setSupForm({ ...supForm, phone: e.target.value })} className={input} /></label>
              <label className="flex items-center gap-2 pt-6 text-sm"><input type="checkbox" checked={supForm.is_active} onChange={(e) => setSupForm({ ...supForm, is_active: e.target.checked })} className="h-4 w-4" />{t("parts.active")}</label>
            </div>
            <div>
              <div className="mb-1 flex items-center justify-between"><span className="text-sm text-slate-600">{t("proc.prices")}</span><button type="button" onClick={() => setSupForm({ ...supForm, prices: [...supForm.prices, { part_id: "", price: "", sku: "", moq: "1" }] })} className="text-sm font-medium text-indigo-600">{t("proc.addPrice")}</button></div>
              {supForm.prices.map((p, i) => (
                <div key={i} className="mb-1 flex gap-1">
                  <div className="flex-1 text-sm"><SearchSelect items={partItems} value={p.part_id} onChange={(id) => { const n = [...supForm.prices]; n[i] = { ...p, part_id: id }; setSupForm({ ...supForm, prices: n }); }} placeholder={t("parts.search")} /></div>
                  <input type="number" min={0} value={p.price} onChange={(e) => { const n = [...supForm.prices]; n[i] = { ...p, price: e.target.value }; setSupForm({ ...supForm, prices: n }); }} placeholder={t("proc.price")} className="w-24 rounded-lg border border-slate-300 px-2 py-1.5 text-sm" />
                  <input value={p.sku} onChange={(e) => { const n = [...supForm.prices]; n[i] = { ...p, sku: e.target.value }; setSupForm({ ...supForm, prices: n }); }} placeholder={t("proc.supplierSku")} className="w-28 rounded-lg border border-slate-300 px-2 py-1.5 text-sm" />
                  <input type="number" min={0} step="any" value={p.moq} onChange={(e) => { const n = [...supForm.prices]; n[i] = { ...p, moq: e.target.value }; setSupForm({ ...supForm, prices: n }); }} placeholder={t("proc.moq")} className="w-16 rounded-lg border border-slate-300 px-2 py-1.5 text-sm" />
                  <button type="button" onClick={() => setSupForm({ ...supForm, prices: supForm.prices.filter((_, idx) => idx !== i) })} className="px-1 text-slate-400 hover:text-red-600">✕</button>
                </div>
              ))}
            </div>
            <label className="block text-sm">{t("parts.notes")}<input value={supForm.notes} onChange={(e) => setSupForm({ ...supForm, notes: e.target.value })} className={input} /></label>
            <div className="flex justify-end gap-2 pt-1"><button type="button" onClick={() => setSupForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button><button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{t("common.save")}</button></div>
          </form>
        </div>
      )}

      {/* ── Visszáru modal ── */}
      {retForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={saveReturn} className="relative w-full max-w-lg space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            {closeX(() => setRetForm(null))}
            <h2 className="text-lg font-semibold">↩️ {t("proc.newReturn")}</h2>
            <div className="text-sm"><p className="mb-1 text-xs text-slate-500">{t("parts.part")} *</p><SearchSelect items={partItems} value={retForm.part_id} onChange={(id) => setRetForm({ ...retForm, part_id: id })} placeholder={t("parts.search")} /></div>
            <div className="grid grid-cols-2 gap-3">
              <div className="text-sm"><p className="mb-1 text-xs text-slate-500">{t("proc.returnFrom")} *</p><SearchSelect items={whItems} value={retForm.warehouse_id} onChange={(id) => setRetForm({ ...retForm, warehouse_id: id, location: "" })} placeholder={t("parts.warehouse")} /></div>
              <label className="block text-sm">{t("parts.location")}<input value={retForm.location} onChange={(e) => setRetForm({ ...retForm, location: e.target.value.toUpperCase() })} placeholder={locationHint(retForm.warehouse_id)} className={input} /></label>
              <label className="block text-sm">{t("parts.quantity")} *<input required type="number" min={0} step="any" value={retForm.quantity} onChange={(e) => setRetForm({ ...retForm, quantity: e.target.value })} className={input} /></label>
              <label className="block text-sm">{t("parts.serial")}<input value={retForm.serial_no} onChange={(e) => setRetForm({ ...retForm, serial_no: e.target.value })} className={input} /></label>
            </div>
            <div className="text-sm"><p className="mb-1 text-xs text-slate-500">{t("proc.col.supplier")}</p><SearchSelect items={supItems} value={retForm.supplier_id} onChange={(id) => setRetForm({ ...retForm, supplier_id: id })} placeholder={t("proc.col.supplier")} /></div>
            <label className="block text-sm">{t("proc.returnReason")}<input value={retForm.reason} onChange={(e) => setRetForm({ ...retForm, reason: e.target.value })} className={input} /></label>
            <div className="flex justify-end gap-2 pt-1"><button type="button" onClick={() => setRetForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button><button disabled={busy || !retForm.part_id || !retForm.warehouse_id} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{t("common.save")}</button></div>
          </form>
        </div>
      )}

      {/* ── Kit modal ── */}
      {kitForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={saveKit} className="relative max-h-[90vh] w-full max-w-lg space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            {closeX(() => setKitForm(null))}
            <h2 className="text-lg font-semibold">🧰 {kitForm.id ? t("common.edit") : t("proc.newKit")}</h2>
            <div className="grid grid-cols-3 gap-3">
              <label className="block text-sm">{t("parts.sku")} *<input required value={kitForm.sku} onChange={(e) => setKitForm({ ...kitForm, sku: e.target.value })} className={input} /></label>
              <label className="col-span-2 block text-sm">{t("proc.kitName")} *<input required value={kitForm.name} onChange={(e) => setKitForm({ ...kitForm, name: e.target.value })} className={input} /></label>
            </div>
            <div>
              <div className="mb-1 flex items-center justify-between"><span className="text-sm text-slate-600">{t("proc.kitItems")}</span><button type="button" onClick={() => setKitForm({ ...kitForm, items: [...kitForm.items, { part_id: "", qty: "1" }] })} className="text-sm font-medium text-indigo-600">{t("proc.addItem")}</button></div>
              {kitForm.items.map((it, i) => (
                <div key={i} className="mb-1 flex gap-1">
                  <div className="flex-1 text-sm"><SearchSelect items={partItems} value={it.part_id} onChange={(id) => { const n = [...kitForm.items]; n[i] = { ...it, part_id: id }; setKitForm({ ...kitForm, items: n }); }} placeholder={t("parts.search")} /></div>
                  <input type="number" min={0} step="any" value={it.qty} onChange={(e) => { const n = [...kitForm.items]; n[i] = { ...it, qty: e.target.value }; setKitForm({ ...kitForm, items: n }); }} className="w-20 rounded-lg border border-slate-300 px-2 py-1.5 text-sm" />
                  <span className="self-center text-xs text-slate-400">{partById(it.part_id)?.unit ?? ""}</span>
                  <button type="button" onClick={() => setKitForm({ ...kitForm, items: kitForm.items.filter((_, idx) => idx !== i) })} className="px-1 text-slate-400 hover:text-red-600">✕</button>
                </div>
              ))}
            </div>
            <label className="block text-sm">{t("parts.notes")}<input value={kitForm.notes} onChange={(e) => setKitForm({ ...kitForm, notes: e.target.value })} className={input} /></label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={kitForm.is_active} onChange={(e) => setKitForm({ ...kitForm, is_active: e.target.checked })} className="h-4 w-4" />{t("parts.active")}</label>
            <div className="flex justify-end gap-2 pt-1"><button type="button" onClick={() => setKitForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button><button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{t("common.save")}</button></div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
