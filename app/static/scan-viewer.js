/*
 * 3D scan viewer setup. Loaded (as a classic script, before the viewer module)
 * only on pages that show a scan.
 *
 * Points <model-viewer> at decoders served by this app instead of Google's CDN,
 * then reports loading problems in plain words.
 */
(function () {
  "use strict";
  // Must be set before model-viewer.min.js runs.
  self.ModelViewerElement = self.ModelViewerElement || {};
  self.ModelViewerElement.dracoDecoderLocation = "/static/vendor/draco/";
  self.ModelViewerElement.ktx2TranscoderLocation = "/static/vendor/basis/";
  // Meshopt-compressed models aren't supported (no self-hosted decoder); they
  // report an error and can still be downloaded.

  function wire(viewer) {
    var status = viewer.parentElement && viewer.parentElement.querySelector(".scan-status");
    function say(text, isError) {
      if (!status) return;
      status.textContent = text;
      status.classList.toggle("error", Boolean(isError));
    }
    viewer.addEventListener("load", function () {
      viewer.dataset.state = "loaded";
      say("Drag to turn it, pinch or scroll to zoom.", false);
    });
    viewer.addEventListener("error", function () {
      viewer.dataset.state = "error";
      say("This scan couldn't be shown in the browser. Use Download to open it in another app.", true);
    });
  }

  function start() {
    Array.prototype.forEach.call(document.querySelectorAll("model-viewer.scan-viewer"), wire);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
