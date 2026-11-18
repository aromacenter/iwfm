"use client";

/** Tárhelyezett raktár (161. kör): sor → állás → szint → fakk rácsnézet,
 *  szerkezet-generálás, átrendezés (a készlet a tárhellyel megy), berakás,
 *  tárhelyek közti áthelyezés, „hol van?” kereső, QR-címkék. */

import { useCallback, useEffect, useMemo, useState } from "react";
import SearchSelect from "@/components/SearchSelect";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

export interface Loc {
  id: string;
  row_code: string;
  bay: number;
  level: number;
  slot: number;
  code: string;
  label: string | null;
  is_active: boolean;
  quantity: number;
  product_count: number;
}

interface RowSummary { row_code: string; bays: number; levels: number; slots: number; locations: number; used: number }
interface LocationsOut { layout: string; row_scheme: string; rows: RowSummary[]; locations: Loc[] }
interface Place { location_id: string; code: string; quantity: number }
interface LocatedStock { product_id: string; product_name: string; unit: string; total: number; unplaced: number; places: Place[] }
interface Content { product_id: string; product_name: string; unit: string; quantity: number }
interface Found { warehouse_id: string; warehouse_name: string; location_id: string | null; code: string | null; quantity: number }

interface ProductLite { id: string; name: string; unit: string; category?: string | null }

const fmt = (n: number) => n.toLocaleString("hu-HU", { maximumFractionDigits: 3 });

