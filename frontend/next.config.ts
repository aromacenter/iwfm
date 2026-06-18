import type { NextConfig } from "next";

/**
 * Az API-t a frontend saját originjén át proxyzzuk (/api/* → backend).
 * Így a session cookie first-party marad — a két külön Railway domain
 * (up.railway.app public suffix!) között a böngészők (főleg Safari/iPhone)
 * a cross-site sütit eldobnák.
 */
const API_TARGET =
  process.env.API_PROXY_TARGET ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:8000";

// Biztonsági fejlécek a Next által kiszolgált HTML-hez (a /api a backendre
// proxyzódik, annak saját fejlécei vannak). A CSP szándékosan elnéző a Next
// működéséhez (inline bootstrap-script + Tailwind inline style, HMR eval),
// de blokkolja az iframe-be ágyazást és a külső kapcsolatokat.
const CSP = [
  "default-src 'self'",
  "img-src 'self' data: blob:",
  "style-src 'self' 'unsafe-inline'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
  "connect-src 'self'",
  "font-src 'self'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Content-Security-Policy", value: CSP },
];

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_TARGET}/api/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
