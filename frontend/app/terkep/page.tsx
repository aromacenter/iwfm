"use client";

/** Térkép-nézet (8f78ba71): kihelyezett gépek és bevétel városonként, a
 * városra kattintva az egyes partnerek a település térképén. Budapesten
 * Buda/Pest szűrő (kerület az irányítószámból). A Statisztika oldalról
 * új ablakban nyílik. A Leaflet a saját buildünkből jön (790165df reopened:
 * a CDN-ek mobilneten rendre elérhetetlenek voltak) — csak az OSM-csempék
 * külsők. */

import { useEffect, useMemo, useRef, useState } from "react";
import "leaflet/dist/leaflet.css";
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
  counters: number;
  revenue: number;
  agent_user_id: string | null;
  contracted?: boolean;
}
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
  // Képviselő-szűrő (8f78ba71 reopened) — alapból minden AKTÍV partner látszik
  const [agentFilter, setAgentFilter] = useState("");
  const [agents, setAgents] = useState<{ id: string; display_name: string }[]>([]);
  // d650f1d5: alapból MINDEN aktív partner látszik (gép nélküliek szürkén); szűrhető a gépesekre
  const [onlyMachines, setOnlyMachines] = useState(false);
  const [geoBusy, setGeoBusy] = useState(false);

  async function geocodeMissing() {
    setGeoBusy(true);
    try {
      const r = await api.post<{ queued: number; missing: number }>("/api/stats/map/geocode-missing", {});
      toast(r.missing === 0 ? t("map.geocodeNone") : t("map.geocodeQueued", { n: r.queued }), r.missing === 0 ? "success" : "info");
    } catch (e) {
      toast(errorMessage(e), "error");
    } finally {
      setGeoBusy(false);
    }
  }
  const mapRef = useRef<any>(null);
  const layerRef = useRef<any>(null);
  const LRef = useRef<any>(null);
  const divRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.get<MapPartner[]>("/api/stats/map").then(setRows).catch((e) => toast(errorMessage(e), "error"));
    api.get<{ id: string; display_name: string }[]>("/api/quotes/agents").then(setAgents).catch(() => {});
    // A Leaflet a bundle részeként töltődik (dinamikus import, mert a
    // modul window-t vár, SSR alatt nem futhat) — hálózat nem kell hozzá.
    import("leaflet")
      .then((mod: any) => { LRef.current = mod.default ?? mod; setReady(true); })
      .catch(() => toast(t("map.loadFailed"), "error"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filtered = useMemo(
    () =>
      rows.filter((r) => {
        if (onlyMachines && r.machines === 0) return false;
        if (bp && budaPest(r.zip) !== bp) return false;
        if (agentFilter === "none" && r.agent_user_id) return false;
        if (agentFilter && agentFilter !== "none" && r.agent_user_id !== agentFilter) return false;
        return true;
      }),
    [rows, bp, agentFilter, onlyMachines],
  );
  const missingGeo = rows.filter((r) => r.lat == null).length;

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
      (acc, r) => ({ partners: acc.partners + 1, machines: acc.machines + r.machines, counters: acc.counters + (r.counters || 0), revenue: acc.revenue + r.revenue }),
      { partners: 0, machines: 0, counters: 0, revenue: 0 },
    ),
    [filtered],
  );

  const ft = (n: number) => `${Math.round(n).toLocaleString("hu-HU")} Ft`;

  // Térkép felépítése / frissítése
  useEffect(() => {
    if (!ready || !divRef.current) return;
    const L = LRef.current;
    if (!L) return;
    if (!mapRef.current) {
      mapRef.current = L.map(divRef.current).setView([47.3, 19.1], 8);
      // Csempe-források tartalékkal: ha az OSM nem elérhető (pl. mobilnet),
      // átváltunk a Carto alaptérképre (9192f8d5). A CSP img-src engedi őket.
      const TILE_SOURCES = [
        { url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", attribution: "© OpenStreetMap" },
        { url: "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png", attribution: "© OpenStreetMap · © CARTO" },
      ];
      let tileIdx = 0;
      let errCount = 0;
      const addTiles = () => {
        const src = TILE_SOURCES[tileIdx];
        const layer = L.tileLayer(src.url, { maxZoom: 19, attribution: src.attribution });
        layer.on("tileerror", () => {
          errCount += 1;
          if (errCount >= 3 && tileIdx + 1 < TILE_SOURCES.length) {
            tileIdx += 1;
            errCount = 0;
            layer.remove();
            addTiles();
          }
        });
        layer.addTo(mapRef.current);
      };
      addTiles();
      // Mobilon a konténer mérete az első render után áll be — enélkül a
      // térkép üresnek látszik (8f78ba71 reopened).
      setTimeout(() => mapRef.current?.invalidateSize(), 150);
      window.addEventListener("resize", () => mapRef.current?.invalidateSize());
    }
    if (layerRef.current) layerRef.current.remove();
    const layer = L.layerGroup().addTo(mapRef.current);
    layerRef.current = layer;
    for (const r of filtered) {
      if (r.lat == null || r.lng == null) continue;
      const hasMachines = r.machines > 0;
      const radius = hasMachines ? Math.max(6, Math.min(18, 5 + r.machines * 2)) : 5;
      const color = hasMachines ? "#E31E24" : "#64748b";
      L.circleMarker([r.lat, r.lng], {
        radius, color, weight: 1.5, fillColor: color, fillOpacity: hasMachines ? 0.55 : 0.4,
      })
        .bindPopup(
          `<b>${r.name}</b><br/>${r.city ?? ""} ${r.zip ?? ""}<br/>` +
          (hasMachines
            ? `☕ ${r.machines} ${t("map.machinesShort")} · 💰 ${ft(r.revenue)}`
            : `⚪ ${t("map.noMachines")}${r.contracted === false ? "" : ""}`),
        )
        .addTo(layer);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, filtered]);

  function zoomCity(city: string) {
    const L = LRef.current;
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
        <select
          value={agentFilter}
          onChange={(e) => setAgentFilter(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm"
        >
          <option value="">{t("partners.allAgents")}</option>
          <option value="none">{t("partners.noAgent")}</option>
          {agents.map((a) => (
            <option key={a.id} value={a.id}>💼 {a.display_name}</option>
          ))}
        </select>
        <button
          onClick={() => setOnlyMachines((v) => !v)}
          className={`rounded-full px-3 py-1.5 text-xs font-medium ${onlyMachines ? "bg-indigo-600 text-white" : "bg-white text-slate-600 ring-1 ring-slate-200"}`}
        >
          ☕ {t("map.onlyMachines")}
        </button>
        {missingGeo > 0 && (
          <button
            onClick={() => void geocodeMissing()}
            disabled={geoBusy}
            className="rounded-full bg-amber-100 px-3 py-1.5 text-xs font-medium text-amber-800 hover:bg-amber-200 disabled:opacity-50"
          >
            {t("map.geocodeMissing", { n: missingGeo })}
          </button>
        )}
        <div className="ml-auto flex flex-wrap gap-2 text-sm">
          <span className="rounded-lg bg-slate-100 px-3 py-1.5">🤝 {totals.partners} {t("map.partners")}</span>
          <span className="rounded-lg bg-slate-100 px-3 py-1.5">
            ☕ {totals.machines} {t("map.machines")}
            {totals.counters > totals.machines && (
              <span className="text-slate-400"> · {totals.counters} {t("map.counters")}</span>
            )}
          </span>
          <span className="rounded-lg bg-emerald-50 px-3 py-1.5 font-medium text-emerald-800">💰 {ft(totals.revenue)}</span>
        </div>
      </div>
      {filtered.length > 0 && geocoded < filtered.length && (
        <p className="mb-2 text-xs text-amber-700">
          ⚠️ {t("map.missingGeo", { n: filtered.length - geocoded })}
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        {/* világos háttér: csempe-betöltés közben se legyen fekete (9192f8d5) */}
        <div ref={divRef} className="h-[60vh] min-h-80 w-full rounded-2xl border border-slate-200 !bg-slate-200 shadow-sm lg:h-[70vh]" />
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
