/* iwfm service worker — statikus fájlok gyorsítótárazása.
 *
 * Stratégia:
 *  - /api kérések: SOHA nem gyorsítótárazzuk (mindig hálózat).
 *  - Next statikus asset-ek (/_next/static) és public fájlok: cache-first
 *    (immutable hash-elt fájlnevek), a többi GET: network-first, cache-be
 *    mentett másolattal offline tartalékként.
 */

const CACHE = "iwfm-v1";

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.pathname.startsWith("/api")) {
    return; // API és mutációk: mindig hálózat, SW nem nyúl bele
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
