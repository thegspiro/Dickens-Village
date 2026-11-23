/*
 * Service worker for the Dickens Village Tracker.
 *
 * - Caches the app's own files, the phone-app shell (/app) and an offline page,
 *   all from one deploy: the server fills in CACHE_VERSION with a hash of
 *   those files, so a deploy that changes any of them installs a fresh copy of
 *   all of them, and the old copy is deleted once the new one is complete.
 * - Website pages always come from the network. When the server can't be
 *   reached, or a proxy in front of it answers 502/503/504 (it is restarting),
 *   the phone app (/app) opens instead; it works from the copy of your
 *   collection kept in the phone's own storage (IndexedDB), not from here.
 * - Photo thumbnails are cached as they are viewed so they show offline.
 *   Cached ones are refreshed after a week when online, dropped when the
 *   server no longer serves them, and erased when you log out.
 */
"use strict";

var CACHE_VERSION = "__BUILD__";
var CACHE_NAME = "dickens-static-" + CACHE_VERSION;
var DATA_CACHE = "dickens-data-v1";
var OFFLINE_URL = "/offline";
var APP_URL = "/app";
var THUMB_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
var CACHED_AT = "X-Dickens-Cached-At";
var PRECACHE = [
  OFFLINE_URL,
  APP_URL,
  "/manifest.webmanifest",
  "/static/offline/main.mjs",
  "/static/offline/model.mjs",
  "/static/offline/sync.mjs",
  "/static/offline/db.mjs",
  "/static/offline/api.mjs",
  "/static/offline/logout.mjs",
  "/static/style.css",
  "/static/app.js",
  "/static/catalog-lookup.js",
  "/static/gallery.mjs",
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
    event.respondWith(page(request));
    return;
  }

  if (/^\/(catalog-)?photos\/\d+\/thumb$/.test(url.pathname)) {
    event.respondWith(thumbnail(request));
    return;
  }

  if (url.pathname.indexOf("/static/vendor/") === 0) return; // large 3D viewer files: browser cache only

  if (url.pathname.indexOf("/static/") === 0 || url.pathname === APP_URL) {
    event.respondWith(appFile(request));
  }
  // Everything else (photos, catalog search, pages' data) goes to the network untouched.
});

self.addEventListener("message", function (event) {
  // Sent by the page on logout: forget cached photos.
  if (event.data === "clear-data") {
    event.waitUntil(caches.delete(DATA_CACHE));
  }
});

/* The server (or the proxy in front of it) is down or restarting. */
function unavailable(response) {
  return response.status === 502 || response.status === 503 || response.status === 504;
}

function offlineFallback() {
  return caches.match(APP_URL, { cacheName: CACHE_NAME }).then(function (app) {
    return app || caches.match(OFFLINE_URL);
  });
}

/* Pages: always the network; the phone app when the server can't be reached. */
function page(request) {
  return fetch(request).then(function (response) {
    if (!unavailable(response)) return response;
    return offlineFallback().then(function (fallback) { return fallback || response; });
  }, function () {
    return offlineFallback();
  });
}

/*
 * App files: the copy cached for this version, so every file the phone app
 * loads comes from the same deploy. Files not in the precache are fetched once
 * and kept with this version's copy.
 */
function appFile(request) {
  return caches.open(CACHE_NAME).then(function (cache) {
    return cache.match(request).then(function (cached) {
      if (cached) return cached;
      return fetch(request).then(function (response) {
        if (response.ok && response.type === "basic") cache.put(request, response.clone());
        return response;
      });
    });
  });
}

/* A response copy marked with when it was cached (headers of a cached response can't be changed later). */
function stamped(response) {
  var headers = new Headers(response.headers);
  headers.set(CACHED_AT, String(Date.now()));
  return response.blob().then(function (body) {
    return new Response(body, { status: response.status, statusText: response.statusText, headers: headers });
  });
}

/*
 * Thumbnails: cached as they are viewed. A cached copy is used while it is
 * under a week old, or whenever the server can't be reached. A thumbnail the
 * server no longer serves (photo deleted, or logged out: redirected to the
 * login page) is removed from the cache.
 */
function thumbnail(request) {
  return caches.open(DATA_CACHE).then(function (cache) {
    return cache.match(request).then(function (cached) {
      var age = cached ? Date.now() - Number(cached.headers.get(CACHED_AT) || 0) : Infinity;
      if (cached && age < THUMB_MAX_AGE_MS) return cached;
      return fetch(request).then(function (response) {
        if (response.ok && response.type === "basic" && !response.redirected) {
          return stamped(response.clone()).then(function (copy) {
            return cache.put(request, copy);
          }).then(function () { return response; }, function () { return response; });
        }
        if (unavailable(response)) return cached || response;
        if (cached) return cache.delete(request).then(function () { return response; });
        return response;
      }, function () {
        return cached || Response.error();
      });
    });
  });
}
