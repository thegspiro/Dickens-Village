/*
 * Service worker for the Dickens Village Tracker.
 *
 * - Caches the app's own files, the phone-app shell (/app) and an offline page.
 * - Website pages always come from the network. When the server can't be
 *   reached, the phone app (/app) opens instead; it works from the copy of
 *   your collection kept in the phone's own storage (IndexedDB), not from here.
 * - Photo thumbnails are cached as they are viewed so they show offline.
 *   That cache is erased when you log out.
 *
 * Bump CACHE_VERSION whenever a file in PRECACHE changes so phones pick it up.
 */
"use strict";

var CACHE_VERSION = "v3";
var CACHE_NAME = "dickens-static-" + CACHE_VERSION;
var DATA_CACHE = "dickens-data-v1";
var OFFLINE_URL = "/offline";
var APP_URL = "/app";
var PRECACHE = [
  OFFLINE_URL,
  APP_URL,
  "/manifest.webmanifest",
  "/static/offline/main.mjs",
  "/static/offline/model.mjs",
  "/static/offline/sync.mjs",
  "/static/offline/db.mjs",
  "/static/offline/api.mjs",
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
        return (key.indexOf("dickens-static-") === 0 && key !== CACHE_NAME) ||
          (key.indexOf("dickens-data-") === 0 && key !== DATA_CACHE);
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
    // Pages: always the network. If it can't be reached, open the phone app
    // (or the offline page if the app isn't cached yet).
    event.respondWith(
      fetch(request).catch(function () {
        return caches.match(APP_URL).then(function (app) {
          return app || caches.match(OFFLINE_URL);
        });
      })
    );
    return;
  }

  if (/^\/(catalog-)?photos\/\d+\/thumb$/.test(url.pathname)) {
    // Thumbnails never change for a given id: cache-first, filled as you browse.
    event.respondWith(
      caches.open(DATA_CACHE).then(function (cache) {
        return cache.match(request).then(function (cached) {
          return cached || fetch(request).then(function (response) {
            if (response.ok && response.type === "basic" && !response.redirected) {
              cache.put(request, response.clone());
            }
            return response;
          });
        });
      })
    );
    return;
  }

  if (url.pathname.indexOf("/static/") === 0 || url.pathname === APP_URL) {
    // App files: network first, so a new version is used as soon as it is
    // deployed (the phone app's modules must all come from the same version);
    // the cached copy is used when the network is slow (4 s) or unavailable.
    event.respondWith(networkFirst(request));
  }
  // Everything else (photos, catalog search, pages' data) goes to the network untouched.
});

self.addEventListener("message", function (event) {
  // Sent by the page on logout: forget cached photos.
  if (event.data === "clear-data") {
    event.waitUntil(caches.delete(DATA_CACHE));
  }
});

function networkFirst(request) {
  return caches.open(CACHE_NAME).then(function (cache) {
    var network = fetch(request).then(function (response) {
      if (response.ok && response.type === "basic") cache.put(request, response.clone());
      return response;
    });
    var timeout = new Promise(function (resolve) { setTimeout(resolve, 4000); });
    return Promise.race([network.catch(function () { return null; }), timeout]).then(function (response) {
      if (response) return response;
      return cache.match(request).then(function (cached) {
        return cached || network; // nothing cached: keep waiting for the network
      });
    });
  });
}
