"use client";

/** Vending → Promóció: reklámképek távoli feltöltése a gépekre. A képet a
 * böngésző 1280×800 JPEG-re méretezi, a szerver tárolja, az ügynök a
 * következő jelentéskor a gép Jetinno/MakingImage vagy ScreenProtect
 * mappájába másolja és visszajelez (delivered / failed). */

import { useCallback, useEffect, useRef, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
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

  async function upload(m: Machine, slot: (typeof SLOTS)[number], files: FileList | null) {
    if (!files || files.length === 0) return;
    const list = Array.from(files);
    setProgress({ machine: m.id, n: 0, total: list.length });
    let ok = 0;
    for (const [i, f] of list.entries()) {
      setProgress({ machine: m.id, n: i + 1, total: list.length });
      try {
        const img = await fitImage(f);
        await api.post(`/api/saferpay/machines/${m.id}/promos`, { slot, title: f.name, ...img });
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
                            accept="image/*"
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
                        <p className="mt-1 text-xs text-slate-400">{t(`vpromo.slotHint.${slot}`)}</p>
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
                                {p.thumb ? (
                                  // eslint-disable-next-line @next/next/no-img-element
                                  <img src={p.thumb} alt={p.title} className="aspect-[16/10] w-full object-cover" />
                                ) : (
                                  <div className="flex aspect-[16/10] items-center justify-center text-2xl text-slate-300">🖼️</div>
                                )}
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
    </AppShell>
  );
}
