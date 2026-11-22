/*
 * The phone app's offline screens. Works from the copy in IndexedDB, so
 * everything here runs with or without a connection. Changes are stored
 * locally and sent by sync.mjs.
 *
 * All text is inserted with textContent (via h()), never as HTML.
 */

import * as db from "./db.mjs";
import * as m from "./model.mjs";
import { events, sync, countPending, isRunning } from "./sync.mjs";

var root = document.getElementById("app");
var pill = document.getElementById("sync-pill");
var choices = {
  categories: { building: "Building", figure: "Figure / Person", accessory: "Accessory / Decoration" },
  conditions: { mint: "Mint", excellent: "Excellent", good: "Good", fair: "Fair", poor: "Poor", damaged: "Damaged" },
};
var lastStatus = { state: "idle" };

var LABELS = {
  category: "Category", name: "Name", item_number: "Item number", series: "Series / sub-collection",
  year_introduced: "Year introduced", year_retired: "Year retired", condition_grade: "Condition",
  condition_checked_on: "Last checked", has_original_box: "Original box", has_sleeve: "Sleeve",
  condition_notes: "Condition notes", storage_location: "Location", storage_tub: "Tub", storage_tray: "Tray",
  on_display: "Currently on display", height_in: "Height", width_in: "Width", depth_in: "Depth",
  box_height_in: "Box height", box_width_in: "Box width", box_depth_in: "Box depth",
  purchase_date: "Purchase date", price_paid: "Price paid ($)", purchased_from: "Bought from",
  estimated_value: "Estimated value ($)", notes: "Notes", catalog_id: "Catalog link",
  description: "Description", quantity: "Quantity",
};
var NUMBER_FIELDS = ["year_introduced", "year_retired", "quantity", "catalog_id"];
var BOOL_FIELDS = ["has_original_box", "has_sleeve", "on_display"];

/* ---- tiny DOM helper ---------------------------------------------------- */

function h(tag, attrs) {
  var el = document.createElement(tag);
  var a = attrs || {};
  Object.keys(a).forEach(function (k) {
    var v = a[k];
    if (v === null || v === undefined || v === false) return;
    if (k.indexOf("on") === 0 && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = Boolean(v);
    else el.setAttribute(k, v === true ? "" : String(v));
  });
  for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
  return el;
}

function append(el, child) {
  if (child === null || child === undefined || child === false) return;
  if (Array.isArray(child)) { child.forEach(function (c) { append(el, c); }); return; }
  el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
}

var shownHash = null;
function show(view) {
  var sameView = shownHash === location.hash;
  var y = window.scrollY;
  while (root.firstChild) root.removeChild(root.firstChild);
  append(root, view);
  window.scrollTo(0, sameView ? y : 0); // a background refresh keeps your place
  shownHash = location.hash;
}

function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  var b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 15) | 64; b[8] = (b[8] & 63) | 128;
  var hex = Array.prototype.map.call(b, function (x) { return (x + 256).toString(16).slice(1); }).join("");
  return hex.slice(0, 8) + "-" + hex.slice(8, 12) + "-" + hex.slice(12, 16) + "-" + hex.slice(16, 20) + "-" + hex.slice(20);
}

/* ---- formatting --------------------------------------------------------- */

