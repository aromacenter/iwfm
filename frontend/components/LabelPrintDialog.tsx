"use client";

/** Tárhely-címkék nyomtatása (174. kör): egyesével / kijelöltek / egy sor /
 *  a teljes raktár; papír (A4/A5/Letter ív vagy címke-/szalagnyomtató),
 *  címkeméret (Avery, Brother, Dymo, Zebra előbeállítások vagy egyedi mm),
 *  mezők (kód, QR, vonalkód, csoport-felirat, koordináták, raktár, megjegyzés),
 *  mentett, névvel ellátott minták. */

import { useEffect, useMemo, useState } from "react";
import LabelPreview from "@/components/LabelPreview";
import type { Loc } from "@/components/WarehouseLocations";
import { api, downloadFilePost, errorMessage, printFilePost } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

export type LabelPaper = "A4" | "A5" | "Letter" | "roll";
export interface LabelCfg {
  paper: LabelPaper;
  landscape: boolean;
  label_w: number;
  label_h: number;
  margin_x: number | null;
  margin_y: number | null;
  gap_x: number;
  gap_y: number;
  border: boolean;
  fields: string[];
  group_text: string;
  qr_content: "link" | "code";
  font_scale: number;
}
interface Template { id: string; name: string; config: Partial<LabelCfg>; updated_at: string }
export interface LabelSelection { mode: "all" | "row" | "picked"; row: string; ids: string[] }

export const DEFAULT_CFG: LabelCfg = {
  paper: "A4", landscape: false, label_w: 63.5, label_h: 33.9, margin_x: null, margin_y: null, gap_x: 2.54, gap_y: 0,  // = Avery L7159
  border: true, fields: ["code", "qr", "coords", "warehouse"], group_text: "", qr_content: "link", font_scale: 1,
};
const FIELDS = ["code", "qr", "barcode", "group", "coords", "warehouse", "note"] as const;
const PAPER_MM: Record<Exclude<LabelPaper, "roll">, [number, number]> = { A4: [210, 297], A5: [148, 210], Letter: [215.9, 279.4] };

interface Preset { key: string; name: string; paper: LabelPaper; w: number; h: number; gx?: number; gy?: number; landscape?: boolean; border?: boolean }
interface PresetGroup { title: string; items: Preset[] }
/** Méret-előbeállítások. Az Avery-íveknél a hézag a gyári (2,54 mm), a margó
 *  középre igazításból adódik — így a kivágásra esik. */
