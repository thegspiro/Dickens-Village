/*
 * Offline record model: pure functions, no browser APIs, tested with node --test.
 *
 * A record is one piece (or one extra) as the phone knows it:
 *   key         stable local key ("s-12" for pieces first seen from the server,
 *               "c-<uuid>" for pieces created on the phone)
 *   id          server id, or null until the server has it
 *   client_uuid the phone-assigned id sent when creating it (idempotent retries)
 *   server      the last values received from the server (null if never sent)
 *   local       the values shown and edited on the phone
 *   dirty       { field: valueTheEditStartedFrom } for fields edited on the
 *               phone and not yet accepted by the server
 *   conflict    [{field, base, mine, theirs}] when the server refused a change
 *   error       { message, errors } when the server rejected the values
 *
 * Only fields in `dirty` are ever sent. Every other field follows the server,
 * so a change made elsewhere to a field the phone didn't touch is never undone.
 */

export const ITEM_FIELDS = [
  "category", "name", "item_number", "series", "purchased_from", "storage_location", "storage_tub",
  "storage_tray", "condition_notes", "notes", "year_introduced", "year_retired", "purchase_date",
  "price_paid", "estimated_value", "condition_grade", "condition_checked_on", "has_original_box",
  "has_sleeve", "on_display", "height_in", "width_in", "depth_in", "box_height_in", "box_width_in",
  "box_depth_in", "catalog_id",
];

export const EXTRA_FIELDS = ["description", "quantity", "condition_grade", "notes"];

/* "" and null both mean empty, matching the server's comparison. */
export function same(a, b) {
  return (a === "" ? null : a) === (b === "" ? null : b);
}

export function pick(values, fields) {
  var out = {};
  fields.forEach(function (f) { if (values && Object.prototype.hasOwnProperty.call(values, f)) out[f] = values[f]; });
  return out;
}

export function isPending(rec) {
  return rec.id == null || Object.keys(rec.dirty || {}).length > 0;
}

export function needsAttention(rec) {
  return Boolean((rec.conflict && rec.conflict.length) || rec.error);
}

export function newLocalRecord(prefix, uuid, values, fields, extra) {
  var local = {};
  fields.forEach(function (f) { local[f] = Object.prototype.hasOwnProperty.call(values, f) ? values[f] : null; });
  return Object.assign({
    key: prefix + "c-" + uuid, id: null, client_uuid: uuid, server: null, local: local,
    dirty: {}, conflict: null, error: null,
  }, extra || {});
}

/* Apply the person's edits. Returns a new record; the input is not modified. */
export function editRecord(rec, values, fields) {
  var next = clone(rec);
  fields.forEach(function (f) {
    if (!Object.prototype.hasOwnProperty.call(values, f)) return;
    if (same(next.local[f], values[f])) return;
    if (next.server && !Object.prototype.hasOwnProperty.call(next.dirty, f)) {
      next.dirty[f] = next.server[f] === undefined ? null : next.server[f];
    }
    next.local[f] = values[f];
  });
  next.error = null; // re-validated on the next sync
  return next;
}

/* What to send for a record the server already has. */
export function patchPayload(rec) {
  var fields = Object.keys(rec.dirty);
  return { base: Object.assign({}, rec.dirty), changes: pick(rec.local, fields) };
}

/* Fold fresh server values into a record, keeping the phone's unsent edits. */
export function withServer(rec, serverValues, fields) {
  var next = clone(rec);
  next.server = pick(serverValues, fields.concat(["version"]));
  next.id = serverValues.id;
  if (serverValues.client_uuid && !next.client_uuid) next.client_uuid = serverValues.client_uuid;
  var local = pick(serverValues, fields);
  Object.keys(next.dirty).forEach(function (f) { local[f] = rec.local[f]; });
  next.local = local;
  return next;
}

/* The server accepted `sent` (the changes object of a patch). */
export function afterPatch(rec, sent, serverValues, fields) {
  var next = clone(rec);
  Object.keys(sent).forEach(function (f) {
    // Only clear fields that weren't edited again while the request was in flight.
    if (same(next.local[f], sent[f])) delete next.dirty[f];
  });
  next.conflict = null;
  next.error = null;
  return withServer(next, serverValues, fields);
}