function money(v) {
  if (v === null || v === undefined || v === "") return "—";
  var n = Number(v);
  return isNaN(n) ? String(v) : "$" + n.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function storageText(v) {
  var parts = [];
  if (v.storage_location) parts.push(v.storage_location);
  if (v.storage_tub) parts.push("Tub " + v.storage_tub);
  if (v.storage_tray) parts.push("Tray " + v.storage_tray);
  return parts.length ? parts.join(" › ") : "Not assigned";
}

function dims(a, b, c) {
  var parts = [];
  if (a) parts.push(a + "″ H");
  if (b) parts.push(b + "″ W");
  if (c) parts.push(c + "″ D");
  return parts.join(" × ");
}

function displayValue(field, v) {
  if (v === null || v === undefined || v === "") return "(blank)";
  if (BOOL_FIELDS.indexOf(field) !== -1) return v ? "Yes" : "No";
  if (field === "category") return choices.categories[v] || v;
  if (field === "condition_grade") return choices.conditions[v] || v;
  return String(v);
}

function badge(grade) {
  return grade ? h("span", { class: "badge cond-" + grade }, choices.conditions[grade] || grade)
    : h("span", { class: "muted" }, "Not graded");
}

function syncBadge(rec) {
  if (m.needsAttention(rec)) return h("span", { class: "badge badge-warn" }, "Needs attention");
  if (m.isPending(rec)) return h("span", { class: "badge badge-pending" }, "Not synced yet");
  return null;
}

/* ---- sync status pill --------------------------------------------------- */

function renderPill(status) {
  lastStatus = status || lastStatus;
  var s = lastStatus;
  var waiting = s.pending ? " · " + s.pending + " waiting" : "";
  var text, cls = "sync-pill";
  if (s.state === "syncing") { text = "Syncing…"; cls += " busy"; }
  else if (!navigator.onLine || s.state === "offline") { text = "Offline" + waiting; cls += " offline"; }
  else if (s.state === "login") { text = "Log in to sync" + waiting; cls += " warn"; }
  else if (s.state === "error") { text = "Sync problem — tap to retry" + waiting; cls += " warn"; }
  else { text = s.pending || s.attention ? "Sync now" + waiting : "All synced"; }
  while (pill.firstChild) pill.removeChild(pill.firstChild);
  if (s.state === "login") {
    pill.appendChild(h("a", { class: cls, href: "/login?next=/app" }, text));
  } else {
    pill.appendChild(h("button", { type: "button", class: cls, onclick: function () { runSync(); } }, text));
  }
  if (s.attention) {
    pill.appendChild(h("a", { class: "sync-pill warn", href: "#/attention" }, s.attention + " need attention"));
  }
}

events.addEventListener("status", function (e) {
  renderPill(e.detail);
  if (e.detail.state === "idle" || e.detail.state === "offline") route(); // show fresh data
});

var syncTimer = null;
function runSync() {
  if (!navigator.onLine) { countPending().then(function (c) { renderPill(Object.assign({ state: "offline" }, c)); }); return; }
  return sync();
}
function syncSoon() {
  if (navigator.onLine) renderPill(Object.assign({}, lastStatus, { state: "syncing" }));
  else refreshPill();
  clearTimeout(syncTimer);
  syncTimer = setTimeout(runSync, 800);
}
function refreshPill() {
  return countPending().then(function (c) {
    var base = Object.assign({}, lastStatus);
    if (!navigator.onLine) base.state = "offline";
    renderPill(Object.assign(base, c));
  });
}

/* ---- list --------------------------------------------------------------- */

async function listView(params) {
  var items = (await db.all("items")).sort(function (a, b) {
    return String(a.local.name || "").localeCompare(String(b.local.name || ""));
  });
  var q = (params.get("q") || "").toLowerCase();
  var cat = params.get("category") || "";
  var shown = items.filter(function (r) {
    var v = r.local;
    if (cat && v.category !== cat) return false;
    if (!q) return true;
    return [v.name, v.item_number, v.series, v.storage_location, v.storage_tub, v.storage_tray]
      .some(function (x) { return x && String(x).toLowerCase().indexOf(q) !== -1; });
  });
  var lastSync = await db.getMeta("lastSync", null);

  var search = h("input", { type: "search", value: params.get("q") || "", placeholder: "Name, number, tub…",
    "aria-label": "Search your pieces" });
  var catSel = h("select", { "aria-label": "Category" }, h("option", { value: "" }, "All categories"),
    Object.keys(choices.categories).map(function (k) {
      return h("option", { value: k, selected: k === cat }, choices.categories[k]);
    }));
  function apply() {
    var p = new URLSearchParams();
    if (search.value.trim()) p.set("q", search.value.trim());
    if (catSel.value) p.set("category", catSel.value);
    location.hash = "#/" + (p.toString() ? "?" + p.toString() : "");
  }
  search.addEventListener("change", apply);
  catSel.addEventListener("change", apply);

  return [
    h("div", { class: "title-row" }, h("h1", null, "My collection ", h("span", { class: "muted" }, "(" + items.length + ")")),
      h("a", { class: "button", href: "#/new" }, "Add piece")),
    h("div", { class: "card offline-filters" }, search, catSel),
    !items.length && !lastSync ? h("p", { class: "card" },
      "Nothing is stored on this phone yet. Open the app once while you're online so it can copy your collection.") : null,
    !shown.length && items.length ? h("p", { class: "card" }, "No pieces match.") : null,
    h("ul", { class: "offline-list" }, shown.map(function (r) {
      var v = r.local;
      return h("li", null, h("a", { class: "card offline-card", href: "#/piece/" + encodeURIComponent(r.key) },
        h("strong", null, v.name || "(no name)"), " ", syncBadge(r),
        h("span", { class: "muted block" }, [v.item_number, choices.categories[v.category]].filter(Boolean).join(" · ")),
        h("span", { class: "block" }, badge(v.condition_grade), " ", storageText(v))));
    })),
    lastSync ? h("p", { class: "muted" }, "Last synced " + new Date(lastSync).toLocaleString()) : null,
  ];
}

/* ---- piece -------------------------------------------------------------- */

async function pieceView(key) {
  var rec = await db.get("items", key);
  if (!rec) return notFound();
  var v = rec.local;
  var extras = await db.byItem("extras", key);
  var pending = await db.byItem("photos", key);
  var catalog = v.catalog_id ? await db.get("catalog", Number(v.catalog_id)) : null;

  var rows = [
    ["Item number", v.item_number || "—"],
    catalog ? ["Catalog listing", (catalog.item_number || "No number") + " · " + catalog.name +
      (catalog.srp_usd ? " · original retail $" + catalog.srp_usd : "")] : null,
    ["Series", v.series || "—"],
    ["Introduced / retired", (v.year_introduced || "?") + " – " + (v.year_retired || "?")],
    ["Condition", badge(v.condition_grade), v.condition_checked_on ? " checked " + v.condition_checked_on : ""],
    v.condition_notes ? ["Condition notes", v.condition_notes] : null,
    ["Box / sleeve", (v.has_original_box ? "Original box" : "No box") + " · " + (v.has_sleeve ? "Sleeve" : "No sleeve")],
    (v.height_in || v.width_in || v.depth_in) ? ["Size", dims(v.height_in, v.width_in, v.depth_in)] : null,
    (v.box_height_in || v.box_width_in || v.box_depth_in) ? ["Box size", dims(v.box_height_in, v.box_width_in, v.box_depth_in)] : null,
    ["Storage", storageText(v), v.on_display ? " · On display" : ""],
    ["Purchased", v.purchase_date || "—", v.purchased_from ? " from " + v.purchased_from : ""],
    ["Price paid", money(v.price_paid)],
    ["Estimated value", money(v.estimated_value)],
    v.notes ? ["Notes", v.notes] : null,
  ].filter(Boolean);

  var fileInput = h("input", { type: "file", accept: "image/jpeg,image/png,image/webp", multiple: true,
    "aria-label": "Choose photos", onchange: function () { addPhotos(key, fileInput.files); } });
  var camInput = h("input", { type: "file", accept: "image/jpeg,image/png,image/webp", capture: "environment",
    class: "visually-hidden-file", id: "cam", onchange: function () { addPhotos(key, camInput.files); } });

  return [
    h("div", { class: "title-row" },
      h("h1", null, v.name || "(no name)", " ", h("span", { class: "muted" }, "· " + (choices.categories[v.category] || ""))),
      h("div", { class: "actions" }, h("a", { class: "button", href: "#/edit/" + encodeURIComponent(key) }, "Edit"))),
    statusBanner(rec, "piece"),
    h("section", { class: "card" }, h("dl", { class: "details" }, rows.map(function (r) {
      return [h("dt", null, r[0]), h("dd", null, r.slice(1))];
    }))),
    h("section", { class: "card" },
      h("h2", null, "Extras"),
      extras.length ? h("ul", { class: "links" }, extras.map(function (e) {
        return h("li", null, h("a", { href: "#/piece/" + encodeURIComponent(key) + "/extra/" + encodeURIComponent(e.key) },
          e.local.description), " ", h("span", { class: "muted" }, "×" + (e.local.quantity || 1)), " ",
          badge(e.local.condition_grade), " ", syncBadge(e));
      })) : h("p", { class: "muted" }, "No extras recorded."),
      h("a", { class: "button", href: "#/piece/" + encodeURIComponent(key) + "/extra/new" }, "Add extra")),
    h("section", { class: "card" },
      h("h2", null, "Photos"),
      h("div", { class: "photos" },
        (rec.photos || []).map(function (p) {
          return h("figure", null, h("img", { src: "/photos/" + Number(p.id) + "/thumb", alt: "Photo of " + (v.name || "piece"),
            loading: "lazy", onerror: function (e) { e.target.replaceWith(h("span", { class: "photo-missing" }, "Shows when online")); } }));
        }),
        pending.map(function (p) {
          var url = URL.createObjectURL(p.blob);
          return h("figure", null, h("img", { src: url, alt: "Photo waiting to upload", onload: function () { URL.revokeObjectURL(url); } }),
            h("figcaption", { class: p.error ? "error" : "muted" }, p.error ? p.error.message : "Waiting to upload"),
            h("button", { type: "button", class: "link danger", onclick: async function () {
              await db.del("photos", p.key); await refreshPill(); route();
            } }, "Remove"));
        })),
      h("div", { class: "photo-pickers" }, h("label", { class: "button", for: "cam" }, "Take a photo"), camInput, fileInput)),
    rec.id && navigator.onLine ? h("p", null, h("a", { href: "/items/" + Number(rec.id) }, "Open on the full site")) : null,
    h("p", null, h("a", { href: "#/" }, "Back to my collection")),
  ];
}

async function addPhotos(itemKey, files) {
  var list = Array.prototype.slice.call(files || [], 0, 20);
  for (var i = 0; i < list.length; i++) {
    var f = list[i];
    var id = uuid();
    try {
      await db.put("photos", { key: "p-" + id, client_uuid: id, itemKey: itemKey, blob: f, name: f.name || "photo.jpg",
        error: null, created: Date.now() });
    } catch (err) {
      window.alert("This phone is out of storage space for the app, so " + (list.length - i) +
        " photo(s) weren't saved. Sync or remove waiting photos, then try again.");
      break;
    }
  }
  await countPending();
  route();
  syncSoon();
}

function statusBanner(rec, noun) {
  if (rec.conflict && rec.conflict.length) {
    return h("p", { class: "flash flash-error" }, "This " + noun + " was also changed on another device. ",
      h("a", { href: "#/attention" }, "Choose which values to keep"));
  }
  if (rec.error) {
    var detail = rec.error.errors ? Object.keys(rec.error.errors).map(function (f) {
      return (LABELS[f] || f) + ": " + rec.error.errors[f];
    }).join(" ") : "";
    return h("p", { class: "flash flash-error" }, rec.error.message, " ", detail);
  }
  if (m.isPending(rec)) return h("p", { class: "flash flash-pending" }, "Saved on this phone. It will sync when you're online.");
  return null;
}

/* ---- piece form ----------------------------------------------------------- */

var FORM = [
  ["Catalog", [["category", "select-cat", true], ["name", "text", true], ["item_number", "text"], ["series", "text"],
    ["year_introduced", "year"], ["year_retired", "year"]]],
  ["Current condition", [["condition_grade", "select-cond"], ["condition_checked_on", "date"],
    ["has_original_box", "check"], ["has_sleeve", "check"], ["condition_notes", "textarea"]]],
  ["Storage", [["storage_location", "text", false, "dl-location"], ["storage_tub", "text", false, "dl-tub"],
    ["storage_tray", "text", false, "dl-tray"], ["on_display", "check"]]],
  ["Measurements (inches)", [["height_in", "text"], ["width_in", "text"], ["depth_in", "text"],
    ["box_height_in", "text"], ["box_width_in", "text"], ["box_depth_in", "text"]]],
  ["Purchase and value", [["purchase_date", "date"], ["price_paid", "decimal"], ["purchased_from", "text"],
    ["estimated_value", "decimal"]]],
  ["Notes", [["notes", "textarea"]]],
];

function field(spec, values, errors) {
  var name = spec[0], kind = spec[1], required = spec[2], list = spec[3];
  var v = values[name];
  var id = "f-" + name;
  var input;
  if (kind === "check") {
    return h("label", { class: "check" }, h("input", { type: "checkbox", id: id, name: name, checked: Boolean(v) }), " ", LABELS[name]);
  }
  if (kind === "select-cat" || kind === "select-cond") {
    var opts = kind === "select-cat" ? choices.categories : choices.conditions;
    input = h("select", { id: id, name: name, required: required },
      h("option", { value: "" }, kind === "select-cat" ? "Choose…" : "Not graded"),
      Object.keys(opts).map(function (k) { return h("option", { value: k, selected: v === k }, opts[k]); }));
  } else if (kind === "textarea") {
    input = h("textarea", { id: id, name: name, rows: 3 });
    input.value = v == null ? "" : String(v);
  } else {
    input = h("input", { id: id, name: name, required: required, list: list,
      type: kind === "date" ? "date" : "text",
      inputmode: kind === "year" ? "numeric" : kind === "decimal" ? "decimal" : null,
      value: v == null ? "" : String(v) });
  }
  var err = errors && errors[name];
  return h("div", { class: kind === "textarea" ? "full" : null },
    h("label", { for: id }, LABELS[name], required ? h("span", { class: "req" }, " *") : null),
    input, err ? h("span", { class: "error" }, err) : null);
}

function readForm(form, fields) {
  var out = {};
  fields.forEach(function (name) {
    var el = form.elements[name];
    if (!el) return;
    if (BOOL_FIELDS.indexOf(name) !== -1) { out[name] = el.checked; return; }
    var raw = String(el.value || "").trim();
    if (raw === "") { out[name] = null; return; }
    if (NUMBER_FIELDS.indexOf(name) !== -1 && /^\d+$/.test(raw)) { out[name] = Number(raw); return; }
    out[name] = raw;
  });
  return out;
}

function validate(values, fields) {
  var errors = {};
  if (fields.indexOf("name") !== -1 && !values.name) errors.name = "Name is required.";
  if (fields.indexOf("category") !== -1 && !values.category) errors.category = "Choose a category.";
  ["year_introduced", "year_retired"].forEach(function (f) {
    if (values[f] != null && !/^\d{4}$/.test(String(values[f]))) errors[f] = "Enter a four-digit year.";
  });
  if (fields.indexOf("description") !== -1 && !values.description) errors.description = "Describe the extra piece.";
  return errors;
}

async function suggestions() {
  var items = await db.all("items");
  function distinct(f) {
    var s = {};
    items.forEach(function (r) { if (r.local[f]) s[r.local[f]] = true; });
    return Object.keys(s).sort();
  }
  return ["storage_location", "storage_tub", "storage_tray"].map(function (f) {
    return h("datalist", { id: "dl-" + f.replace("storage_", "") }, distinct(f).map(function (x) { return h("option", { value: x }); }));
  });
}

async function editView(key) {
  var rec = key ? await db.get("items", key) : null;
  if (key && !rec) return notFound();
  var values = rec ? Object.assign({}, rec.local) : { catalog_id: null };
  var catalog = await db.all("catalog");
  var form = h("form", { class: "card", novalidate: true });
  var lookupResults = h("ul", { class: "combo-results", role: "listbox", hidden: true });
  var linked = h("p", { class: "catalog-linked", hidden: !values.catalog_id });

  function setLinked(entry) {
    while (linked.firstChild) linked.removeChild(linked.firstChild);
    if (!entry) { linked.hidden = true; return; }
    linked.hidden = false;
    append(linked, ["Linked to catalog: " + (entry.item_number || "no number") + " · " + entry.name + " ",
      h("button", { type: "button", class: "link", onclick: function () { values.catalog_id = null; setLinked(null); } }, "Remove link")]);
  }
  if (values.catalog_id) setLinked(catalog.find(function (c) { return c.id === Number(values.catalog_id); }));

  var lookup = h("input", { type: "search", placeholder: "Item number, e.g. 56.65005 or 65005", "aria-label": "Look up in catalog",
    autocomplete: "off", oninput: function () {
      var hits = m.searchCatalog(catalog, lookup.value, 15);
      while (lookupResults.firstChild) lookupResults.removeChild(lookupResults.firstChild);
      hits.forEach(function (e) {
        lookupResults.appendChild(h("li", { role: "option", onclick: function () {
          var fill = m.catalogFill(e);
          Object.keys(fill).forEach(function (f) {
            values[f] = fill[f];
            var el = form.elements[f];
            if (el) el.value = fill[f] == null ? "" : String(fill[f]);
          });
          setLinked(e);
          lookup.value = "";
          lookupResults.hidden = true;
        } }, h("span", { class: "combo-title" }, h("span", { class: "combo-number" }, e.item_number || "No number"), " · ",
          h("strong", null, e.name), " · " + (e.year_issued || "?") + "–" + (e.is_current ? "Current" : e.year_retired || "?")),
          h("span", { class: "combo-detail" }, (e.list === "accessories" ? "Accessories list" : "Lighted list") +
            (e.parent_name ? " · part of “" + e.parent_name + "”" : ""))));
      });
      lookupResults.hidden = !hits.length;
    },
    onkeydown: function (e) { if (e.key === "Enter") e.preventDefault(); } });

  function build(errors) {
    while (form.firstChild) form.removeChild(form.firstChild);
    append(form, [
      catalog.length ? h("section", { class: "catalog-lookup" }, h("label", null, "Look up in catalog"),
        h("div", { class: "combo" }, lookup, lookupResults)) : null,
      linked,
      FORM.map(function (group) {
        return h("fieldset", null, h("legend", null, group[0]),
          h("div", { class: "grid" }, group[1].map(function (spec) { return field(spec, values, errors); })));
      }),
      h("button", { type: "submit" }, rec ? "Save changes" : "Add piece"), " ",
      h("a", { href: rec ? "#/piece/" + encodeURIComponent(key) : "#/" }, "Cancel"),
    ]);
  }
  build({});
  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    var fields = m.ITEM_FIELDS.filter(function (f) { return f !== "catalog_id"; });
    var input = readForm(form, fields);
    input.catalog_id = values.catalog_id == null ? null : Number(values.catalog_id);
    var errors = validate(input, fields);
    if (Object.keys(errors).length) {
      Object.assign(values, input);
      build(errors);
      return;
    }
    var saved = rec ? m.editRecord(rec, input, m.ITEM_FIELDS)
      : Object.assign(m.newLocalRecord("", uuid(), input, m.ITEM_FIELDS), { photos: [] });
    if (rec) saved.photos = rec.photos || [];
    await db.put("items", saved);
    await countPending();
    location.hash = "#/piece/" + encodeURIComponent(saved.key);
    syncSoon();
  });
  return [h("h1", null, rec ? "Edit “" + (rec.local.name || "") + "”" : "Add a piece"), form, await suggestions()];
}

