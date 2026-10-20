"use client";

/** Vending → Promóció: reklámképek távoli feltöltése a gépekre. A képet a
 * böngésző 1280×800 JPEG-re méretezi, a szerver tárolja, az ügynök a
 * következő jelentéskor a gép Jetinno/MakingImage vagy ScreenProtect
 * mappájába másolja és visszajelez (delivered / failed). */

import { useCallback, useEffect, useRef, useState } from "react";
import AppShell from "@/components/AppShell";
import { API_URL, api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface Machine {
  id: string;
  machine_code: string;
  name: string;
  note: string | null;
  is_active: boolean;
  last_seen_at: string | null;
}

interface Promo {
  id: string;
  slot: "making" | "screensaver";
  filename: string;
  title: string;
  content_type?: string;
  size: number;
  width: number;
  height: number;
  thumb: string | null;
  sort_order: number;
  status: "pending" | "delivered" | "failed" | "remove_pending";
  error: string | null;
  created_at: string;
  delivered_at: string | null;
}

interface PromoList {
  exclusive: { making: boolean; screensaver: boolean };
  items: Promo[];
}

interface MachineAsset { id: string; kind: string; name: string; content_type: string; size: number; created_at: string }

/** Sütis auth miatt fetch + blob (az <img src> cross-origin nem vinné a sütit). */
async function fetchBlobUrl(path: string): Promise<string> {
  const res = await fetch(`${API_URL}${path}`, { credentials: "include" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return URL.createObjectURL(await res.blob());
}

async function downloadBlob(path: string, filename: string) {
  const url = await fetchBlobUrl(path);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

const SLOTS = ["making", "screensaver"] as const;
const TARGET_W = 1280;
const TARGET_H = 800;

const STATUS_CHIP: Record<Promo["status"], string> = {
  pending: "bg-amber-100 text-amber-800",
  delivered: "bg-emerald-100 text-emerald-800",
  failed: "bg-rose-100 text-rose-800",
  remove_pending: "bg-slate-200 text-slate-700",
};

/** A kép „cover” módban 1280×800-ra, JPEG; plusz kis bélyegkép a listához. */
async function fitImage(file: File): Promise<{ data_b64: string; thumb: string; width: number; height: number }> {
  const bmp = await createImageBitmap(file);
  const draw = (w: number, h: number, quality: number) => {
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("canvas");
    const scale = Math.max(w / bmp.width, h / bmp.height);
    const dw = bmp.width * scale;
    const dh = bmp.height * scale;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(bmp, (w - dw) / 2, (h - dh) / 2, dw, dh);
    return canvas.toDataURL("image/jpeg", quality);
  };
  const full = draw(TARGET_W, TARGET_H, 0.86);
  const thumb = draw(320, 200, 0.7);
  bmp.close();
  return { data_b64: full.split(",")[1], thumb, width: TARGET_W, height: TARGET_H };
}

export default function VendingPromocioPage() {
  const { t, lang } = useT();
  const { toast, confirm } = useUI();
  const fmt = (dt: string) =>
    new Date(dt).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { dateStyle: "short", timeStyle: "short" });

  const [machines, setMachines] = useState<Machine[]>([]);
  const [promos, setPromos] = useState<Record<string, PromoList>>({});
  const [progress, setProgress] = useState<{ machine: string; n: number; total: number } | null>(null);
  const fileInputs = useRef<Record<string, HTMLInputElement | null>>({});

  const loadPromos = useCallback(async (ms: Machine[]) => {
    const entries = await Promise.all(
      ms.map(async (m) => [m.id, await api.get<PromoList>(`/api/saferpay/machines/${m.id}/promos`).catch(() => null)] as const),
    );
    setPromos((prev) => {
      const next = { ...prev };
      for (const [id, list] of entries) if (list) next[id] = list;
      return next;
    });
  }, []);

  const load = useCallback(() => {
    api.get<Machine[]>("/api/saferpay/machines")
      .then((ms) => {
        setMachines(ms);
        void loadPromos(ms);
      })
      .catch(() => {});
  }, [loadPromos]);

  useEffect(() => {
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, [load]);

  /** MP4: nyers base64 (nincs átméretezés) + bélyegkép az első képkockából. */
  async function readVideo(f: File): Promise<{ data_b64: string; thumb: string | null; content_type: string }> {
    const data_b64 = await new Promise<string>((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result).split(",")[1] ?? "");
      r.onerror = () => reject(r.error);
      r.readAsDataURL(f);
    });
    let thumb: string | null = null;
    try {
      const url = URL.createObjectURL(f);
      const v = document.createElement("video");
      v.muted = true;
      v.src = url;
      await new Promise<void>((res, rej) => { v.onloadeddata = () => res(); v.onerror = () => rej(new Error("video")); });
      v.currentTime = Math.min(1, (v.duration || 1) / 2);
      await new Promise<void>((res) => { v.onseeked = () => res(); });
      const canvas = document.createElement("canvas");
      canvas.width = 320; canvas.height = 200;
      canvas.getContext("2d")?.drawImage(v, 0, 0, 320, 200);
      thumb = canvas.toDataURL("image/jpeg", 0.7);
      URL.revokeObjectURL(url);
    } catch { /* bélyegkép nélkül is mehet */ }
    return { data_b64, thumb, content_type: "video/mp4" };
  }

  async function upload(m: Machine, slot: (typeof SLOTS)[number], files: FileList | null) {
    if (!files || files.length === 0) return;
    const list = Array.from(files);
    setProgress({ machine: m.id, n: 0, total: list.length });
    let ok = 0;
    for (const [i, f] of list.entries()) {
      setProgress({ machine: m.id, n: i + 1, total: list.length });
      try {
        const isVideo = f.type === "video/mp4" || f.name.toLowerCase().endsWith(".mp4");
        if (isVideo) {
          if (slot !== "screensaver") { toast(t("vpromo.videoOnlyScreensaver"), "error"); continue; }
          if (f.size > 30 * 1024 * 1024) { toast(t("vpromo.videoTooBig", { name: f.name }), "error"); continue; }
          const vid = await readVideo(f);
          await api.post(`/api/saferpay/machines/${m.id}/promos`, { slot, title: f.name, width: 0, height: 0, ...vid });
        } else {
          const img = await fitImage(f);
          await api.post(`/api/saferpay/machines/${m.id}/promos`, { slot, title: f.name, ...img });
        }
        ok++;
      } catch (err) {
        toast(err instanceof Error && err.message === "canvas" ? t("vpromo.badImage", { name: f.name }) : errorMessage(err), "error");
      }
    }
    setProgress(null);
    if (ok) toast(t("vpromo.uploaded", { n: ok }), "success");
    void loadPromos([m]);
  }

  async function remove(m: Machine, p: Promo) {
    if (!(await confirm(t("vpromo.deleteConfirm", { title: p.title })))) return;
    try {
      await api.delete(`/api/saferpay/machines/${m.id}/promos/${p.id}`);
      void loadPromos([m]);
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  const [assets, setAssets] = useState<{ machine: Machine; rows: MachineAsset[]; thumbs: Record<string, string> } | null>(null);

  async function openAssets(m: Machine) {
    try {
      const rows = await api.get<MachineAsset[]>(`/api/saferpay/machines/${m.id}/assets`);
      setAssets({ machine: m, rows, thumbs: {} });
      // bélyegképek lustán, egymás után (sütis fetch → blob URL)
      const thumbs: Record<string, string> = {};
      for (const r of rows) {
        if (!r.content_type.startsWith("image/")) continue;
        try {
          thumbs[r.id] = await fetchBlobUrl(`/api/saferpay/machines/${m.id}/assets/${r.id}`);
          setAssets((prev) => (prev && prev.machine.id === m.id ? { ...prev, thumbs: { ...thumbs } } : prev));
        } catch { /* egy kép kimaradhat */ }
      }
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function restoreFactory(m: Machine) {
    if (!(await confirm(t("vpromo.restoreFactoryConfirm", { name: m.name })))) return;
    try {
      await api.post(`/api/saferpay/machines/${m.id}/promos/restore-factory`, {});
      toast(t("vpromo.restoreFactoryQueued"), "success");
      void loadPromos([m]);
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function setExclusive(m: Machine, slot: (typeof SLOTS)[number], value: boolean) {
    const cur = promos[m.id]?.exclusive ?? { making: false, screensaver: false };
    const next = { ...cur, [slot]: value };
    try {
      await api.put(`/api/saferpay/machines/${m.id}/promo-settings`, {
        exclusive_making: next.making, exclusive_screensaver: next.screensaver,
      });
      setPromos((prev) => ({ ...prev, [m.id]: { ...(prev[m.id] ?? { items: [] }), exclusive: next } }));
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  return (
    <AppShell>
      <div className="mb-4">
        <h1 className="text-xl font-semibold">🖼️ {t("vpromo.title")}</h1>
        <p className="text-sm text-slate-500">{t("vpromo.subtitle")}</p>
      </div>

      {machines.length === 0 ? (
        <div className="rounded-2xl border border-slate-200 bg-white p-10 text-center text-sm text-slate-400 shadow-sm">
          {t("vpromo.noMachines")}
        </div>
      ) : (
        <div className="space-y-4">
          {machines.map((m) => {
            const list = promos[m.id];
            return (
              <div key={m.id} className={`rounded-2xl border border-slate-200 bg-white p-4 shadow-sm ${m.is_active ? "" : "opacity-60"}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="font-semibold">🤖 {m.name}</h2>
                  <span className="font-mono text-xs text-slate-400">{m.machine_code}</span>
                  {m.note && <span className="text-xs text-slate-400">· {m.note}</span>}
                  {m.last_seen_at && (
                    <span className="ml-auto text-xs text-slate-400">{t("vtel.lastSeen", { at: fmt(m.last_seen_at) })}</span>
                  )}
                  <button
                    onClick={() => void openAssets(m)}
                    className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs hover:bg-slate-100"
                  >
                    📥 {t("vpromo.assets")}
                  </button>
                  <button
                    onClick={() => void restoreFactory(m)}
                    className="rounded-lg border border-amber-300 px-2.5 py-1 text-xs text-amber-700 hover:bg-amber-50"
                  >
                    ↩️ {t("vpromo.restoreFactoryPromo")}
                  </button>
                </div>

                <div className="mt-3 grid gap-4 md:grid-cols-2">
                  {SLOTS.map((slot) => {
                    const items = (list?.items ?? []).filter((p) => p.slot === slot);
                    const busy = progress?.machine === m.id;
                    return (
                      <div key={slot} className="rounded-xl border border-slate-100 bg-slate-50 p-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="text-sm font-semibold">{t(`vpromo.slot.${slot}`)}</h3>
                          <span className="rounded bg-white px-1.5 py-0.5 text-xs text-slate-500">{items.length}</span>
                          <input
                            ref={(el) => { fileInputs.current[`${m.id}:${slot}`] = el; }}
                            type="file"
                            accept={slot === "screensaver" ? "image/*,video/mp4" : "image/*"}
                            multiple
                            className="hidden"
                            onChange={(e) => {
                              void upload(m, slot, e.target.files);
                              e.target.value = "";
                            }}
                          />
                          <button
                            disabled={busy}
                            onClick={() => fileInputs.current[`${m.id}:${slot}`]?.click()}
                            className="ml-auto rounded-lg bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
                          >
                            {busy && progress ? t("vpromo.uploading", { n: progress.n, total: progress.total }) : t("vpromo.upload")}
                          </button>
                        </div>
                        <p className="mt-1 text-xs text-slate-400">{t(`vpromo.slotHint.${slot}`)}{slot === "screensaver" && ` ${t("vpromo.videoHint")}`}</p>
                        <label className="mt-2 flex items-start gap-2 text-xs text-slate-600" title={t("vpromo.exclusiveHint")}>
                          <input
                            type="checkbox"
                            className="mt-0.5 h-3.5 w-3.5"
                            checked={list?.exclusive?.[slot] ?? false}
                            onChange={(e) => void setExclusive(m, slot, e.target.checked)}
                          />
                          {t("vpromo.exclusive")}
                        </label>

                        {items.length === 0 ? (
                          <p className="mt-3 text-center text-xs text-slate-400">{t("vpromo.empty")}</p>
                        ) : (
                          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
                            {items.map((p) => (
                              <div key={p.id} className="overflow-hidden rounded-lg border border-slate-200 bg-white">
                                <div className="relative">
                                  {p.thumb ? (
                                    // eslint-disable-next-line @next/next/no-img-element
                                    <img src={p.thumb} alt={p.title} className="aspect-[16/10] w-full object-cover" />
                                  ) : (
                                    <div className="flex aspect-[16/10] items-center justify-center text-2xl text-slate-300">{p.content_type === "video/mp4" ? "🎬" : "🖼️"}</div>
                                  )}
                                  {p.content_type === "video/mp4" && (
                                    <span className="absolute left-1 top-1 rounded bg-black/60 px-1 text-[10px] text-white">🎬 MP4</span>
                                  )}
                                </div>
                                <div className="p-1.5">
                                  <p className="truncate text-xs font-medium" title={p.filename}>{p.title}</p>
                                  <div className="mt-1 flex items-center gap-1">
                                    <span className={`rounded px-1.5 py-0.5 text-[10px] ${STATUS_CHIP[p.status]}`} title={p.error ?? undefined}>
                                      {t(`vpromo.status.${p.status}`)}
                                    </span>
                                    <button
                                      onClick={() => void remove(m, p)}
                                      className="ml-auto rounded px-1 text-xs text-rose-600 hover:bg-rose-50"
                                      title={t("common.delete")}
                                    >
                                      🗑
                                    </button>
                                  </div>
                                  {p.error && <p className="mt-0.5 truncate text-[10px] text-rose-600" title={p.error}>{p.error}</p>}
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}
      <p className="mt-3 text-xs text-slate-400">{t("vpromo.deliveryHint")}</p>

      {/* Gépen lévő képek modal — gyári reklámképek, ikonok; ZIP letöltés */}
      {assets && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="relative max-h-[85vh] w-full max-w-4xl space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => setAssets(null)} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <div className="flex flex-wrap items-center gap-2 pr-8">
              <h2 className="text-lg font-semibold">📥 {assets.machine.name} — {t("vpromo.assets")}</h2>
              {assets.rows.length > 0 && (
                <button
                  onClick={() => void downloadBlob(`/api/saferpay/machines/${assets.machine.id}/assets.zip`, `gep-${assets.machine.machine_code}-kepek.zip`).catch((e) => toast(errorMessage(e), "error"))}
                  className="ml-auto rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700"
                >
                  ⬇️ {t("vpromo.downloadZip")} ({Math.round(assets.rows.reduce((s, r) => s + r.size, 0) / 1024)} KB)
                </button>
              )}
            </div>
            <p className="text-xs text-slate-500">{t("vpromo.assetsHint")}</p>
            {assets.rows.length === 0 ? (
              <p className="py-6 text-center text-sm text-slate-400">{t("vpromo.assetsEmpty")}</p>
            ) : (
              ["making", "screensaver", "product_image", "other"].map((kind) => {
                const rows = assets.rows.filter((r) => r.kind === kind);
                if (rows.length === 0) return null;
                return (
                  <div key={kind}>
                    <div className="mb-1.5 flex items-center gap-2">
                      <h3 className="text-sm font-semibold">{t(`vpromo.kind.${kind}`)}</h3>
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">{rows.length}</span>
                      <button
                        onClick={() => void downloadBlob(`/api/saferpay/machines/${assets.machine.id}/assets.zip?kind=${kind}`, `gep-${assets.machine.machine_code}-${kind}.zip`).catch((e) => toast(errorMessage(e), "error"))}
                        className="ml-auto text-xs text-indigo-600 hover:underline"
                      >
                        ⬇️ {t("vpromo.downloadZipKind")}
                      </button>
                    </div>
                    <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-5">
                      {rows.map((r) => (
                        <a
                          key={r.id}
                          href={assets.thumbs[r.id]}
                          download={r.name}
                          className="overflow-hidden rounded-lg border border-slate-200 bg-slate-50"
                          title={`${r.name} · ${Math.round(r.size / 1024)} KB`}
                        >
                          {assets.thumbs[r.id] ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img src={assets.thumbs[r.id]} alt={r.name} className="aspect-[16/10] w-full object-contain bg-white" />
                          ) : (
                            <div className="flex aspect-[16/10] items-center justify-center text-xl text-slate-300">{r.content_type.startsWith("video/") ? "🎬" : "🖼️"}</div>
                          )}
                          <p className="truncate px-1.5 py-1 text-[10px] text-slate-600">{r.name} · {Math.round(r.size / 1024)} KB</p>
                        </a>
                      ))}
                    </div>
                  </div>
                );
              })
            )}
            <div className="flex justify-end">
              <button onClick={() => setAssets(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.close")}</button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
