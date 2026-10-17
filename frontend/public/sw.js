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

const CACHE = "iwfm-v2";
const API_CACHE = "iwfm-api-v1";

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE));
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
      .catch(() => caches.match(event.request))
  );
});
