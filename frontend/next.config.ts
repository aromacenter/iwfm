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

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_TARGET}/api/:path*` }];
  },
};

export default nextConfig;
