/*
 * Photo viewer and drag-to-reorder for photo galleries.
 *
 * Website pages: every [data-gallery] gets a full-screen viewer when a photo
 * is clicked, and, if it has data-order-url, drag-to-reorder (mouse and pen;
 * touch screens use the Edit buttons, which also work without JavaScript).
 * The phone app calls openViewer() itself.
 *
 * Viewer: previous/next by button, arrow keys or swipe; tap or click the
 * photo to zoom to full size (then drag or scroll to look around); Escape or
 * the close button returns to the page. Text goes in with textContent only.
 */

var viewer = null;

function el(tag, attrs, text) {
  var node = document.createElement(tag);
  Object.keys(attrs || {}).forEach(function (k) {
    if (k === "class") node.className = attrs[k];
    else node.setAttribute(k, attrs[k]);
  });
  if (text) node.textContent = text;
  return node;
}

function build() {
  var dialog = el("dialog", { class: "viewer", "aria-label": "Photo viewer" });
  var stage = el("div", { class: "viewer-stage" });
  var img = el("img", { class: "viewer-image", alt: "" });
  stage.appendChild(img);
  var prev = el("button", { type: "button", class: "viewer-nav viewer-prev", "aria-label": "Previous photo" }, "‹");
  var next = el("button", { type: "button", class: "viewer-nav viewer-next", "aria-label": "Next photo" }, "›");
  var close = el("button", { type: "button", class: "viewer-close", "aria-label": "Close" }, "×");
  var bar = el("div", { class: "viewer-bar" });
  var counter = el("span", { class: "viewer-counter", "aria-live": "polite" });
  var caption = el("span", { class: "viewer-caption" });
  var download = el("a", { class: "viewer-download", download: "" }, "Download");
  bar.appendChild(counter);
  bar.appendChild(caption);
  bar.appendChild(download);
  [stage, prev, next, close, bar].forEach(function (n) { dialog.appendChild(n); });
  document.body.appendChild(dialog);

  var state = { items: [], index: 0 };

  function show(index) {
    var count = state.items.length;
    state.index = (index + count) % count;
    var item = state.items[state.index];
    dialog.classList.remove("zoomed");
    img.onerror = item.fallback ? function () { img.onerror = null; img.src = item.fallback; } : null;
    img.src = item.src;
    img.alt = item.caption || item.alt || "Photo";
    counter.textContent = count > 1 ? (state.index + 1) + " of " + count : "";
    caption.textContent = item.caption || "";
    if (item.download) { download.href = item.download; download.hidden = false; } else { download.hidden = true; }
    prev.hidden = next.hidden = count < 2;
    // Fetch the neighbours now so moving between photos is instant.
    [state.index - 1, state.index + 1].forEach(function (i) {
      if (count > 1) { var pre = new Image(); pre.src = state.items[(i + count) % count].src; }
    });
  }

  function step(delta) { if (state.items.length > 1) show(state.index + delta); }

  prev.addEventListener("click", function () { step(-1); });
  next.addEventListener("click", function () { step(1); });
  close.addEventListener("click", function () { dialog.close(); });
  dialog.addEventListener("keydown", function (e) {
    if (e.key === "ArrowLeft") { e.preventDefault(); step(-1); }
    else if (e.key === "ArrowRight") { e.preventDefault(); step(1); }
  });
  dialog.addEventListener("click", function (e) { if (e.target === dialog || e.target === stage) dialog.close(); });
  img.addEventListener("click", function () { dialog.classList.toggle("zoomed"); });

  // Swipe left/right to move between photos (not while zoomed: then dragging pans).
  var start = null;
  stage.addEventListener("pointerdown", function (e) {
    if (e.pointerType === "mouse" || dialog.classList.contains("zoomed")) return;
    start = { x: e.clientX, y: e.clientY };
  });
  stage.addEventListener("pointerup", function (e) {
    if (!start) return;
    var dx = e.clientX - start.x, dy = e.clientY - start.y;
    start = null;
    if (Math.abs(dx) > 50 && Math.abs(dx) > 1.5 * Math.abs(dy)) step(dx < 0 ? 1 : -1);
  });
  stage.addEventListener("pointercancel", function () { start = null; });
  dialog.addEventListener("close", function () {
    img.removeAttribute("src");
    if (state.opener && document.contains(state.opener)) state.opener.focus();
  });

  return {
    open: function (items, index, opener) {
      state.items = items;
      state.opener = opener || null;
      show(index || 0);
      if (!dialog.open) dialog.showModal();
      close.focus();
    },
    dialog: dialog,
  };
}

/*
 * Show photos full screen. items: [{ src, fallback?, caption?, alt?, download? }];
 * fallback is shown if src can't be loaded (e.g. the thumbnail when offline).
 */
export function openViewer(items, index, opener) {
  if (!items.length) return;
  if (!viewer) viewer = build();
  viewer.open(items, index, opener);
}

function galleryItems(gallery) {
  return Array.prototype.map.call(gallery.querySelectorAll(".photo-link"), function (link) {
    var thumb = link.querySelector("img");
    return { src: link.href, fallback: thumb ? thumb.src : null, caption: link.dataset.caption || "",
      alt: thumb ? thumb.alt : "", download: link.href, link: link };
  });
}

function enableViewer(gallery) {
  gallery.addEventListener("click", function (e) {
    var link = e.target.closest(".photo-link");
    if (!link || !gallery.contains(link) || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    var items = galleryItems(gallery);
    var index = items.findIndex(function (item) { return item.link === link; });
    openViewer(items, Math.max(index, 0), link);
  });
}

/* Drag photos into a new order; the order is saved with the same form the Edit buttons use. */
function enableReorder(gallery) {
  var url = gallery.dataset.orderUrl;
  var token = document.querySelector('input[name="csrf_token"]');
  if (!url || !token) return;
  var dragged = null;
  var status = el("p", { class: "hint gallery-status", role: "status", "aria-live": "polite" });
  gallery.parentNode.insertBefore(status, gallery.nextSibling);

  Array.prototype.forEach.call(gallery.querySelectorAll(".photo"), function (fig) {
    fig.draggable = true;
    fig.addEventListener("dragstart", function (e) {
      dragged = fig;
      fig.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", fig.dataset.photoId);
    });
    fig.addEventListener("dragend", function () { fig.classList.remove("dragging"); dragged = null; });
    fig.addEventListener("dragover", function (e) {
      if (!dragged || dragged === fig) return;
      e.preventDefault();
      var box = fig.getBoundingClientRect();
      var after = (e.clientX - box.left) > box.width / 2;
      gallery.insertBefore(dragged, after ? fig.nextSibling : fig);
    });
    fig.addEventListener("drop", function (e) { e.preventDefault(); save(); });
  });

  function save() {
    var ids = Array.prototype.map.call(gallery.querySelectorAll(".photo"), function (f) { return f.dataset.photoId; });
    var form = new FormData();
    form.append("csrf_token", token.value);
    form.append("order", ids.join(","));
    status.textContent = "Saving the new order…";
    fetch(url, { method: "POST", body: form, credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        // Reload so the cover badge and the Edit buttons match the new order.
        window.location.reload();
      })
      .catch(function () {
        status.textContent = "Couldn't save the new order. Reload the page and try again.";
      });
  }
}

Array.prototype.forEach.call(document.querySelectorAll("[data-gallery]"), function (gallery) {
  enableViewer(gallery);
  if (gallery.dataset.orderUrl && window.matchMedia("(pointer: fine)").matches) enableReorder(gallery);
});
