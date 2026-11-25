"use client";

/** Raktár — szkenner-mód (176. kör): egyképernyős, telefonra szabott nézet.
 *  Beolvasod a polccímkét (kamera / kézi olvasó / beírt kód) → a tárhely
 *  tartalma; innen berakás, levétel, áthelyezés (cél-tárhely beolvasásával)
 *  és tárhelyi leltár. Termékkód beolvasásával fordított sorrend is megy:
 *  termék + mennyiség, aztán a tárhely. */

import { useCallback, useEffect, useRef, useState } from "react";
import AppShell from "@/components/AppShell";
import CameraScanner, { cameraScanSupported } from "@/components/CameraScanner";
import SearchSelect from "@/components/SearchSelect";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface Warehouse { id: string; name: string; kind: string; layout: "simple" | "located"; is_active: boolean }
interface Product { id: string; name: string; unit: string; code?: string | null; category?: string | null; is_active: boolean }
interface Loc { id: string; code: string; label: string | null; row_code: string; bay: number; level: number; slot: number; is_active: boolean }
interface Content { product_id: string; product_name: string; unit: string; quantity: number }
interface ScanOut { kind: "location" | "product"; location: Loc | null; contents: Content[]; product: { id: string; name: string; unit: string; code: string | null } | null }

type Pending = { product_id: string; product_name: string; unit: string; qty: number; from: Loc | null };

const fmt = (n: number) => n.toLocaleString("hu-HU", { maximumFractionDigits: 3 });
const LS_WH = "wh.scanWh";

