"use client";

/** Térkép-nézet (8f78ba71): kihelyezett gépek és bevétel városonként, a
 * városra kattintva az egyes partnerek a település térképén. Budapesten
 * Buda/Pest szűrő (kerület az irányítószámból). A Statisztika oldalról
 * új ablakban nyílik. Leaflet + OpenStreetMap (CDN-ről töltve). */

import { useEffect, useMemo, useRef, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */

interface MapPartner {
  id: string;
  name: string;
  city: string | null;
  zip: string | null;
  lat: number | null;
  lng: number | null;
  machines: number;
  revenue: number;
}

const LEAFLET_JS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
const LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
// Budai kerületek (I., II., III., XI., XII., XXII.) — az irányítószám 2-3.
// számjegye a kerület.
const BUDA_DISTRICTS = new Set(["01", "02", "03", "11", "12", "22"]);

function budaPest(zip: string | null): "buda" | "pest" | null {
  if (!zip || !zip.startsWith("1") || zip.length !== 4) return null;
  return BUDA_DISTRICTS.has(zip.slice(1, 3)) ? "buda" : "pest";
}

export default function TerkepPage() {
  const { t } = useT();
  const { toast } = useUI();
  const [rows, setRows] = useState<MapPartner[]>([]);
  const [ready, setReady] = useState(false);
  const [bp, setBp] = useState<"" | "buda" | "pest">("");
  const mapRef = useRef<any>(null);
  const layerRef = useRef<any>(null);
  const divRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.get<MapPartner[]>("/api/stats/map").then(setRows).catch((e) => toast(errorMessage(e), "error"));
    // Leaflet betöltése CDN-ről (css + js), utána indul a térkép
    if ((window as any).L) { setReady(true); return; }
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = LEAFLET_CSS;
    document.head.appendChild(css);
    const js = document.createElement("script");
    js.src = LEAFLET_JS;
    js.onload = () => setReady(true);
    js.onerror = () => toast(t("map.loadFailed"), "error");
    document.head.appendChild(js);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filtered = useMemo(
    () => rows.filter((r) => !bp || budaPest(r.zip) === bp),
    [rows, bp],
  );

  const cities = useMemo(() => {
    const by: Record<string, { partners: number; machines: number; revenue: number; lat: number; lng: number; n: number }> = {};
    for (const r of filtered) {
      const key = (r.city || "?").trim() || "?";
      const c = (by[key] ||= { partners: 0, machines: 0, revenue: 0, lat: 0, lng: 0, n: 0 });
      c.partners += 1;
      c.machines += r.machines;
      c.revenue += r.revenue;
      if (r.lat != null && r.lng != null) { c.lat += r.lat; c.lng += r.lng; c.n += 1; }
    }
    return Object.entries(by)
      .map(([city, c]) => ({
        city, partners: c.partners, machines: c.machines, revenue: c.revenue,
        lat: c.n ? c.lat / c.n : null, lng: c.n ? c.lng / c.n : null,
      }))
      .sort((a, b) => b.machines - a.machines);
  }, [filtered]);

  const totals = useMemo(
    () => filtered.reduce(
      (acc, r) => ({ partners: acc.partners + 1, machines: acc.machines + r.machines, revenue: acc.revenue + r.revenue }),
      { partners: 0, machines: 0, revenue: 0 },
    ),
    [filtered],
  );

  const ft = (n: number) => `${Math.round(n).toLocaleString("hu-HU")} Ft`;

  // Térkép felépítése / frissítése
  useEffect(() => {
    if (!ready || !divRef.current) return;
    const L = (window as any).L;
    if (!L) return;
    if (!mapRef.current) {
      mapRef.current = L.map(divRef.current).setView([47.3, 19.1], 8);
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: "© OpenStreetMap",
      }).addTo(mapRef.current);
    }
    if (layerRef.current) layerRef.current.remove();
    const layer = L.layerGroup().addTo(mapRef.current);
    layerRef.current = layer;
    for (const r of filtered) {
      if (r.lat == null || r.lng == null) continue;
      const radius = Math.max(6, Math.min(18, 5 + r.machines * 2));
      L.circleMarker([r.lat, r.lng], {
        radius, color: "#E31E24", weight: 1.5, fillColor: "#E31E24", fillOpacity: 0.55,
      })
        .bindPopup(
          `<b>${r.name}</b><br/>${r.city ?? ""} ${r.zip ?? ""}<br/>` +
          `☕ ${r.machines} ${t("map.machinesShort")} · 💰 ${ft(r.revenue)}`,
        )
        .addTo(layer);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, filtered]);

  function zoomCity(city: string) {
    const L = (window as any).L;
    if (!L || !mapRef.current) return;
    const pts = filtered.filter((r) => (r.city || "?") === city && r.lat != null && r.lng != null);
    if (pts.length === 0) return;
    const bounds = L.latLngBounds(pts.map((r) => [r.lat, r.lng]));
    mapRef.current.fitBounds(bounds.pad(0.25), { maxZoom: 14 });
  }

  const geocoded = filtered.filter((r) => r.lat != null).length;

  return (
    <AppShell>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">🗺️ {t("map.title")}</h1>
        <div className="flex gap-1.5">
          {([["", "map.all"], ["buda", "map.buda"], ["pest", "map.pest"]] as const).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setBp(key as typeof bp)}
              className={`rounded-full px-3 py-1.5 text-xs font-medium ${
                bp === key ? "bg-indigo-600 text-white" : "bg-white text-slate-600 ring-1 ring-slate-200"
              }`}
            >
              {t(label)}
            </button>
          ))}
        </div>
        <div className="ml-auto flex flex-wrap gap-2 text-sm">
          <span className="rounded-lg bg-slate-100 px-3 py-1.5">🤝 {totals.partners} {t("map.partners")}</span>
          <span className="rounded-lg bg-slate-100 px-3 py-1.5">☕ {totals.machines} {t("map.machines")}</span>
          <span className="rounded-lg bg-emerald-50 px-3 py-1.5 font-medium text-emerald-800">💰 {ft(totals.revenue)}</span>
        </div>
      </div>
      {filtered.length > 0 && geocoded < filtered.length && (
        <p className="mb-2 text-xs text-amber-700">
          ⚠️ {t("map.missingGeo", { n: filtered.length - geocoded })}
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <div ref={divRef} className="h-[70vh] rounded-2xl border border-slate-200 shadow-sm" />
        <div className="max-h-[70vh] overflow-y-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-sm">
            <thead>
              <tr className="sticky top-0 bg-white text-left text-xs uppercase text-slate-500">
                <th className="px-3 py-2">{t("map.city")}</th>
                <th className="px-3 py-2 text-right">🤝</th>
                <th className="px-3 py-2 text-right">☕</th>
                <th className="px-3 py-2 text-right">💰</th>
              </tr>
            </thead>
            <tbody>
              {cities.map((c) => (
                <tr
                  key={c.city}
                  onClick={() => zoomCity(c.city)}
                  className="cursor-pointer border-t border-slate-100 hover:bg-indigo-50"
                >
                  <td className="px-3 py-2 font-medium text-indigo-700">{c.city}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{c.partners}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{c.machines}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{ft(c.revenue)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </AppShell>
  );
}
