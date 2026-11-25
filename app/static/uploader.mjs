/*
 * Photo upload forms marked data-uploader: drop photos onto the form or paste
 * them, see previews (remove any before uploading), then upload them one at a
 * time with a progress bar each. A photo the server refuses is marked and the
 * rest carry on. Without this script the form posts as an ordinary form.
 *
 * The server is asked for JSON ({added, errors}) instead of its usual redirect.
 * Text goes in with textContent only.
 */

var TYPES = ["image/jpeg", "image/png", "image/webp"];

function el(tag, cls, text) {
  var node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text) node.textContent = text;
  return node;
}

function size(bytes) {
  return bytes >= 1024 * 1024 ? (bytes / (1024 * 1024)).toFixed(1) + " MB" : Math.max(1, Math.round(bytes / 1024)) + " KB";
}

/* Send one file; resolves to { ok, message } and reports progress as 0..1. */
export function sendFile(url, token, file, onProgress) {
  return new Promise(function (resolve) {
    var xhr = new XMLHttpRequest();
    var form = new FormData();
    form.append("csrf_token", token);
    form.append("photo", file, file.name);
    xhr.open("POST", url);
    xhr.setRequestHeader("Accept", "application/json");
    xhr.upload.onprogress = function (e) { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = function () {
      var data = null;
      try { data = JSON.parse(xhr.responseText); } catch (err) { data = null; }
      if (xhr.status === 200 && data && data.added > 0) { resolve({ ok: true }); return; }
      if (data && data.errors && data.errors.length) { resolve({ ok: false, message: data.errors[0].message }); return; }
      if (xhr.status === 413) { resolve({ ok: false, message: "Too large for the server to accept." }); return; }
      if (xhr.status === 400) { resolve({ ok: false, message: "Your page expired. Reload it and try again." }); return; }
      resolve({ ok: false, message: "The server refused it (HTTP " + xhr.status + ")." });
    };
    xhr.onerror = function () { resolve({ ok: false, message: "Couldn't reach the server." }); };
    xhr.send(form);
  });
}

/* Reload the page; the server then shows "Added N photos." as after a normal upload. */
function reloadWith(count) {
  var url = new URL(window.location.href);
  url.searchParams.delete("uploaded");
  if (count) url.searchParams.set("uploaded", String(count));
  url.hash = "photos";
  window.location.assign(url.toString());
  if (url.pathname + url.search === window.location.pathname + window.location.search) window.location.reload();
}

// The message has been shown; don't repeat it if this page is reloaded.
(function () {
  var url = new URL(window.location.href);
  if (url.searchParams.has("uploaded") && window.history.replaceState) {
    url.searchParams.delete("uploaded");
    window.history.replaceState(null, "", url.toString());
  }
})();

function enhance(form) {
  var token = form.querySelector('input[name="csrf_token"]');
  var inputs = form.querySelectorAll('input[type="file"]');
  var submit = form.querySelector('button[type="submit"]');
  var status = form.querySelector(".picker-status");
  if (!token || !inputs.length || !submit) return;
  var maxBytes = (Number(form.dataset.maxMb) || 25) * 1024 * 1024;
  var queue = []; // { file, row, bar, note, done, url }
  var busy = false;

  form.classList.add("uploader");
  var hint = el("p", "hint uploader-hint", "Or drop photos here, or paste one (Ctrl+V / ⌘V).");
  var list = el("ul", "upload-queue");
  list.setAttribute("aria-live", "polite");
  form.insertBefore(hint, submit);
  form.insertBefore(list, submit);

  function refresh() {
    var waiting = queue.filter(function (q) { return !q.done && !q.bad; }).length;
    submit.disabled = busy || waiting === 0;
    submit.textContent = busy ? "Uploading…" : waiting ? "Upload " + waiting + (waiting === 1 ? " photo" : " photos") : "Upload";
    if (status) status.textContent = "";
  }

  function add(files) {
    Array.prototype.forEach.call(files, function (file, i) {
      if (!file) return;
      var name = file.name || "pasted-photo-" + (queue.length + i + 1) + "." + ((file.type || "image/png").split("/")[1] || "png");
      if (!file.name) file = new File([file], name, { type: file.type });
      var entry = { file: file, done: false, bad: false };
      var row = el("li", "upload-item");
      var preview = el("img", "upload-preview");
      preview.alt = "";
      var info = el("div", "upload-info");
      info.appendChild(el("span", "upload-name", name + " · " + size(file.size)));
      var bar = el("progress", "upload-bar");
      bar.max = 1;
      bar.value = 0;
      var note = el("span", "upload-note");
      info.appendChild(bar);
      info.appendChild(note);
      var remove = el("button", "link danger upload-remove", "Remove");
      remove.type = "button";
      remove.addEventListener("click", function () {
        if (busy) return;
        queue = queue.filter(function (q) { return q !== entry; });
        if (entry.url) URL.revokeObjectURL(entry.url);
        row.remove();
        refresh();
      });
      row.appendChild(preview);
      row.appendChild(info);
      row.appendChild(remove);
      entry.row = row;
      entry.bar = bar;
      entry.note = note;
      entry.remove = remove;
      if (TYPES.indexOf(file.type) === -1) {
        entry.bad = true;
        note.textContent = "Not a JPEG, PNG or WebP photo; it won't be uploaded.";
        row.classList.add("failed");
      } else if (file.size > maxBytes) {
        entry.bad = true;
        note.textContent = "Larger than " + Math.round(maxBytes / (1024 * 1024)) + " MB; it won't be uploaded.";
        row.classList.add("failed");
      } else {
        entry.url = URL.createObjectURL(file);
        preview.src = entry.url;
      }
      queue.push(entry);
      list.appendChild(row);
    });
    refresh();
  }

  Array.prototype.forEach.call(inputs, function (input) {
    input.addEventListener("change", function () {
      add(input.files);
      input.value = ""; // the queue holds them now; the same photo can be picked again
    });
  });

  form.addEventListener("dragover", function (e) {
    if (!e.dataTransfer || Array.prototype.indexOf.call(e.dataTransfer.types, "Files") === -1) return;
    e.preventDefault();
    form.classList.add("drop-target");
  });
  form.addEventListener("dragleave", function (e) {
    if (!form.contains(e.relatedTarget)) form.classList.remove("drop-target");
  });
  form.addEventListener("drop", function (e) {
    if (!e.dataTransfer || !e.dataTransfer.files.length) return;
    e.preventDefault();
    form.classList.remove("drop-target");
    add(e.dataTransfer.files);
  });
  // Pasting a photo anywhere on the page adds it, unless you're typing in a field.
  document.addEventListener("paste", function (e) {
    var target = e.target;
    if (target && /^(INPUT|TEXTAREA)$/.test(target.tagName) && target.type !== "file") return;
    var files = e.clipboardData ? Array.prototype.filter.call(e.clipboardData.files || [], function (f) {
      return f.type.indexOf("image/") === 0;
    }) : [];
    if (!files.length) return;
    e.preventDefault();
    add(files);
    form.scrollIntoView({ block: "nearest" });
  });

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    if (busy) return;
    var todo = queue.filter(function (q) { return !q.done && !q.bad; });
    if (!todo.length) return;
    busy = true;
    refresh();
    var failed = 0;
    for (var i = 0; i < todo.length; i++) {
      var entry = todo[i];
      entry.remove.hidden = true;
      entry.note.textContent = "Uploading…";
      var result = await sendFile(form.action, token.value, entry.file, function (fraction) { entry.bar.value = fraction; });
      if (result.ok) {
        entry.done = true;
        entry.bar.value = 1;
        entry.note.textContent = "Uploaded";
        entry.row.classList.add("uploaded");
      } else {
        failed++;
        entry.bad = true;
        entry.note.textContent = result.message;
        entry.row.classList.add("failed");
        entry.remove.hidden = false;
      }
    }
    busy = false;
    refresh();
    if (!failed) { reloadWith(todo.length); return; }
    var uploaded = queue.filter(function (q) { return q.done; }).length;
    var again = el("button", "secondary upload-reload", uploaded ? "Show the uploaded photos" : "Start over");
    again.type = "button";
    again.addEventListener("click", function () { reloadWith(uploaded); });
    list.appendChild(again);
  });

  refresh();
}

Array.prototype.forEach.call(document.querySelectorAll("form[data-uploader]"), enhance);