/* ---- extras --------------------------------------------------------------- */

async function extraView(itemKey, extraKey) {
  var item = await db.get("items", itemKey);
  if (!item) return notFound();
  var rec = extraKey === "new" ? null : await db.get("extras", extraKey);
  if (extraKey !== "new" && !rec) return notFound();
  var values = rec ? Object.assign({}, rec.local) : { quantity: 1 };
  var form = h("form", { class: "card", novalidate: true });
  function build(errors) {
    while (form.firstChild) form.removeChild(form.firstChild);
    append(form, [
      rec ? statusBanner(rec, "extra") : null,
      field(["description", "text", true], values, errors),
      field(["quantity", "year"], values, errors),
      field(["condition_grade", "select-cond"], values, errors),
      field(["notes", "text"], values, errors),
      h("button", { type: "submit" }, rec ? "Save" : "Add extra"), " ",
      h("a", { href: "#/piece/" + encodeURIComponent(itemKey) }, "Cancel"),
    ]);
  }
  build({});
  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    var input = readForm(form, m.EXTRA_FIELDS);
    if (input.quantity == null) input.quantity = 1;
    var errors = validate(input, m.EXTRA_FIELDS);
    if (input.quantity !== null && !(Number(input.quantity) >= 1 && Number(input.quantity) <= 999)) {
      errors.quantity = "Quantity must be a whole number from 1 to 999.";
    }
    if (Object.keys(errors).length) { Object.assign(values, input); build(errors); return; }
    var saved = rec ? m.editRecord(rec, input, m.EXTRA_FIELDS)
      : m.newLocalRecord("e", uuid(), input, m.EXTRA_FIELDS, { itemKey: itemKey });
    await db.put("extras", saved);
    await countPending();
    location.hash = "#/piece/" + encodeURIComponent(itemKey);
    syncSoon();
  });
  return [h("h1", null, (rec ? "Edit extra for “" : "Add an extra to “") + (item.local.name || "") + "”"), form];
}

