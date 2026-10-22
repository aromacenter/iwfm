"use client";

/** Vending → Telemetria: a kávégépek nyilvántartása és a beküldött
 * telemetria. A gép a /api/pay/telemetry végpontra jelent a saját
 * API-kulcsával — a kulcsot itt generáljuk és visszük fel a gépre. */

import { Fragment, useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import TelemetryHistory from "@/components/TelemetryHistory";
import SearchSelect from "@/components/SearchSelect";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface Machine {
  id: string;
  machine_code: string;
  name: string;
  note: string | null;
  api_key: string | null;
  is_active: boolean;
  last_seen_at: string | null;
  telemetry: Record<string, unknown> | null;
  paid_today: number;
  revenue_today: number;
  open_errors: number;
  drinks_today: number | null;
  counter_total: number | null;
  online: boolean;
  pending_commands: string[];
  ip: string | null;
  geo: { city?: string; region?: string; country?: string; lat?: number; lon?: number; isp?: string } | null;
  location: { lat: number; lon: number; src: string | null; acc_m: number | null } | null;
}
interface SalesData {
  days: { day: string; orders: number; drinks_ok: number; pay: Record<string, number>; counter_total: number | null }[];
  products: { product_id: number; name_key: string; name: string; total: number }[];
  matrix: Record<string, Record<string, number>>;
  pay_totals: Record<string, number>;
  total_drinks: number;
  counter_total: number | null;
  counter_days: number | null;
}

interface MachineError {
  id: string;
  source: "exception" | "operation";
  message: string;
  count: number;
  first_seen: string;
  last_seen: string;
  resolved_at: string | null;
  diagnosis?: { code: string; severity: "info" | "warn" | "critical"; title: string; cause: string; steps: string[]; known: boolean; component: string };
  technical?: string;
}

interface TelemetryRow {
  created_at: string;
  data: Record<string, unknown>;
}

/** Az ügynök által a gép Product/Recipe fájljaiból összeállított katalógus. */
interface CatalogStep { canister: number; water_ml: number; coffee_g: number; order?: number }
interface CatalogRecipe { name: string; key?: string; file?: string; steps: CatalogStep[] }
interface CatalogProduct {
  id: number; product_id?: number; name: string; name_en?: string; price: number; sale_price?: number;
  recipe: string; cup?: number; enabled?: boolean; visible?: boolean; order?: number;
}
interface Catalog {
  products: CatalogProduct[];
  recipes: CatalogRecipe[];
  canisters?: { id: number; name: string }[];
  // 3.6-os ügynöktől: products/recipes = minden fájl neve, active_* = amit a gép használ
  files?: { product?: string; recipe?: string; products?: string[]; recipes?: string[]; active_recipe?: string; active_product?: string };
}
interface MachineFile { id: string; kind: string; name: string; size: number; created_at: string; text?: string }
interface RecipeEdit {
  id: string; action: "set" | "restore"; product_id: number; product_name: string; recipe_name: string;
  price: number | null; steps: CatalogStep[] | null; status: "pending" | "applied" | "failed"; error: string | null;
  created_at: string; applied_at: string | null;
}
interface RecipeEditList { originals_saved: boolean; items: RecipeEdit[] }
interface AgentRelease { id: string; version_code: number; version_name: string; filename: string; size: number; note: string | null; created_at: string }

/** Az APK verziókódja/neve a nevéből nem derül ki — a feltöltő adja meg (build.gradle). */
function readFileB64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1] ?? "");
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

function onlineState(lastSeen: string | null): "online" | "stale" | "offline" {
  if (!lastSeen) return "offline";
  const age = Date.now() - new Date(lastSeen).getTime();
  if (age < 5 * 60_000) return "online";
  if (age < 60 * 60_000) return "stale";
  return "offline";
}

const STATE_CHIP: Record<string, string> = {
  online: "bg-emerald-100 text-emerald-800",
  stale: "bg-amber-100 text-amber-800",
  offline: "bg-rose-100 text-rose-800",
};

