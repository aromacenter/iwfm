"use client";

/** PUBLIKUS térkép-diagnosztika (790165df/9192f8d5): bejelentkezés nélkül
 * megnyitható, és kiírja, hogy a térkép-motor és a csempék betöltődnek-e.
 * Üzleti adat nincs rajta — csak egy üres Budapest-térkép. */

import { useEffect, useRef, useState } from "react";
import "leaflet/dist/leaflet.css";

export default function TerkepTesztPage() {
  const divRef = useRef<HTMLDivElement>(null);
  const [engine, setEngine] = useState("betöltés…");
  const [tiles, setTiles] = useState({ ok: 0, err: 0 });

  useEffect(() => {
    let map: { remove: () => void } | null = null;
    import("leaflet")
      .then((mod) => {
        const L = (mod as { default?: unknown }).default ?? mod;
        setEngine("✅ térkép-motor betöltve");
        if (!divRef.current) return;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const m = (L as any).map(divRef.current).setView([47.4979, 19.0402], 11);
        map = m;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const layer = (L as any).tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 19,
          attribution: "© OpenStreetMap",
        });
        layer.on("tileload", () => setTiles((s) => ({ ...s, ok: s.ok + 1 })));
        layer.on("tileerror", () => setTiles((s) => ({ ...s, err: s.err + 1 })));
        layer.addTo(m);
        setTimeout(() => m.invalidateSize(), 150);
      })
      .catch((e) => setEngine(`❌ térkép-motor hiba: ${String(e)}`));
    return () => { if (map) map.remove(); };
  }, []);

  const tileStatus =
    tiles.ok > 0
      ? `✅ csempék jönnek (${tiles.ok} betöltve${tiles.err ? `, ${tiles.err} hiba` : ""})`
      : tiles.err > 0
        ? `❌ csempe-hiba (${tiles.err}) — a tile-szerver nem érhető el erről a hálózatról`
        : "⏳ csempékre várunk…";

  return (
    <div style={{ padding: 16, fontFamily: "system-ui, sans-serif", background: "#f1f5f9", minHeight: "100vh" }}>
      <h1 style={{ fontSize: 18, fontWeight: 700 }}>🗺️ Térkép-teszt</h1>
      <p style={{ fontSize: 14 }}>{engine}</p>
      <p style={{ fontSize: 14 }}>{tileStatus}</p>
      <div ref={divRef} style={{ height: "60vh", background: "#cbd5e1", borderRadius: 12 }} />
      <p style={{ fontSize: 12, color: "#64748b", marginTop: 8 }}>
        Ha itt minden ✅, de a Térkép oldal mégis üres: jelentkezz ki/be és frissíts (Ctrl+F5).
      </p>
    </div>
  );
}
