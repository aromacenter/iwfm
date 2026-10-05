"use client";

/** Leltár modul: raktár kiválasztása → leltár-ív nyitása (könyv szerinti
 * pillanatképpel) → számolt mennyiségek rögzítése (részmentés is) → zárás:
 * az eltérések leltár-korrekciós mozgással a készletre állnak. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface Warehouse {
  id: string;
  name: string;
  kind: string;
}

interface Line {
  id: string;
  product_id: string;
  product_name: string;
  unit: string;
  system_qty: number;
  current_qty: number;
  counted_qty: number | null;
}

interface Stocktake {
  id: string;
  warehouse_id: string;
  warehouse_name: string;
  status: "open" | "closed";
  note: string | null;
  opened_by_name: string | null;
  closed_by_name: string | null;
  opened_at: string;
  closed_at: string | null;
  line_count: number;
  counted_count: number;
  diff_count: number;
  lines: Line[];
}

export default function LeltarPage() {
  const { t } = useT();
  const { toast, confirm } = useUI();
  const [list, setList] = useState<Stocktake[]>([]);
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [whId, setWhId] = useState("");
  const [note, setNote] = useState("");
  const [current, setCurrent] = useState<Stocktake | null>(null);
  const [counts, setCounts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [sts, whs] = await Promise.all([
        api.get<Stocktake[]>("/api/stocktakes"),
        api.get<Warehouse[]>("/api/warehouses"),
      ]);
      setList(sts);
      setWarehouses(whs);
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  }, [toast]);
  useEffect(() => {
    void load();
  }, [load]);

  const openDetail = async (id: string) => {
    try {
      const st = await api.get<Stocktake>(`/api/stocktakes/${id}`);
      setCurrent(st);
      const c: Record<string, string> = {};
      for (const ln of st.lines) c[ln.id] = ln.counted_qty === null ? "" : String(ln.counted_qty);
      setCounts(c);
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  };

  const createStocktake = async () => {
    if (!whId) return;
    setBusy(true);
    try {
      const st = await api.post<Stocktake>("/api/stocktakes", {
        warehouse_id: whId,
        note: note.trim() || null,
      });
      setNote("");
      toast(t("stk.opened"), "success");
      await load();
      setCurrent(st);
      const c: Record<string, string> = {};
      for (const ln of st.lines) c[ln.id] = "";
      setCounts(c);
    } catch (e) {
      toast(errorMessage(e), "error");
    } finally {
      setBusy(false);
    }
  };

  const saveCounts = async (): Promise<Stocktake | null> => {
    if (!current) return null;
    const lines = current.lines.map((ln) => ({
      id: ln.id,
      counted_qty: counts[ln.id] === "" || counts[ln.id] === undefined ? null : Number(counts[ln.id]),
    }));
    const st = await api.put<Stocktake>(`/api/stocktakes/${current.id}/lines`, { lines });
    setCurrent(st);
    return st;
  };

  const onSave = async () => {
    setBusy(true);
    try {
      await saveCounts();
      toast(t("stk.saved"), "success");
    } catch (e) {
      toast(errorMessage(e), "error");
    } finally {
      setBusy(false);
    }
  };

  const onClose = async () => {
    if (!current) return;
    const ok = await confirm(t("stk.closeConfirm"));
    if (!ok) return;
    setBusy(true);
    try {
      await saveCounts();
      const st = await api.post<Stocktake>(`/api/stocktakes/${current.id}/close`, {});
      setCurrent(st);
      toast(t("stk.closed"), "success");
      await load();
    } catch (e) {
      toast(errorMessage(e), "error");
    } finally {
      setBusy(false);
    }
  };

  const diffOf = (ln: Line): number | null => {
    const v = counts[ln.id];
    if (current?.status === "closed") {
      return ln.counted_qty === null ? null : ln.counted_qty - ln.system_qty;
    }
    if (v === "" || v === undefined) return null;
    return Number(v) - ln.current_qty;
  };

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-xl font-semibold">📋 {t("stk.title")}</h1>
          <p className="text-sm text-slate-500">{t("stk.subtitle")}</p>
        </div>
        <div className="ml-auto flex flex-wrap items-end gap-2">
          <div>
            <label className="block text-xs text-slate-500">{t("stk.warehouse")}</label>
            <select
              value={whId}
              onChange={(e) => setWhId(e.target.value)}
              className="rounded border px-2 py-1.5 text-sm"
            >
              <option value="">—</option>
              {warehouses.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.kind === "van" ? "🚐 " : "🏬 "}
                  {w.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-500">{t("stk.note")}</label>
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              className="rounded border px-2 py-1.5 text-sm"
              placeholder={t("stk.notePh")}
            />
          </div>
          <button
            onClick={createStocktake}
            disabled={!whId || busy}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            ➕ {t("stk.open")}
          </button>
        </div>
      </div>

      <div className="mb-6 overflow-x-auto rounded-xl border bg-white shadow-sm">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b bg-slate-50 text-left text-xs uppercase text-slate-500">
              <th className="px-3 py-2">{t("stk.warehouse")}</th>
              <th className="px-3 py-2">{t("stk.status")}</th>
              <th className="px-3 py-2">{t("stk.openedAt")}</th>
              <th className="px-3 py-2">{t("stk.openedBy")}</th>
              <th className="px-3 py-2 text-right">{t("stk.lines")}</th>
              <th className="px-3 py-2 text-right">{t("stk.counted")}</th>
              <th className="px-3 py-2 text-right">{t("stk.diffs")}</th>
            </tr>
          </thead>
          <tbody>
            {list.length === 0 && (
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-slate-400">
                  {t("stk.empty")}
                </td>
              </tr>
            )}
            {list.map((st) => (
              <tr
                key={st.id}
                onClick={() => void openDetail(st.id)}
                className={`cursor-pointer border-b hover:bg-indigo-50 ${
                  current?.id === st.id ? "bg-indigo-50" : ""
                }`}
              >
                <td className="px-3 py-2 font-medium">{st.warehouse_name}</td>
                <td className="px-3 py-2">
                  {st.status === "open" ? (
                    <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-700">
                      {t("stk.statusOpen")}
                    </span>
                  ) : (
                    <span className="rounded bg-emerald-100 px-2 py-0.5 text-xs text-emerald-700">
                      {t("stk.statusClosed")}
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-slate-500">
                  {new Date(st.opened_at).toLocaleString("hu-HU")}
                </td>
                <td className="px-3 py-2 text-slate-500">{st.opened_by_name || "—"}</td>
                <td className="px-3 py-2 text-right">{st.line_count}</td>
                <td className="px-3 py-2 text-right">{st.counted_count}</td>
                <td className={`px-3 py-2 text-right ${st.diff_count ? "font-semibold text-rose-600" : ""}`}>
                  {st.diff_count}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {current && (
        <div className="rounded-xl border bg-white p-4 shadow-sm">
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <h2 className="text-lg font-semibold">
              {current.warehouse_name} — {new Date(current.opened_at).toLocaleDateString("hu-HU")}
            </h2>
            {current.note && <span className="text-sm text-slate-500">({current.note})</span>}
            <div className="ml-auto flex gap-2">
              {current.status === "open" && (
                <>
                  <button
                    onClick={onSave}
                    disabled={busy}
                    className="rounded border border-indigo-300 px-3 py-1.5 text-sm text-indigo-700 disabled:opacity-50"
                  >
                    💾 {t("stk.save")}
                  </button>
                  <button
                    onClick={onClose}
                    disabled={busy}
                    className="rounded bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
                  >
                    ✅ {t("stk.close")}
                  </button>
                </>
              )}
              <button
                onClick={() => setCurrent(null)}
                className="rounded border px-3 py-1.5 text-sm text-slate-500"
              >
                ✕
              </button>
            </div>
          </div>
          {current.status === "open" && (
            <p className="mb-3 text-xs text-slate-500">{t("stk.hint")}</p>
          )}
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b bg-slate-50 text-left text-xs uppercase text-slate-500">
                  <th className="px-3 py-2">{t("stk.product")}</th>
                  <th className="px-3 py-2 text-right">{t("stk.systemQty")}</th>
                  <th className="px-3 py-2 text-right">{t("stk.countedQty")}</th>
                  <th className="px-3 py-2 text-right">{t("stk.diff")}</th>
                </tr>
              </thead>
              <tbody>
                {current.lines.map((ln) => {
                  const d = diffOf(ln);
                  return (
                    <tr key={ln.id} className="border-b">
                      <td className="px-3 py-2">{ln.product_name}</td>
                      <td className="px-3 py-2 text-right text-slate-500">
                        {(current.status === "closed" ? ln.system_qty : ln.current_qty).toLocaleString(
                          "hu-HU",
                        )}{" "}
                        {ln.unit}
                      </td>
                      <td className="px-3 py-2 text-right">
                        {current.status === "open" ? (
                          <input
                            type="number"
                            min={0}
                            step="0.01"
                            value={counts[ln.id] ?? ""}
                            onChange={(e) =>
                              setCounts((c) => ({ ...c, [ln.id]: e.target.value }))
                            }
                            className="w-28 rounded border px-2 py-1 text-right"
                            placeholder="—"
                          />
                        ) : ln.counted_qty === null ? (
                          <span className="text-slate-400">{t("stk.notCounted")}</span>
                        ) : (
                          `${ln.counted_qty.toLocaleString("hu-HU")} ${ln.unit}`
                        )}
                      </td>
                      <td
                        className={`px-3 py-2 text-right font-medium ${
                          d === null
                            ? "text-slate-300"
                            : d < 0
                              ? "text-rose-600"
                              : d > 0
                                ? "text-emerald-600"
                                : "text-slate-400"
                        }`}
                      >
                        {d === null ? "—" : `${d > 0 ? "+" : ""}${d.toLocaleString("hu-HU")}`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </AppShell>
  );
}
