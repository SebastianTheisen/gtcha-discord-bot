// Service-Worker der Beta: App-Gerüst offline verfügbar, Daten immer frisch vom Server (bei Ausfall: letzter Stand)
const CACHE = "gtcha-beta-v1";

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(["./", "manifest.webmanifest", "icon-180.png"])).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.endsWith("/api/stream")) return;
  const isData = /\/api\//.test(url.pathname);
  const isAsset = /\/assets\//.test(url.pathname) || /\/img$/.test(url.pathname);
  if (isAsset) {
    // gebaute Dateien haben einen Hash im Namen, Bilder ändern sich nicht: Cache zuerst
    e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request).then((res) => {
      if (res.ok) caches.open(CACHE).then((c) => c.put(e.request, res.clone()));
      return res;
    })));
    return;
  }
  // Seite und Daten: Netz zuerst, bei Ausfall der letzte Stand
  e.respondWith(fetch(e.request).then((res) => {
    if (res.ok && (isData || e.request.mode === "navigate")) {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy));
    }
    return res;
  }).catch(() => caches.match(e.request).then((hit) => hit || Response.error())));
});
