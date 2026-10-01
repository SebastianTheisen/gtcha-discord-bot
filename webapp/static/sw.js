// Service Worker: App-Hülle offline verfügbar halten, Push-Benachrichtigungen anzeigen.
const CACHE = "gtcha-tracker-v25";
const SHELL = ["/", "/static/style.css", "/static/app.js", "/static/icon-180.png?v=4", "/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Daten immer frisch vom Server, nur die App-Hülle aus dem Cache (wenn offline)
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/") || url.pathname === "/img") return;
  event.respondWith(fetch(event.request)
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
