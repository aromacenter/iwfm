"use client";

/** Raktár: telephelyi és autó-raktárak készlete (bevét, áthelyezés, leltár)
 *  + beszerzési rendelések (szállító → raktár, beérkeztetéssel). Az áruút:
 *  beszerzés → telephely → üzletkötői autó → partner külső raktára. */

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import IconLegend from "@/components/IconLegend";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface Warehouse {
  id: string;
  name: string;
  kind: "site" | "van";
  user_id: string | null;
  user_name: string | null;
  notes: string | null;
  is_active: boolean;
  product_count: number;
  total_quantity: number;
}

interface WhStock {
  product_id: string;
  product_name: string;
  unit: string;
  quantity: number;
  min_quantity: number | null;
}

interface Product {
  id: string;
  name: string;
  unit: string;
  purchase_price: number | null;
  is_active: boolean;
}

interface PartnerLite {
  id: string;
  name: string;
  partner_type: string;
}

interface POLine {
  id: string;
  product_id: string;
  product_name: string;
  unit: string;
  quantity: number;
  unit_cost: number | null;
  received_qty: number;
}

interface PO {
  id: string;
  order_no: string;
  supplier_name: string | null;
  warehouse_id: string | null;
  warehouse_name: string | null;
  status: "draft" | "ordered" | "received" | "cancelled";
  expected_at: string | null;
  note: string | null;
  created_at: string;
  lines: POLine[];
}

interface Suggestion {
  warehouse_name: string;
  product_name: string;
  unit: string;
  quantity: number;
  min_quantity: number;
  suggested_qty: number;
}

const PO_CHIP: Record<PO["status"], string> = {
  draft: "bg-slate-100 text-slate-700",
  ordered: "bg-sky-100 text-sky-800",
  received: "bg-emerald-100 text-emerald-800",
  cancelled: "bg-slate-200 text-slate-500 line-through",
};

