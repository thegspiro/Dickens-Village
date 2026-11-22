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

  if ("serviceWorker" in navigator && window.isSecureContext) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function (err) {
        if (window.console) console.warn("Service worker registration failed", err);
      });
    });
  }
})();
