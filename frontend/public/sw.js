/* iwfm service worker — statikus fájlok + OFFLINE adattartalék (841171f6).
 *
 * Stratégia:
 *  - /api GET: network-first, a friss válasz másolata az API-cache-be kerül —
 *    hálózat NÉLKÜL a legutóbb látott adat jön vissza (a felület jelzi a
 *    korat a maga módján). POST/PUT/DELETE: mindig hálózat (az elszámolás
 *    offline várólistáját az app kezeli localStorage-ban).
 *  - Next statikus asset-ek és public fájlok: cache-first (hash-elt nevek),
 *  - minden más GET (oldalak): network-first, offline tartalék-cache-sel.
 */

const CACHE = "iwfm-v4";
const API_CACHE = "iwfm-api-v1";

// Offline tartalék-oldal + a szerelői/üzletkötői alaplapok előgyorsítótára
// (130. kör): az oldalak best-effort kerülnek be (bejelentkezett sütivel), hogy
// hálózat nélkül akkor is megnyíljanak, ha ezen az eszközön még nem jártak ott.
const OFFLINE_PAGE = "/offline.html";
const PRECACHE_PAGES = ["/", "/feladataim", "/elszamolas", "/jelenlet"];

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE).then(async (cache) => {
      await cache.add(OFFLINE_PAGE).catch(() => {});
      await Promise.all(
        PRECACHE_PAGES.map((p) =>
          fetch(p, { credentials: "include" })
            .then((res) => (res.ok ? cache.put(p, res) : null))
            .catch(() => null)
        )
      );
    })
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE && k !== API_CACHE).map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// Nem cache-elt API-utak: fizetés/nyomtató-ügynök (gép-forgalom), kijelentkezés
function apiCacheable(url) {
  return (
    !url.pathname.startsWith("/api/pay") &&
    !url.pathname.startsWith("/api/print-agent") &&
    !url.pathname.startsWith("/api/auth/logout")
  );
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET") {
    return; // mutációk: mindig hálózat, az app várólistázik, ha kell
  }
  // IDEGEN domain (pl. térkép-csempék): a SW NEM nyúl hozzá (790165df) —
  // a worker saját CSP-je (connect-src 'self') blokkolná a benne futó
  // fetch-et; a böngésző natívan tölti, az oldal img-src szabálya szerint.
  if (url.origin !== self.location.origin) {
    return;
  }

  if (url.pathname.startsWith("/api")) {
    if (!apiCacheable(url)) return;
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(API_CACHE).then((c) => c.put(event.request, copy));
          }
          return res;
        })
        .catch(async () => {
          const hit = await caches.match(event.request, { cacheName: API_CACHE });
          if (hit) return hit;
          return new Response(
            JSON.stringify({ detail: { code: "offline.no_cache" } }),
            { status: 503, headers: { "Content-Type": "application/json" } }
          );
        })
    );
    return;
  }

  const isStatic =
    url.pathname.startsWith("/_next/static") ||
    /\.(svg|png|ico|woff2?)$/.test(url.pathname);

  if (isStatic) {
    event.respondWith(
      caches.match(event.request).then(
        (hit) =>
          hit ||
          fetch(event.request).then((res) => {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(event.request, copy));
            return res;
          })
      )
    );
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(event.request, copy));
        return res;
      })
      .catch(async () => {
        const hit = await caches.match(event.request);
        if (hit) return hit;
        // Navigáció hálózat és mentett példány nélkül: offline tartalék-oldal
        if (event.request.mode === "navigate") {
          const page = await caches.match(OFFLINE_PAGE);
          if (page) return page;
        }
        return Response.error();
      })
  );
});
