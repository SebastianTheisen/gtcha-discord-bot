// Service Worker: App-Hülle offline verfügbar halten, Push-Benachrichtigungen anzeigen.
const CACHE = "gtcha-tracker-v45";
// Bilder dauerhaft auf dem Gerät halten (iOS leert den normalen Browser-Cache installierter Apps oft)
const IMG_CACHE = "gtcha-img-v2";
const IMG_MAX = 4000;
const SHELL = ["/", "/static/style.css?v=45", "/static/app.js?v=45", "/static/icon-180.png?v=4", "/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Bilder: zuerst aus dem Gerätespeicher, sonst vom VPS holen und ablegen
async function cachedImage(request) {
  const cache = await caches.open(IMG_CACHE);
  const hit = await cache.match(request);
  if (hit) return hit;
  const res = await fetch(request);
  if (res.ok) cache.put(request, res.clone());   // nicht warten - das Bild geht sofort an die Seite
  return res;
}
self.addEventListener("message", (event) => {
  if (event.data?.type === "stats") {
    caches.open(IMG_CACHE).then((c) => c.keys()).then((k) => event.ports[0]?.postMessage({ images: k.length, queue: 0 }));
  }
});

let trimming = false;
async function trimImages(cachePromise) {
  if (trimming) return;
  const cache = await cachePromise;
  trimming = true;
  try {
    const keys = await cache.keys();
    for (const key of keys.slice(0, Math.max(0, keys.length - IMG_MAX))) await cache.delete(key);   // älteste zuerst
  } finally { trimming = false; }
}

// Daten immer frisch vom Server, nur die App-Hülle aus dem Cache (wenn offline)
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin) return;
  // Bilder nicht über den Hintergrund-Helfer: beim Kaltstart muss iOS ihn und seinen Speicher erst
  // hochfahren (~2 s bis zum ersten Bild). Direkt vom VPS kommen sie in ~40 ms (plus Safari-Cache).
  if (url.pathname === "/img") return;
  if (url.pathname.startsWith("/api/")) return;
  event.respondWith(fetch(event.request, { cache: "no-cache" })
    .then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(event.request, copy));
      return res;
    })
    .catch(() => caches.match(event.request).then((r) => r || caches.match("/"))));
});

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { title: "GTCHA Tracker", body: event.data?.text() }; }
  event.waitUntil(self.registration.showNotification(data.title || "GTCHA Tracker", {
    body: data.body || "",
    icon: "/static/icon-512.png?v=4",
    badge: "/static/icon-180.png?v=4",
    data: { url: data.url || "/" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = event.notification.data?.url || "/";
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const client of list) {
      if ("focus" in client) { client.navigate(target); return client.focus(); }
    }
    return self.clients.openWindow(target);
  }));
});
