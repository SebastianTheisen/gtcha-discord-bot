// Service-Worker der Beta: App-Gerüst offline verfügbar, Daten immer frisch vom Server (bei Ausfall: letzter Stand)
const CACHE = "gtcha-beta-v4";

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
  // Bilder nie über den Service-Worker (wie in der Live-App): iOS lädt sie sonst langsam oder gar nicht -
  // sie kommen direkt vom VPS und bleiben im Browser-Cache
  if (/\/img$/.test(url.pathname)) return;
  const isData = /\/api\//.test(url.pathname);
  const isAsset = /\/assets\//.test(url.pathname);
  if (isAsset) {
    // gebaute Dateien haben einen Hash im Namen, Bilder ändern sich nicht: Cache zuerst
    e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request).then((res) => {
      if (res.ok) {
        const copy = res.clone();   // sofort kopieren - die Seite liest den Inhalt gleich
        caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
      }
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

// Pushes: gleiche Daten wie in der Live-App; Antippen öffnet die Beta (Server-Adressen "/#/…" -> "<beta>/#/…")
self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { title: "GTCHA Tracker", body: event.data && event.data.text() }; }
  const badge = data.unread && self.navigator.setAppBadge ? self.navigator.setAppBadge(data.unread).catch(() => {}) : null;
  const tell = self.clients.matchAll({ type: "window", includeUncontrolled: true })
    .then((list) => list.forEach((c) => c.postMessage({ type: "push", unread: data.unread })));
  event.waitUntil(Promise.all([badge, tell, self.registration.showNotification(data.title || "GTCHA Tracker", {
    body: data.body || "",
    icon: "icon-512.png",
    badge: "icon-180.png",
    data: { url: data.url || "/" },
  })]));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const raw = event.notification.data && event.notification.data.url || "/";
  const target = raw.startsWith("/#") || raw === "/" ? self.registration.scope + raw.slice(1) : raw;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const client of list) {
      if (client.url.startsWith(self.registration.scope) && "focus" in client) { client.navigate(target); return client.focus(); }
    }
    return self.clients.openWindow(target);
  }));
});
