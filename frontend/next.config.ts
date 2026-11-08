import type { NextConfig } from "next";

/**
 * Az API-t a frontend saját originjén át proxyzzuk (/api/* → backend).
 * Így a session cookie first-party marad — a két külön Railway domain
 * (up.railway.app public suffix!) között a böngészők (főleg Safari/iPhone)
 * a cross-site sütit eldobnák.
 */
// `||` és nem `??`: a Dockerfile ARG/ENV az API_PROXY_TARGET-et akkor is
// beállítja, ha nem kapott értéket — üres stringként. A `??` az üres stringet
// érvényes célnak vette, a /api önmagára proxyzódott és a Next 404-et adott
// (program.x-presso.hu belépés, 2026-10-28).
const API_TARGET =
  process.env.API_PROXY_TARGET ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://localhost:8000";

// Biztonsági fejlécek a Next által kiszolgált HTML-hez (a /api a backendre
// proxyzódik, annak saját fejlécei vannak). A CSP szándékosan elnéző a Next
// működéséhez (inline bootstrap-script + Tailwind inline style, HMR eval),
// de blokkolja az iframe-be ágyazást és a külső kapcsolatokat.
// 'unsafe-eval' csak fejlesztésben kell (HMR); éles buildben nem engedjük.
const isDev = process.env.NODE_ENV !== "production";
const CSP = [
  "default-src 'self'",
  // A térkép-csempék külső képek (9192f8d5/d8b2bbe3: az img-src 'self'
  // némán blokkolta őket — ezért volt "vaktérkép"). OSM + Carto tartalék.
  // X-Learning központi oktatóanyag-képek a Flottáról (x-admin.hu) — 146. kör
  "img-src 'self' data: blob: https://x-admin.hu https://tile.openstreetmap.org https://*.tile.openstreetmap.org https://tile.openstreetmap.de https://*.tile.openstreetmap.de https://server.arcgisonline.com https://*.basemaps.cartocdn.com",
  "style-src 'self' 'unsafe-inline'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "connect-src 'self'",
  "font-src 'self'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  // X-Learning (142. kör): a tananyag YouTube-videója beágyazva — a default-src
  // 'self' némán blokkolná az iframe-et (szürke "törött lap")
  // 153. kör: a blob: a saját PDF-ekhez kell (nyomtatás rejtett iframe-ben,
  // oklevél-néző) — a november 3-i CSP óta ezek némán nem működtek
  "frame-src 'self' blob: https://www.youtube-nocookie.com https://www.youtube.com",
].join("; ");

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Content-Security-Policy", value: CSP },
  // Kamera (QR-olvasó, fotó) és helymeghatározás a saját originnek kell;
  // minden mást (mikrofon, fizetés, USB...) letiltunk.
  { key: "Permissions-Policy", value: "camera=(self), geolocation=(self), microphone=(), payment=(), usb=()" },
  // HSTS csak élesben (HTTPS mögött); a Railway edge nem teszi rá magától.
  ...(isDev ? [] : [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }]),
];

const nextConfig: NextConfig = {
  // Ne áruljuk el a keretrendszert a fejlécben.
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_TARGET}/api/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
