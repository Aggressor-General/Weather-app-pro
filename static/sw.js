/* Weather App Pro service worker.
 *
 * What it does:
 *  - Pre-caches the app shell (CSS, JS, icons, offline page) so the app opens fast.
 *  - Pages: always try the network first; if offline, show /offline instead of a browser error.
 *  - Static files: serve from cache instantly and refresh them in the background.
 *
 * What it deliberately does NOT do:
 *  - It never caches /api/* (AI answers) or weather pages, so data is always fresh
 *    and nothing personal is stored on the device.
 *
 * When you change CSS/JS/HTML shell files, bump VERSION so users get the new files.
 */
const VERSION = "v1";
const CACHE = "weather-pro-" + VERSION;
const PRECACHE = [
  "/offline",
  "/static/style.css",
  "/static/app.js",
  "/static/pwa.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/manifest.webmanifest",
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE)
      .then(function (cache) { return cache.addAll(PRECACHE); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(
          keys.filter(function (k) { return k.indexOf("weather-pro-") === 0 && k !== CACHE; })
              .map(function (k) { return caches.delete(k); })
        );
      })
      .then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (event) {
  const request = event.request;
  if (request.method !== "GET") return;                       // searches, AI calls, etc.

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;            // weather/AI providers are never touched
  if (url.pathname.indexOf("/api/") === 0) return;            // never cache AI or user data

  // Page navigations: network first, offline page as the fallback.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(function () { return caches.match("/offline"); })
    );
    return;
  }

  // Static files: cache first, refresh in the background.
  if (url.pathname.indexOf("/static/") === 0 || url.pathname === "/manifest.webmanifest") {
    event.respondWith(
      caches.match(request).then(function (cached) {
        const refresh = fetch(request).then(function (response) {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then(function (cache) { cache.put(request, copy); });
          }
          return response;
        }).catch(function () { return cached; });
        return cached || refresh;
      })
    );
  }
});
