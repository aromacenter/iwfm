"use client";

/** FoxPost automata-választó (155. kör, aaee410b): kereső (város, irányítószám,
 *  név) + térkép a találatokkal; kattintásra a kiválasztott automata kódja
 *  kerül a csomag-űrlapba. A lista a backend /api/gls/foxpost/apms végpontjáról
 *  jön (publikus FoxPost-lista, napi gyorsítótár). */

import { useEffect, useMemo, useRef, useState } from "react";
import type * as Leaflet from "leaflet";
import "leaflet/dist/leaflet.css";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";

export interface FoxpostApm {
  id: string;
  name: string;
  address: string;
  zip: string;
  city: string;
  lat: number | null;
  lng: number | null;
  outdoor: boolean;
  type: string;
}

const norm = (s: string) =>
  (s || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");

export default function FoxpostApmPicker({
  open, onClose, onPick, initialQuery,
}: {
  open: boolean;
  onClose: () => void;
  onPick: (apm: FoxpostApm) => void;
  initialQuery?: string;
}) {
  const { t } = useT();
  const [rows, setRows] = useState<FoxpostApm[]>([]);
  const [q, setQ] = useState(initialQuery ?? "");
  const [err, setErr] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const LRef = useRef<typeof Leaflet | null>(null);
  const mapRef = useRef<Leaflet.Map | null>(null);
  const layerRef = useRef<Leaflet.LayerGroup | null>(null);
  const divRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setQ(initialQuery ?? "");
    api.get<FoxpostApm[]>("/api/gls/foxpost/apms").then(setRows).catch((e) => setErr(errorMessage(e)));
    import("leaflet")
      .then((mod: typeof Leaflet & { default?: typeof Leaflet }) => { LRef.current = mod.default ?? mod; setReady(true); })
      .catch(() => setErr(t("gls.apmMapFailed")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const filtered = useMemo(() => {
    const nq = norm(q.trim());
    if (!nq) return rows.slice(0, 400);
    const words = nq.split(/\s+/);
    return rows.filter((r) => {
      const hay = norm(`${r.name} ${r.address} ${r.zip} ${r.city} ${r.id}`);
      return words.every((w) => hay.includes(w));
    }).slice(0, 400);
  }, [rows, q]);

  // térkép: egyszer létrejön, a szűrt találatok rétege cserélődik
  useEffect(() => {
    if (!open || !ready || !divRef.current) return;
    const L = LRef.current;
    if (!L) return;
    if (!mapRef.current) {
      mapRef.current = L.map(divRef.current, { zoomControl: true }).setView([47.3, 19.3], 7);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19, attribution: "&copy; OpenStreetMap",
      }).addTo(mapRef.current);
      layerRef.current = L.layerGroup().addTo(mapRef.current);
    }
    const layer = layerRef.current;
    if (!layer) return;
    layer.clearLayers();
    const pts: [number, number][] = [];
    for (const r of filtered) {
      if (r.lat == null || r.lng == null) continue;
      pts.push([r.lat, r.lng]);
      const m = L.circleMarker([r.lat, r.lng], { radius: 7, color: "#e31e24", weight: 2, fillColor: "#fff", fillOpacity: 0.95 });
      m.bindTooltip(`${r.name}<br/>${r.address}`, { direction: "top" });
      m.on("click", () => onPick(r));
      layer.addLayer(m);
    }
    if (pts.length > 0 && pts.length < 400) {
      mapRef.current?.fitBounds(pts, { padding: [24, 24], maxZoom: 14 });
    }
    setTimeout(() => mapRef.current?.invalidateSize(), 50);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, ready, filtered]);

  useEffect(() => {
    if (open) return;
    if (mapRef.current) { mapRef.current.remove(); mapRef.current = null; layerRef.current = null; }
  }, [open]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-3" onClick={onClose}>
      <div className="flex h-[90vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-3 border-b border-slate-200 px-4 py-3">
          <h2 className="text-base font-semibold">📍 {t("gls.apmPickTitle")}</h2>
          <input
            autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("gls.apmSearchPh")}
            className="ml-auto w-72 rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
          />
          <button type="button" onClick={onClose} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50">✕</button>
        </div>
        {err && <p className="px-4 py-2 text-sm text-rose-600">{err}</p>}
        <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[320px_1fr]">
          <div className="min-h-0 overflow-y-auto border-r border-slate-200">
            {filtered.length === 0 && <p className="p-4 text-sm text-slate-500">{t("gls.apmNone")}</p>}
            {filtered.slice(0, 80).map((r) => (
              <button
                key={r.id} type="button" onClick={() => onPick(r)}
                className="block w-full border-b border-slate-100 px-4 py-2 text-left text-sm hover:bg-indigo-50"
              >
                <div className="font-medium">{r.name}</div>
                <div className="text-xs text-slate-500">{r.address} · <span className="font-mono">{r.id}</span>{r.outdoor ? ` · ${t("gls.apmOutdoor")}` : ""}</div>
              </button>
            ))}
            {filtered.length > 80 && <p className="p-3 text-xs text-slate-400">{t("gls.apmMore", { n: filtered.length - 80 })}</p>}
          </div>
          <div ref={divRef} className="min-h-[280px]" />
        </div>
      </div>
    </div>
  );
}
