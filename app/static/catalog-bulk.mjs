/*
 * "Add catalog photos by item number": match many photos to catalog entries by
 * their filenames, let the person review and fix the matches, then upload
 * each to its entry one at a time (the same upload request as an entry's own
 * page). Nothing is uploaded until the person presses Upload. Text goes in with
 * textContent only.
 */

import { sendFile } from "./uploader.mjs";

var TYPES = ["image/jpeg", "image/png", "image/webp"];
var root = document.getElementById("bulk");

function el(tag, cls, text) {
  var node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function isLocal(url) {
  return typeof url === "string" && url.charAt(0) === "/" && url.charAt(1) !== "/" && url.indexOf("\\") === -1;
}

function label(entry) {
  return (entry.item_number || "No number") + " · " + entry.name + (entry.parent_name ? " (part of “" + entry.parent_name + "”)" : "");
}

if (root) {
  var token = root.querySelector('input[name="csrf_token"]').value;
  var maxBytes = (Number(root.dataset.maxMb) || 25) * 1024 * 1024;
  var maxFiles = Number(root.dataset.maxFiles) || 500;
  var input = document.getElementById("bulk-files");
  var drop = document.getElementById("bulk-drop");
  var status = document.getElementById("bulk-status");
  var table = document.getElementById("bulk-table");
  var body = document.getElementById("bulk-body");
  var actions = document.getElementById("bulk-actions");
  var uploadButton = document.getElementById("bulk-upload");
  var done = document.getElementById("bulk-done");
  var rows = []; // { file, entries, chosen (entry or null), include (checkbox), note, bar, tr }
  var busy = false;

  function refresh() {
    var ready = rows.filter(function (r) { return r.include.checked && r.chosen && !r.sent; });
    uploadButton.disabled = busy || !ready.length;
    uploadButton.textContent = busy ? "Uploading…" : "Upload " + ready.length + (ready.length === 1 ? " photo" : " photos");
    var matched = rows.filter(function (r) { return r.entries.length; }).length;
    var choose = rows.filter(function (r) { return r.entries.length > 1 && !r.chosen; }).length;
    if (!busy && rows.length) {
      status.textContent = matched + " of " + rows.length + " photos matched a catalog entry" +
        (choose ? "; choose the entry for " + choose + " below." : ".");
    }
  }

  function addRow(file, match) {
    var tr = el("tr");
    var row = { file: file, entries: match.entries || [], chosen: null, tr: tr, sent: false };
    var include = el("input");
    include.type = "checkbox";
    include.setAttribute("aria-label", "Upload " + file.name);
    include.addEventListener("change", refresh);
    row.include = include;
    var preview = el("img", "upload-preview");
    preview.alt = "";
    var bad = TYPES.indexOf(file.type) === -1 ? "Not a JPEG, PNG or WebP photo."
      : file.size > maxBytes ? "Larger than " + Math.round(maxBytes / (1024 * 1024)) + " MB." : null;
    if (!bad) {
      var url = URL.createObjectURL(file);
      preview.src = url;
      preview.addEventListener("load", function () { URL.revokeObjectURL(url); });
    }
    var cell = el("td");
    if (bad) {
      cell.appendChild(el("span", "error", bad));
    } else if (!row.entries.length) {
      cell.appendChild(el("span", "muted", match.key ? "No catalog entry has this number." : "The name doesn't start with an item number."));
    } else if (row.entries.length === 1) {
      row.chosen = row.entries[0];
      var link = el("a", null, label(row.chosen));
      if (isLocal(row.chosen.url)) link.href = row.chosen.url;
      link.target = "_blank";
      cell.appendChild(link);
      if (row.chosen.photo_count) cell.appendChild(el("span", "muted block", "Already has " + row.chosen.photo_count + " photo" + (row.chosen.photo_count === 1 ? "" : "s")));
    } else {
      var select = el("select");
      select.setAttribute("aria-label", "Catalog entry for " + file.name);
      select.appendChild(el("option", null, "Choose which entry…"));
      select.firstChild.value = "";
      row.entries.forEach(function (entry, i) {
        var option = el("option", null, label(entry));
        option.value = String(i);
        select.appendChild(option);
      });
      select.addEventListener("change", function () {
        row.chosen = select.value === "" ? null : row.entries[Number(select.value)];
        include.checked = Boolean(row.chosen);
        refresh();
      });
      cell.appendChild(el("span", "muted block", row.entries.length + " entries share this number:"));
      cell.appendChild(select);
    }
    include.checked = Boolean(row.chosen) && !bad;
    include.disabled = Boolean(bad) || !row.entries.length;
    var note = el("span", "upload-note block");
    var bar = el("progress", "upload-bar");
    bar.max = 1;
    bar.value = 0;
    bar.hidden = true;
    cell.appendChild(bar);
    cell.appendChild(note);
    row.note = note;
    row.bar = bar;
    var tdInclude = el("td");
    tdInclude.setAttribute("data-label", "Upload");
    tdInclude.appendChild(include);
    var tdPhoto = el("td", "thumb-cell");
    tdPhoto.appendChild(preview);
    var tdName = el("td", "primary", file.name);
    cell.setAttribute("data-label", "Catalog entry");
    [tdInclude, tdPhoto, tdName, cell].forEach(function (td) { tr.appendChild(td); });
    if (!row.chosen) tr.classList.add(row.entries.length > 1 ? "needs-choice" : "unmatched");
    body.appendChild(tr);
    rows.push(row);
  }

  async function addFiles(fileList) {
    if (busy) return;
    var files = Array.prototype.slice.call(fileList || []);
    var room = maxFiles - rows.length;
    if (files.length > room) {
      status.textContent = "Only " + maxFiles + " photos at a time; the rest were left out.";
      files = files.slice(0, Math.max(room, 0));
    }
    if (!files.length) return;
    status.textContent = "Matching " + files.length + " photo" + (files.length === 1 ? "" : "s") + "…";
    var form = new FormData();
    form.append("csrf_token", token);
    files.forEach(function (f) { form.append("name", f.name); });
    var data;
    try {
      var response = await fetch(root.dataset.matchUrl, { method: "POST", body: form, credentials: "same-origin",
        headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("HTTP " + response.status);
      data = await response.json();
    } catch (err) {
      status.textContent = "Couldn't match the photos (" + err.message + "). Reload the page and try again.";
      return;
    }
    files.forEach(function (file, i) { addRow(file, data.matches[i] || { entries: [] }); });
    table.hidden = false;
    actions.hidden = false;
    done.hidden = true;
    refresh();
  }

  input.addEventListener("change", function () { addFiles(input.files); input.value = ""; });
  drop.addEventListener("dragover", function (e) {
    if (!e.dataTransfer || Array.prototype.indexOf.call(e.dataTransfer.types, "Files") === -1) return;
    e.preventDefault();
    drop.classList.add("drop-target");
  });
  drop.addEventListener("dragleave", function (e) { if (!drop.contains(e.relatedTarget)) drop.classList.remove("drop-target"); });
  drop.addEventListener("drop", function (e) {
    if (!e.dataTransfer || !e.dataTransfer.files.length) return;
    e.preventDefault();
    drop.classList.remove("drop-target");
    addFiles(e.dataTransfer.files);
  });
  document.getElementById("bulk-clear").addEventListener("click", function () {
    if (busy) return;
    rows = [];
    while (body.firstChild) body.removeChild(body.firstChild);
    table.hidden = actions.hidden = true;
    status.textContent = "";
    done.hidden = true;
  });

  uploadButton.addEventListener("click", async function () {
    var todo = rows.filter(function (r) { return r.include.checked && r.chosen && !r.sent; });
    if (busy || !todo.length) return;
    busy = true;
    refresh();
    var uploaded = 0;
    var failed = 0;
    var entries = {};
    for (var i = 0; i < todo.length; i++) {
      var row = todo[i];
      if (!isLocal(row.chosen.url)) { row.note.textContent = "Skipped."; continue; }
      status.textContent = "Uploading " + (i + 1) + " of " + todo.length + "…";
      row.bar.hidden = false;
      row.note.textContent = "Uploading…";
      var result = await sendFile(row.chosen.url + "/photos", token, row.file, function (f) { row.bar.value = f; });
      if (result.ok) {
        uploaded++;
        row.sent = true;
        row.bar.value = 1;
        row.note.textContent = "Uploaded";
        row.include.disabled = true;
        row.tr.classList.add("uploaded");
        entries[row.chosen.id] = row.chosen;
      } else {
        failed++;
        row.bar.hidden = true;
        row.note.textContent = result.message;
        row.tr.classList.add("failed");
      }
    }
    busy = false;
    refresh();
    status.textContent = "Uploaded " + uploaded + " photo" + (uploaded === 1 ? "" : "s") + " to " +
      Object.keys(entries).length + " catalog entr" + (Object.keys(entries).length === 1 ? "y" : "ies") +
      (failed ? "; " + failed + " could not be uploaded (see the table)." : ".");
    while (done.firstChild) done.removeChild(done.firstChild);
    var list = el("ul", "links");
    Object.keys(entries).forEach(function (id) {
      var li = el("li");
      var a = el("a", null, label(entries[id]));
      if (isLocal(entries[id].url)) a.href = entries[id].url + "#photos";
      li.appendChild(a);
      list.appendChild(li);
    });
    if (list.firstChild) { done.appendChild(el("h2", null, "Updated entries")); done.appendChild(list); done.hidden = false; }
  });
}
