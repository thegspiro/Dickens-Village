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
 *   creates     [fields, ...] sent in create requests that got no reply yet
 *   inflight    { version, attempts: [changes, ...] } for patches sent without a
 *               reply yet, all starting from server version `version`, so a 409
 *               caused by this phone's own earlier write can be recognised
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

/*
 * Looser comparison for values the server may have rewritten in its canonical
 * form: 12 vs "12.00", 1990 vs "1990", extra spaces in text. Only used to
 * decide what still needs sending, never to decide what the server has.
 */
export function equivalent(a, b) {
  if (same(a, b)) return true;
  if (a == null || b == null || a === "" || b === "") return false;
  if (typeof a === "boolean" || typeof b === "boolean") return false;
  var na = Number(a), nb = Number(b);
  if (String(a).trim() !== "" && String(b).trim() !== "" && !isNaN(na) && !isNaN(nb)) return na === nb;
  return String(a).replace(/\s+/g, " ").trim() === String(b).replace(/\s+/g, " ").trim();
}

/*
 * What is still to send after the server turned out to have created this
 * record from one of `attempts` (create requests whose reply was lost), per
 * field, with S the server's value and L the phone's:
 *   L same as S                        -> nothing to send
 *   S matches something the phone sent -> only the phone changed it since: send L on top of S
 *   L matches something it sent        -> only another device changed it since: keep S
 *   neither                            -> both changed it: send L based on the last
 *                                         attempt, so the server reports a conflict
 * With no attempts recorded there is nothing to compare, so the server wins.
 */
function unsentAfterCreate(local, serverValues, fields, attempts) {
  var dirty = {};
  if (!attempts || !attempts.length) return dirty;
  var last = attempts[attempts.length - 1];
  fields.forEach(function (f) {
    var theirs = serverValues[f] === undefined ? null : serverValues[f];
    if (equivalent(local[f], theirs)) return;
    var sentValue = function (v) { return attempts.some(function (a) { return equivalent(a[f], v); }); };
    if (sentValue(theirs)) dirty[f] = theirs;
    else if (!sentValue(local[f])) dirty[f] = last[f] === undefined ? null : last[f];
  });
  return dirty;
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

/* Remember a create that is about to be sent (see afterCreate). */
export function markCreateSent(rec, fields) {
  var next = clone(rec);
  next.creates = (next.creates || []).concat([Object.assign({}, fields)]);
  return next;
}

/*
 * Remember a patch that is about to be sent (see afterConflict). Attempts whose
 * reply never came are kept, with the version the first of them started from:
 * a snapshot may have moved rec.server on to the version one of them created.
 */
export function markSent(rec, changes) {
  var next = clone(rec);
  var version = next.inflight ? next.inflight.version
    : rec.server && rec.server.version != null ? rec.server.version : null;
  var attempts = next.inflight ? next.inflight.attempts : [];
  next.inflight = { version: version, attempts: attempts.concat([Object.assign({}, changes)]) };
  return next;
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
  next.inflight = null;
  return withServer(next, serverValues, fields);
}

/*
 * The server answered a create the phone made. `sent` is the fields object.
 * created === false means an earlier attempt had already created it (its reply
 * was lost): the server kept that attempt's values and ignored `sent`, so
 * whatever the phone has that differs from the server still has to be sent.
 */
export function afterCreate(rec, sent, serverValues, fields, created) {
  var next = clone(rec);
  if (created === false) {
    next.dirty = unsentAfterCreate(next.local, serverValues, fields, next.creates || [sent]);
  } else {
    // Anything edited again while the create was in flight becomes an edit on top.
    next.dirty = {};
    fields.forEach(function (f) {
      if (!same(next.local[f], sent[f])) next.dirty[f] = serverValues[f] === undefined ? null : serverValues[f];
    });
  }
  next.error = null;
  next.creates = null;
  return withServer(next, serverValues, fields);
}

/*
 * The server refused a patch because a field changed there too.
 * If the server is exactly one version past the one this phone's unanswered
 * patches started from, only one write happened since. When the server holds a
 * value one of those patches sent, that write was this phone's own and its
 * reply never arrived: the field is re-based on it instead of being shown as a
 * conflict with "another device". Returns the record; when nothing is left in
 * conflict it is ready to send again.
 */
export function afterConflict(rec, conflicts, serverValues, fields) {
  var next = withServer(rec, serverValues, fields);
  var sent = rec.inflight;
  var ownWrite = Boolean(sent && sent.version != null && serverValues.version === sent.version + 1);
  var real = [];
  (conflicts || []).forEach(function (c) {
    var mine = ownWrite && sent.attempts.some(function (changes) {
      return Object.prototype.hasOwnProperty.call(changes, c.field) && same(c.theirs, changes[c.field]);
    });
    if (mine) next.dirty[c.field] = c.theirs;
    else real.push(c);
  });
  next.conflict = real.length ? real : null;
  next.inflight = null;
  return next;
}

/*
 * An unsent extra or photo whose piece is no longer on the phone. Shallow copy:
 * photo records hold a Blob, which a JSON clone would destroy.
 */
export function orphaned(rec) {
  return Object.assign({}, rec, {
    error: { message: "Its piece was deleted on another device, so this can't be saved. Discard it to clear this." },
  });
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
 * Throw away the phone's unsent changes to a record. Returns the record as
 * the server last sent it, or null if the server never had it (delete it).
 */
export function discardChanges(rec, fields) {
  if (rec.id == null || !rec.server) return null;
  var next = clone(rec);
  next.local = pick(rec.server, fields);
  next.dirty = {};
  next.conflict = null;
  next.error = null;
  next.inflight = null;
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
      // Its create succeeded but the reply was lost. The server has the values that
      // create sent; anything edited on the phone since still has to be sent.
      if (rec.id == null) {
        rec.dirty = unsentAfterCreate(rec.local, row, fields, rec.creates);
        rec.creates = null;
      }
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

/*
 * The size to store a photo at: the long side at most `max` pixels (the size
 * the server keeps), aspect ratio kept. Returns null when it is small enough already.
 */
export function fitWithin(width, height, max) {
  if (!(width > 0 && height > 0) || Math.max(width, height) <= max) return null;
  var scale = max / Math.max(width, height);
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

/* Catalog search on the phone, ranked like the server: item numbers first. */
export function searchCatalog(entries, query, limit) {
  var q = String(query || "").trim().toLowerCase().replace(/\s+/g, " ");
  if (q.length < 2) return [];
  var digits = q.replace(/\D/g, "");
  var hits = [];
  entries.forEach(function (e) {
    if (e.listed === false) return; // removed from the reference lists: kept only for pieces linked to it
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
