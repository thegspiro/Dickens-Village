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

  // Logging out ends the session on the server, then erases the phone's offline
  // copy (after a warning if changes haven't synced). Without JavaScript, or if
  // the module can't load, the form simply submits.
  var logoutForm = document.getElementById("logout-form");
  if (logoutForm && window.indexedDB && window.fetch) {
    logoutForm.addEventListener("submit", function (event) {
      if (logoutForm.dataset.plain) return;
      event.preventDefault();
      var button = logoutForm.querySelector("button");
      import("/static/offline/logout.mjs").then(function (account) {
        return account.pendingCount().then(function (count) {
          var warning = account.lossWarning(count, "Logging out", "Log out anyway?");
          if (warning && !window.confirm(warning)) return;
          if (button) button.disabled = true;
          return account.logout(logoutForm.elements.csrf_token.value).then(function () {
            window.location.href = "/login";
          }, function (err) {
            if (button) button.disabled = false;
            window.alert(err && err.message ? err.message : "Couldn't log out. Try again.");
          });
        });
      }, function () {
        logoutForm.dataset.plain = "1";
        logoutForm.submit();
      });
    });
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
