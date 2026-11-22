/* Site-wide behaviour: phone menu toggle and service worker registration. */
(function () {
  "use strict";

  var toggle = document.getElementById("nav-toggle");
  var nav = document.getElementById("site-nav");
  if (toggle && nav) {
    toggle.hidden = false;
    nav.classList.add("collapsible");
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  // Say how many photos are waiting to upload (the camera button has no visible file name).
  Array.prototype.forEach.call(document.querySelectorAll(".photo-pickers"), function (group) {
    var status = group.querySelector(".picker-status");
    var inputs = group.querySelectorAll("input[type=file]");
    Array.prototype.forEach.call(inputs, function (input) {
      input.addEventListener("change", function () {
        var count = 0;
        Array.prototype.forEach.call(inputs, function (i) { count += i.files ? i.files.length : 0; });
        if (status) status.textContent = count ? count + (count === 1 ? " photo" : " photos") + " ready. Press Upload." : "";
      });
    });
  });

  // Logging out erases the phone's offline copy (after a warning if changes haven't synced).
  var logoutForm = document.getElementById("logout-form");
  if (logoutForm && window.indexedDB) {
    logoutForm.addEventListener("submit", function (event) {
      if (logoutForm.dataset.wiped) return;
      event.preventDefault();
      pendingCount().then(function (count) {
        if (count > 0 && !window.confirm(count + (count === 1 ? " change on this phone hasn't" : " changes on this phone haven't") +
            " synced yet. Logging out deletes them. Log out anyway?")) return;
        return wipeOfflineData().then(function () {
          logoutForm.dataset.wiped = "1";
          logoutForm.submit();
        });
      });
    });
  }

  function pendingCount() {
    return new Promise(function (resolve) {
      var req = indexedDB.open("dickens-offline");
      req.onupgradeneeded = function () { req.transaction.abort(); resolve(0); }; // nothing stored yet
      req.onerror = function () { resolve(0); };
      req.onsuccess = function () {
        var db = req.result;
        try {
          var get = db.transaction("meta").objectStore("meta").get("pendingCount");
          get.onsuccess = function () { db.close(); resolve(get.result ? get.result.v : 0); };
          get.onerror = function () { db.close(); resolve(0); };
        } catch (e) { db.close(); resolve(0); }
      };
    });
  }

  function wipeOfflineData() {
    try {
      localStorage.removeItem("dickens-offline-enabled");
      localStorage.removeItem("dickens-last-bg-sync");
    } catch (e) { /* blocked */ }
    var jobs = [new Promise(function (resolve) {
      var req = indexedDB.deleteDatabase("dickens-offline");
      req.onsuccess = req.onerror = req.onblocked = function () { resolve(); };
    })];
    if (window.caches) {
      jobs.push(caches.keys().then(function (keys) {
        return Promise.all(keys.filter(function (k) { return k.indexOf("dickens-data-") === 0; })
          .map(function (k) { return caches.delete(k); }));
      }));
    }
    if (navigator.serviceWorker && navigator.serviceWorker.controller) {
      navigator.serviceWorker.controller.postMessage("clear-data");
    }
    return Promise.all(jobs).catch(function () {});
  }

  // Keep the offline copy fresh while browsing the website (at most every 2 minutes),
  // but only on devices where the phone app has been opened: other browsers,
  // such as a shared computer, never get a copy of the collection.
  var offlineEnabled = false;
  try { offlineEnabled = localStorage.getItem("dickens-offline-enabled") === "1"; } catch (e) { /* blocked */ }
  if (offlineEnabled && document.body.dataset.loggedIn && window.indexedDB && navigator.onLine && window.isSecureContext) {
    var last = 0;
    try { last = Number(localStorage.getItem("dickens-last-bg-sync") || 0); } catch (e) { /* storage blocked */ }
    if (Date.now() - last > 2 * 60 * 1000) {
      try { localStorage.setItem("dickens-last-bg-sync", String(Date.now())); } catch (e) { /* ignore */ }
      window.addEventListener("load", function () {
        import("/static/offline/sync.mjs").then(function (mod) { return mod.sync(); }).catch(function (err) {
          if (window.console) console.warn("Background sync failed", err);
        });
      });
    }
  }

  if ("serviceWorker" in navigator && window.isSecureContext) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function (err) {
        if (window.console) console.warn("Service worker registration failed", err);
      });
    });
  }
})();