export const PRESETS: PresetGroup[] = [
  { title: "Avery A4 ív", items: [
    { key: "L7651", name: "Avery L7651 — 38,1 × 21,2 mm (5 × 13)", paper: "A4", w: 38.1, h: 21.2, gx: 2.54 },
    { key: "L7159", name: "Avery L7159 — 63,5 × 33,9 mm (3 × 8)", paper: "A4", w: 63.5, h: 33.9, gx: 2.54 },
    { key: "L7160", name: "Avery L7160 — 63,5 × 38,1 mm (3 × 7)", paper: "A4", w: 63.5, h: 38.1, gx: 2.54 },
    { key: "L7161", name: "Avery L7161 — 63,5 × 46,6 mm (3 × 6)", paper: "A4", w: 63.5, h: 46.6, gx: 2.54 },
    { key: "L7162", name: "Avery L7162 — 99,1 × 33,9 mm (2 × 8)", paper: "A4", w: 99.1, h: 33.9, gx: 2.54 },
    { key: "L7163", name: "Avery L7163 — 99,1 × 38,1 mm (2 × 7)", paper: "A4", w: 99.1, h: 38.1, gx: 2.54 },
    { key: "L7173", name: "Avery L7173 — 99,1 × 57 mm (2 × 5)", paper: "A4", w: 99.1, h: 57, gx: 2.54 },
    { key: "L7165", name: "Avery L7165 — 99,1 × 67,7 mm (2 × 4)", paper: "A4", w: 99.1, h: 67.7, gx: 2.54 },
    { key: "L7166", name: "Avery L7166 — 99,1 × 93,1 mm (2 × 3)", paper: "A4", w: 99.1, h: 93.1, gx: 2.54 },
    { key: "L7169", name: "Avery L7169 — 99,1 × 139 mm (2 × 2)", paper: "A4", w: 99.1, h: 139, gx: 2.54 },
    { key: "L7168", name: "Avery L7168 — 199,6 × 143,5 mm (1 × 2)", paper: "A4", w: 199.6, h: 143.5 },
    { key: "L7167", name: "Avery L7167 — 199,6 × 289,1 mm (1 × 1)", paper: "A4", w: 199.6, h: 289.1 },
  ] },
  { title: "Általános A4 ív (hézag nélkül)", items: [
    { key: "a4_52x30", name: "52,5 × 29,7 mm (4 × 10)", paper: "A4", w: 52.5, h: 29.7 },
    { key: "a4_70x37", name: "70 × 37 mm (3 × 8)", paper: "A4", w: 70, h: 37 },
    { key: "a4_70x42", name: "70 × 42,3 mm (3 × 7)", paper: "A4", w: 70, h: 42.3 },
    { key: "a4_105x48", name: "105 × 48 mm (2 × 6)", paper: "A4", w: 105, h: 48 },
    { key: "a4_105x74", name: "105 × 74 mm (2 × 4)", paper: "A4", w: 105, h: 74 },
    { key: "a4_105x148", name: "105 × 148 mm (2 × 2)", paper: "A4", w: 105, h: 148 },
    { key: "a5_full", name: "A5 polctábla (egy címke = egy A5 lap)", paper: "A5", w: 140, h: 100, landscape: true, border: false },
  ] },
  { title: "Címkenyomtató — Brother QL (DK)", items: [
    { key: "dk11209", name: "DK-11209 — 62 × 29 mm", paper: "roll", w: 62, h: 29, border: false },
    { key: "dk11201", name: "DK-11201 — 90 × 29 mm", paper: "roll", w: 90, h: 29, border: false },
    { key: "dk11208", name: "DK-11208 — 90 × 38 mm", paper: "roll", w: 90, h: 38, border: false },
    { key: "dk11202", name: "DK-11202 — 100 × 62 mm", paper: "roll", w: 100, h: 62, border: false },
    { key: "dk22205", name: "DK-22205 folytonos 62 mm — 62 × 40 mm", paper: "roll", w: 62, h: 40, border: false },
  ] },
  { title: "Szalagnyomtató — Brother P-touch (TZe)", items: [
    { key: "tze12", name: "12 mm szalag — 60 × 12 mm", paper: "roll", w: 60, h: 12, border: false },
    { key: "tze18", name: "18 mm szalag — 70 × 18 mm", paper: "roll", w: 70, h: 18, border: false },
    { key: "tze24", name: "24 mm szalag — 80 × 24 mm", paper: "roll", w: 80, h: 24, border: false },
    { key: "tze36", name: "36 mm szalag — 100 × 36 mm", paper: "roll", w: 100, h: 36, border: false },
  ] },
  { title: "Címkenyomtató — Dymo LabelWriter", items: [
    { key: "dymo11352", name: "11352 — 54 × 25 mm", paper: "roll", w: 54, h: 25, border: false },
    { key: "dymo11354", name: "11354 — 57 × 32 mm", paper: "roll", w: 57, h: 32, border: false },
    { key: "dymo99012", name: "99012 — 89 × 36 mm", paper: "roll", w: 89, h: 36, border: false },
    { key: "dymo99014", name: "99014 — 101 × 54 mm", paper: "roll", w: 101, h: 54, border: false },
  ] },
  { title: "Címkenyomtató — Zebra / általános tekercs", items: [
    { key: "roll50x25", name: "50 × 25 mm", paper: "roll", w: 50, h: 25, border: false },
    { key: "roll50x30", name: "50 × 30 mm", paper: "roll", w: 50, h: 30, border: false },
    { key: "roll100x50", name: "100 × 50 mm", paper: "roll", w: 100, h: 50, border: false },
    { key: "roll100x100", name: "100 × 100 mm", paper: "roll", w: 100, h: 100, border: false },
    { key: "roll100x150", name: "100 × 150 mm", paper: "roll", w: 100, h: 150, border: false },
  ] },
];

const LS_KEY = "wh.labelCfg";