export default function ScannerPage() {
  const { t } = useT();
  const { toast, prompt } = useUI();
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [whId, setWhId] = useState<string>("");
  const [products, setProducts] = useState<Product[]>([]);
  const [loc, setLoc] = useState<Loc | null>(null);
  const [contents, setContents] = useState<Content[]>([]);
  const [mode, setMode] = useState<"idle" | "place" | "count">("idle");
  const [pending, setPending] = useState<Pending | null>(null);  // áthelyezés / termékről indított berakás
  const [scanning, setScanning] = useState(false);
  const [manual, setManual] = useState("");
  const [form, setForm] = useState({ product_id: "", qty: "" });
  const [counts, setCounts] = useState<{ product_id: string; name: string; unit: string; counted: string }[]>([]);
  const [addId, setAddId] = useState("");
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const manualRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.get<Warehouse[]>("/api/warehouses").then((rows) => {
      const located = rows.filter((w) => w.layout === "located" && w.is_active);
      setWarehouses(located);
      const fromUrl = new URLSearchParams(window.location.search).get("wh");
      let saved: string | null = null;
      try { saved = localStorage.getItem(LS_WH); } catch { /* privát mód */ }
      const pick = [fromUrl, saved].find((id) => id && located.some((w) => w.id === id)) ?? located[0]?.id ?? "";
      setWhId(pick);
    }).catch(() => {});
    api.get<Product[]>("/api/products").then((p) => setProducts(p.filter((x) => x.is_active !== false))).catch(() => {});
  }, []);
  useEffect(() => {
    if (!whId) return;
    try { localStorage.setItem(LS_WH, whId); } catch { /* privát mód */ }
  }, [whId]);

  const addLog = (s: string) => setLog((l) => [s, ...l].slice(0, 6));

  const refresh = useCallback(async (l: Loc) => {
    const c = await api.get<Content[]>(`/api/warehouses/${whId}/locations/${l.id}/contents`);
    setContents(c);
    return c;
  }, [whId]);

  function openLocation(l: Loc, c: Content[]) {
    setLoc(l); setContents(c); setMode("idle"); setForm({ product_id: "", qty: "" });
  }

  async function onCode(raw: string) {
    const code = raw.trim();
    if (!code || !whId) return;
    setBusy(true);
    try {
      const r = await api.get<ScanOut>(`/api/warehouses/${whId}/scan?code=${encodeURIComponent(code)}`);
      if (r.kind === "location" && r.location) {
        if (pending) {
          // áthelyezés cél-tárhelye vagy termékről indított berakás
          let msg: string;
          if (pending.from) {
            await api.post(`/api/warehouses/${whId}/locations/relocate`, { product_id: pending.product_id, from_location_id: pending.from.id, to_location_id: r.location.id, quantity: pending.qty });
            msg = t("scan.relocated", { product: pending.product_name, qty: fmt(pending.qty), unit: pending.unit, from: pending.from.code, to: r.location.code });
          } else {
            await api.post(`/api/warehouses/${whId}/locations/place`, { product_id: pending.product_id, location_id: r.location.id, quantity: pending.qty });
            msg = t("scan.placed", { product: pending.product_name, qty: fmt(pending.qty), unit: pending.unit, code: r.location.code });
          }
          addLog(msg);
          toast(msg, "success");
          setPending(null);
          openLocation(r.location, await refresh(r.location));
        } else {
          openLocation(r.location, r.contents);
        }
      } else if (r.kind === "product" && r.product) {
        if (loc && !pending) {
          // tárhely nyitva → a beolvasott termék a berakás-űrlapba kerül
          setMode("place"); setForm((f) => ({ ...f, product_id: r.product!.id }));
        } else {
          const qty = await prompt(`${r.product.name} — ${t("scan.qty")} (${r.product.unit})`, { type: "number" });
          const n = Number((qty ?? "").replace(",", "."));
          if (!(n > 0)) return;
          setPending({ product_id: r.product.id, product_name: r.product.name, unit: r.product.unit, qty: n, from: null });
          setLoc(null); setContents([]);
        }
      }
    } catch (err) {
      const e = err as { code?: string };
      toast(e?.code === "warehouse.scan_unknown" ? t("scan.notFound", { code }) : errorMessage(err), "error");
    } finally { setBusy(false); setScanning(false); setManual(""); manualRef.current?.focus(); }
  }

  async function doPlace(e: React.FormEvent) {
    e.preventDefault();
    if (!loc) return;
    const p = products.find((x) => x.id === form.product_id);
    const n = Number(form.qty.replace(",", "."));
    if (!p || !(n > 0)) return;
    setBusy(true);
    try {
      await api.post(`/api/warehouses/${whId}/locations/place`, { product_id: p.id, location_id: loc.id, quantity: n });
      const msg = t("scan.placed", { product: p.name, qty: fmt(n), unit: p.unit, code: loc.code });
      addLog(msg); toast(msg, "success");
      setForm({ product_id: "", qty: "" }); setMode("idle");
      await refresh(loc);
    } catch (err) { toast(errorMessage(err), "error"); } finally { setBusy(false); }
  }

  async function doUnplace(c: Content) {
    if (!loc) return;
    const qty = await prompt(`${c.product_name} — ${t("scan.unplace")} (${t("scan.qty")}, ${c.unit})`, { type: "number", initial: String(c.quantity) });
    const n = Number((qty ?? "").replace(",", "."));
    if (!(n > 0)) return;
    setBusy(true);
    try {
      await api.post(`/api/warehouses/${whId}/locations/unplace`, { product_id: c.product_id, location_id: loc.id, quantity: n });
      const msg = t("scan.unplaced", { product: c.product_name, qty: fmt(n), unit: c.unit, code: loc.code });
      addLog(msg); toast(msg, "success");
      await refresh(loc);
    } catch (err) { toast(errorMessage(err), "error"); } finally { setBusy(false); }
  }

  async function startRelocate(c: Content) {
    if (!loc) return;
    const qty = await prompt(`${c.product_name} — ${t("scan.relocate")} (${t("scan.qty")}, ${c.unit})`, { type: "number", initial: String(c.quantity) });
    const n = Number((qty ?? "").replace(",", "."));
    if (!(n > 0)) return;
    setPending({ product_id: c.product_id, product_name: c.product_name, unit: c.unit, qty: n, from: loc });
  }

  function startCount() {
    setCounts(contents.map((c) => ({ product_id: c.product_id, name: c.product_name, unit: c.unit, counted: String(c.quantity) })));
    setAddId(""); setMode("count");
  }
  async function doCount(e: React.FormEvent) {
    e.preventDefault();
    if (!loc) return;
    setBusy(true);
    try {
      const items = counts.map((c) => ({ product_id: c.product_id, counted: Number(c.counted.replace(",", ".")) || 0 }));
      const before = new Map(contents.map((c) => [c.product_id, c.quantity]));
      const changes = items.filter((i) => Math.abs((before.get(i.product_id) ?? 0) - i.counted) > 1e-9).length;
      const c = await api.post<Content[]>(`/api/warehouses/${whId}/locations/${loc.id}/count`, { items });
      const msg = t("scan.counted", { code: loc.code, changes });
      addLog(msg); toast(msg, "success");
      setContents(c); setMode("idle");
    } catch (err) { toast(errorMessage(err), "error"); } finally { setBusy(false); }
  }

  const prodItems = products.map((p) => ({ id: p.id, label: p.name, sublabel: p.code ?? p.category ?? undefined, badge: p.unit }));
  const canCamera = cameraScanSupported();

  return (
    <AppShell>
      <div className="mx-auto max-w-lg space-y-3 pb-24">
        <div className="flex items-center justify-between gap-2">
          <h1 className="text-xl font-bold">📷 {t("scan.title")}</h1>
          <a href="/raktar" className="text-sm text-indigo-700 hover:underline">{t("scan.back")}</a>
        </div>
        {warehouses.length === 0 ? (
          <p className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-6 text-center text-sm text-amber-900">{t("scan.noLocated")}</p>
        ) : (
          <>
            <label className="block text-sm">{t("scan.warehouse")}
              <select value={whId} onChange={(e) => { setWhId(e.target.value); setLoc(null); setContents([]); setPending(null); setMode("idle"); }} className="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-base">
                {warehouses.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
            </label>

            {/* Beolvasás */}
            <div className="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
              <p className="mb-2 text-xs text-slate-500">{t("scan.hint")}</p>
              <div className="flex gap-2">
                {canCamera && (
                  <button type="button" onClick={() => setScanning(true)} disabled={busy} className="flex-1 rounded-xl bg-indigo-600 px-4 py-3 text-base font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
                    📷 {t("scan.scanBtn")}
                  </button>
                )}
              </div>
              <form onSubmit={(e) => { e.preventDefault(); void onCode(manual); }} className="mt-2 flex gap-2">
                <input ref={manualRef} autoFocus value={manual} onChange={(e) => setManual(e.target.value)} placeholder={t("scan.manual")} className="min-w-0 flex-1 rounded-xl border border-slate-300 px-3 py-2.5 font-mono text-base uppercase" />
                <button disabled={busy || !manual.trim()} className="rounded-xl border border-slate-300 px-4 py-2.5 text-base font-medium hover:bg-slate-100 disabled:opacity-50">↵</button>
              </form>
            </div>

            {/* Függő művelet: cél-tárhelyre várunk */}
            {pending && (
              <div className="rounded-2xl border border-sky-300 bg-sky-50 p-3 text-sm text-sky-900">
                <p className="font-semibold">
                  {pending.from
                    ? t("scan.relocateHint", { product: pending.product_name, qty: fmt(pending.qty), unit: pending.unit, code: pending.from.code })
                    : `${pending.product_name} · ${fmt(pending.qty)} ${pending.unit} — ${t("scan.productTarget")}`}
                </p>
                <button type="button" onClick={() => setPending(null)} className="mt-2 rounded-lg border border-sky-300 px-3 py-1 text-xs hover:bg-white">{t("scan.cancel")}</button>
              </div>
            )}

            {/* Tárhely-kártya */}
            {loc && !pending && (
              <div className="rounded-2xl border border-indigo-200 bg-white p-3 shadow-sm">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="text-xs uppercase tracking-wide text-slate-400">{t("scan.location")}</span>
                  <span className="font-mono text-2xl font-bold">{loc.code}</span>
                  {loc.label && <span className="text-sm text-slate-500">— {loc.label}</span>}
                </div>
                {mode !== "count" && (
                  <div className="mt-2 divide-y divide-slate-100">
                    {contents.length === 0 && <p className="py-2 text-sm text-slate-400">{t("scan.empty")}</p>}
                    {contents.map((c) => (
                      <div key={c.product_id} className="flex items-center gap-2 py-2">
                        <div className="min-w-0 flex-1"><div className="truncate font-medium">{c.product_name}</div><div className="text-sm text-slate-500">{fmt(c.quantity)} {c.unit}</div></div>
                        <button type="button" onClick={() => void startRelocate(c)} className="rounded-lg border border-sky-300 px-2.5 py-1.5 text-sm text-sky-700 hover:bg-sky-50" title={t("scan.relocate")}>🔁</button>
                        <button type="button" onClick={() => void doUnplace(c)} className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm hover:bg-slate-100" title={t("scan.unplace")}>📤</button>
                      </div>
                    ))}
                  </div>
                )}
                {mode === "idle" && (
                  <div className="mt-3 grid grid-cols-2 gap-2">
                    <button type="button" onClick={() => setMode("place")} className="rounded-xl bg-emerald-600 px-3 py-2.5 text-sm font-semibold text-white hover:bg-emerald-700">📥 {t("scan.place")}</button>
                    <button type="button" onClick={startCount} className="rounded-xl border border-amber-300 bg-amber-50 px-3 py-2.5 text-sm font-semibold text-amber-900 hover:bg-amber-100">🔢 {t("scan.count")}</button>
                  </div>
                )}
                {mode === "place" && (
                  <form onSubmit={doPlace} className="mt-3 space-y-2 rounded-xl border border-emerald-200 bg-emerald-50/50 p-3">
                    <p className="text-xs text-slate-600">{t("scan.placeHint")}</p>
                    <SearchSelect required items={prodItems} value={form.product_id} onChange={(id) => setForm({ ...form, product_id: id })} placeholder={t("scan.product")} />
                    <input required inputMode="decimal" value={form.qty} onChange={(e) => setForm({ ...form, qty: e.target.value })} placeholder={t("scan.qty")} className="w-full rounded-xl border border-slate-300 px-3 py-2.5 text-base" />
                    <div className="flex gap-2">
                      <button type="button" onClick={() => { setMode("idle"); setForm({ product_id: "", qty: "" }); }} className="rounded-xl border border-slate-300 px-3 py-2 text-sm hover:bg-white">{t("scan.cancel")}</button>
                      <button disabled={busy} className="flex-1 rounded-xl bg-emerald-600 px-3 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-50">📥 {t("scan.placeDo")}</button>
                    </div>
                  </form>
                )}
                {mode === "count" && (
                  <form onSubmit={doCount} className="mt-3 space-y-2 rounded-xl border border-amber-200 bg-amber-50/50 p-3">
                    <p className="text-xs text-slate-600">{t("scan.countHint")}</p>
                    {counts.map((c, i) => (
                      <label key={c.product_id} className="flex items-center gap-2 text-sm">
                        <span className="min-w-0 flex-1 truncate">{c.name}</span>
                        <input inputMode="decimal" value={c.counted} onChange={(e) => setCounts((cs) => cs.map((x, j) => (j === i ? { ...x, counted: e.target.value } : x)))} className="w-24 rounded-lg border border-slate-300 px-2 py-1.5 text-right text-base" />
                        <span className="w-8 text-xs text-slate-500">{c.unit}</span>
                      </label>
                    ))}
                    <div className="flex items-center gap-2">
                      <div className="min-w-0 flex-1"><SearchSelect items={prodItems.filter((p) => !counts.some((c) => c.product_id === p.id))} value={addId} onChange={(id) => {
                        const p = products.find((x) => x.id === id);
                        if (p) setCounts((cs) => [...cs, { product_id: p.id, name: p.name, unit: p.unit, counted: "" }]);
                        setAddId("");
                      }} allowEmpty placeholder={t("scan.countAdd")} /></div>
                    </div>
                    <div className="flex gap-2">
                      <button type="button" onClick={() => setMode("idle")} className="rounded-xl border border-slate-300 px-3 py-2 text-sm hover:bg-white">{t("scan.cancel")}</button>
                      <button disabled={busy} className="flex-1 rounded-xl bg-amber-600 px-3 py-2 text-sm font-semibold text-white hover:bg-amber-700 disabled:opacity-50">🔢 {t("scan.countDo")}</button>
                    </div>
                  </form>
                )}
              </div>
            )}

            {log.length > 0 && (
              <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
                <div className="mb-1 font-semibold text-slate-500">{t("scan.log")}</div>
                {log.map((l, i) => <div key={i}>✓ {l}</div>)}
              </div>
            )}
          </>
        )}
      </div>
      {scanning && <CameraScanner onDetect={(v) => void onCode(v)} onClose={() => setScanning(false)} />}
    </AppShell>
  );
}