export default function WarehouseLocations({
  warehouseId, warehouseName, rowScheme, products, isMgr, onChanged,
}: {
  warehouseId: string;
  warehouseName: string;
  rowScheme: string;
  products: ProductLite[];
  isMgr: boolean;
  onChanged: () => void;
}) {
  const { t } = useT();
  const { toast, confirm } = useUI();
  const [data, setData] = useState<LocationsOut | null>(null);
  const [stock, setStock] = useState<LocatedStock[]>([]);
  const [row, setRow] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get<LocationsOut>(`/api/warehouses/${warehouseId}/locations`).then((d) => {
      setData(d);
      setRow((cur) => (cur && d.rows.some((r) => r.row_code === cur) ? cur : d.rows[0]?.row_code ?? ""));
    }).catch(() => {});
    api.get<LocatedStock[]>(`/api/warehouses/${warehouseId}/locations/stock`).then(setStock).catch(() => {});
  }, [warehouseId]);
  useEffect(load, [load]);

  function done(msg?: string) {
    if (msg) toast(msg, "success");
    load();
    onChanged();
  }

  // ── Sor generálása / bővítése ──
  const [gen, setGen] = useState<{ row_code: string; bays: string; levels: string; slots: string } | null>(null);
  async function runGen(e: React.FormEvent) {
    e.preventDefault();
    if (!gen) return;
    setBusy(true); setError(null);
    try {
      await api.post(`/api/warehouses/${warehouseId}/locations/generate`, {
        row_code: gen.row_code, bays: Number(gen.bays), levels: Number(gen.levels), slots: Number(gen.slots || 0),
      });
      setGen(null);
      setRow(gen.row_code.toUpperCase());
      done(t("wh.locGenerated"));
    } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }

  // ── Tömeges átrendezés ──
  const [bulk, setBulk] = useState<{ op: "rename_row" | "shift_bays" | "shift_levels"; value: string } | null>(null);
  async function runBulk(e: React.FormEvent) {
    e.preventDefault();
    if (!bulk || !row) return;
    setBusy(true); setError(null);
    try {
      const body = bulk.op === "rename_row"
        ? { op: bulk.op, row_code: row, new_row_code: bulk.value }
        : { op: bulk.op, row_code: row, offset: Number(bulk.value) };
      await api.post(`/api/warehouses/${warehouseId}/locations/bulk`, body);
      if (bulk.op === "rename_row") setRow(bulk.value.toUpperCase());
      setBulk(null);
      done(t("wh.locRearranged"));
    } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  async function deleteEmpty() {
    if (!row || !(await confirm(t("wh.locDeleteEmptyConfirm", { row })))) return;
    try {
      await api.post(`/api/warehouses/${warehouseId}/locations/bulk`, { op: "delete_empty", row_code: row });
      done(t("wh.locDeleted"));
    } catch (err) { toast(errorMessage(err), "error"); }
  }

  // ── Kiválasztott tárhely panel ──
  const [cell, setCell] = useState<Loc | null>(null);
  const [contents, setContents] = useState<Content[]>([]);
  useEffect(() => {
    if (!cell) return;
    api.get<Content[]>(`/api/warehouses/${warehouseId}/locations/${cell.id}/contents`).then(setContents).catch(() => setContents([]));
  }, [cell, warehouseId, data]);

  const [edit, setEdit] = useState<{ row_code: string; bay: string; level: string; slot: string; label: string; is_active: boolean } | null>(null);
  async function saveEdit(e: React.FormEvent) {
    e.preventDefault();
    if (!cell || !edit) return;
    setBusy(true); setError(null);
    try {
      const out = await api.patch<Loc>(`/api/warehouses/${warehouseId}/locations/${cell.id}`, {
        row_code: edit.row_code, bay: Number(edit.bay), level: Number(edit.level), slot: Number(edit.slot || 0),
        label: edit.label, is_active: edit.is_active,
      });
      setEdit(null);
      setCell(out);
      setRow(out.row_code);
      done(t("wh.locRearranged"));
    } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  async function deleteCell() {
    if (!cell || !(await confirm(t("wh.locDeleteConfirm", { code: cell.code })))) return;
    try {
      await api.delete(`/api/warehouses/${warehouseId}/locations/${cell.id}`);
      setCell(null);
      done(t("wh.locDeleted"));
    } catch (err) { toast(errorMessage(err), "error"); }
  }

  // ── Készlet-műveletek: berakás / áthelyezés / levétel ──
  type Op = "place" | "relocate" | "unplace";
  const [op, setOp] = useState<{ kind: Op; product_id: string; location_id: string; to_location_id: string; qty: string } | null>(null);
  function openOp(kind: Op, productId?: string, locationId?: string) {
    setError(null);
    setOp({ kind, product_id: productId ?? products[0]?.id ?? "", location_id: locationId ?? cell?.id ?? "", to_location_id: "", qty: "" });
  }
  async function runOp(e: React.FormEvent) {
    e.preventDefault();
    if (!op) return;
    setBusy(true); setError(null);
    try {
      const qty = Number(op.qty);
      if (op.kind === "place") await api.post(`/api/warehouses/${warehouseId}/locations/place`, { product_id: op.product_id, location_id: op.location_id, quantity: qty });
      else if (op.kind === "unplace") await api.post(`/api/warehouses/${warehouseId}/locations/unplace`, { product_id: op.product_id, location_id: op.location_id, quantity: qty });
      else await api.post(`/api/warehouses/${warehouseId}/locations/relocate`, { product_id: op.product_id, from_location_id: op.location_id, to_location_id: op.to_location_id, quantity: qty });
      setOp(null);
      done(t(`wh.loc_${op.kind}Done`));
    } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }

  // ── Hol van? ──
  const [findId, setFindId] = useState("");
  const [found, setFound] = useState<Found[] | null>(null);
  useEffect(() => {
    if (!findId) return;
    api.get<Found[]>(`/api/warehouses/locate?product_id=${findId}`).then(setFound).catch(() => setFound([]));
  }, [findId, data, stock]);

  const locs = useMemo(() => data?.locations ?? [], [data]);
  const rowLocs = useMemo(() => locs.filter((l) => l.row_code === row), [locs, row]);
  // tárhely → mi van benne (a kockán rögtön látszik a termék és a mennyiség)
  const byLoc = useMemo(() => {
    const m: Record<string, { name: string; qty: number; unit: string }[]> = {};
    for (const s of stock) for (const p of s.places) (m[p.location_id] ??= []).push({ name: s.product_name, qty: p.quantity, unit: s.unit });
    return m;
  }, [stock]);
  const rowInfo = data?.rows.find((r) => r.row_code === row);
  const unplaced = stock.filter((s) => s.unplaced > 1e-9);
  const activeLocs = locs.filter((l) => l.is_active);
  const locItems = activeLocs.map((l) => ({ id: l.id, label: l.code, sublabel: l.label ?? undefined, badge: l.product_count ? `${l.product_count}` : undefined }));
  const prodItems = products.map((p) => ({ id: p.id, label: p.name, sublabel: p.category ?? undefined, badge: p.unit }));
  const schemeHint = rowScheme === "numbers" ? "1, 2, 3…" : "A, B, C…";

  return (
    <div className="border-t border-slate-200">
      <div className="flex flex-wrap items-center gap-2 px-4 py-3">
        <h3 className="text-sm font-semibold">🗄 {t("wh.locTitle")}</h3>
        <span className="text-xs text-slate-500">{t("wh.locSummary", { rows: data?.rows.length ?? 0, count: locs.length, used: locs.filter((l) => l.product_count > 0).length })}</span>
        <div className="ml-auto flex flex-wrap gap-2">
          {isMgr && (
            <button onClick={() => { setError(null); setGen({ row_code: "", bays: "4", levels: "3", slots: "0" }); }} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700">
              ➕ {t("wh.locGenerate")}
            </button>
          )}
          {unplaced.length > 0 && (
            <button onClick={() => openOp("place", unplaced[0].product_id, activeLocs[0]?.id)} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700">
              📥 {t("wh.locPlace")}
            </button>
          )}
          {locs.length > 0 && (
            <a href={`/api/warehouses/${warehouseId}/locations/labels.pdf${row ? `?row=${row}` : ""}`} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium hover:bg-slate-100">
              🏷 {t("wh.locLabels")}
            </a>
          )}
        </div>
      </div>

      {/* Nem tárhelyezett készlet — ide kell berakni */}
      {unplaced.length > 0 && (
        <div className="mx-4 mb-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <span className="font-semibold">📦 {t("wh.locUnplacedTitle")}</span> — {t("wh.locUnplacedHint")}
          <div className="mt-1 flex flex-wrap gap-1.5">
            {unplaced.map((s) => (
              <button key={s.product_id} onClick={() => openOp("place", s.product_id, activeLocs[0]?.id)} className="rounded-full border border-amber-300 bg-white px-2 py-0.5 hover:bg-amber-100">
                {s.product_name}: {fmt(s.unplaced)} {s.unit} →
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Hol van? */}
      <div className="mx-4 mb-3 flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium">🔎 {t("wh.locFind")}</span>
        <div className="min-w-[220px]"><SearchSelect items={prodItems} value={findId} onChange={(id) => { setFindId(id); if (!id) setFound(null); }} allowEmpty placeholder={t("wh.locFindPlaceholder")} /></div>
        {found && (
          <span className="text-slate-700">
            {found.length === 0 ? t("wh.locFindNone") : found.map((f) => `${f.warehouse_name} ${f.code ?? t("wh.locUnplacedShort")}: ${fmt(f.quantity)}`).join(" · ")}
          </span>
        )}
      </div>

      {data && data.rows.length === 0 && (
        <p className="px-4 pb-4 text-sm text-slate-400">{t("wh.locEmpty", { scheme: schemeHint })}</p>
      )}

      {data && data.rows.length > 0 && (
        <div className="px-4 pb-4">
          {/* Sor-választó + sor-műveletek */}
          <div className="mb-2 flex flex-wrap items-center gap-1">
            {data.rows.map((r) => (
              <button key={r.row_code} onClick={() => setRow(r.row_code)} className={`rounded-full px-3 py-1 text-xs ${row === r.row_code ? "bg-indigo-600 font-medium text-white" : "border border-slate-300 text-slate-600 hover:bg-slate-100"}`}>
                {t("wh.locRow")} {r.row_code} <span className="opacity-70">({r.used}/{r.locations})</span>
              </button>
            ))}
            {isMgr && row && (
              <div className="ml-auto flex flex-wrap gap-1">
                <button onClick={() => { setError(null); setBulk({ op: "rename_row", value: "" }); }} className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100" title={t("wh.locRename")}>✏️ {t("wh.locRename")}</button>
                <button onClick={() => { setError(null); setBulk({ op: "shift_bays", value: "1" }); }} className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100" title={t("wh.locShiftBays")}>↔️ {t("wh.locShiftBays")}</button>
                <button onClick={() => { setError(null); setBulk({ op: "shift_levels", value: "1" }); }} className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100" title={t("wh.locShiftLevels")}>↕️ {t("wh.locShiftLevels")}</button>
                <button onClick={() => { setError(null); setGen({ row_code: row, bays: String((rowInfo?.bays ?? 0) + 1), levels: String(rowInfo?.levels ?? 1), slots: String(rowInfo?.slots ?? 0) }); }} className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100">➕ {t("wh.locExtend")}</button>
                <button onClick={deleteEmpty} className="rounded border border-rose-300 px-2 py-1 text-xs text-rose-700 hover:bg-rose-50">🗑 {t("wh.locDeleteEmpty")}</button>
              </div>
            )}
          </div>

          {/* Rács: vízszintesen az állások, függőlegesen a szintek (felül a legmagasabb) */}
          {rowInfo && (
            <div className="overflow-x-auto rounded-xl border border-slate-200 bg-slate-50 p-2">
              <table className="text-xs">
                <thead>
                  <tr>
                    <th className="px-1 py-1 text-right text-slate-400">{t("wh.locLevel")} \ {t("wh.locBay")}</th>
                    {Array.from({ length: rowInfo.bays }, (_, i) => i + 1).map((b) => (
                      <th key={b} className="px-1 py-1 text-center font-medium text-slate-600">{String(b).padStart(2, "0")}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Array.from({ length: rowInfo.levels }, (_, i) => rowInfo.levels - i).map((lv) => (
                    <tr key={lv}>
                      <td className="px-1 pt-3 text-right align-top font-medium text-slate-600">{lv}</td>
                      {Array.from({ length: rowInfo.bays }, (_, i) => i + 1).map((b) => {
                        const cells = rowLocs.filter((l) => l.bay === b && l.level === lv).sort((a, c) => a.slot - c.slot);
                        return (
                          <td key={b} className="p-0.5 align-top">
                            <div className={`flex ${cells.length > 1 ? "flex-col" : ""} gap-0.5`}>
                              {cells.length === 0 && <div className="h-10 w-28 rounded border border-dashed border-slate-200" />}
                              {cells.map((l) => (
                                <button
                                  key={l.id}
                                  onClick={() => { setCell(l); setEdit(null); }}
                                  title={`${l.code}${l.label ? ` — ${l.label}` : ""}`}
                                  className={`min-h-10 w-28 rounded border px-1 py-0.5 text-left leading-tight ${
                                    cell?.id === l.id ? "border-indigo-500 ring-2 ring-indigo-200" : "border-slate-200"
                                  } ${!l.is_active ? "bg-slate-200 text-slate-400 line-through" : l.product_count > 0 ? "bg-emerald-100 text-emerald-900 hover:bg-emerald-200" : "bg-white text-slate-500 hover:bg-slate-100"}`}
                                >
                                  <div className="font-mono font-semibold">{l.slot ? `${String(l.bay).padStart(2, "0")}-${l.level}-${l.slot}` : l.code}</div>
                                  {(byLoc[l.id] ?? []).slice(0, 2).map((c) => (
                                    <div key={c.name} className="flex items-baseline justify-between gap-1"><span className="truncate">{c.name}</span><b className="shrink-0">{fmt(c.qty)} {c.unit}</b></div>
                                  ))}
                                  {(byLoc[l.id]?.length ?? 0) > 2 && <div className="truncate">+{byLoc[l.id].length - 2} {t("wh.locProducts")}</div>}
                                  {l.product_count === 0 && l.label && <div className="truncate">{l.label}</div>}
                                </button>
                              ))}
                            </div>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Kiválasztott tárhely panel */}
          {cell && (
            <div className="mt-3 rounded-xl border border-indigo-200 bg-indigo-50/40 p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-base font-bold">{cell.code}</span>
                {cell.label && <span className="text-slate-600">— {cell.label}</span>}
                {!cell.is_active && <span className="rounded-full bg-slate-200 px-2 py-0.5 text-xs">{t("wh.locInactive")}</span>}
                <span className="text-xs text-slate-500">{t("wh.locCoords", { row: cell.row_code, bay: cell.bay, level: cell.level })}{cell.slot ? ` · ${t("wh.locSlot")} ${cell.slot}` : ""}</span>
                <div className="ml-auto flex flex-wrap gap-1">
                  <button onClick={() => openOp("place", undefined, cell.id)} className="rounded border border-emerald-300 px-2 py-1 text-xs text-emerald-700 hover:bg-emerald-50">📥 {t("wh.locPlace")}</button>
                  {contents.length > 0 && <button onClick={() => openOp("relocate", contents[0].product_id, cell.id)} className="rounded border border-sky-300 px-2 py-1 text-xs text-sky-700 hover:bg-sky-50">🔁 {t("wh.locRelocate")}</button>}
                  {contents.length > 0 && <button onClick={() => openOp("unplace", contents[0].product_id, cell.id)} className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100">📤 {t("wh.locUnplace")}</button>}
                  {isMgr && <button onClick={() => { setError(null); setEdit({ row_code: cell.row_code, bay: String(cell.bay), level: String(cell.level), slot: String(cell.slot), label: cell.label ?? "", is_active: cell.is_active }); }} className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100">✏️ {t("wh.locEdit")}</button>}
                  {isMgr && contents.length === 0 && <button onClick={deleteCell} className="rounded border border-rose-300 px-2 py-1 text-xs text-rose-700 hover:bg-rose-50">🗑</button>}
                  <button onClick={() => setCell(null)} className="rounded px-2 py-1 text-xs text-slate-500 hover:bg-slate-100">✕</button>
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {contents.length === 0 && <span className="text-xs text-slate-400">{t("wh.locCellEmpty")}</span>}
                {contents.map((c) => (
                  <span key={c.product_id} className="rounded-full bg-white px-2.5 py-0.5 text-xs shadow-sm">{c.product_name}: <b>{fmt(c.quantity)} {c.unit}</b></span>
                ))}
              </div>
              {edit && (
                <form onSubmit={saveEdit} className="mt-3 grid grid-cols-2 gap-2 rounded-lg border border-slate-200 bg-white p-3 text-xs sm:grid-cols-6">
                  <label>{t("wh.locRow")} <input value={edit.row_code} onChange={(e) => setEdit({ ...edit, row_code: e.target.value })} className="mt-1 w-full rounded border border-slate-300 px-2 py-1" /></label>
                  <label>{t("wh.locBay")} <input type="number" min={1} max={99} value={edit.bay} onChange={(e) => setEdit({ ...edit, bay: e.target.value })} className="mt-1 w-full rounded border border-slate-300 px-2 py-1" /></label>
                  <label>{t("wh.locLevel")} <input type="number" min={1} max={20} value={edit.level} onChange={(e) => setEdit({ ...edit, level: e.target.value })} className="mt-1 w-full rounded border border-slate-300 px-2 py-1" /></label>
                  <label>{t("wh.locSlot")} <input type="number" min={0} max={20} value={edit.slot} onChange={(e) => setEdit({ ...edit, slot: e.target.value })} className="mt-1 w-full rounded border border-slate-300 px-2 py-1" /></label>
                  <label className="col-span-2">{t("wh.locLabel")} <input value={edit.label} onChange={(e) => setEdit({ ...edit, label: e.target.value })} className="mt-1 w-full rounded border border-slate-300 px-2 py-1" /></label>
                  <label className="col-span-2 flex items-center gap-2"><input type="checkbox" checked={edit.is_active} onChange={(e) => setEdit({ ...edit, is_active: e.target.checked })} /> {t("cons.active")}</label>
                  {error && <p className="col-span-full text-rose-600">{error}</p>}
                  <div className="col-span-full flex justify-end gap-2">
                    <button type="button" onClick={() => setEdit(null)} className="rounded border border-slate-300 px-3 py-1 hover:bg-slate-100">{t("common.cancel")}</button>
                    <button disabled={busy} className="rounded bg-indigo-600 px-3 py-1 font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{t("common.save")}</button>
                  </div>
                  <p className="col-span-full text-slate-500">{t("wh.locEditHint")}</p>
                </form>
              )}
            </div>
          )}

          {/* Termékenkénti bontás */}
          {stock.length > 0 && (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left uppercase text-slate-500">
                    <th className="px-2 py-1">{t("wh.product")}</th>
                    <th className="px-2 py-1">{t("wh.quantity")}</th>
                    <th className="px-2 py-1">{t("wh.locUnplacedShort")}</th>
                    <th className="px-2 py-1">{t("wh.locPlaces")}</th>
                  </tr>
                </thead>
                <tbody>
                  {stock.map((s) => (
                    <tr key={s.product_id} className="border-t border-slate-100">
                      <td className="px-2 py-1 font-medium">{s.product_name}</td>
                      <td className="px-2 py-1">{fmt(s.total)} {s.unit}</td>
                      <td className={`px-2 py-1 ${s.unplaced > 1e-9 ? "font-semibold text-amber-700" : "text-slate-400"}`}>{s.unplaced > 1e-9 ? fmt(s.unplaced) : "—"}</td>
                      <td className="px-2 py-1">
                        <div className="flex flex-wrap gap-1">
                          {s.places.map((p) => (
                            <button key={p.location_id} onClick={() => { const l = locs.find((x) => x.id === p.location_id); if (l) { setRow(l.row_code); setCell(l); setEdit(null); } }} className="rounded-full bg-emerald-50 px-2 py-0.5 font-mono text-emerald-900 hover:bg-emerald-100">
                              {p.code}: {fmt(p.quantity)}
                            </button>
                          ))}
                          {s.places.length === 0 && <span className="text-slate-400">—</span>}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── Dialógusok ── */}
      {gen && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4">
          <form onSubmit={runGen} className="relative my-8 w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <button type="button" onClick={() => setGen(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">{t("wh.locGenerate")} — {warehouseName}</h2>
            <p className="text-xs text-slate-500">{t("wh.locGenerateHint")}</p>
            <label className="block text-sm">{t("wh.locRow")} * <span className="text-xs text-slate-400">({schemeHint})</span>
              <input required value={gen.row_code} onChange={(e) => setGen({ ...gen, row_code: e.target.value })} maxLength={4} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 font-mono uppercase" />
            </label>
            <div className="grid grid-cols-3 gap-2">
              <label className="block text-sm">{t("wh.locBays")} *<input required type="number" min={1} max={99} value={gen.bays} onChange={(e) => setGen({ ...gen, bays: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" /></label>
              <label className="block text-sm">{t("wh.locLevels")} *<input required type="number" min={1} max={20} value={gen.levels} onChange={(e) => setGen({ ...gen, levels: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" /></label>
              <label className="block text-sm">{t("wh.locSlots")}<input type="number" min={0} max={20} value={gen.slots} onChange={(e) => setGen({ ...gen, slots: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" /></label>
            </div>
            <p className="text-xs text-slate-500">{t("wh.locPreview", { code: `${(gen.row_code || "A").toUpperCase()}-01-1${Number(gen.slots) > 0 ? "-1" : ""}`, count: Math.max(1, Number(gen.bays) || 1) * Math.max(1, Number(gen.levels) || 1) * Math.max(1, Number(gen.slots) || 1) })}</p>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setGen(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{busy ? t("common.saving") : t("common.save")}</button>
            </div>
          </form>
        </div>
      )}

      {bulk && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4">
          <form onSubmit={runBulk} className="relative my-8 w-full max-w-sm space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <button type="button" onClick={() => setBulk(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">{t(`wh.loc_${bulk.op}`)} — {t("wh.locRow")} {row}</h2>
            <p className="text-xs text-slate-500">{t("wh.locBulkHint")}</p>
            <label className="block text-sm">
              {bulk.op === "rename_row" ? t("wh.locNewRow") : t("wh.locOffset")}
              {bulk.op === "rename_row"
                ? <input required value={bulk.value} onChange={(e) => setBulk({ ...bulk, value: e.target.value })} maxLength={4} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 font-mono uppercase" />
                : <input required type="number" min={-98} max={98} value={bulk.value} onChange={(e) => setBulk({ ...bulk, value: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />}
            </label>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setBulk(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{busy ? t("common.saving") : t("common.save")}</button>
            </div>
          </form>
        </div>
      )}

      {op && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4">
          <form onSubmit={runOp} className="relative my-8 w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <button type="button" onClick={() => setOp(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">{t(`wh.loc_${op.kind}`)} — {warehouseName}</h2>
            <label className="block text-sm">{t("wh.product")} *
              <SearchSelect required items={prodItems} value={op.product_id} onChange={(id) => setOp({ ...op, product_id: id })} className="mt-1" />
            </label>
            {op.kind === "place" && (() => { const s = stock.find((x) => x.product_id === op.product_id); return s ? <p className="text-xs text-amber-700">{t("wh.locUnplacedAvail", { qty: fmt(s.unplaced), unit: s.unit })}</p> : null; })()}
            <label className="block text-sm">{op.kind === "relocate" ? t("wh.locFrom") : t("wh.locLocation")} *
              <SearchSelect required placeholder={t("wh.locPickPlaceholder")} items={locItems} value={op.location_id} onChange={(id) => setOp({ ...op, location_id: id })} className="mt-1" />
            </label>
            {op.kind === "relocate" && (
              <label className="block text-sm">{t("wh.locTo")} *
                <SearchSelect required placeholder={t("wh.locPickPlaceholder")} items={locItems.filter((l) => l.id !== op.location_id)} value={op.to_location_id} onChange={(id) => setOp({ ...op, to_location_id: id })} className="mt-1" />
              </label>
            )}
            <label className="block text-sm">{t("wh.quantity")} *
              <input required type="number" min={0.001} step="0.001" value={op.qty} onChange={(e) => setOp({ ...op, qty: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setOp(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{busy ? t("common.saving") : t("common.save")}</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