export function labelsPerPage(cfg: LabelCfg): { cols: number; rows: number } | null {
  if (cfg.paper === "roll") return { cols: 1, rows: 1 };
  const [pw0, ph0] = PAPER_MM[cfg.paper];
  const [pw, ph] = cfg.landscape ? [ph0, pw0] : [pw0, ph0];
  const mx = cfg.margin_x ?? 4;
  const my = cfg.margin_y ?? 4;
  const cols = Math.floor((pw - 2 * mx + cfg.gap_x) / (cfg.label_w + cfg.gap_x) + 1e-6);
  const rows = Math.floor((ph - 2 * my + cfg.gap_y) / (cfg.label_h + cfg.gap_y) + 1e-6);
  if (cols < 1 || rows < 1) return null;
  return { cols, rows };
}

export default function LabelPrintDialog({
  warehouseId, warehouseName, locs, rowCodes, initial, isMgr, onClose,
}: {
  warehouseId: string;
  warehouseName: string;
  locs: Loc[];
  rowCodes: string[];
  initial: LabelSelection;
  isMgr: boolean;
  onClose: () => void;
}) {
  const { t } = useT();
  const { toast, confirm, prompt } = useUI();
  const [sel, setSel] = useState<LabelSelection>(initial);
  const [cfg, setCfg] = useState<LabelCfg>(() => {
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (raw) return { ...DEFAULT_CFG, ...(JSON.parse(raw) as Partial<LabelCfg>) };
    } catch { /* nincs tárolt beállítás */ }
    return DEFAULT_CFG;
  });
  const [copies, setCopies] = useState(1);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [tplId, setTplId] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pickFilter, setPickFilter] = useState("");

  useEffect(() => {
    api.get<Template[]>("/api/warehouses/label-templates").then(setTemplates).catch(() => {});
  }, []);
  useEffect(() => {
    try { localStorage.setItem(LS_KEY, JSON.stringify(cfg)); } catch { /* privát mód */ }
  }, [cfg]);

  const set = <K extends keyof LabelCfg>(k: K, v: LabelCfg[K]) => setCfg((c) => ({ ...c, [k]: v }));
  const toggleField = (f: string) => setCfg((c) => ({ ...c, fields: c.fields.includes(f) ? c.fields.filter((x) => x !== f) : [...c.fields, f] }));

  const presetKey = useMemo(() => {
    for (const g of PRESETS) for (const p of g.items) {
      if (p.paper === cfg.paper && Math.abs(p.w - cfg.label_w) < 0.01 && Math.abs(p.h - cfg.label_h) < 0.01 && Math.abs((p.gx ?? 0) - cfg.gap_x) < 0.01 && Math.abs((p.gy ?? 0) - cfg.gap_y) < 0.01) return p.key;
    }
    return "custom";
  }, [cfg]);
  function applyPreset(key: string) {
    for (const g of PRESETS) for (const p of g.items) {
      if (p.key === key) {
        setCfg((c) => ({ ...c, paper: p.paper, label_w: p.w, label_h: p.h, gap_x: p.gx ?? 0, gap_y: p.gy ?? 0, margin_x: null, margin_y: null, landscape: p.landscape ?? false, border: p.border ?? c.border }));
        return;
      }
    }
  }

  const activeLocs = locs.filter((l) => l.is_active);
  const selectedLocs = sel.mode === "all" ? activeLocs : sel.mode === "row" ? activeLocs.filter((l) => l.row_code === sel.row) : activeLocs.filter((l) => sel.ids.includes(l.id));
  const count = selectedLocs.length * copies;
  const per = labelsPerPage(cfg);
  const pages = per ? Math.ceil(count / (per.cols * per.rows)) : 0;

  function body() {
    const base: Record<string, unknown> = { ...cfg, group_text: cfg.group_text.trim() || null, copies };
    if (sel.mode === "row") base.row = sel.row;
    if (sel.mode === "picked") base.location_ids = sel.ids;
    return base;
  }
  async function run(kind: "print" | "download") {
    if (selectedLocs.length === 0) { setError(t("wh.lblNone")); return; }
    if (cfg.fields.length === 0) { setError(t("wh.lblNoFields")); return; }
    setBusy(true); setError(null);
    try {
      const path = `/api/warehouses/${warehouseId}/locations/labels`;
      if (kind === "print") await printFilePost(path, body());
      else await downloadFilePost(path, body(), `tarhely-cimkek-${warehouseName.replace(/[^\w-]+/g, "-")}.pdf`);
    } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }

  // ── Mentett minták ──
  function loadTemplate(id: string) {
    setTplId(id);
    const tp = templates.find((x) => x.id === id);
    if (tp) setCfg({ ...DEFAULT_CFG, ...tp.config, group_text: tp.config.group_text ?? "" });
  }
  async function saveTemplate() {
    const cur = templates.find((x) => x.id === tplId);
    const name = await prompt(t("wh.lblTplName"), { initial: cur?.name ?? "" });
    if (!name?.trim()) return;
    try {
      const tp = await api.post<Template>("/api/warehouses/label-templates", { name: name.trim(), config: { ...cfg, group_text: cfg.group_text.trim() || null } });
      setTemplates((ts) => [...ts.filter((x) => x.id !== tp.id), tp].sort((a, b) => a.name.localeCompare(b.name, "hu")));
      setTplId(tp.id);
      toast(t("wh.lblTplSaved", { name: tp.name }), "success");
    } catch (err) { toast(errorMessage(err), "error"); }
  }
  async function deleteTemplate() {
    const cur = templates.find((x) => x.id === tplId);
    if (!cur || !(await confirm(t("wh.lblTplDeleteConfirm", { name: cur.name })))) return;
    try {
      await api.delete(`/api/warehouses/label-templates/${cur.id}`);
      setTemplates((ts) => ts.filter((x) => x.id !== cur.id));
      setTplId("");
    } catch (err) { toast(errorMessage(err), "error"); }
  }

  const previewLoc = selectedLocs[0] ?? activeLocs[0] ?? null;
  const previewLink = previewLoc ? `${typeof window !== "undefined" ? window.location.origin : ""}/raktar?wh=${warehouseId}&loc=${encodeURIComponent(previewLoc.code)}` : "";

  const num = (v: string, fallback: number) => { const n = Number(v.replace(",", ".")); return Number.isFinite(n) ? n : fallback; };
  const pickList = activeLocs.filter((l) => !pickFilter || l.code.toLowerCase().includes(pickFilter.toLowerCase()) || (l.label ?? "").toLowerCase().includes(pickFilter.toLowerCase()));

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4">
      <div className="relative my-6 w-full max-w-3xl space-y-4 rounded-2xl bg-white p-6 shadow-xl">
        <button type="button" onClick={onClose} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
        <h2 className="text-lg font-semibold">🏷 {t("wh.lblTitle")} — {warehouseName}</h2>

        {/* Mentett minták */}
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm">
          <span className="font-medium">💾 {t("wh.lblTemplates")}</span>
          <select value={tplId} onChange={(e) => loadTemplate(e.target.value)} className="min-w-[200px] rounded-lg border border-slate-300 px-2 py-1 text-sm">
            <option value="">{t("wh.lblTplPick")}</option>
            {templates.map((tp) => <option key={tp.id} value={tp.id}>{tp.name}</option>)}
          </select>
          {isMgr && <button type="button" onClick={saveTemplate} className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs hover:bg-white">💾 {tplId ? t("wh.lblTplUpdate") : t("wh.lblTplSave")}</button>}
          {isMgr && tplId && <button type="button" onClick={deleteTemplate} className="rounded-lg border border-rose-300 px-2.5 py-1 text-xs text-rose-700 hover:bg-rose-50">🗑</button>}
          <span className="text-xs text-slate-500">{t("wh.lblTplHint")}</span>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          {/* Bal: mit nyomtatunk */}
          <div className="space-y-3">
            <h3 className="text-sm font-semibold">{t("wh.lblWhat")}</h3>
            <div className="space-y-1 text-sm">
              <label className="flex items-center gap-2"><input type="radio" checked={sel.mode === "all"} onChange={() => setSel({ ...sel, mode: "all" })} /> {t("wh.lblAll", { count: activeLocs.length })}</label>
              <label className="flex items-center gap-2"><input type="radio" checked={sel.mode === "row"} onChange={() => setSel({ ...sel, mode: "row", row: sel.row || rowCodes[0] || "" })} /> {t("wh.lblRow")}
                <select value={sel.row} onChange={(e) => setSel({ ...sel, mode: "row", row: e.target.value })} className="rounded border border-slate-300 px-2 py-0.5 text-sm">
                  {rowCodes.map((r) => <option key={r} value={r}>{t("wh.locRow")} {r} ({activeLocs.filter((l) => l.row_code === r).length})</option>)}
                </select>
              </label>
              <label className="flex items-center gap-2"><input type="radio" checked={sel.mode === "picked"} onChange={() => setSel({ ...sel, mode: "picked" })} /> {t("wh.lblPicked", { count: sel.ids.length })}</label>
            </div>
            {sel.mode === "picked" && (
              <div className="rounded-xl border border-slate-200">
                <div className="flex items-center gap-2 border-b border-slate-200 px-2 py-1.5 text-xs">
                  <input value={pickFilter} onChange={(e) => setPickFilter(e.target.value)} placeholder={t("wh.lblFilter")} className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-1" />
                  <button type="button" onClick={() => setSel({ ...sel, ids: Array.from(new Set([...sel.ids, ...pickList.map((l) => l.id)])) })} className="rounded border border-slate-300 px-2 py-1 hover:bg-slate-100">{t("wh.lblPickAll")}</button>
                  <button type="button" onClick={() => setSel({ ...sel, ids: [] })} className="rounded border border-slate-300 px-2 py-1 hover:bg-slate-100">{t("wh.lblPickNone")}</button>
                </div>
                <div className="max-h-56 overflow-y-auto p-2">
                  <div className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-xs">
                    {pickList.map((l) => (
                      <label key={l.id} className="flex items-center gap-1.5 truncate">
                        <input type="checkbox" checked={sel.ids.includes(l.id)} onChange={(e) => setSel({ ...sel, ids: e.target.checked ? [...sel.ids, l.id] : sel.ids.filter((x) => x !== l.id) })} />
                        <span className="font-mono">{l.code}</span>{l.label && <span className="truncate text-slate-500">— {l.label}</span>}
                      </label>
                    ))}
                    {pickList.length === 0 && <span className="col-span-2 text-slate-400">{t("wh.lblNone")}</span>}
                  </div>
                </div>
              </div>
            )}
            <label className="flex items-center gap-2 text-sm">{t("wh.lblCopies")}
              <input type="number" min={1} max={20} value={copies} onChange={(e) => setCopies(Math.min(20, Math.max(1, Number(e.target.value) || 1)))} className="w-20 rounded border border-slate-300 px-2 py-1" />
            </label>

            <h3 className="pt-2 text-sm font-semibold">{t("wh.lblFields")}</h3>
            <div className="grid grid-cols-2 gap-1 text-sm">
              {FIELDS.map((f) => (
                <label key={f} className="flex items-center gap-2"><input type="checkbox" checked={cfg.fields.includes(f)} onChange={() => toggleField(f)} /> {t(`wh.lblField_${f}`)}</label>
              ))}
            </div>
            {cfg.fields.includes("group") && (
              <label className="block text-sm">{t("wh.lblGroupText")}
                <input value={cfg.group_text} onChange={(e) => set("group_text", e.target.value)} maxLength={40} placeholder={t("wh.lblGroupPlaceholder")} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5" />
              </label>
            )}
            {previewLoc && (
              <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                <div className="mb-2 flex items-baseline justify-between text-xs text-slate-500">
                  <span>👁 {t("wh.lblPreview", { code: previewLoc.code })}</span>
                  <span>{cfg.label_w} × {cfg.label_h} mm</span>
                </div>
                <div className="flex justify-center overflow-x-auto rounded-lg p-2" style={{ background: "#e2e8f0" }}>
                  <LabelPreview cfg={cfg} loc={previewLoc} warehouseName={warehouseName} link={previewLink} />
                </div>
              </div>
            )}
            {cfg.fields.includes("qr") && (
              <div className="text-sm">
                <span className="text-slate-600">{t("wh.lblQrContent")}</span>
                <div className="mt-1 space-y-0.5 text-xs">
                  <label className="flex items-center gap-2"><input type="radio" checked={cfg.qr_content === "link"} onChange={() => set("qr_content", "link")} /> {t("wh.lblQrLink")}</label>
                  <label className="flex items-center gap-2"><input type="radio" checked={cfg.qr_content === "code"} onChange={() => set("qr_content", "code")} /> {t("wh.lblQrCode")}</label>
                </div>
              </div>
            )}
          </div>

          {/* Jobb: papír és méret */}
          <div className="space-y-3">
            <h3 className="text-sm font-semibold">{t("wh.lblSize")}</h3>
            <label className="block text-sm">{t("wh.lblPreset")}
              <select value={presetKey} onChange={(e) => applyPreset(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm">
                <option value="custom">{t("wh.lblCustom")}</option>
                {PRESETS.map((g) => (
                  <optgroup key={g.title} label={g.title}>
                    {g.items.map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}
                  </optgroup>
                ))}
              </select>
            </label>
            <div className="grid grid-cols-2 gap-2 text-sm">
              <label className="block">{t("wh.lblPaper")}
                <select value={cfg.paper} onChange={(e) => set("paper", e.target.value as LabelPaper)} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm">
                  <option value="A4">A4 {t("wh.lblSheet")}</option>
                  <option value="A5">A5 {t("wh.lblSheet")}</option>
                  <option value="Letter">Letter {t("wh.lblSheet")}</option>
                  <option value="roll">{t("wh.lblRoll")}</option>
                </select>
              </label>
              {cfg.paper !== "roll" && (
                <label className="flex items-end gap-2 pb-2"><input type="checkbox" checked={cfg.landscape} onChange={(e) => set("landscape", e.target.checked)} /> {t("wh.lblLandscape")}</label>
              )}
              <label className="block">{t("wh.lblW")}
                <input type="number" min={20} max={300} step="0.1" value={cfg.label_w} onChange={(e) => set("label_w", num(e.target.value, cfg.label_w))} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5" />
              </label>
              <label className="block">{cfg.paper === "roll" ? t("wh.lblHTape") : t("wh.lblH")}
                <input type="number" min={6} max={300} step="0.1" value={cfg.label_h} onChange={(e) => set("label_h", num(e.target.value, cfg.label_h))} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5" />
              </label>
              {cfg.paper !== "roll" && (<>
                <label className="block">{t("wh.lblGapX")}
                  <input type="number" min={0} max={30} step="0.01" value={cfg.gap_x} onChange={(e) => set("gap_x", num(e.target.value, 0))} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5" />
                </label>
                <label className="block">{t("wh.lblGapY")}
                  <input type="number" min={0} max={30} step="0.01" value={cfg.gap_y} onChange={(e) => set("gap_y", num(e.target.value, 0))} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5" />
                </label>
                <label className="block">{t("wh.lblMarginX")}
                  <input type="number" min={0} max={50} step="0.1" value={cfg.margin_x ?? ""} placeholder={t("wh.lblAuto")} onChange={(e) => set("margin_x", e.target.value === "" ? null : num(e.target.value, 0))} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5" />
                </label>
                <label className="block">{t("wh.lblMarginY")}
                  <input type="number" min={0} max={50} step="0.1" value={cfg.margin_y ?? ""} placeholder={t("wh.lblAuto")} onChange={(e) => set("margin_y", e.target.value === "" ? null : num(e.target.value, 0))} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5" />
                </label>
              </>)}
              <label className="block">{t("wh.lblFont")}
                <select value={String(cfg.font_scale)} onChange={(e) => set("font_scale", Number(e.target.value))} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm">
                  <option value="0.7">{t("wh.lblFontS")}</option>
                  <option value="1">{t("wh.lblFontM")}</option>
                  <option value="1.3">{t("wh.lblFontL")}</option>
                  <option value="1.6">{t("wh.lblFontXL")}</option>
                </select>
              </label>
              <label className="flex items-end gap-2 pb-2"><input type="checkbox" checked={cfg.border} onChange={(e) => set("border", e.target.checked)} /> {t("wh.lblBorder")}</label>
            </div>
            <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">
              {per
                ? (cfg.paper === "roll" ? t("wh.lblSummaryRoll", { count }) : t("wh.lblSummarySheet", { count, cols: per.cols, rows: per.rows, pages }))
                : <span className="text-rose-700">{t("wh.lblTooBig")}</span>}
              <br /><span className="text-slate-400">{cfg.paper === "roll" ? t("wh.lblRollHint") : t("wh.lblSheetHint")}</span>
            </p>
          </div>
        </div>

        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex flex-wrap justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
          <button type="button" disabled={busy || !per} onClick={() => run("download")} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100 disabled:opacity-50">⬇ PDF</button>
          <button type="button" disabled={busy || !per} onClick={() => run("print")} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">🖨 {t("wh.lblPrint")}</button>
        </div>
      </div>
    </div>
  );
}
