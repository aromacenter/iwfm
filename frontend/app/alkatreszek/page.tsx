"use client";

/** Alkatrész-raktár (parts modul, A fázis): cikktörzs, helykódos raktárak
 * (oszlop × sor), készlet, vételezés / áthelyezés / leltári korrekció,
 * mozgásnapló. A termék-raktártól (/raktar) teljesen független. */

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import SearchSelect from "@/components/SearchSelect";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface Warehouse {
  id: string; name: string; kind: "central" | "vehicle"; owner_user_id: string | null;
  columns: string[]; rows: number; notes: string | null; is_active: boolean; part_count: number; total_qty: number;
}
interface Part {
  id: string; sku: string; name: string; manufacturer_code: string | null; unit: string; min_stock: number;
  price_net: number | null; cost_net: number | null; supplier: string | null; notes: string | null; is_active: boolean;
  total_qty: number; low: boolean;
}
interface StockRow { warehouse_id: string; warehouse_name: string; part_id: string; sku: string; name: string; unit: string; location: string; quantity: number }
interface Movement {
  id: string; created_at: string; kind: string; part_id: string; sku: string; name: string; unit: string;
  warehouse_id: string; warehouse_name: string; location: string; quantity_delta: number; unit_cost: number | null;
  ref_warehouse_name: string | null; ref_location: string | null; document_no: string | null; note: string | null; actor_name: string | null;
}
interface AssignableUser { id: string; display_name: string }
interface PartsReq {
  id: string; part_id: string; sku: string; name: string; unit: string; quantity: number; note: string | null;
  status: "pending" | "issued" | "received" | "declined" | "cancelled"; requester_name: string | null; warehouse_id: string; warehouse_name: string | null;
  decided_by_name: string | null; decided_at: string | null; decision_note: string | null;
  issued_from_name: string | null; issued_from_location: string | null; issued_quantity: number | null; received_at: string | null; created_at: string;
}
const REQ_CHIP: Record<PartsReq["status"], string> = {
  pending: "bg-amber-100 text-amber-800", issued: "bg-sky-100 text-sky-800", received: "bg-emerald-100 text-emerald-800",
  declined: "bg-rose-100 text-rose-800", cancelled: "bg-slate-100 text-slate-500",
};

type Tab = "stock" | "parts" | "warehouses" | "movements" | "requests";
type MoveKind = "receipt" | "transfer" | "adjust";

const emptyPart = { sku: "", name: "", manufacturer_code: "", unit: "db", min_stock: "0", price_net: "", cost_net: "", supplier: "", notes: "", is_active: true };
const emptyWh = { name: "", kind: "central" as "central" | "vehicle", owner_user_id: "", columns: "A, B, C", rows: "10", notes: "", is_active: true };