export default function VendingTelemetriaPage() {
  const { t, lang } = useT();
  const { toast, confirm } = useUI();
  const ft = (n: number) => `${Math.round(n).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB")} Ft`;
  const fmt = (dt: string) =>
    new Date(dt).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { dateStyle: "short", timeStyle: "medium" });

  const [machines, setMachines] = useState<Machine[]>([]);
  const [form, setForm] = useState<{
    id: string | null; code: string; name: string; note: string; active: boolean;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<{ machine: Machine; rows: TelemetryRow[] } | null>(null);
  const [sales, setSales] = useState<{ machine: Machine; days: number; data: SalesData } | null>(null);
  async function pingNow(m: Machine) {
    try {
      await api.post(`/api/saferpay/machines/${m.id}/commands`, { commands: ["report", "errors", "promos", "recipes", "catalog", "assets"] });
      toast(t("vtel.queuedToast"), "success");
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }
  const geoText = (m: Machine) => {
    if (m.location) return { text: `${m.location.lat.toFixed(4)}, ${m.location.lon.toFixed(4)}${m.location.acc_m ? ` ±${m.location.acc_m} m` : ""} (${t("vtel.geoAgent")})`, lat: m.location.lat, lon: m.location.lon };
    if (m.geo && m.geo.lat != null && m.geo.lon != null) return { text: `${[m.geo.city, m.geo.region, m.geo.country].filter(Boolean).join(", ")} (${t("vtel.geoIp")}${m.geo.isp ? ` · ${m.geo.isp}` : ""})`, lat: m.geo.lat, lon: m.geo.lon };
    return null;
  };
  async function openSales(m: Machine, days = 30) {
    try {
      const data = await api.get<SalesData>(`/api/saferpay/machines/${m.id}/sales?days=${days}`);
      setSales({ machine: m, days, data });
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }
  const [keyShown, setKeyShown] = useState<string | null>(null); // machine id
  const [catalog, setCatalog] = useState<{ machine: Machine; updated_at: string | null; catalog: Catalog | null } | null>(null);
  const [edits, setEdits] = useState<RecipeEditList>({ originals_saved: false, items: [] });
  // szerkesztés alatt álló ital: ár + lépésenkénti adagok (szövegként, hogy a beírás ne ugráljon)
  // rowKey: a gép termékfájljában az `id` nem mindig egyedi → productId + sorindex
  const [editing, setEditing] = useState<{ rowKey: string; product: CatalogProduct; price: string; steps: { canister: number; water_ml: string; coffee_g: string }[] } | null>(null);
  const [editBusy, setEditBusy] = useState(false);
  const [files, setFiles] = useState<{ machine: Machine; rows: MachineFile[]; open: MachineFile | null } | null>(null);
  const [errors, setErrors] = useState<{ machine: Machine; rows: MachineError[]; withResolved: boolean } | null>(null);
  const [releases, setReleases] = useState<AgentRelease[]>([]);
  const [relForm, setRelForm] = useState<{ file: File | null; code: string; name: string; note: string } | null>(null);
  const [relBusy, setRelBusy] = useState(false);
  // Meglévő gépek a Gépek modulból — a 11 jegyű vonalkód betöltéséhez (7dc9d3ce)
  const [assets, setAssets] = useState<{ id: string; name: string; barcode: string }[]>([]);

  const load = useCallback(() => {
    api.get<Machine[]>("/api/saferpay/machines").then(setMachines).catch(() => {});
    api.get<{ id: string; name: string; barcode: string }[]>("/api/assets").then(setAssets).catch(() => {});
    api.get<AgentRelease[]>("/api/saferpay/agent/releases").then(setReleases).catch(() => {});
  }, []);

  async function uploadRelease(e: React.FormEvent) {
    e.preventDefault();
    if (!relForm?.file || !relForm.code.trim() || !relForm.name.trim()) return;
    if (!relForm.file.name.toLowerCase().endsWith(".apk")) {
      toast(t("vtel.agentBadApk"), "error");
      return;
    }
    setRelBusy(true);
    try {
      const data_b64 = await readFileB64(relForm.file);
      await api.post("/api/saferpay/agent/releases", {
        version_code: Number(relForm.code), version_name: relForm.name.trim(),
        filename: relForm.file.name, note: relForm.note.trim() || null, data_b64,
      });
      toast(t("vtel.agentUploaded", { v: relForm.name.trim() }), "success");
      setRelForm(null);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setRelBusy(false);
    }
  }

  async function deleteRelease(r: AgentRelease) {
    if (!(await confirm(t("vtel.agentDeleteConfirm", { v: r.version_name })))) return;
    try {
      await api.delete(`/api/saferpay/agent/releases/${r.id}`);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }
  useEffect(() => {
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, [load]);

  async function saveMachine(e: React.FormEvent) {
    e.preventDefault();
    if (!form || !form.code.trim() || !form.name.trim()) return;
    setBusy(true);
    const body = {
      machine_code: form.code.trim(), name: form.name.trim(),
      note: form.note.trim() || null, is_active: form.active,
    };
    try {
      if (form.id) await api.put(`/api/saferpay/machines/${form.id}`, body);
      else await api.post("/api/saferpay/machines", body);
      toast(t("common.saved"), "success");
      setForm(null);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function regenKey(m: Machine) {
    if (!(await confirm(t("vtel.regenConfirm", { name: m.name })))) return;
    try {
      await api.post(`/api/saferpay/machines/${m.id}/regen-key`, {});
      toast(t("vtel.keyRegenerated"), "success");
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function deleteMachine(m: Machine) {
    if (!(await confirm(t("vtel.deleteConfirm", { name: m.name })))) return;
    try {
      await api.delete(`/api/saferpay/machines/${m.id}`);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function openHistory(m: Machine) {
    try {
      const rows = await api.get<TelemetryRow[]>(`/api/saferpay/machines/${m.id}/telemetry`);
      setHistory({ machine: m, rows });
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function openCatalog(m: Machine) {
    try {
      const [r, e] = await Promise.all([
        api.get<{ updated_at: string | null; catalog: Catalog | null }>(`/api/saferpay/machines/${m.id}/catalog`),
        api.get<RecipeEditList>(`/api/saferpay/machines/${m.id}/recipes`),
      ]);
      setCatalog({ machine: m, ...r });
      setEdits(e);
      setEditing(null);
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  function startEdit(p: CatalogProduct, cat: Catalog, rowKey: string) {
    const q = portions(p, cat);
    setEditing({
      rowKey,
      product: p,
      price: String(p.sale_price ?? p.price ?? 0),
      steps: q.steps.map((s) => ({ canister: s.canister, water_ml: String(s.water_ml), coffee_g: String(s.coffee_g) })),
    });
  }

  async function saveEdit() {
    if (!catalog || !editing) return;
    const num = (s: string) => Number(String(s).replace(",", "."));
    setEditBusy(true);
    try {
      await api.put(`/api/saferpay/machines/${catalog.machine.id}/recipes/${editing.product.product_id ?? editing.product.id}`, {
        product_name: editing.product.name,
        recipe_name: editing.product.recipe,
        price: num(editing.price),
        steps: editing.steps.map((s) => ({ canister: s.canister, water_ml: num(s.water_ml), coffee_g: num(s.coffee_g) })),
      });
      toast(t("vtel.editQueued"), "success");
      setEditing(null);
      setEdits(await api.get<RecipeEditList>(`/api/saferpay/machines/${catalog.machine.id}/recipes`));
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setEditBusy(false);
    }
  }

  async function restoreOriginal() {
    if (!catalog) return;
    if (!(await confirm(t("vtel.restoreConfirm", { name: catalog.machine.name })))) return;
    try {
      await api.post(`/api/saferpay/machines/${catalog.machine.id}/recipes/restore`, {});
      toast(t("vtel.restoreQueued"), "success");
      setEdits(await api.get<RecipeEditList>(`/api/saferpay/machines/${catalog.machine.id}/recipes`));
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function deleteEdit(e: RecipeEdit) {
    if (!catalog) return;
    try {
      await api.delete(`/api/saferpay/machines/${catalog.machine.id}/recipes/${e.id}`);
      setEdits(await api.get<RecipeEditList>(`/api/saferpay/machines/${catalog.machine.id}/recipes`));
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  /** Az adott italra vonatkozó legfrissebb módosítás-kérés (chiphez). */
  const editFor = (p: CatalogProduct) =>
    edits.items.find((e) => e.action === "set" && e.product_id === (p.product_id ?? p.id));

  async function openErrors(m: Machine, withResolved = false) {
    try {
      const rows = await api.get<MachineError[]>(
        `/api/saferpay/machines/${m.id}/errors${withResolved ? "?include_resolved=true" : ""}`,
      );
      setErrors({ machine: m, rows, withResolved });
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  const [explained, setExplained] = useState<Record<string, string>>({});
  const [explaining, setExplaining] = useState<string | null>(null);
  async function explainError(e: MachineError) {
    if (!errors) return;
    setExplaining(e.id);
    try {
      const r = await api.post<{ explanation: string }>(`/api/saferpay/machines/${errors.machine.id}/errors/${e.id}/explain`, {});
      setExplained((x) => ({ ...x, [e.id]: r.explanation }));
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setExplaining(null);
    }
  }

  async function resolveError(e: MachineError) {
    if (!errors) return;
    try {
      await api.post(`/api/saferpay/machines/${errors.machine.id}/errors/${e.id}/resolve`, {});
      await openErrors(errors.machine, errors.withResolved);
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function openFiles(m: Machine) {
    try {
      const rows = await api.get<MachineFile[]>(`/api/saferpay/machines/${m.id}/files`);
      setFiles({ machine: m, rows, open: null });
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function openFile(f: MachineFile) {
    if (!files) return;
    try {
      const full = await api.get<MachineFile>(`/api/saferpay/machines/${files.machine.id}/files/${f.id}`);
      setFiles({ ...files, open: full });
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  /** Tartály → olvasható név (170 kávébab, 0 víz, 225 tej, 226 tejhab; egyébként a gép neve). */
  const canisterLabel = (id: number, cat: Catalog) => {
    const known = t(`vtel.canister.${id}`);
    if (!known.startsWith("vtel.")) return known;
    return cat.canisters?.find((c) => c.id === id)?.name ?? `#${id}`;
  };

  /** Egy ital adagjai a receptjéből: kávé g, víz ml, tej-lépések. */
  const portions = (p: CatalogProduct, cat: Catalog) => {
    const r = cat.recipes.find((x) => x.name === p.recipe);
    if (!r) return { coffee: 0, water: 0, milk: [] as string[], steps: [] as CatalogStep[], recipeFile: null as string | null };
    const steps = [...r.steps].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    return {
      recipeFile: (r.file ?? "") as string | null,
      coffee: steps.reduce((s, x) => s + (Number(x.coffee_g) || 0), 0),
      water: steps.reduce((s, x) => s + (Number(x.water_ml) || 0), 0),
      milk: steps.filter((x) => x.canister === 225 || x.canister === 226).map((x) => `${canisterLabel(x.canister, cat)} ${x.water_ml} ml`),
      steps,
    };
  };

  async function copyKey(m: Machine) {
    if (!m.api_key) return;
    try {
      await navigator.clipboard.writeText(m.api_key);
      toast(t("vtel.keyCopied"), "success");
    } catch {
      setKeyShown(m.id); // ha a vágólap nem megy, mutatjuk kijelöléshez
    }
  }

  /** Egy csempe a gépkártyán: nagy ikon, cím, kis állapot-sor; kiemelés hibánál / a „Most” gombnál. */
  const tile = (icon: string, label: string, sub: string | null, onClick: () => void, tone?: "alert" | "accent", title?: string) => {
    const toneCls = tone === "alert"
      ? "border-rose-300 ring-1 ring-rose-200 hover:border-rose-400"
      : tone === "accent"
        ? "border-indigo-300 ring-1 ring-indigo-200 hover:border-indigo-400"
        : "border-slate-200 hover:border-indigo-300";
    const subCls = tone === "alert" ? "text-rose-600 font-semibold" : tone === "accent" ? "text-indigo-600" : "text-slate-400";
    return (
      <button
        type="button"
        onClick={onClick}
        title={title}
        className={`flex min-h-[4.5rem] flex-col items-center justify-center gap-0.5 rounded-xl border bg-slate-50 px-1 py-2 text-center transition hover:shadow-sm ${toneCls}`}
      >
        <span className="text-xl leading-none">{icon}</span>
        <span className="text-[11px] font-medium leading-tight text-slate-700">{label}</span>
        {sub && <span className={`text-[10px] leading-tight ${subCls}`}>{sub}</span>}
      </button>
    );
  };

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-xl font-semibold">📡 {t("vtel.title")}</h1>
          <p className="text-sm text-slate-500">{t("vtel.subtitle")}</p>
        </div>
        <button
          onClick={() => setForm({ id: null, code: "", name: "", note: "", active: true })}
          className="ml-auto rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
        >
          + {t("vtel.addMachine")}
        </button>
      </div>

      {machines.length === 0 ? (
        <div className="rounded-2xl border border-slate-200 bg-white p-10 text-center text-sm text-slate-400 shadow-sm">
          {t("vtel.empty")}
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {machines.map((m) => {
            const state = onlineState(m.last_seen_at);
            return (
              <div key={m.id} className={`rounded-2xl border bg-white p-4 shadow-sm ${m.is_active ? "border-slate-200" : "border-slate-200 opacity-60"}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="font-semibold">🤖 {m.name}</h2>
                  <span className="font-mono text-xs text-slate-400">{m.machine_code}</span>
                  <span className={`ml-auto rounded px-2 py-0.5 text-xs font-medium ${STATE_CHIP[state]}`}>
                    {t(`vtel.state.${state}`)}
                  </span>
                </div>
                <p className="mt-0.5 text-xs text-slate-500">
                  {m.last_seen_at
                    ? t("vtel.lastSeen", { at: fmt(m.last_seen_at) })
                    : t("vtel.neverSeen")}
                  {" · "}✅ {t("vtel.todayStats", { n: m.paid_today })} · {ft(m.revenue_today)}
                  {" · "}<span className={`inline-block h-2 w-2 rounded-full align-middle ${m.online ? "bg-emerald-500" : "bg-rose-500"}`} /> {m.online ? t("vtel.online") : t("vtel.offline")}
                  {m.counter_total != null && <> · 🧮 {t("vtel.counter")}: <b>{m.counter_total.toLocaleString("hu-HU")}</b></>}
                  {m.drinks_today != null && <> · ☕ {t("vtel.drinksToday", { n: m.drinks_today })}</>}
                </p>
                {m.note && <p className="mt-0.5 text-xs text-slate-400">{m.note}</p>}
                {(() => { const g = geoText(m); return (
                  <p className="mt-0.5 text-xs text-slate-500">📍 {g ? <>{g.text} · <a href={`https://www.google.com/maps?q=${g.lat},${g.lon}`} target="_blank" rel="noreferrer" className="text-indigo-600 hover:underline">{t("vtel.map")}</a></> : t("vtel.noLocation")}{m.ip && <span className="ml-1 font-mono text-[10px] text-slate-400">· {m.ip}</span>}</p>
                ); })()}
                {m.pending_commands.length > 0 && <p className="mt-0.5 text-xs text-amber-700">⏳ {t("vtel.queued", { list: m.pending_commands.map((c) => t(`vtel.cmd.${c}`)).join(", ") })}</p>}
                {keyShown === m.id && m.api_key && (
                  <input
                    readOnly
                    value={m.api_key}
                    onFocus={(e) => e.target.select()}
                    className="mt-2 w-full rounded-lg border border-slate-300 bg-slate-50 px-2 py-1 font-mono text-xs"
                  />
                )}
                {/* Csempék: a gép fő nézetei kiemelve, az adminisztráció halkabban alatta (user-kérés) */}
                <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-6">
                  {tile("📜", t("vtel.history"), m.last_seen_at ? fmt(m.last_seen_at).split(" ").slice(-1)[0] : "—", () => void openHistory(m))}
                  {tile("📊", t("vtel.sales"), m.drinks_today != null ? t("vtel.drinksToday", { n: m.drinks_today }) : ft(m.revenue_today), () => void openSales(m))}
                  {tile("☕", t("vtel.catalog"), null, () => void openCatalog(m))}
                  {tile("⚠️", t("vtel.errors"), m.open_errors > 0 ? t("vtel.errorsBadge", { n: m.open_errors }) : "0", () => void openErrors(m), m.open_errors > 0 ? "alert" : undefined)}
                  {tile("📄", t("vtel.files"), null, () => void openFiles(m))}
                  {tile("📡", t("vtel.pingNow"), m.pending_commands.length > 0 ? `⏳ ${m.pending_commands.length}` : (m.online ? t("vtel.online") : t("vtel.offline")), () => void pingNow(m), "accent", t("vtel.pingNowHint"))}
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <button onClick={() => void copyKey(m)} className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs text-slate-500 hover:bg-slate-100">
                    🔑 {t("vtel.copyKey")}
                  </button>
                  <button onClick={() => void regenKey(m)} className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs text-slate-500 hover:bg-slate-100">
                    ♻️ {t("vtel.regenKey")}
                  </button>
                  <button
                    onClick={() => setForm({ id: m.id, code: m.machine_code, name: m.name, note: m.note ?? "", active: m.is_active })}
                    className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs text-slate-500 hover:bg-slate-100"
                  >
                    ✏️ {t("common.edit")}
                  </button>
                  <button onClick={() => void deleteMachine(m)} className="ml-auto rounded-lg border border-slate-200 px-2.5 py-1 text-xs text-rose-600 hover:bg-rose-50">
                    🗑 {t("common.delete")}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
      <p className="mt-3 text-xs text-slate-400">{t("vtel.apiHint")}</p>

      {/* Ügynök-kiadások: APK feltöltése → a gépek online frissülnek */}
      <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-semibold">📦 {t("vtel.agent")}</h2>
          <button
            onClick={() => setRelForm({ file: null, code: "", name: "", note: "" })}
            className="ml-auto rounded-lg border border-slate-300 px-3 py-1 text-xs hover:bg-slate-100"
          >
            {t("vtel.agentUpload")}
          </button>
        </div>
        <p className="mt-1 text-xs text-slate-500">{t("vtel.agentHint")}</p>
        {releases.length === 0 ? (
          <p className="mt-3 text-xs text-slate-400">{t("vtel.agentNoRelease")}</p>
        ) : (
          <div className="mt-3 space-y-1 text-sm">
            {releases.map((r, i) => (
              <div key={r.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-100 px-3 py-1.5">
                <span className="font-medium">v{r.version_name}</span>
                <span className="font-mono text-xs text-slate-400">#{r.version_code}</span>
                {i === 0 && <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] text-emerald-800">{t("vtel.agentLatest")}</span>}
                <span className="text-xs text-slate-400">{r.filename} · {Math.round(r.size / 1024)} KB · {fmt(r.created_at)}</span>
                {r.note && <span className="text-xs text-slate-500">— {r.note}</span>}
                <button onClick={() => void deleteRelease(r)} className="ml-auto rounded px-1.5 text-xs text-rose-600 hover:bg-rose-50">🗑</button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* APK-feltöltő modal */}
      {relForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={uploadRelease} className="relative w-full max-w-sm space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => setRelForm(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">📦 {t("vtel.agentUpload")}</h2>
            <input
              type="file"
              accept=".apk,application/vnd.android.package-archive"
              required
              onChange={(e) => setRelForm({ ...relForm, file: e.target.files?.[0] ?? null })}
              className="w-full text-sm"
            />
            <div className="grid grid-cols-2 gap-2">
              <label className="block text-sm">
                {t("vtel.agentVersionCode")} *
                <input required type="number" min={1} value={relForm.code} onChange={(e) => setRelForm({ ...relForm, code: e.target.value })} placeholder="9" className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
              <label className="block text-sm">
                {t("vtel.agentVersionName")} *
                <input required value={relForm.name} onChange={(e) => setRelForm({ ...relForm, name: e.target.value })} placeholder="2.5" className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
            </div>
            <label className="block text-sm">
              {t("vtel.agentNote")}
              <input value={relForm.note} onChange={(e) => setRelForm({ ...relForm, note: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setRelForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={relBusy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
                {relBusy ? t("common.saving") : t("common.save")}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Gép-űrlap modal */}
      {form && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={saveMachine} className="relative w-full max-w-sm space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => setForm(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">🤖 {form.id ? t("vtel.editMachine") : t("vtel.addMachine")}</h2>
            <label className="block text-sm">
              {t("vtel.name")} *
              <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="block text-sm">
              {t("vtel.code")} *
              <div className="mt-1 flex gap-2">
                <input required value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="pl. 26959572663" className="w-full rounded-lg border border-slate-300 px-3 py-2" />
                <button
                  type="button"
                  title={t("vtel.generateCode")}
                  onClick={() =>
                    api.get<{ barcode: string }>("/api/assets/generate-barcode")
                      .then((r) => setForm((f) => (f ? { ...f, code: r.barcode } : f)))
                      .catch((err) => toast(errorMessage(err), "error"))
                  }
                  className="shrink-0 rounded-lg border border-slate-300 px-3 py-2 text-sm hover:bg-slate-100"
                >
                  🎲
                </button>
              </div>
            </label>
            {/* Meglévő gép kiválasztása a Gépek modulból → a vonalkódja lesz a kód */}
            {assets.length > 0 && (
              <div className="text-sm">
                <p className="mb-1 text-xs text-slate-500">{t("vtel.pickAsset")}</p>
                <SearchSelect
                  items={assets.map((a) => ({ id: a.id, label: a.name, badge: a.barcode, keywords: a.barcode }))}
                  value={assets.find((a) => a.barcode === form.code)?.id ?? ""}
                  onChange={(id) => {
                    const a = assets.find((x) => x.id === id);
                    if (a) setForm((f) => (f ? { ...f, code: a.barcode, name: f.name || a.name } : f));
                  }}
                  placeholder={t("vtel.pickAssetPh")}
                />
              </div>
            )}
            <label className="block text-sm">
              {t("vtel.note")}
              <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.active} onChange={(e) => setForm({ ...form, active: e.target.checked })} className="h-4 w-4" />
              {t("vtel.activeLabel")}
            </label>
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
                {busy ? t("common.saving") : t("common.save")}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Italok és adagok modal — a gép Product/Recipe fájljaiból */}
      {catalog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="relative max-h-[85vh] w-full max-w-3xl space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => setCatalog(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">☕ {catalog.machine.name} — {t("vtel.catalog")}</h2>
            {!catalog.catalog ? (
              <p className="py-6 text-center text-sm text-slate-400">{t("vtel.catalogEmpty")}</p>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
                  <span>
                    {catalog.updated_at && t("vtel.catalogUpdated", { at: fmt(catalog.updated_at) })}
                    {catalog.catalog.files?.product && ` · ${catalog.catalog.files.product}`}
                    {(catalog.catalog.files?.recipes?.length ?? 0) > 1
                      ? ` · ${t("vtel.recipeFiles", { n: catalog.catalog.files!.recipes!.length })}: ${catalog.catalog.files!.recipes!.map((f) => f === catalog.catalog!.files!.active_recipe ? `${f} (${t("vtel.activeFile")})` : f).join(", ")}`
                      : catalog.catalog.files?.recipe && ` · ${catalog.catalog.files.recipe}`}
                  </span>
                  <span className={`rounded px-1.5 py-0.5 ${edits.originals_saved ? "bg-emerald-50 text-emerald-700" : "bg-slate-100"}`}>
                    {edits.originals_saved ? `🛡 ${t("vtel.originalsSaved")}` : t("vtel.originalsMissing")}
                  </span>
                  <button onClick={() => void restoreOriginal()} className="ml-auto rounded-lg border border-amber-300 px-2.5 py-1 text-xs text-amber-700 hover:bg-amber-50">
                    ↩️ {t("vtel.restoreOriginal")}
                  </button>
                </div>
                <p className="text-xs text-slate-400">{t("vtel.editHint")}</p>
                {edits.items.some((e) => e.status !== "applied") && (
                  <div className="space-y-1 rounded-xl border border-slate-100 bg-slate-50 p-2 text-xs">
                    <p className="font-medium text-slate-600">{t("vtel.pendingEdits")}</p>
                    {edits.items.filter((e) => e.status !== "applied").map((e) => (
                      <div key={e.id} className="flex flex-wrap items-center gap-2">
                        <span>{e.action === "restore" ? `↩️ ${t("vtel.restoreOriginal")}` : e.product_name}</span>
                        {e.action === "set" && e.price !== null && <span className="text-slate-500">{ft(e.price)}</span>}
                        {e.action === "set" && e.steps && (
                          <span className="text-slate-500">
                            {e.steps.map((s, i) => `${canisterLabel(s.canister, catalog.catalog as Catalog)}${s.coffee_g ? ` ${s.coffee_g} g` : ""}${s.water_ml ? ` ${s.water_ml} ml` : ""}`).join(" · ")}
                          </span>
                        )}
                        <span className="rounded bg-white px-1.5 py-0.5" title={e.error ?? undefined}>{t(`vtel.editStatus.${e.status}`)}</span>
                        {e.error && <span className="text-rose-600">{e.error}</span>}
                        <button onClick={() => void deleteEdit(e)} className="ml-auto rounded px-1 text-rose-600 hover:bg-rose-50">🗑</button>
                      </div>
                    ))}
                  </div>
                )}
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                        <th className="py-1.5 pr-2">{t("vtel.product")}</th>
                        <th className="py-1.5 pr-2 text-right">{t("vtel.price")}</th>
                        <th className="py-1.5 pr-2 text-right">{t("vtel.coffeeG")}</th>
                        <th className="py-1.5 pr-2 text-right">{t("vtel.waterMl")}</th>
                        <th className="py-1.5 pr-2">{t("vtel.milk")}</th>
                        <th className="py-1.5">{t("vtel.steps")}</th>
                        <th className="py-1.5"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {/* A gép termékfájljában ugyanaz az ital több gomb-pozíción is szerepelhet
                          (azonos id + név + recept): egyszer mutatjuk, a pozíciók számával. */}
                      {(() => {
                        const sorted = [...catalog.catalog.products].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
                        const seen = new Map<string, number[]>();
                        const uniq: typeof sorted = [];
                        for (const p of sorted) {
                          const k = `${p.product_id ?? p.id}|${p.name}|${p.recipe ?? ""}|${p.price ?? ""}`;
                          const hit = seen.get(k);
                          if (hit) { hit.push(p.order ?? 0); continue; }
                          seen.set(k, [p.order ?? 0]);
                          uniq.push(p);
                        }
                        return uniq.map((p, idx) => {
                          const positions = seen.get(`${p.product_id ?? p.id}|${p.name}|${p.recipe ?? ""}|${p.price ?? ""}`) ?? [];
                          const cat = catalog.catalog as Catalog;
                          const q = portions(p, cat);
                          const muted = p.enabled === false || p.visible === false;
                          const ed = editFor(p);
                          const rowKey = `${p.product_id ?? p.id}:${idx}`;
                          const isEditing = editing?.rowKey === rowKey;
                          const inputCls = "w-20 rounded-lg border border-slate-300 bg-white px-2 py-1 text-right text-sm text-slate-900";
                          return (
                            <Fragment key={rowKey}>
                            {isEditing && editing && (
                              <tr key={`${rowKey}:edit`} className="bg-indigo-50">
                                <td colSpan={7} className="p-3">
                                  <div className="rounded-xl border border-indigo-200 bg-white p-3 text-slate-900">
                                    <p className="mb-2 text-sm font-semibold">✏️ {p.name} <span className="font-normal text-slate-500">— {t("vtel.editHint")}</span></p>
                                    <div className="flex flex-wrap items-end gap-3">
                                      <label className="text-xs text-slate-600">
                                        {t("vtel.price")} (Ft)
                                        <input value={editing.price} onChange={(e) => setEditing({ ...editing, price: e.target.value })} className={`mt-1 block ${inputCls}`} />
                                      </label>
                                      {editing.steps.map((s, i) => (
                                        <div key={i} className="flex items-end gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5">
                                          <span className="pb-1.5 text-xs font-semibold text-slate-700">{i + 1}. {canisterLabel(s.canister, cat)}</span>
                                          {s.canister === 170 && (
                                            <label className="text-xs text-slate-600">
                                              {t("vtel.coffeeG")}
                                              <input value={s.coffee_g} onChange={(e) => setEditing({ ...editing, steps: editing.steps.map((x, j) => (j === i ? { ...x, coffee_g: e.target.value } : x)) })} className={`mt-1 block ${inputCls}`} />
                                            </label>
                                          )}
                                          <label className="text-xs text-slate-600">
                                            {t("vtel.waterMl")}
                                            <input value={s.water_ml} onChange={(e) => setEditing({ ...editing, steps: editing.steps.map((x, j) => (j === i ? { ...x, water_ml: e.target.value } : x)) })} className={`mt-1 block ${inputCls}`} />
                                          </label>
                                        </div>
                                      ))}
                                      <div className="ml-auto flex gap-2">
                                        <button onClick={() => setEditing(null)} className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs hover:bg-slate-100">{t("common.cancel")}</button>
                                        <button disabled={editBusy} onClick={() => void saveEdit()} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
                                          🚀 {t("vtel.saveEdit")}
                                        </button>
                                      </div>
                                    </div>
                                  </div>
                                </td>
                              </tr>
                            )}
                            <tr key={rowKey} className={`border-b border-slate-100 ${muted ? "text-slate-400" : ""} ${isEditing ? "bg-indigo-50" : ""}`}>
                              <td className="py-1.5 pr-2">
                                <span className="font-medium">{p.name}</span>
                                {p.name_en && p.name_en !== p.name && <span className="ml-1 text-xs text-slate-400">({p.name_en})</span>}
                                {p.visible === false && <span className="ml-1 rounded bg-slate-100 px-1 text-[10px]">{t("vtel.hidden")}</span>}
                                {p.enabled === false && <span className="ml-1 rounded bg-rose-50 px-1 text-[10px] text-rose-600">{t("vtel.disabled")}</span>}
                                {positions.length > 1 && <span className="ml-1 rounded bg-slate-100 px-1 text-[10px] text-slate-500" title={positions.join(", ")}>{t("vtel.dupButtons", { n: positions.length })}</span>}
                                {q.recipeFile === null && (
                                  <span className="ml-1 rounded bg-amber-50 px-1 text-[10px] text-amber-700" title={t("vtel.noRecipeHint", { name: p.recipe ?? "?" })}>⚠ {t("vtel.noRecipe")}</span>
                                )}
                              </td>
                              <td className="py-1.5 pr-2 text-right tabular-nums">{ft(p.sale_price ?? p.price)}</td>
                              <td className="py-1.5 pr-2 text-right tabular-nums">{q.coffee || "—"}</td>
                              <td className="py-1.5 pr-2 text-right tabular-nums">{q.water || "—"}</td>
                              <td className="py-1.5 pr-2 text-xs">{q.milk.length ? q.milk.join(", ") : "—"}</td>
                              <td className="py-1.5 text-xs text-slate-500" title={q.recipeFile ?? undefined}>
                                {q.steps.map((s, i) => (
                                  <span key={i} className="mr-1 inline-block rounded bg-slate-100 px-1.5 py-0.5">
                                    {canisterLabel(s.canister, cat)}{s.coffee_g ? ` ${s.coffee_g} g` : ""}{s.water_ml ? ` ${s.water_ml} ml` : ""}
                                  </span>
                                ))}
                              </td>
                              <td className="py-1.5 text-right">
                                {ed && ed.status !== "applied" && (
                                  <span className="mr-1 rounded bg-amber-50 px-1 text-[10px] text-amber-700" title={ed.error ?? undefined}>{t(`vtel.editStatus.${ed.status}`)}</span>
                                )}
                                {q.steps.length > 0 && !isEditing && (
                                  <button onClick={() => startEdit(p, cat, rowKey)} className="rounded-lg border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-100">
                                    ✏️ {t("vtel.edit")}
                                  </button>
                                )}
                              </td>
                            </tr>
                            </Fragment>
                          );
                        }); })()}
                    </tbody>
                  </table>
                </div>
              </>
            )}
            <div className="flex justify-end">
              <button onClick={() => setCatalog(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">
                {t("common.close")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Hibák modal — a gép által jelentett, összevont hibák; lezárható */}
      {errors && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="relative max-h-[85vh] w-full max-w-2xl space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => setErrors(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">⚠️ {errors.machine.name} — {t("vtel.errors")}</h2>
            <label className="flex items-center gap-2 text-xs text-slate-500">
              <input
                type="checkbox"
                className="h-3.5 w-3.5"
                checked={errors.withResolved}
                onChange={(e) => void openErrors(errors.machine, e.target.checked)}
              />
              {t("vtel.showResolved")}
            </label>
            {errors.rows.length === 0 ? (
              <p className="py-6 text-center text-sm text-slate-400">{t("vtel.noErrors")}</p>
            ) : (
              <div className="space-y-2 text-sm">
                {[...errors.rows].sort((a, b) => {
                  const w = { critical: 0, warn: 1, info: 2 } as Record<string, number>;
                  const sa = (a.resolved_at ? 9 : 0) + (w[a.diagnosis?.severity ?? "warn"] ?? 1);
                  const sb = (b.resolved_at ? 9 : 0) + (w[b.diagnosis?.severity ?? "warn"] ?? 1);
                  return sa - sb || (b.last_seen > a.last_seen ? 1 : -1);
                }).map((e) => {
                  const d = e.diagnosis;
                  const sev = d?.severity ?? "warn";
                  const tone = e.resolved_at ? "border-slate-200 opacity-60" : sev === "critical" ? "border-rose-300 bg-rose-50" : sev === "warn" ? "border-amber-300 bg-amber-50" : "border-slate-200 bg-slate-50";
                  const chip = sev === "critical" ? "bg-rose-600 text-white" : sev === "warn" ? "bg-amber-500 text-white" : "bg-slate-400 text-white";
                  return (
                    <div key={e.id} className={`rounded-xl border p-3 ${tone}`}>
                      <div className="flex items-start gap-2">
                        <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${chip}`}>{t(`vtel.sev.${sev}`)}</span>
                        <div className="min-w-0 flex-1">
                          <p className="font-semibold text-slate-900">{d?.title ?? e.technical ?? e.message}</p>
                          <p className="mt-0.5 text-xs text-slate-600">{d?.cause}</p>
                        </div>
                        {e.resolved_at ? (
                          <span className="shrink-0 text-[10px] text-slate-400">{t("vtel.resolved")}</span>
                        ) : (
                          <button onClick={() => void resolveError(e)} className="shrink-0 rounded-lg border border-slate-300 bg-white px-2 py-0.5 text-xs hover:bg-slate-100">
                            ✔ {t("vtel.resolve")}
                          </button>
                        )}
                      </div>
                      {d && d.steps.length > 0 && (
                        <div className="mt-2 rounded-lg border border-slate-200 bg-white px-3 py-2">
                          <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{t("vtel.solution")}</div>
                          <ol className="mt-0.5 list-decimal space-y-0.5 pl-4 text-xs text-slate-700">{d.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
                        </div>
                      )}
                      {explained[e.id] && (
                        <div className="mt-2 rounded-lg border border-indigo-200 bg-white px-3 py-2">
                          <div className="text-[10px] font-semibold uppercase tracking-wide text-indigo-700">{t("vtel.aiExplanation")}</div>
                          <p className="mt-0.5 whitespace-pre-wrap text-xs text-slate-700">{explained[e.id]}</p>
                        </div>
                      )}
                      <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                        <span>{t("vtel.seen", { n: e.count, first: fmt(e.first_seen), last: fmt(e.last_seen) })}</span>
                        <span className="rounded border border-slate-200 bg-white px-1.5 py-0.5 text-[10px]">{t(`vtel.source.${e.source}`)}{d?.component && ` · ${d.component}`}</span>
                        {!explained[e.id] && (
                          <button onClick={() => void explainError(e)} disabled={explaining === e.id} className="rounded-lg border border-indigo-300 bg-white px-2 py-0.5 text-[11px] text-indigo-700 hover:bg-indigo-50 disabled:opacity-50">
                            {explaining === e.id ? "…" : t("vtel.explainBtn")}
                          </button>
                        )}
                        {e.technical && (
                          <details className="ml-auto">
                            <summary className="cursor-pointer text-[10px] text-slate-400">{t("vtel.technical")}</summary>
                            <code className="block break-all font-mono text-[10px] text-slate-500">{e.technical}</code>
                          </details>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
            <div className="flex justify-end">
              <button onClick={() => setErrors(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">
                {t("common.close")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Feltöltött fájlok modal (napló-minták, nyers katalógus) */}
      {files && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="relative max-h-[85vh] w-full max-w-3xl space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => setFiles(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">📄 {files.machine.name} — {t("vtel.files")}</h2>
            {files.rows.length === 0 ? (
              <p className="py-6 text-center text-sm text-slate-400">{t("vtel.noFiles")}</p>
            ) : (
              <div className="space-y-1 text-sm">
                {files.rows.map((f) => (
                  <button
                    key={f.id}
                    onClick={() => void openFile(f)}
                    className={`flex w-full items-center gap-2 rounded-lg border px-3 py-1.5 text-left hover:bg-slate-50 ${files.open?.id === f.id ? "border-indigo-300 bg-indigo-50" : "border-slate-200"}`}
                  >
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{t(`vtel.kind.${f.kind}`)}</span>
                    <span className="font-mono text-xs">{f.name}</span>
                    <span className="ml-auto text-xs text-slate-400">{Math.round(f.size / 1024)} KB · {fmt(f.created_at)}</span>
                  </button>
                ))}
              </div>
            )}
            {files.open && (
              <pre className="max-h-[45vh] overflow-auto rounded-xl bg-slate-900 p-3 text-xs text-slate-100">{files.open.text}</pre>
            )}
            <div className="flex justify-end">
              <button onClick={() => setFiles(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">
                {t("common.close")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Fogyás modal: napok × italok */}
      {sales && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="relative max-h-[88vh] w-full max-w-5xl space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => setSales(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">📊 {t("vtel.salesTitle", { name: sales.machine.name })}</h2>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <div className="inline-flex rounded-xl bg-indigo-50 p-1">{([7, 30, 90] as const).map((d) => <button key={d} onClick={() => void openSales(sales.machine, d)} className={`rounded-lg px-3 py-1 text-sm ${sales.days === d ? "bg-indigo-600 text-white" : "text-indigo-800 hover:bg-indigo-100"}`}>{t(`vtel.d${d}`)}</button>)}</div>
              {sales.data.counter_total != null && <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs">🧮 {t("vtel.counter")}: <b>{sales.data.counter_total.toLocaleString("hu-HU")}</b>{sales.data.counter_days != null && ` · ${t("vtel.counterDays", { n: sales.data.counter_days })}`}</span>}
              <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs">☕ {t("vtel.salesTotal")}: <b>{sales.data.total_drinks}</b></span>
              {Object.keys(sales.data.pay_totals).length > 0 && <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs">💳 {t("vtel.salesPay")}: {Object.entries(sales.data.pay_totals).map(([k, v]) => `${k} ${v}`).join(" · ")}</span>}
              <a href={`/api/saferpay/machines/${sales.machine.id}/sales.csv?days=${sales.days}`} className="ml-auto rounded-lg border border-slate-300 px-3 py-1.5 text-xs hover:bg-slate-100">{t("vtel.salesCsv")}</a>
            </div>
            {sales.data.days.length === 0 ? (
              <p className="py-6 text-center text-sm text-slate-400">{t("vtel.salesEmpty")}</p>
            ) : (
              <div className="overflow-x-auto rounded-xl border border-slate-200">
                <table className="w-full text-xs">
                  <thead><tr className="border-b border-slate-200 text-left text-[10px] uppercase tracking-wide text-slate-500">
                    <th className="sticky left-0 bg-white px-2 py-1.5">{t("vtel.salesDays")}</th><th className="px-2 py-1.5 text-right">{t("vtel.salesOrders")}</th><th className="px-2 py-1.5 text-right">{t("vtel.salesOk")}</th>
                    {sales.data.products.map((p) => <th key={p.product_id} className="px-2 py-1.5 text-right" title={p.name_key}>{p.name}</th>)}
                    <th className="px-2 py-1.5 text-right">🧮</th>
                  </tr></thead>
                  <tbody>
                    {[...sales.data.days].reverse().map((d) => {
                      const row = sales.data.matrix[d.day] ?? {};
                      const max = Math.max(1, ...Object.values(row));
                      return (
                        <tr key={d.day} className="border-b border-slate-100 hover:bg-slate-50">
                          <td className="sticky left-0 bg-white px-2 py-1 font-mono">{d.day}</td>
                          <td className="px-2 py-1 text-right tabular-nums text-slate-500">{d.orders}</td>
                          <td className="px-2 py-1 text-right tabular-nums font-semibold">{d.drinks_ok}</td>
                          {sales.data.products.map((p) => { const v = row[String(p.product_id)] ?? 0; return <td key={p.product_id} className="px-2 py-1 text-right tabular-nums" style={{ background: v > 0 ? `rgba(79,70,229,${0.08 + 0.35 * (v / max)})` : undefined }}>{v || ""}</td>; })}
                          <td className="px-2 py-1 text-right tabular-nums text-slate-400">{d.counter_total ?? ""}</td>
                        </tr>
                      );
                    })}
                    <tr className="bg-slate-50 font-semibold">
                      <td className="sticky left-0 bg-slate-50 px-2 py-1">{t("vtel.salesTotal")}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{sales.data.days.reduce((a, d) => a + d.orders, 0)}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{sales.data.total_drinks}</td>
                      {sales.data.products.map((p) => <td key={p.product_id} className="px-2 py-1 text-right tabular-nums">{p.total}</td>)}
                      <td></td>
                    </tr>
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Telemetria-előzmények modal */}
      {history && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="relative max-h-[88vh] w-full max-w-5xl space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => setHistory(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">📜 {history.machine.name} — {t("vtel.history")}</h2>
            <TelemetryHistory rows={history.rows} />
            <div className="flex justify-end">
              <button onClick={() => setHistory(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">
                {t("common.close")}
              </button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