/* ---- needs attention ------------------------------------------------------ */

async function attentionView() {
  var items = (await db.all("items")).filter(m.needsAttention);
  var extras = (await db.all("extras")).filter(m.needsAttention);
  var photos = (await db.all("photos")).filter(function (p) { return p.error; });
  var itemNames = {};
  (await db.all("items")).forEach(function (r) { itemNames[r.key] = r.local.name; });

  function conflictForm(rec, store, title) {
    var form = h("form", { class: "card" },
      h("h2", null, title),
      h("p", { class: "muted" }, "Changed on this phone and somewhere else. Pick the value to keep for each:"),
      rec.conflict.map(function (c) {
        var name = "c-" + rec.key + "-" + c.field;
        return h("fieldset", { class: "conflict" }, h("legend", null, LABELS[c.field] || c.field),
          h("label", { class: "check" }, h("input", { type: "radio", name: name, value: "mine", checked: true }),
            " This phone: ", h("strong", null, displayValue(c.field, c.mine))),
          h("label", { class: "check" }, h("input", { type: "radio", name: name, value: "theirs" }),
            " Other device: ", h("strong", null, displayValue(c.field, c.theirs))));
      }),
      h("button", { type: "submit" }, "Save my choices"), " ",
      discardButton(rec, store, store === "items" ? m.ITEM_FIELDS : m.EXTRA_FIELDS));
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      var picks = {};
      rec.conflict.forEach(function (c) {
        picks[c.field] = form.elements["c-" + rec.key + "-" + c.field].value;
      });
      var fresh = await db.get(store, rec.key);
      await db.put(store, m.resolveConflict(fresh, picks));
      await countPending();
      syncSoon();
      route();
    });
    return form;
  }

  function discardButton(rec, store, fields) {
    return h("button", { type: "button", class: "danger", onclick: async function () {
      var what = rec.id == null ? "This deletes it from the phone; it was never saved to the server."
        : "The phone goes back to the server's version of it.";
      if (!window.confirm("Discard your unsynced changes? " + what)) return;
      var fresh = await db.get(store, rec.key);
      var kept = fresh && m.discardChanges(fresh, fields);
      // Fetch the full server copy next time even if nothing changed there, so a
      // piece that was deleted elsewhere disappears from the phone too.
      await db.setMeta("snapshotEtag", null);
      if (kept) await db.put(store, kept);
      else if (fresh) {
        await db.del(store, rec.key);
        if (store === "items") { // its unsent extras and photos go with it
          var ex = await db.byItem("extras", rec.key);
          await db.apply("extras", [], ex.map(function (e) { return e.key; }));
          var ph = await db.byItem("photos", rec.key);
          await db.apply("photos", [], ph.map(function (p) { return p.key; }));
        }
      }
      await refreshPill();
      syncSoon();
      route();
    } }, "Discard my changes");
  }

  function errorCard(rec, title, href, store, fields) {
    return h("div", { class: "card" }, h("h2", null, title), statusBanner(rec, "item"),
      h("a", { class: "button", href: href }, "Edit to fix"), " ", discardButton(rec, store, fields));
  }

  var parts = [];
  items.forEach(function (r) {
    var title = r.local.name || "(no name)";
    parts.push(r.conflict ? conflictForm(r, "items", title)
      : errorCard(r, title, "#/edit/" + encodeURIComponent(r.key), "items", m.ITEM_FIELDS));
  });
  extras.forEach(function (r) {
    var title = "Extra “" + r.local.description + "” on " + (itemNames[r.itemKey] || "a piece");
    parts.push(r.conflict ? conflictForm(r, "extras", title)
      : errorCard(r, title, "#/piece/" + encodeURIComponent(r.itemKey) + "/extra/" + encodeURIComponent(r.key),
        "extras", m.EXTRA_FIELDS));
  });
  photos.forEach(function (p) {
    parts.push(h("div", { class: "card" }, h("h2", null, "Photo for " + (itemNames[p.itemKey] || "a piece")),
      h("p", { class: "flash flash-error" }, p.error.message),
      h("button", { type: "button", class: "danger", onclick: async function () {
        await db.del("photos", p.key); await refreshPill(); route();
      } }, "Remove this photo")));
  });
  return [h("h1", null, "Needs attention"),
    parts.length ? parts : h("p", { class: "card" }, "Nothing needs attention. Everything on this phone can sync."),
    h("p", null, h("a", { href: "#/" }, "Back to my collection"))];
}

