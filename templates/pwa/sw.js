/* ==========================================================================
   Service worker for the installed price-checker app.
   --------------------------------------------------------------------------
   Rendered by kiosk.pwa.service_worker. The only template value is the
   version below: it changes whenever the CSS or JS does, which is what makes
   browsers fetch this worker again and drop the old cached files.

   What is cached, and why only that:

     /static/  CSS, JS, fonts. Files carrying a ?v= stamp never change under
               that address, so they are served straight from the cache —
               this is most of what makes the app open instantly. Unstamped
               ones (the admin's) are served from cache and refreshed behind.
     /media/   Product pictures and logos: from cache, refreshed behind, and
               trimmed so the cache cannot grow without end.

   What is never cached: pages and everything under /api/. A price shown
   from a cache could be yesterday's price, so those always go to the
   server. With no connection a page gets a short "you are offline" screen
   instead of the browser's own error.
   ========================================================================== */

"use strict";

var VERSION = "{{ version }}";
var STATIC_CACHE = "pc-static-" + VERSION;
var MEDIA_CACHE = "pc-media";
var MEDIA_LIMIT = 300;

var OFFLINE_HTML =
  '<!doctype html><html lang="en"><head><meta charset="utf-8">' +
  '<meta name="viewport" content="width=device-width, initial-scale=1">' +
  "<title>Offline</title><style>" +
  "body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;" +
  "font-family:system-ui,sans-serif;background:#070b16;color:#f4f7fd;text-align:center;padding:24px}" +
  "button{margin-top:18px;padding:12px 22px;border:0;border-radius:10px;" +
  "background:#ffc247;color:#1a1200;font-size:16px;font-weight:700}" +
  "</style></head><body><div><h1>No connection</h1>" +
  "<p>Prices come live from the shop's server, so the app needs the network.</p>" +
  '<button onclick="location.reload()">Try again</button></div></body></html>';

self.addEventListener("install", function () {
  // Take over straight away rather than waiting for every window to close —
  // a counter screen is never closed.
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(names.filter(function (name) {
        return name.indexOf("pc-static-") === 0 && name !== STATIC_CACHE;
      }).map(function (name) { return caches.delete(name); }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") return;

  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.indexOf("/static/") === 0) {
    event.respondWith(url.searchParams.has("v")
      ? cacheFirst(request, STATIC_CACHE)
      : staleWhileRevalidate(request, STATIC_CACHE));
    return;
  }

  if (url.pathname.indexOf("/media/") === 0) {
    event.respondWith(staleWhileRevalidate(request, MEDIA_CACHE, MEDIA_LIMIT));
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(offlinePage));
  }
  // Everything else — above all /api/ — is left to the network untouched.
});

function cacheFirst(request, cacheName) {
  return caches.open(cacheName).then(function (cache) {
    return cache.match(request).then(function (hit) {
      return hit || fetch(request).then(function (response) {
        if (response.ok) cache.put(request, response.clone());
        return response;
      });
    });
  });
}

function staleWhileRevalidate(request, cacheName, limit) {
  return caches.open(cacheName).then(function (cache) {
    return cache.match(request).then(function (hit) {
      var refreshed = fetch(request).then(function (response) {
        if (response.ok) {
          cache.put(request, response.clone()).then(function () {
            if (limit) trim(cache, limit);
          });
        }
        return response;
      }).catch(function () { return hit; });
      return hit || refreshed;
    });
  });
}

/** Drop the oldest entries once a cache holds more than `limit`. */
function trim(cache, limit) {
  cache.keys().then(function (keys) {
    for (var i = 0; i < keys.length - limit; i++) cache.delete(keys[i]);
  });
}

function offlinePage() {
  return new Response(OFFLINE_HTML, {
    headers: { "Content-Type": "text/html; charset=utf-8" }
  });
}