export default function AlkatreszekPage() {
  const { t, lang } = useT();
  const { toast, confirm } = useUI();
  const fmt = (dt: string) => new Date(dt).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { dateStyle: "short", timeStyle: "short" });
  const ft = (n: number | null) => (n == null ? "—" : `${Math.round(n).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB")} Ft`);
  const num = (s: string) => (s.trim() === "" ? null : Number(String(s).replace(",", ".")));

  const [tab, setTab] = useState<Tab>("stock");
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [parts, setParts] = useState<Part[]>([]);
  const [stock, setStock] = useState<StockRow[]>([]);
  const [movements, setMovements] = useState<Movement[]>([]);
  const [users, setUsers] = useState<AssignableUser[]>([]);
  const [requests, setRequests] = useState<PartsReq[]>([]);
  const [onlyOpen, setOnlyOpen] = useState(true);
  const [issue, setIssue] = useState<{ req: PartsReq; from_warehouse_id: string; from_location: string; quantity: string; note: string } | null>(null);
  const [q, setQ] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const [busy, setBusy] = useState(false);

  const [partForm, setPartForm] = useState<(typeof emptyPart & { id?: string }) | null>(null);
  const [whForm, setWhForm] = useState<(typeof emptyWh & { id?: string }) | null>(null);
  const [gridWh, setGridWh] = useState<Warehouse | null>(null);
  const [move, setMove] = useState<{ kind: MoveKind; part_id: string; warehouse_id: string; location: string; to_warehouse_id: string; to_location: string; quantity: string; unit_cost: string; document_no: string; note: string } | null>(null);

  const load = useCallback(() => {
    api.get<Warehouse[]>("/api/parts/warehouses").then(setWarehouses).catch((e) => toast(errorMessage(e), "error"));
    api.get<Part[]>(`/api/parts?include_inactive=${showInactive}`).then(setParts).catch(() => {});
    api.get<StockRow[]>("/api/parts/stock").then(setStock).catch(() => {});
    api.get<Movement[]>("/api/parts/movements").then(setMovements).catch(() => {});
    api.get<AssignableUser[]>("/api/warehouses/assignable-users").then(setUsers).catch(() => {});
    api.get<PartsReq[]>("/api/parts/requests").then(setRequests).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showInactive]);
  useEffect(() => { load(); }, [load]);

  const filteredParts = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return parts;
    return parts.filter((p) => [p.name, p.sku, p.manufacturer_code ?? "", p.supplier ?? ""].some((x) => x.toLowerCase().includes(s)));
  }, [parts, q]);

  const partItems = useMemo(() => parts.filter((p) => p.is_active).map((p) => ({ id: p.id, label: p.name, badge: p.sku, sublabel: `${p.total_qty} ${p.unit}`, keywords: p.manufacturer_code })), [parts]);
  const whItems = useMemo(() => warehouses.filter((w) => w.is_active).map((w) => ({ id: w.id, label: w.name, badge: w.kind === "vehicle" ? "🚚" : "🏬" })), [warehouses]);

  // ── cikktörzs ─────────────────────────────────────────────────────────────
  async function savePart(e: React.FormEvent) {
    e.preventDefault();
    if (!partForm) return;
    setBusy(true);
    try {
      const body = {
        sku: partForm.sku, name: partForm.name, manufacturer_code: partForm.manufacturer_code || null, unit: partForm.unit || "db",
        min_stock: num(partForm.min_stock) ?? 0, price_net: num(partForm.price_net), cost_net: num(partForm.cost_net),
        supplier: partForm.supplier || null, notes: partForm.notes || null, is_active: partForm.is_active,
      };
      if (partForm.id) await api.patch(`/api/parts/${partForm.id}`, body);
      else await api.post("/api/parts", body);
      toast(t("parts.saved"), "success");
      setPartForm(null);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  // ── raktárak ──────────────────────────────────────────────────────────────
  async function saveWh(e: React.FormEvent) {
    e.preventDefault();
    if (!whForm) return;
    setBusy(true);
    try {
      const body = {
        name: whForm.name, kind: whForm.kind, owner_user_id: whForm.kind === "vehicle" && whForm.owner_user_id ? whForm.owner_user_id : null,
        columns: whForm.kind === "vehicle" ? [] : whForm.columns.split(/[,\s;]+/).filter(Boolean),
        rows: Math.max(1, Number(whForm.rows) || 1), notes: whForm.notes || null, is_active: whForm.is_active,
      };
      if (whForm.id) await api.patch(`/api/parts/warehouses/${whForm.id}`, body);
      else await api.post("/api/parts/warehouses", body);
      toast(t("parts.saved"), "success");
      setWhForm(null);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function deleteWh(w: Warehouse) {
    if (!(await confirm(t("parts.deleteWh", { name: w.name })))) return;
    try {
      await api.delete(`/api/parts/warehouses/${w.id}`);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  // ── mozgások ──────────────────────────────────────────────────────────────
  function openMove(kind: MoveKind, preset?: Partial<NonNullable<typeof move>>) {
    setMove({ kind, part_id: "", warehouse_id: warehouses[0]?.id ?? "", location: "", to_warehouse_id: "", to_location: "", quantity: "", unit_cost: "", document_no: "", note: "", ...preset });
  }

  async function submitMove(e: React.FormEvent) {
    e.preventDefault();
    if (!move || !move.part_id || !move.warehouse_id) return;
    setBusy(true);
    try {
      const qty = num(move.quantity) ?? 0;
      if (move.kind === "receipt") {
        await api.post("/api/parts/receipt", { part_id: move.part_id, warehouse_id: move.warehouse_id, location: move.location, quantity: qty, unit_cost: num(move.unit_cost), document_no: move.document_no || null, note: move.note || null });
      } else if (move.kind === "transfer") {
        await api.post("/api/parts/transfer", { part_id: move.part_id, from_warehouse_id: move.warehouse_id, from_location: move.location, to_warehouse_id: move.to_warehouse_id, to_location: move.to_location, quantity: qty, note: move.note || null });
      } else {
        await api.post("/api/parts/adjust", { part_id: move.part_id, warehouse_id: move.warehouse_id, location: move.location, quantity: qty, note: move.note || null });
      }
      toast(t("parts.done"), "success");
      setMove(null);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  // ── kiadás-kérések ────────────────────────────────────────────────────────
  async function submitIssue(e: React.FormEvent) {
    e.preventDefault();
    if (!issue || !issue.from_warehouse_id) return;
    setBusy(true);
    try {
      await api.post(`/api/parts/requests/${issue.req.id}/issue`, {
        from_warehouse_id: issue.from_warehouse_id, from_location: issue.from_location,
        quantity: num(issue.quantity), note: issue.note || null,
      });
      toast(t("parts.req.issuedToast"), "success");
      setIssue(null);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function declineReq(r: PartsReq) {
    if (!(await confirm(t("parts.req.declineConfirm")))) return;
    try {
      await api.post(`/api/parts/requests/${r.id}/decline`, {});
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }
  const pendingCount = requests.filter((r) => r.status === "pending").length;
  const shownRequests = onlyOpen ? requests.filter((r) => r.status === "pending" || r.status === "issued") : requests;

  const whById = (id: string) => warehouses.find((w) => w.id === id);
  const locationHint = (whId: string) => {
    const w = whById(whId);
    if (!w || w.kind === "vehicle" || w.columns.length === 0) return "";
    return `${w.columns.join("/")} × 1–${w.rows}`;
  };

  const tabBtn = (key: Tab, label: string) => (
    <button key={key} onClick={() => setTab(key)} className={`px-3 py-1.5 text-sm ${tab === key ? "bg-indigo-600 text-white" : "bg-white text-slate-600 hover:bg-slate-100"}`}>{label}</button>
  );
  const input = "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2";

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-xl font-semibold">🔩 {t("parts.title")}</h1>
          <p className="text-sm text-slate-500">{t("parts.subtitle")}</p>
        </div>
        <div className="ml-auto flex flex-wrap gap-2">
          <button onClick={() => openMove("receipt")} className="rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-700">{t("parts.receipt")}</button>
          <button onClick={() => openMove("transfer")} className="rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700">{t("parts.transfer")}</button>
          <button onClick={() => openMove("adjust")} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-100">{t("parts.adjust")}</button>
        </div>
      </div>
      <div className="mb-4 flex overflow-hidden rounded-lg border border-slate-300 text-sm w-fit">
        {tabBtn("stock", t("parts.tab.stock"))}{tabBtn("parts", t("parts.tab.parts"))}{tabBtn("warehouses", t("parts.tab.warehouses"))}{tabBtn("movements", t("parts.tab.movements"))}{tabBtn("requests", `${t("parts.tab.requests")}${pendingCount ? ` (${pendingCount})` : ""}`)}
      </div>

      {/* ── Készlet ── */}
      {tab === "stock" && (
        <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
          {stock.length === 0 ? <p className="p-8 text-center text-sm text-slate-400">{t("parts.emptyStock")}</p> : (
            <table className="w-full text-sm">
              <thead><tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="px-3 py-2">{t("parts.warehouse")}</th><th className="px-3 py-2">{t("parts.location")}</th><th className="px-3 py-2">{t("parts.sku")}</th><th className="px-3 py-2">{t("parts.name")}</th><th className="px-3 py-2 text-right">{t("parts.quantity")}</th><th className="px-3 py-2"></th>
              </tr></thead>
              <tbody>
                {stock.map((s, i) => (
                  <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="px-3 py-2">{s.warehouse_name}</td>
                    <td className="px-3 py-2 font-mono text-xs">{s.location || "—"}</td>
                    <td className="px-3 py-2 font-mono text-xs">{s.sku}</td>
                    <td className="px-3 py-2">{s.name}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{s.quantity} {s.unit}</td>
                    <td className="px-3 py-2 text-right">
                      <button onClick={() => openMove("transfer", { part_id: s.part_id, warehouse_id: s.warehouse_id, location: s.location })} className="rounded-lg border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-100">🔁</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ── Cikktörzs ── */}
      {tab === "parts" && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("parts.search")} className="w-80 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm" />
            <label className="flex items-center gap-1.5 text-xs text-slate-500"><input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} className="h-3.5 w-3.5" />{t("parts.showInactive")}</label>
            <button onClick={() => setPartForm({ ...emptyPart })} className="ml-auto rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700">{t("parts.newPart")}</button>
          </div>
          <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
            {filteredParts.length === 0 ? <p className="p-8 text-center text-sm text-slate-400">{t("parts.emptyParts")}</p> : (
              <table className="w-full text-sm">
                <thead><tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="px-3 py-2">{t("parts.sku")}</th><th className="px-3 py-2">{t("parts.name")}</th><th className="px-3 py-2">{t("parts.manufacturerCode")}</th><th className="px-3 py-2 text-right">{t("parts.total")}</th><th className="px-3 py-2 text-right">{t("parts.minStock")}</th><th className="px-3 py-2 text-right">{t("parts.priceNet")}</th><th className="px-3 py-2"></th>
                </tr></thead>
                <tbody>
                  {filteredParts.map((p) => (
                    <tr key={p.id} className={`border-b border-slate-100 hover:bg-slate-50 ${p.is_active ? "" : "opacity-50"}`}>
                      <td className="px-3 py-2 font-mono text-xs">{p.sku}</td>
                      <td className="px-3 py-2 font-medium">{p.name}{p.low && <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-800">{t("parts.low")}</span>}</td>
                      <td className="px-3 py-2 text-xs text-slate-500">{p.manufacturer_code ?? "—"}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{p.total_qty} {p.unit}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-slate-500">{p.min_stock}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{ft(p.price_net)}</td>
                      <td className="px-3 py-2 text-right">
                        <button onClick={() => openMove("receipt", { part_id: p.id })} className="mr-1 rounded-lg border border-emerald-300 px-2 py-0.5 text-xs text-emerald-700 hover:bg-emerald-50">📥</button>
                        <button onClick={() => setPartForm({ id: p.id, sku: p.sku, name: p.name, manufacturer_code: p.manufacturer_code ?? "", unit: p.unit, min_stock: String(p.min_stock), price_net: p.price_net == null ? "" : String(p.price_net), cost_net: p.cost_net == null ? "" : String(p.cost_net), supplier: p.supplier ?? "", notes: p.notes ?? "", is_active: p.is_active })} className="rounded-lg border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-100">✏️</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* ── Raktárak ── */}
      {tab === "warehouses" && (
        <div className="space-y-3">
          <div className="flex"><button onClick={() => setWhForm({ ...emptyWh })} className="ml-auto rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700">{t("parts.newWarehouse")}</button></div>
          {warehouses.length === 0 ? <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-400 shadow-sm">{t("parts.emptyWarehouses")}</div> : (
            <div className="grid gap-3 md:grid-cols-2">
              {warehouses.map((w) => (
                <div key={w.id} className={`rounded-2xl border border-slate-200 bg-white p-4 shadow-sm ${w.is_active ? "" : "opacity-60"}`}>
                  <div className="flex items-center gap-2">
                    <h2 className="font-semibold">{w.kind === "vehicle" ? "🚚" : "🏬"} {w.name}</h2>
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">{t(`parts.kind.${w.kind}`)}</span>
                    {w.kind === "vehicle" && w.owner_user_id && <span className="text-xs text-slate-500">· {users.find((u) => u.id === w.owner_user_id)?.display_name ?? ""}</span>}
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    {w.kind === "central" && `${w.columns.join(", ")} × 1–${w.rows} = ${w.columns.length * w.rows} ${t("parts.locations")} · `}
                    {w.part_count} {t("parts.tab.parts").toLowerCase()} · {w.total_qty} db
                  </p>
                  {w.notes && <p className="mt-0.5 text-xs text-slate-400">{w.notes}</p>}
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {w.kind === "central" && <button onClick={() => setGridWh(w)} className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs hover:bg-slate-100">🗂 {t("parts.grid")}</button>}
                    <button onClick={() => setWhForm({ id: w.id, name: w.name, kind: w.kind, owner_user_id: w.owner_user_id ?? "", columns: w.columns.join(", "), rows: String(w.rows), notes: w.notes ?? "", is_active: w.is_active })} className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs hover:bg-slate-100">✏️ {t("common.edit")}</button>
                    <button onClick={() => void deleteWh(w)} className="rounded-lg border border-rose-300 px-2.5 py-1 text-xs text-rose-700 hover:bg-rose-50">🗑 {t("common.delete")}</button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Mozgások ── */}
      {tab === "movements" && (
        <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
          {movements.length === 0 ? <p className="p-8 text-center text-sm text-slate-400">{t("parts.emptyMovements")}</p> : (
            <table className="w-full text-sm">
              <thead><tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="px-3 py-2">{t("parts.when")}</th><th className="px-3 py-2">{t("parts.part")}</th><th className="px-3 py-2">{t("parts.warehouse")}</th><th className="px-3 py-2 text-right">{t("parts.delta")}</th><th className="px-3 py-2">{t("parts.ref")}</th><th className="px-3 py-2">{t("parts.who")}</th>
              </tr></thead>
              <tbody>
                {movements.map((m) => (
                  <tr key={m.id} className="border-b border-slate-100">
                    <td className="px-3 py-2 whitespace-nowrap text-xs text-slate-500">{fmt(m.created_at)}</td>
                    <td className="px-3 py-2"><span className="font-mono text-xs text-slate-400">{m.sku}</span> {m.name}</td>
                    <td className="px-3 py-2">{m.warehouse_name}{m.location && <span className="ml-1 font-mono text-xs text-slate-400">{m.location}</span>}</td>
                    <td className={`px-3 py-2 text-right tabular-nums font-medium ${m.quantity_delta < 0 ? "text-rose-700" : "text-emerald-700"}`}>{m.quantity_delta > 0 ? "+" : ""}{m.quantity_delta} {m.unit}</td>
                    <td className="px-3 py-2 text-xs text-slate-500">
                      <span className="rounded bg-slate-100 px-1.5 py-0.5">{t(`parts.mv.${m.kind}`)}</span>
                      {m.ref_warehouse_name && ` → ${m.ref_warehouse_name}${m.ref_location ? " " + m.ref_location : ""}`}
                      {m.document_no && ` · ${m.document_no}`}{m.unit_cost != null && ` · ${ft(m.unit_cost)}`}{m.note && ` · ${m.note}`}
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-500">{m.actor_name ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ── Kérések (szerelői kiadás-kérések) ── */}
      {tab === "requests" && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-xs text-slate-500">{t("parts.req.hint")}</p>
            <label className="ml-auto flex items-center gap-1.5 text-xs text-slate-500"><input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} className="h-3.5 w-3.5" />{t("parts.req.onlyOpen")}</label>
          </div>
          <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
            {shownRequests.length === 0 ? <p className="p-8 text-center text-sm text-slate-400">{t("parts.req.empty")}</p> : (
              <table className="w-full text-sm">
                <thead><tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="px-3 py-2">{t("parts.when")}</th><th className="px-3 py-2">{t("parts.req.requester")}</th><th className="px-3 py-2">{t("parts.part")}</th><th className="px-3 py-2 text-right">{t("parts.quantity")}</th><th className="px-3 py-2">{t("parts.req.target")}</th><th className="px-3 py-2">{t("parts.req.status")}</th><th className="px-3 py-2"></th>
                </tr></thead>
                <tbody>
                  {shownRequests.map((r) => (
                    <tr key={r.id} className="border-b border-slate-100">
                      <td className="px-3 py-2 whitespace-nowrap text-xs text-slate-500">{fmt(r.created_at)}</td>
                      <td className="px-3 py-2">{r.requester_name ?? "—"}</td>
                      <td className="px-3 py-2"><span className="font-mono text-xs text-slate-400">{r.sku}</span> {r.name}{r.note && <span className="block text-[11px] text-slate-400">„{r.note}”</span>}</td>
                      <td className="px-3 py-2 text-right tabular-nums font-medium">{r.quantity} {r.unit}</td>
                      <td className="px-3 py-2 text-xs">🚚 {r.warehouse_name ?? "—"}</td>
                      <td className="px-3 py-2 text-xs">
                        <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${REQ_CHIP[r.status]}`}>{t(`parts.req.${r.status}`)}</span>
                        {r.issued_from_name && <span className="block text-[11px] text-slate-400">{t("parts.req.issuedFrom")}: {r.issued_from_name} {r.issued_from_location}{r.decided_by_name ? ` · ${r.decided_by_name}` : ""}</span>}
                        {r.status === "declined" && r.decision_note && <span className="block text-[11px] text-slate-400">{r.decision_note}</span>}
                      </td>
                      <td className="px-3 py-2 text-right whitespace-nowrap">
                        {r.status === "pending" && (
                          <>
                            <button onClick={() => setIssue({ req: r, from_warehouse_id: warehouses.find((w) => w.kind === "central" && w.is_active)?.id ?? "", from_location: stock.find((s) => s.part_id === r.part_id && whById(s.warehouse_id)?.kind === "central")?.location ?? "", quantity: String(r.quantity), note: "" })} className="mr-1 rounded-lg bg-emerald-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-emerald-700">{t("parts.req.issue")}</button>
                            <button onClick={() => void declineReq(r)} className="rounded-lg border border-rose-300 px-2.5 py-1 text-xs text-rose-700 hover:bg-rose-50">{t("parts.req.decline")}</button>
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* ── Kiadás modal (kérésre) ── */}
      {issue && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={submitIssue} className="relative w-full max-w-lg space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => setIssue(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">📤 {t("parts.req.issueTitle")}</h2>
            <p className="text-sm"><span className="font-mono text-xs text-slate-400">{issue.req.sku}</span> {issue.req.name} · {issue.req.quantity} {issue.req.unit} → 🚚 {issue.req.warehouse_name} ({issue.req.requester_name})</p>
            <div className="grid grid-cols-2 gap-3">
              <div className="text-sm">
                <p className="mb-1 text-xs text-slate-500">{t("parts.from")} *</p>
                <SearchSelect items={whItems.filter((w) => w.id !== issue.req.warehouse_id)} value={issue.from_warehouse_id} onChange={(id) => setIssue({ ...issue, from_warehouse_id: id, from_location: "" })} placeholder={t("parts.warehouse")} />
              </div>
              <label className="block text-sm">{t("parts.location")}
                <input value={issue.from_location} disabled={whById(issue.from_warehouse_id)?.kind === "vehicle"} onChange={(e) => setIssue({ ...issue, from_location: e.target.value.toUpperCase() })} placeholder={locationHint(issue.from_warehouse_id)} className={input} />
              </label>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-sm">{t("parts.quantity")} *<input required type="number" min={0} step="any" value={issue.quantity} onChange={(e) => setIssue({ ...issue, quantity: e.target.value })} className={input} /></label>
              <label className="block text-sm">{t("parts.req.decisionNote")}<input value={issue.note} onChange={(e) => setIssue({ ...issue, note: e.target.value })} className={input} /></label>
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setIssue(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy || !issue.from_warehouse_id} className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50">{busy ? t("common.saving") : t("parts.req.issue")}</button>
            </div>
          </form>
        </div>
      )}

      {/* ── Alkatrész modal ── */}
      {partForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={savePart} className="relative max-h-[90vh] w-full max-w-lg space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => setPartForm(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">🔩 {partForm.id ? t("parts.editPart") : t("parts.newPart")}</h2>
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-sm">{t("parts.sku")} *<input required value={partForm.sku} onChange={(e) => setPartForm({ ...partForm, sku: e.target.value })} className={input} /></label>
              <label className="block text-sm">{t("parts.unit")}<input value={partForm.unit} onChange={(e) => setPartForm({ ...partForm, unit: e.target.value })} className={input} /></label>
            </div>
            <label className="block text-sm">{t("parts.name")} *<input required value={partForm.name} onChange={(e) => setPartForm({ ...partForm, name: e.target.value })} className={input} /></label>
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-sm">{t("parts.manufacturerCode")}<input value={partForm.manufacturer_code} onChange={(e) => setPartForm({ ...partForm, manufacturer_code: e.target.value })} className={input} /></label>
              <label className="block text-sm">{t("parts.supplier")}<input value={partForm.supplier} onChange={(e) => setPartForm({ ...partForm, supplier: e.target.value })} className={input} /></label>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <label className="block text-sm">{t("parts.minStock")}<input type="number" min={0} step="any" value={partForm.min_stock} onChange={(e) => setPartForm({ ...partForm, min_stock: e.target.value })} className={input} /></label>
              <label className="block text-sm">{t("parts.costNet")}<input type="number" min={0} value={partForm.cost_net} onChange={(e) => setPartForm({ ...partForm, cost_net: e.target.value })} className={input} /></label>
              <label className="block text-sm">{t("parts.priceNet")}<input type="number" min={0} value={partForm.price_net} onChange={(e) => setPartForm({ ...partForm, price_net: e.target.value })} className={input} /></label>
            </div>
            <label className="block text-sm">{t("parts.notes")}<input value={partForm.notes} onChange={(e) => setPartForm({ ...partForm, notes: e.target.value })} className={input} /></label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={partForm.is_active} onChange={(e) => setPartForm({ ...partForm, is_active: e.target.checked })} className="h-4 w-4" />{t("parts.active")}</label>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setPartForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{busy ? t("common.saving") : t("common.save")}</button>
            </div>
          </form>
        </div>
      )}

      {/* ── Raktár modal ── */}
      {whForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={saveWh} className="relative max-h-[90vh] w-full max-w-lg space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => setWhForm(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">🏬 {whForm.id ? t("parts.editWarehouse") : t("parts.newWarehouse")}</h2>
            <label className="block text-sm">{t("parts.whName")} *<input required value={whForm.name} onChange={(e) => setWhForm({ ...whForm, name: e.target.value })} className={input} /></label>
            <label className="block text-sm">{t("parts.whKind")}
              <select value={whForm.kind} onChange={(e) => setWhForm({ ...whForm, kind: e.target.value as "central" | "vehicle" })} className={input}>
                <option value="central">{t("parts.kind.central")}</option><option value="vehicle">{t("parts.kind.vehicle")}</option>
              </select>
            </label>
            {whForm.kind === "vehicle" ? (
              <label className="block text-sm">{t("parts.owner")}
                <select value={whForm.owner_user_id} onChange={(e) => setWhForm({ ...whForm, owner_user_id: e.target.value })} className={input}>
                  <option value="">—</option>{users.map((u) => <option key={u.id} value={u.id}>{u.display_name}</option>)}
                </select>
              </label>
            ) : (
              <div className="grid grid-cols-3 gap-3">
                <label className="col-span-2 block text-sm">{t("parts.columns")}<input value={whForm.columns} onChange={(e) => setWhForm({ ...whForm, columns: e.target.value })} className={input} /></label>
                <label className="block text-sm">{t("parts.rows")}<input type="number" min={1} max={999} value={whForm.rows} onChange={(e) => setWhForm({ ...whForm, rows: e.target.value })} className={input} /></label>
              </div>
            )}
            <label className="block text-sm">{t("parts.notes")}<input value={whForm.notes} onChange={(e) => setWhForm({ ...whForm, notes: e.target.value })} className={input} /></label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={whForm.is_active} onChange={(e) => setWhForm({ ...whForm, is_active: e.target.checked })} className="h-4 w-4" />{t("parts.active")}</label>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setWhForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{busy ? t("common.saving") : t("common.save")}</button>
            </div>
          </form>
        </div>
      )}

      {/* ── Helykód-rács modal ── */}
      {gridWh && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="relative max-h-[85vh] w-full max-w-4xl space-y-3 overflow-auto rounded-2xl bg-white p-6 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => setGridWh(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">🗂 {gridWh.name} — {t("parts.grid")}</h2>
            <p className="text-xs text-slate-500">{t("parts.gridHint")}</p>
            <div className="overflow-auto">
              <table className="text-xs">
                <thead><tr><th className="p-1"></th>{gridWh.columns.map((c) => <th key={c} className="p-1 text-center font-semibold">{c}</th>)}</tr></thead>
                <tbody>
                  {Array.from({ length: gridWh.rows }, (_, i) => i + 1).map((r) => (
                    <tr key={r}>
                      <td className="p-1 text-right font-semibold text-slate-500">{r}</td>
                      {gridWh.columns.map((c) => {
                        const loc = `${c}-${r}`;
                        const here = stock.filter((s) => s.warehouse_id === gridWh.id && s.location === loc);
                        const qty = here.reduce((a, s) => a + s.quantity, 0);
                        return (
                          <td key={c} className="p-0.5">
                            <button
                              title={here.map((s) => `${s.name}: ${s.quantity} ${s.unit}`).join("\n") || loc}
                              onClick={() => openMove("receipt", { warehouse_id: gridWh.id, location: loc, part_id: here[0]?.part_id ?? "" })}
                              className={`h-9 w-16 rounded border text-[10px] ${qty > 0 ? "border-indigo-300 bg-indigo-100 font-semibold text-indigo-900" : "border-slate-200 bg-slate-50 text-slate-400"}`}
                            >
                              {loc}{qty > 0 && <><br />{here.length === 1 ? `${qty}` : `${here.length} cikk`}</>}
                            </button>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex justify-end"><button onClick={() => setGridWh(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.close")}</button></div>
          </div>
        </div>
      )}

      {/* ── Mozgás modal (vételezés / áthelyezés / korrekció) ── */}
      {move && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={submitMove} className="relative max-h-[90vh] w-full max-w-lg space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => setMove(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">{t(`parts.${move.kind}`)}</h2>
            <div className="text-sm">
              <p className="mb-1 text-xs text-slate-500">{t("parts.part")} *</p>
              <SearchSelect items={partItems} value={move.part_id} onChange={(id) => setMove({ ...move, part_id: id })} placeholder={t("parts.search")} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="text-sm">
                <p className="mb-1 text-xs text-slate-500">{move.kind === "transfer" ? t("parts.from") : t("parts.warehouse")} *</p>
                <SearchSelect items={whItems} value={move.warehouse_id} onChange={(id) => setMove({ ...move, warehouse_id: id, location: "" })} placeholder={t("parts.warehouse")} />
              </div>
              <label className="block text-sm">{t("parts.location")}
                <input value={move.location} disabled={whById(move.warehouse_id)?.kind === "vehicle"} onChange={(e) => setMove({ ...move, location: e.target.value.toUpperCase() })} placeholder={locationHint(move.warehouse_id)} className={input} />
              </label>
            </div>
            {move.kind === "transfer" && (
              <div className="grid grid-cols-2 gap-3">
                <div className="text-sm">
                  <p className="mb-1 text-xs text-slate-500">{t("parts.to")} *</p>
                  <SearchSelect items={whItems} value={move.to_warehouse_id} onChange={(id) => setMove({ ...move, to_warehouse_id: id, to_location: "" })} placeholder={t("parts.warehouse")} />
                </div>
                <label className="block text-sm">{t("parts.location")}
                  <input value={move.to_location} disabled={whById(move.to_warehouse_id)?.kind === "vehicle"} onChange={(e) => setMove({ ...move, to_location: e.target.value.toUpperCase() })} placeholder={locationHint(move.to_warehouse_id)} className={input} />
                </label>
              </div>
            )}
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-sm">{move.kind === "adjust" ? t("parts.actualQty") : t("parts.quantity")} *<input required type="number" min={0} step="any" value={move.quantity} onChange={(e) => setMove({ ...move, quantity: e.target.value })} className={input} /></label>
              {move.kind === "receipt" && <label className="block text-sm">{t("parts.unitCost")}<input type="number" min={0} value={move.unit_cost} onChange={(e) => setMove({ ...move, unit_cost: e.target.value })} className={input} /></label>}
            </div>
            {move.kind === "receipt" && <label className="block text-sm">{t("parts.documentNo")}<input value={move.document_no} onChange={(e) => setMove({ ...move, document_no: e.target.value })} className={input} /></label>}
            <label className="block text-sm">{t("parts.notes")}<input value={move.note} onChange={(e) => setMove({ ...move, note: e.target.value })} className={input} /></label>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setMove(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy || !move.part_id || !move.warehouse_id || (move.kind === "transfer" && !move.to_warehouse_id)} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{busy ? t("common.saving") : t("common.save")}</button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