function notFound() {
  return h("section", { class: "card" }, h("h1", null, "Not found"),
    h("p", null, "That isn't on this phone (it may have been deleted elsewhere)."), h("a", { href: "#/" }, "Back"));
}

/* ---- routing -------------------------------------------------------------- */

async function route() {
  var hash = location.hash.replace(/^#/, "") || "/";
  var qIndex = hash.indexOf("?");
  var path = qIndex === -1 ? hash : hash.slice(0, qIndex);
  var params = new URLSearchParams(qIndex === -1 ? "" : hash.slice(qIndex + 1));
  var parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  var view;
  try {
    if (!parts.length) view = await listView(params);
    else if (parts[0] === "new") view = await editView(null);
    else if (parts[0] === "edit" && parts[1]) view = await editView(parts[1]);
    else if (parts[0] === "piece" && parts[2] === "extra" && parts[3]) view = await extraView(parts[1], parts[3]);
    else if (parts[0] === "piece" && parts[1]) view = await pieceView(parts[1]);
    else if (parts[0] === "attention") view = await attentionView();
    else view = notFound();
  } catch (err) {
    if (window.console) console.error(err);
    view = h("section", { class: "card" }, h("h1", null, "Something went wrong"),
      h("p", null, "This phone's storage couldn't be read. Your saved changes are still there; try reloading."));
  }
  // Don't replace a form the person is typing in when a background sync finishes.
  var active = document.activeElement;
  if (active && root.contains(active) && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName) &&
      /^(new|edit|piece\/.+\/extra)/.test(path.replace(/^\//, ""))) return;
  show(view);
}

async function start() {
  try { localStorage.setItem("dickens-offline-enabled", "1"); } catch (e) { /* private mode */ }
  var session = await db.getMeta("session", null);
  if (session && session.choices) choices = session.choices;
  // Until the first sync of this visit finishes, don't claim everything is synced.
  renderPill(Object.assign({ state: navigator.onLine ? "syncing" : "offline" }, await countPending()));
  window.addEventListener("hashchange", route);
  window.addEventListener("online", function () { runSync(); });
  window.addEventListener("offline", function () { countPending().then(function (c) { renderPill(Object.assign({ state: "offline" }, c)); }); });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible" && !isRunning()) runSync();
  });
  setInterval(function () { if (document.visibilityState === "visible") runSync(); }, 5 * 60 * 1000);
  await route();
  runSync();
}

start();