export default function RaktarPage() {
  const { t } = useT();
  const { toast } = useUI();
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [stock, setStock] = useState<WhStock[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [partners, setPartners] = useState<PartnerLite[]>([]);
  const [pos, setPos] = useState<PO[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);

  const selected = warehouses.find((w) => w.id === selectedId) ?? null;

  const load = useCallback(() => {
    api.get<Warehouse[]>("/api/warehouses").then((rows) => {
      setWarehouses(rows);
      setSelectedId((cur) => cur ?? rows[0]?.id ?? null);
    }).catch(() => {});
    api.get<Product[]>("/api/products").then(setProducts).catch(() => {});
    api.get<PartnerLite[]>("/api/partners").then(setPartners).catch(() => {});
    api.get<PO[]>("/api/purchase-orders").then(setPos).catch(() => {});
    api.get<Suggestion[]>("/api/warehouses/reorder-suggestions").then(setSuggestions).catch(() => {});
  }, []);
  useEffect(load, [load]);

  const loadStock = useCallback(() => {
    if (!selectedId) { setStock([]); return; }
    api.get<WhStock[]>(`/api/warehouses/${selectedId}/stock`).then(setStock).catch(() => {});
  }, [selectedId]);
  useEffect(loadStock, [loadStock]);

  const activeProducts = useMemo(() => products.filter((p) => p.is_active), [products]);
  const suppliers = useMemo(
    () => partners.filter((p) => p.partner_type === "supplier" || p.partner_type === "both"),
    [partners],
  );

  // ── Raktár űrlap ──
  const [whForm, setWhForm] = useState<{ id: string; name: string; kind: "site" | "van"; notes: string; is_active: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function saveWarehouse(e: React.FormEvent) {
    e.preventDefault();
    if (!whForm) return;
    setBusy(true);
    setError(null);
    try {
      const body = { name: whForm.name, kind: whForm.kind, notes: whForm.notes || null, is_active: whForm.is_active };
      if (whForm.id) await api.patch(`/api/warehouses/${whForm.id}`, body);
      else await api.post("/api/warehouses", body);
      setWhForm(null);
      load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  // ── Készlet-műveletek ──
  type StockAction = "receive" | "transfer" | "adjust";
  const [action, setAction] = useState<{ kind: StockAction; product_id: string; qty: string; unit_cost: string; to_id: string; min_qty: string; note: string } | null>(null);

  function openAction(kind: StockAction, productId?: string) {
    const row = productId ? stock.find((s) => s.product_id === productId) : null;
    setError(null);
    setAction({
      kind,
      product_id: productId ?? activeProducts[0]?.id ?? "",
      qty: kind === "adjust" && row ? String(row.quantity) : "",
      unit_cost: "",
      to_id: warehouses.find((w) => w.id !== selectedId)?.id ?? "",
      min_qty: row?.min_quantity?.toString() ?? "",
      note: "",
    });
  }

  async function runAction(e: React.FormEvent) {
    e.preventDefault();
    if (!action || !selectedId) return;
    setBusy(true);
    setError(null);
    try {
      if (action.kind === "receive") {
        await api.post(`/api/warehouses/${selectedId}/receive`, {
          product_id: action.product_id,
          quantity: Number(action.qty),
          unit_cost: action.unit_cost ? Number(action.unit_cost) : null,
          note: action.note || null,
        });
      } else if (action.kind === "transfer") {
        await api.post("/api/warehouses/transfer", {
          from_warehouse_id: selectedId,
          to_warehouse_id: action.to_id,
          product_id: action.product_id,
          quantity: Number(action.qty),
          note: action.note || null,
        });
      } else {
        await api.post(`/api/warehouses/${selectedId}/adjust`, {
          product_id: action.product_id,
          counted_qty: Number(action.qty),
          min_quantity: action.min_qty ? Number(action.min_qty) : null,
          note: action.note || null,
        });
      }
      toast(t(`wh.${action.kind}Done`), "success");
      setAction(null);
      load();
      loadStock();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  // ── Beszerzési rendelés űrlap ──
  const [poForm, setPoForm] = useState<{
    supplier_partner_id: string;
    warehouse_id: string;
    expected_at: string;
    note: string;
    lines: { product_id: string; quantity: string; unit_cost: string }[];
  } | null>(null);

  function openPoForm() {
    setError(null);
    setPoForm({
      supplier_partner_id: suppliers[0]?.id ?? "",
      warehouse_id: warehouses.find((w) => w.kind === "site")?.id ?? warehouses[0]?.id ?? "",
      expected_at: "",
      note: "",
      lines: [{
        product_id: activeProducts[0]?.id ?? "",
        quantity: "",
        unit_cost: activeProducts[0]?.purchase_price?.toString() ?? "",
      }],
    });
  }

  async function savePo(e: React.FormEvent) {
    e.preventDefault();
    if (!poForm) return;
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/purchase-orders", {
        supplier_partner_id: poForm.supplier_partner_id || null,
        warehouse_id: poForm.warehouse_id,
        expected_at: poForm.expected_at || null,
        note: poForm.note || null,
        lines: poForm.lines
          .filter((l) => l.product_id && Number(l.quantity) > 0)
          .map((l) => ({
            product_id: l.product_id,
            quantity: Number(l.quantity),
            unit_cost: l.unit_cost ? Number(l.unit_cost) : null,
          })),
      });
      toast(t("po.created"), "success");
      setPoForm(null);
      load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function poTransition(po: PO, verb: "order" | "receive" | "cancel") {
    try {
      await api.post(`/api/purchase-orders/${po.id}/${verb}`, {});
      toast(t(`po.${verb}Done`), "success");
      load();
      loadStock();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  const fmtQty = (n: number) => n.toLocaleString("hu-HU", { maximumFractionDigits: 2 });

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("wh.title")}</h1>
        <button
          onClick={() => { setError(null); setWhForm({ id: "", name: "", kind: "site", notes: "", is_active: true }); }}
          className="ml-auto rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
        >
          {t("wh.new")}
        </button>
      </div>

      {suggestions.length > 0 && (
        <div className="mb-4 rounded-2xl border border-rose-200 bg-rose-50 p-4">
          <div className="mb-1 text-sm font-semibold text-rose-900">{t("wh.reorderTitle")}</div>
          <ul className="space-y-0.5 text-sm text-rose-800">
            {suggestions.map((s, i) => (
              <li key={i}>
                {t("wh.reorderRow", {
                  warehouse: s.warehouse_name, product: s.product_name,
                  qty: fmtQty(s.quantity), unit: s.unit, suggested: fmtQty(s.suggested_qty),
                })}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {warehouses.map((w) => (
          <button
            key={w.id}
            onClick={() => setSelectedId(w.id)}
            className={`rounded-2xl border p-4 text-left shadow-sm transition ${
              w.id === selectedId
                ? "border-indigo-400 bg-indigo-50"
                : "border-slate-200 bg-white hover:border-indigo-200"
            } ${w.is_active ? "" : "opacity-60"}`}
          >
            <div className="flex items-center justify-between">
              <span className="font-semibold">{w.kind === "van" ? "🚐" : "🏬"} {w.name}</span>
              <span
                onClick={(e) => {
                  e.stopPropagation();
                  setError(null);
                  setWhForm({ id: w.id, name: w.name, kind: w.kind, notes: w.notes ?? "", is_active: w.is_active });
                }}
                title={t("common.edit")}
                className="cursor-pointer rounded border border-slate-300 px-1.5 py-0.5 text-xs hover:bg-slate-100"
              >
                ✏️
              </span>
            </div>
            <div className="mt-1 text-xs text-slate-500">
              {t(w.kind === "van" ? "wh.kindVan" : "wh.kindSite")}
              {!w.is_active && ` · ${t("partners.inactive")}`}
            </div>
            <div className="mt-2 text-sm text-slate-700">
              {t("wh.cardSummary", { products: w.product_count, qty: fmtQty(w.total_quantity) })}
            </div>
          </button>
        ))}
        {warehouses.length === 0 && (
          <div className="col-span-full rounded-2xl border border-dashed border-slate-300 p-8 text-center text-sm text-slate-400">
            {t("wh.empty")}
          </div>
        )}
      </div>

      {selected && (
        <div className="mb-6 rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-4 py-3">
            <h2 className="font-semibold">{t("wh.stockTitle", { name: selected.name })}</h2>
            <div className="ml-auto flex flex-wrap gap-2">
              <button onClick={() => openAction("receive")} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700">
                ⬇️ {t("wh.receive")}
              </button>
              {warehouses.length > 1 && (
                <button onClick={() => openAction("transfer")} className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-700">
                  🔁 {t("wh.transfer")}
                </button>
              )}
              <button onClick={() => openAction("adjust")} className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium hover:bg-slate-100">
                📋 {t("wh.adjust")}
              </button>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-slate-500">
                  <th className="px-4 py-2">{t("cons.name")}</th>
                  <th className="px-4 py-2">{t("wh.quantity")}</th>
                  <th className="px-4 py-2">{t("wh.minQuantity")}</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {stock.map((s) => (
                  <tr key={s.product_id} className="border-t border-slate-100">
                    <td className="px-4 py-2 font-medium">{s.product_name}</td>
                    <td className={`px-4 py-2 ${s.min_quantity != null && s.quantity <= s.min_quantity ? "font-semibold text-rose-700" : ""}`}>
                      {fmtQty(s.quantity)} {s.unit}
                    </td>
                    <td className="px-4 py-2 text-slate-500">
                      {s.min_quantity != null ? `${fmtQty(s.min_quantity)} ${s.unit}` : "—"}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <button
                        onClick={() => openAction("adjust", s.product_id)}
                        title={t("wh.adjust")}
                        className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100"
                      >
                        📋
                      </button>
                    </td>
                  </tr>
                ))}
                {stock.length === 0 && (
                  <tr><td colSpan={4} className="px-4 py-8 text-center text-slate-400">{t("wh.noStock")}</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-4 py-3">
          <h2 className="font-semibold">{t("po.title")}</h2>
          <button
            onClick={openPoForm}
            className="ml-auto rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700"
          >
            {t("po.new")}
          </button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase text-slate-500">
                <th className="px-4 py-2">{t("po.orderNo")}</th>
                <th className="px-4 py-2">{t("po.supplier")}</th>
                <th className="px-4 py-2">{t("po.warehouse")}</th>
                <th className="px-4 py-2">{t("po.linesCol")}</th>
                <th className="px-4 py-2">{t("po.expected")}</th>
                <th className="px-4 py-2">{t("po.status")}</th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {pos.map((po) => (
                <tr key={po.id} className="border-t border-slate-100 align-top">
                  <td className="px-4 py-2 font-medium">{po.order_no}</td>
                  <td className="px-4 py-2">{po.supplier_name ?? "—"}</td>
                  <td className="px-4 py-2">{po.warehouse_name ?? "—"}</td>
                  <td className="px-4 py-2 text-xs text-slate-600">
                    {po.lines.map((l) => (
                      <div key={l.id}>
                        {l.product_name}: {fmtQty(l.quantity)} {l.unit}
                        {l.unit_cost != null && ` × ${l.unit_cost.toLocaleString("hu-HU")} Ft`}
                        {l.received_qty > 0 && l.received_qty < l.quantity && (
                          <span className="ml-1 text-sky-700">({t("po.partial", { qty: fmtQty(l.received_qty) })})</span>
                        )}
                      </div>
                    ))}
                  </td>
                  <td className="px-4 py-2 text-slate-500">{po.expected_at ?? "—"}</td>
                  <td className="px-4 py-2">
                    <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${PO_CHIP[po.status]}`}>
                      {t(`po.status_${po.status}`)}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right">
                    <div className="flex justify-end gap-1">
                      {po.status === "draft" && (
                        <button onClick={() => poTransition(po, "order")} title={t("po.markOrdered")} className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100">📨</button>
                      )}
                      {(po.status === "draft" || po.status === "ordered") && (
                        <>
                          <button onClick={() => poTransition(po, "receive")} title={t("po.receiveAll")} className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100">✅</button>
                          <button onClick={() => poTransition(po, "cancel")} title={t("po.cancel")} className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100">🚫</button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
              {pos.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-8 text-center text-slate-400">{t("po.empty")}</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <IconLegend
        items={[
          { icon: "📨", label: t("po.markOrdered") },
          { icon: "✅", label: t("po.receiveAll") },
          { icon: "🚫", label: t("po.cancel") },
          { icon: "📋", label: t("wh.adjust") },
        ]}
      />

      {whForm && (
        <div
          onMouseDown={(e) => { if (e.target === e.currentTarget) setWhForm(null); }}
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4"
        >
          <form onSubmit={saveWarehouse} className="my-8 w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{whForm.id ? t("wh.edit") : t("wh.new")}</h2>
            <label className="block text-sm">
              {t("cons.name")} *
              <input required value={whForm.name} onChange={(e) => setWhForm({ ...whForm, name: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              {t("wh.kind")}
              <select value={whForm.kind} onChange={(e) => setWhForm({ ...whForm, kind: e.target.value as "site" | "van" })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2">
                <option value="site">{t("wh.kindSite")}</option>
                <option value="van">{t("wh.kindVan")}</option>
              </select>
            </label>
            <label className="block text-sm">
              {t("cons.notes")}
              <textarea value={whForm.notes} onChange={(e) => setWhForm({ ...whForm, notes: e.target.value })} rows={2} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={whForm.is_active} onChange={(e) => setWhForm({ ...whForm, is_active: e.target.checked })} className="h-4 w-4" />
              {t("cons.active")}
            </label>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setWhForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{busy ? t("common.saving") : t("common.save")}</button>
            </div>
          </form>
        </div>
      )}

      {action && selected && (
        <div
          onMouseDown={(e) => { if (e.target === e.currentTarget) setAction(null); }}
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4"
        >
          <form onSubmit={runAction} className="my-8 w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">
              {t(`wh.${action.kind}`)} — {selected.name}
            </h2>
            <label className="block text-sm">
              {t("wh.product")} *
              <select
                required
                value={action.product_id}
                onChange={(e) => setAction({ ...action, product_id: e.target.value })}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              >
                {activeProducts.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </label>
            {action.kind === "transfer" && (
              <label className="block text-sm">
                {t("wh.transferTo")} *
                <select
                  required
                  value={action.to_id}
                  onChange={(e) => setAction({ ...action, to_id: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                >
                  {warehouses.filter((w) => w.id !== selectedId).map((w) => (
                    <option key={w.id} value={w.id}>{w.kind === "van" ? "🚐" : "🏬"} {w.name}</option>
                  ))}
                </select>
              </label>
            )}
            <label className="block text-sm">
              {action.kind === "adjust" ? t("wh.countedQty") : t("wh.quantity")} *
              <input required type="number" min={action.kind === "adjust" ? 0 : 0.001} step="0.001" value={action.qty} onChange={(e) => setAction({ ...action, qty: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            {action.kind === "receive" && (
              <label className="block text-sm">
                {t("cons.purchasePrice")}
                <input type="number" min={0} step="0.01" value={action.unit_cost} onChange={(e) => setAction({ ...action, unit_cost: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
            )}
            {action.kind === "adjust" && (
              <label className="block text-sm">
                {t("wh.minQuantity")}
                <input type="number" min={0} step="0.1" value={action.min_qty} onChange={(e) => setAction({ ...action, min_qty: e.target.value })} placeholder={t("cons.noAlert")} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
            )}
            <label className="block text-sm">
              {t("cons.notes")}
              <input value={action.note} onChange={(e) => setAction({ ...action, note: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setAction(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{busy ? t("common.saving") : t("common.save")}</button>
            </div>
          </form>
        </div>
      )}

      {poForm && (
        <div
          onMouseDown={(e) => { if (e.target === e.currentTarget) setPoForm(null); }}
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4"
        >
          <form onSubmit={savePo} className="my-8 w-full max-w-lg space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{t("po.new")}</h2>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="block text-sm">
                {t("po.supplier")}
                <select value={poForm.supplier_partner_id} onChange={(e) => setPoForm({ ...poForm, supplier_partner_id: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2">
                  <option value="">—</option>
                  {suppliers.map((s) => (
                    <option key={s.id} value={s.id}>{s.name}</option>
                  ))}
                </select>
              </label>
              <label className="block text-sm">
                {t("po.warehouse")} *
                <select required value={poForm.warehouse_id} onChange={(e) => setPoForm({ ...poForm, warehouse_id: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2">
                  {warehouses.filter((w) => w.is_active).map((w) => (
                    <option key={w.id} value={w.id}>{w.kind === "van" ? "🚐" : "🏬"} {w.name}</option>
                  ))}
                </select>
              </label>
            </div>
            <label className="block text-sm">
              {t("po.expected")}
              <input type="date" value={poForm.expected_at} onChange={(e) => setPoForm({ ...poForm, expected_at: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <div>
              <div className="mb-1 text-sm font-medium">{t("po.linesCol")}</div>
              {poForm.lines.map((line, i) => (
                <div key={i} className="mb-2 flex items-center gap-2">
                  <select
                    value={line.product_id}
                    onChange={(e) => {
                      const prod = activeProducts.find((p) => p.id === e.target.value);
                      const lines = [...poForm.lines];
                      lines[i] = {
                        ...line, product_id: e.target.value,
                        unit_cost: line.unit_cost || prod?.purchase_price?.toString() || "",
                      };
                      setPoForm({ ...poForm, lines });
                    }}
                    className="flex-1 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                  >
                    {activeProducts.map((p) => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                  </select>
                  <input
                    type="number" min={0.001} step="0.001" required
                    placeholder={t("wh.quantity")}
                    value={line.quantity}
                    onChange={(e) => {
                      const lines = [...poForm.lines];
                      lines[i] = { ...line, quantity: e.target.value };
                      setPoForm({ ...poForm, lines });
                    }}
                    className="w-24 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                  />
                  <input
                    type="number" min={0} step="0.01"
                    placeholder={t("po.unitCost")}
                    value={line.unit_cost}
                    onChange={(e) => {
                      const lines = [...poForm.lines];
                      lines[i] = { ...line, unit_cost: e.target.value };
                      setPoForm({ ...poForm, lines });
                    }}
                    className="w-28 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                  />
                  {poForm.lines.length > 1 && (
                    <button
                      type="button"
                      onClick={() => setPoForm({ ...poForm, lines: poForm.lines.filter((_, j) => j !== i) })}
                      className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100"
                    >
                      ✕
                    </button>
                  )}
                </div>
              ))}
              <button
                type="button"
                onClick={() => setPoForm({
                  ...poForm,
                  lines: [...poForm.lines, { product_id: activeProducts[0]?.id ?? "", quantity: "", unit_cost: "" }],
                })}
                className="text-sm text-indigo-600 hover:underline"
              >
                + {t("po.addLine")}
              </button>
            </div>
            <label className="block text-sm">
              {t("cons.notes")}
              <input value={poForm.note} onChange={(e) => setPoForm({ ...poForm, note: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setPoForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{busy ? t("common.saving") : t("common.save")}</button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
