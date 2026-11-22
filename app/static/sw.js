/*
 * Service worker for the Dickens Village Tracker.
 *
 * Stage 1 (this version): caches only the app's own static files and an
 * offline page. Pages that show your collection are always fetched from the
 * server and are never stored by the service worker.
 *
 * Bump CACHE_VERSION whenever a file in PRECACHE changes so phones pick it up.
 */
"use strict";

var CACHE_VERSION = "v1";
var CACHE_NAME = "dickens-static-" + CACHE_VERSION;
var OFFLINE_URL = "/offline";
var PRECACHE = [
  OFFLINE_URL,
  "/static/style.css",
  "/static/app.js",
  "/static/catalog-lookup.js",
  "/static/icons/icon-192.png",
  "/static/icons/favicon-32.png",
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      return cache.addAll(PRECACHE.map(function (url) { return new Request(url, { cache: "reload" }); }));
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (key) {
        return key.indexOf("dickens-static-") === 0 && key !== CACHE_NAME;
      }).map(function (key) { return caches.delete(key); }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") return; // never intercept form posts
  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    // Pages: always the network; the offline page only when it can't be reached.
    event.respondWith(
      fetch(request).catch(function () {
        return caches.match(OFFLINE_URL);
      })
    );
    return;
  }

  if (url.pathname.indexOf("/static/") === 0) {
    // App files: answer from the cache, refresh it in the background.
    event.respondWith(
      caches.open(CACHE_NAME).then(function (cache) {
        return cache.match(request).then(function (cached) {
          var refresh = fetch(request).then(function (response) {
            if (response.ok) cache.put(request, response.clone());
            return response;
          });
          if (cached) {
            event.waitUntil(refresh.catch(function () {}));
            return cached;
          }
          return refresh;
        });
      })
    );
  }
  // Everything else (photos, catalog search, pages' data) goes to the network untouched.
});