/* The server created a record the phone made. `sent` is the fields object. */
export function afterCreate(rec, sent, serverValues, fields) {
  var next = clone(rec);
  // Anything edited again while the create was in flight becomes an edit on top.
  next.dirty = {};
  fields.forEach(function (f) {
    if (!same(next.local[f], sent[f])) next.dirty[f] = serverValues[f] === undefined ? null : serverValues[f];
  });
  next.error = null;
  return withServer(next, serverValues, fields);
}

export function afterConflict(rec, conflicts, serverValues, fields) {
  var next = withServer(rec, serverValues, fields);
  next.conflict = conflicts;
  return next;
}

/*
 * The person chose, per conflicting field, "mine" or "theirs".
 * theirs: take the server's value and drop the edit.
 * mine:   keep the phone's value and re-base it on the server's current value,
 *         so the next sync applies it.
 */
export function resolveConflict(rec, choices) {
  var next = clone(rec);
  (next.conflict || []).forEach(function (c) {
    if (choices[c.field] === "theirs") {
      next.local[c.field] = c.theirs;
      delete next.dirty[c.field];
    } else {
      next.dirty[c.field] = c.theirs;
    }
  });
  next.conflict = null;
  return next;
}

/*
 * Merge a full server snapshot into the phone's records.
 * Returns { put: [records to store], remove: [keys to delete] }.
 *   - records the server no longer has are removed, unless the phone still
 *     has unsent edits for them (then they're flagged instead)
 *   - pieces created on the phone and not yet uploaded are left alone
 */
export function mergeSnapshot(existing, serverRows, fields, keyPrefix, extraFor) {
  var byId = {};
  var byUuid = {};
  existing.forEach(function (rec) {
    if (rec.id != null) byId[rec.id] = rec;
    if (rec.client_uuid) byUuid[rec.client_uuid] = rec;
  });
  var put = [];
  var seen = {};
  serverRows.forEach(function (row) {
    var rec = byId[row.id] || (row.client_uuid && byUuid[row.client_uuid]);
    var extra = extraFor ? extraFor(row) : {};
    if (!rec) {
      rec = Object.assign({ key: keyPrefix + "s-" + row.id, id: row.id, client_uuid: row.client_uuid || null,
        server: null, local: {}, dirty: {}, conflict: null, error: null }, extra);
    } else {
      rec = Object.assign(clone(rec), extra);
      if (rec.id == null) rec.dirty = {}; // its create succeeded but the reply was lost
    }
    put.push(withServer(rec, row, fields));
    seen[rec.key] = true;
  });
  var remove = [];
  existing.forEach(function (rec) {
    if (seen[rec.key] || rec.id == null) return;
    if (Object.keys(rec.dirty).length) {
      var flagged = clone(rec);
      flagged.error = { message: "This was deleted on another device. Your unsynced changes to it can't be saved." };
      put.push(flagged);
    } else {
      remove.push(rec.key);
    }
  });
  return { put: put, remove: remove };
}

/* Catalog search on the phone, ranked like the server: item numbers first. */
export function searchCatalog(entries, query, limit) {
  var q = String(query || "").trim().toLowerCase().replace(/\s+/g, " ");
  if (q.length < 2) return [];
  var digits = q.replace(/\D/g, "");
  var hits = [];
  entries.forEach(function (e) {
    var num = String(e.item_number || "").toLowerCase();
    var name = String(e.name || "").toLowerCase();
    var rank = -1;
    if (num === q) rank = 0;
    else if (num.indexOf(q) === 0) rank = 1;
    else if (num.indexOf(q) !== -1 || (digits.length >= 2 && num.replace(/\./g, "").indexOf(digits) !== -1)) rank = 2;
    else if (name.indexOf(q) === 0) rank = 3;
    else if (name.indexOf(q) !== -1) rank = 4;
    if (rank >= 0) hits.push({ rank: rank, piece: e.parent_name ? 1 : 0, num: num, e: e });
  });
  hits.sort(function (a, b) {
    return a.rank - b.rank || a.piece - b.piece || (a.num < b.num ? -1 : a.num > b.num ? 1 : 0);
  });
  return hits.slice(0, limit || 15).map(function (h) { return h.e; });
}

/* Values a catalog entry fills into the piece form (same as the website). */
export function catalogFill(entry) {
  var v = { catalog_id: entry.id, name: entry.name, item_number: entry.item_number || null };
  if (entry.category) v.category = entry.category;
  if (entry.series) v.series = entry.series;
  if (entry.year_issued) v.year_introduced = entry.year_issued;
  if (entry.year_retired) v.year_retired = entry.year_retired;
  return v;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}
