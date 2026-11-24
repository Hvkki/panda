// Panda service worker. Navigations + the SW itself always come from the network,
// so a new deploy loads immediately (the old SW was serving a stale app — the
// cause of "my fixes didn't show up"). Only hashed /assets/ are cached, and those
// are safe because their name changes every build.
const CACHE = "panda-assets-v3";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  // Only hashed build assets are cache-first; everything else (HTML, sw.js, API) is network.
  if (url.pathname.startsWith("/assets/")) {
    e.respondWith(caches.open(CACHE).then(async (c) => {
      const hit = await c.match(e.request);
      if (hit) return hit;
      const res = await fetch(e.request);
      if (res.ok) c.put(e.request, res.clone());
      return res;
    }));
  }
});

// Tapping a "Panda replied" notification brings the app back.
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window" }).then((cs) => (cs[0] ? cs[0].focus() : self.clients.openWindow("/"))));
});
