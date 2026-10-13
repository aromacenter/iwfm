"use client";

/** Saját raktár (7bd7dbd8): a dolgozó saját (autó-)raktárának készlete,
 * áru-igénylés a központi raktárból, és a beérkező átadások elfogadása —
 * az áru csak elfogadás után kerül a raktárába. Innen adható át áru a
 * központi raktárba vagy másik dolgozó autójába is (90b463c4) — a fogadó
 * fél elfogadásáig függőben. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import SearchSelect from "@/components/SearchSelect";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface StockRow {
  product_id: string;
  product_name: string;
  unit: string;
  quantity: number;
}

interface Incoming {
  id: string;
  product_name: string;
  quantity: number;
  unit: string;
  from_name: string;
  note: string | null;
  created_at: string;
}

interface MyWarehouse {
  warehouse_id: string | null;
  warehouse_name: string | null;
  stock: StockRow[];
  incoming: Incoming[];
}

interface Req {
  id: string;
  product_name: string | null;
  unit: string | null;
  quantity: number;
  note: string | null;
  status: "pending" | "approved" | "declined";
  decided_by_name: string | null;
  created_at: string;
}

const REQ_CHIP: Record<string, string> = {
  pending: "bg-amber-100 text-amber-800",
  approved: "bg-emerald-100 text-emerald-800",
  declined: "bg-rose-100 text-rose-800",
};

interface WarehouseRow {
  id: string;
  name: string;
  kind: string;
  user_id: string | null;
  user_name: string | null;
  is_active: boolean;
}

interface OutTransfer {
  id: string;
  from_warehouse: string;
  to_warehouse: string;
  product_name: string;
  unit: string;
  quantity: number;
  status: string;
  can_cancel: boolean;
}

export default function SajatRaktarPage() {
  const { t } = useT();
  const { toast } = useUI();
  const [wh, setWh] = useState<MyWarehouse | null>(null);
  const [reqs, setReqs] = useState<Req[]>([]);
  const [products, setProducts] = useState<{ id: string; name: string; unit: string; code: string | null; is_active: boolean }[]>([]);
  const [productId, setProductId] = useState("");
  const [qty, setQty] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  // Áru átadása innen (90b463c4): cél-raktár + termék a saját készletből
  const [warehouses, setWarehouses] = useState<WarehouseRow[]>([]);
  const [outgoing, setOutgoing] = useState<OutTransfer[]>([]);
  const [giveProductId, setGiveProductId] = useState("");
  const [giveQty, setGiveQty] = useState("");
  const [giveTo, setGiveTo] = useState("");
  const [giveNote, setGiveNote] = useState("");

  const load = useCallback(() => {
    api.get<MyWarehouse>("/api/stock-requests/my-warehouse").then(setWh).catch(() => {});
    api.get<Req[]>("/api/stock-requests").then(setReqs).catch(() => {});
    api.get<typeof products>("/api/products").then((rows) => setProducts(rows.filter((p) => p.is_active))).catch(() => {});
    api.get<WarehouseRow[]>("/api/warehouses").then((rows) => setWarehouses(rows.filter((w) => w.is_active))).catch(() => {});
    api.get<OutTransfer[]>("/api/warehouses/transfers?status=pending").then(setOutgoing).catch(() => {});
  }, []);
  useEffect(load, [load]);

  async function sendRequest(e: React.FormEvent) {
    e.preventDefault();
    if (!productId || !qty) return;
    setBusy(true);
    try {
      await api.post("/api/stock-requests", {
        product_id: productId,
        quantity: Number(qty),
        note: note.trim() || null,
      });
      toast(t("streq.sent"), "success");
      setProductId("");
      setQty("");
      setNote("");
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function decideTransfer(id: string, accept: boolean) {
    try {
      await api.post(`/api/warehouses/transfers/${id}/${accept ? "accept" : "reject"}`, {});
      toast(accept ? t("streq.transferAccepted") : t("streq.transferRejected"), "success");
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  // Áru átadása a saját raktárból (90b463c4) — a fogadó elfogadásáig függő
  async function sendGive(e: React.FormEvent) {
    e.preventDefault();
    if (!wh?.warehouse_id || !giveProductId || !giveQty || !giveTo) return;
    setBusy(true);
    try {
      await api.post("/api/warehouses/transfer", {
        from_warehouse_id: wh.warehouse_id,
        to_warehouse_id: giveTo,
        product_id: giveProductId,
        quantity: Number(giveQty),
        note: giveNote.trim() || null,
      });
      toast(t("streq.giveSent"), "success");
      setGiveProductId("");
      setGiveQty("");
      setGiveTo("");
      setGiveNote("");
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function cancelOutgoing(id: string) {
    try {
      await api.post(`/api/warehouses/transfers/${id}/cancel`, {});
      toast(t("streq.giveCancelled"), "success");
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  return (
    <AppShell>
      <div className="mb-4">
        <h1 className="text-xl font-semibold">🚚 {t("streq.title")}</h1>
        <p className="text-sm text-slate-500">{t("streq.subtitle")}</p>
      </div>

      {wh && wh.warehouse_id === null && (
        <div className="mb-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {t("streq.noWarehouse")}
        </div>
      )}

      {/* Beérkező átadások — elfogadásig nincs a raktárban */}
      {wh && wh.incoming.length > 0 && (
        <div className="mb-4 space-y-2">
          {wh.incoming.map((tr) => (
            <div key={tr.id} className="flex flex-wrap items-center gap-3 rounded-2xl border border-indigo-200 bg-indigo-50 px-4 py-3 text-sm">
              <span className="font-medium text-indigo-900">
                📦 {tr.quantity.toLocaleString("hu-HU")} {tr.unit} {tr.product_name}
              </span>
              <span className="text-indigo-700">← {tr.from_name}</span>
              {tr.note && <span className="text-xs text-indigo-600">({tr.note})</span>}
              <div className="ml-auto flex gap-2">
                <button
                  onClick={() => void decideTransfer(tr.id, true)}
                  className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white"
                >
                  ✔ {t("streq.accept")}
                </button>
                <button
                  onClick={() => void decideTransfer(tr.id, false)}
                  className="rounded-lg border border-rose-300 px-3 py-1.5 text-xs text-rose-700"
                >
                  ✕ {t("streq.reject")}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Saját készlet + átadás innen */}
        <div className="space-y-4">
          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="mb-2 font-semibold">
              📦 {wh?.warehouse_name ?? t("streq.myStock")}
            </h2>
            {wh && wh.stock.length === 0 ? (
              <p className="py-6 text-center text-sm text-slate-400">{t("streq.emptyStock")}</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-slate-500">
                    <th className="py-1.5">{t("stk.product")}</th>
                    <th className="py-1.5 text-right">{t("streq.qty")}</th>
                  </tr>
                </thead>
                <tbody>
                  {(wh?.stock ?? []).map((s) => (
                    <tr key={s.product_id} className="border-t border-slate-100">
                      <td className="py-1.5">{s.product_name}</td>
                      <td className="py-1.5 text-right tabular-nums">
                        {s.quantity.toLocaleString("hu-HU")} {s.unit}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Áru átadása a saját raktárból (90b463c4) */}
          {wh?.warehouse_id && (
            <form onSubmit={sendGive} className="rounded-2xl border border-indigo-200 bg-indigo-50 p-4 shadow-sm">
              <h2 className="mb-2 font-semibold text-indigo-900">🤝 {t("streq.giveTitle")}</h2>
              <div className="space-y-2">
                <SearchSelect
                  items={(wh.stock ?? []).filter((s) => s.quantity > 0).map((s) => ({
                    id: s.product_id,
                    label: s.product_name,
                    badge: `${s.quantity.toLocaleString("hu-HU")} ${s.unit}`,
                  }))}
                  value={giveProductId}
                  onChange={setGiveProductId}
                  placeholder={t("streq.giveProductPh")}
                />
                <div className="flex gap-2">
                  <input
                    type="number"
                    min={0.5}
                    step="0.5"
                    value={giveQty}
                    onChange={(e) => setGiveQty(e.target.value)}
                    placeholder={t("streq.qty")}
                    className="w-28 rounded-lg border border-indigo-300 px-3 py-2 text-sm"
                  />
                  <select
                    value={giveTo}
                    onChange={(e) => setGiveTo(e.target.value)}
                    className="flex-1 rounded-lg border border-indigo-300 bg-white px-3 py-2 text-sm"
                  >
                    <option value="">{t("streq.giveToPh")}</option>
                    {warehouses
                      .filter((w) => w.id !== wh.warehouse_id)
                      .map((w) => (
                        <option key={w.id} value={w.id}>
                          {w.kind === "van" ? "🚐" : "🏭"} {w.name}
                          {w.user_name ? ` — ${w.user_name}` : ""}
                        </option>
                      ))}
                  </select>
                </div>
                <input
                  value={giveNote}
                  onChange={(e) => setGiveNote(e.target.value)}
                  placeholder={t("streq.notePh")}
                  className="w-full rounded-lg border border-indigo-300 px-3 py-2 text-sm"
                />
                <button
                  disabled={busy || !giveProductId || !giveQty || !giveTo}
                  className="w-full rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
                >
                  {t("streq.giveSend")}
                </button>
                <p className="text-xs text-indigo-800">{t("streq.giveHint")}</p>
              </div>
            </form>
          )}

          {/* Függő kimenő átadásaim */}
          {outgoing.filter((o) => o.status === "pending" && o.from_warehouse === wh?.warehouse_name).length > 0 && (
            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <h2 className="mb-2 font-semibold">⏳ {t("streq.outgoingTitle")}</h2>
              <div className="space-y-1.5 text-sm">
                {outgoing
                  .filter((o) => o.status === "pending" && o.from_warehouse === wh?.warehouse_name)
                  .map((o) => (
                    <div key={o.id} className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-1.5 first:border-0 first:pt-0">
                      <span>
                        {o.quantity.toLocaleString("hu-HU")} {o.unit} {o.product_name} → <b>{o.to_warehouse}</b>
                      </span>
                      {o.can_cancel && (
                        <button
                          onClick={() => void cancelOutgoing(o.id)}
                          className="ml-auto rounded-lg border border-rose-300 px-2.5 py-1 text-xs text-rose-700"
                        >
                          ✕ {t("streq.giveCancel")}
                        </button>
                      )}
                    </div>
                  ))}
              </div>
            </div>
          )}
        </div>

        {/* Igénylés + saját igénylések */}
        <div className="space-y-4">
          <form onSubmit={sendRequest} className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 shadow-sm">
            <h2 className="mb-2 font-semibold text-emerald-900">➕ {t("streq.newRequest")}</h2>
            <div className="space-y-2">
              <SearchSelect
                items={products.map((p) => ({ id: p.id, label: p.name, badge: p.unit, keywords: p.code }))}
                value={productId}
                onChange={setProductId}
                placeholder={t("streq.productPh")}
              />
              <div className="flex gap-2">
                <input
                  type="number"
                  min={0.5}
                  step="0.5"
                  value={qty}
                  onChange={(e) => setQty(e.target.value)}
                  placeholder={t("streq.qty")}
                  className="w-28 rounded-lg border border-emerald-300 px-3 py-2 text-sm"
                />
                <input
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder={t("streq.notePh")}
                  className="flex-1 rounded-lg border border-emerald-300 px-3 py-2 text-sm"
                />
              </div>
              <button
                disabled={busy || !productId || !qty}
                className="w-full rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                {t("streq.send")}
              </button>
              <p className="text-xs text-emerald-800">{t("streq.flowHint")}</p>
            </div>
          </form>

          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="mb-2 font-semibold">🗂 {t("streq.myRequests")}</h2>
            {reqs.length === 0 ? (
              <p className="py-4 text-center text-sm text-slate-400">{t("streq.noRequests")}</p>
            ) : (
              <div className="space-y-1.5 text-sm">
                {reqs.map((r) => (
                  <div key={r.id} className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-1.5 first:border-0 first:pt-0">
                    <span className="text-xs text-slate-400">{new Date(r.created_at).toLocaleDateString("hu-HU")}</span>
                    <span className="font-medium">
                      {r.quantity.toLocaleString("hu-HU")} {r.unit ?? ""} {r.product_name ?? "?"}
                    </span>
                    {r.note && <span className="text-xs text-slate-500">({r.note})</span>}
                    <span className={`ml-auto rounded px-1.5 py-0.5 text-xs font-medium ${REQ_CHIP[r.status]}`}>
                      {t(`streq.status.${r.status}`)}
                      {r.decided_by_name ? ` — ${r.decided_by_name}` : ""}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  );
}
