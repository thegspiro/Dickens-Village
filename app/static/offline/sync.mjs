/*
 * Sync engine: sends the phone's changes, then downloads the server's copy.
 *
 * Order: new pieces -> piece edits -> new extras -> extra edits -> photos ->
 * snapshot -> catalog. Every upload is idempotent (client_uuid) or guarded by
 * base values, so a sync interrupted at any point can simply run again.
 */

import * as api from "./api.mjs";
import * as db from "./db.mjs";
import * as m from "./model.mjs";

export const events = new EventTarget();
let running = null;

function emit(state, detail) {
  events.dispatchEvent(new CustomEvent("status", { detail: Object.assign({ state: state }, detail || {}) }));
}

export function sync() {
  if (running) return running;
  var job = function () { return runSync(); };
  running = (navigator.locks ? navigator.locks.request("dickens-sync", job) : job())
    .finally(function () { running = null; });
  return running;
}

export function isRunning() {
  return Boolean(running);
}

async function runSync() {
  emit("syncing");
  try {
    var session = await api.refreshToken();
    await db.setMeta("session", { user: session.data.user, choices: session.data.choices,
      money: session.data.money || null });
    await db.setMeta("authExpired", false);
    await pushItems();
    await pushExtras();
    await pushPhotos();
    await pullSnapshot();
    await pullCatalog();
    await db.setMeta("lastSync", Date.now());
    var counts = await countPending();
    emit("idle", counts);
    return counts;
  } catch (err) {
    var counts2 = await countPending().catch(function () { return {}; });
    if (err instanceof db.StorageErased) {
      emit("erased", counts2); // logged out in another tab while this one was syncing
    } else if (err instanceof api.OfflineError) {
      emit("offline", counts2);
    } else if (err instanceof api.AuthError) {
      await db.setMeta("authExpired", true).catch(function () {});
      emit("login", counts2);
    } else {
      if (window.console) console.error("Sync failed", err);
      emit("error", Object.assign({ message: String(err && err.message || err) }, counts2));
    }
    return counts2;
  }
}

function validationError(res) {
  return { message: (res.data && res.data.error) || "The server rejected this change.",
    errors: (res.data && res.data.errors) || null };
}

/*
 * Store the outcome of a request against the record as it is now (re-read in the
 * same transaction), never against the copy read before the request was sent:
 * the person may have edited it in the meantime. Resolves to the stored record.
 */
function settle(store, key, fn) {
  return db.update(store, key, function (current) {
    return current ? fn(current) : undefined; // discarded while the request was out
  });
}

/*
 * Send one piece or extra: create it if the server doesn't have it, then send
 * its edits. A record that a create or a recognised own-write conflict leaves
 * with edits still to send gets one more patch in this same sync.
 */
async function pushRecord(store, fields, key, collectionPath, field) {
  var rec = await db.get(store, key);
  if (!rec || m.needsAttention(rec)) return; // waits for the person to fix or resolve it
  if (rec.id == null) {
    var sent = Object.assign({}, rec.local);
    rec = await settle(store, key, function (current) { return m.markCreateSent(current, sent); });
    if (!rec) return;
    var res = await api.call("POST", collectionPath, { client_uuid: rec.client_uuid, fields: sent });
    rec = await settle(store, key, function (current) {
      if (res.status === 200 || res.status === 201) {
        return m.afterCreate(current, sent, res.data[field], fields, res.data.created);
      }
      return Object.assign(current, { error: validationError(res) });
    });
  }
  for (var attempt = 0; attempt < 2 && rec && rec.id != null && !m.needsAttention(rec) &&
       Object.keys(rec.dirty).length; attempt++) {
    var payload = m.patchPayload(rec);
    rec = await settle(store, key, function (current) { return m.markSent(current, payload.changes); });
    if (!rec) return;
    var res2 = await api.call("PATCH", collectionPath + "/" + rec.id, payload);
    rec = await settle(store, key, function (current) {
      if (res2.status === 200) return m.afterPatch(current, payload.changes, res2.data[field], fields);
      if (res2.status === 409) return m.afterConflict(current, res2.data.conflicts, res2.data[field], fields);
      return Object.assign(current, { error: validationError(res2) });
    });
  }
}

async function pushItems() {
  var items = await db.all("items");
  for (var i = 0; i < items.length; i++) {
    await pushRecord("items", m.ITEM_FIELDS, items[i].key, "/items", "item");
  }
}

/* The server id of an extra's or photo's piece; undefined if the piece is gone from the phone. */
async function itemIdFor(itemKey) {
  var item = await db.get("items", itemKey);
  return item ? item.id : undefined;
}

async function pushExtras() {
  var extras = await db.all("extras");
  for (var i = 0; i < extras.length; i++) {
    var rec = extras[i];
    if (m.needsAttention(rec)) continue;
    var itemId = await itemIdFor(rec.itemKey);
    if (itemId === undefined) {
      await settle("extras", rec.key, function (current) { return m.orphaned(current); });
      continue;
    }
    if (itemId == null) continue; // its piece hasn't been uploaded yet
    await pushRecord("extras", m.EXTRA_FIELDS, rec.key, "/items/" + itemId + "/extras", "extra");
  }
}

async function pushPhotos() {
  var pending = await db.all("photos");
  for (var i = 0; i < pending.length; i++) {
    var p = pending[i];
    if (p.error) continue;
    var itemId = await itemIdFor(p.itemKey);
    if (itemId === undefined) {
      await settle("photos", p.key, function (current) { return m.orphaned(current); });
      continue;
    }
    if (itemId == null) continue;
    var form = new FormData();
    form.append("client_uuid", p.client_uuid);
    form.append("photo", p.blob, p.name || "photo.jpg");
    var res = await api.call("POST", "/items/" + itemId + "/photos", form);
    if (res.status === 200 || res.status === 201) {
      var photo = res.data.photo;
      await settle("items", p.itemKey, function (item) {
        item.photos = (item.photos || []).filter(function (x) { return x.id !== photo.id; });
        item.photos.push({ id: photo.id, client_uuid: p.client_uuid });
        return item;
      });
      await db.del("photos", p.key);
    } else {
      await settle("photos", p.key, function (current) { return Object.assign(current, { error: validationError(res) }); });
    }
  }
}

async function pullSnapshot() {
  var etag = await db.getMeta("snapshotEtag", null);
  var res = await api.call("GET", "/snapshot", undefined, { etag: etag });
  if (res.status === 304) return;
  var rows = res.data.items;
  await db.setMeta("places", res.data.places || []);
  // Read, merge and write in one transaction so an edit saved meanwhile isn't overwritten.
  var merged = await db.mergeAll("items", function (items) {
    return m.mergeSnapshot(items, rows, m.ITEM_FIELDS, "", function (row) {
      return { photos: row.photos, scans: row.scans || [] };
    });
  });
  var removed = {};
  merged.remove.forEach(function (k) { removed[k] = true; });

  var keyById = {};
  merged.put.forEach(function (rec) { if (rec.id != null) keyById[rec.id] = rec.key; });
  var extraRows = [];
  rows.forEach(function (row) { (row.extras || []).forEach(function (e) { extraRows.push(e); }); });
  await db.mergeAll("extras", function (extras) {
    var out = m.mergeSnapshot(extras, extraRows, m.EXTRA_FIELDS, "e",
      function (row) { return { itemKey: keyById[row.item_id] }; });
    // Unsent extras of a piece that was just removed can never be saved: say so.
    extras.forEach(function (rec) {
      if (rec.id == null && removed[rec.itemKey] && !rec.error) out.put.push(m.orphaned(rec));
    });
    return out;
  });
  await db.mergeAll("photos", function (photos) {
    return { put: photos.filter(function (p) { return removed[p.itemKey] && !p.error; }).map(m.orphaned), remove: [] };
  });
  await db.setMeta("snapshotEtag", res.etag);
  await pruneThumbnails(rows);
}

/* Forget cached thumbnails of photos the server no longer has (deleted elsewhere). */
async function pruneThumbnails(rows) {
  if (typeof caches === "undefined") return;
  var keep = {};
  rows.forEach(function (row) { (row.photos || []).forEach(function (p) { keep[p.id] = true; }); });
  try {
    var cache = await caches.open("dickens-data-v1");
    var requests = await cache.keys();
    await Promise.all(requests.map(function (req) {
      var match = /^\/photos\/(\d+)\/thumb$/.exec(new URL(req.url).pathname);
      return match && !keep[match[1]] ? cache.delete(req) : null;
    }));
  } catch (err) {
    if (window.console) console.warn("Couldn't tidy cached photos", err);
  }
}

async function pullCatalog() {
  var etag = await db.getMeta("catalogEtag", null);
  var res = await api.call("GET", "/catalog", undefined, { etag: etag });
  if (res.status === 304) return;
  await db.replaceAll("catalog", res.data.entries);
  await db.setMeta("catalogEtag", res.etag);
}

export async function countPending() {
  var items = await db.all("items");
  var extras = await db.all("extras");
  var photos = await db.all("photos");
  var waiting = function (r) { return m.isPending(r) && !m.needsAttention(r); };
  var pending = items.filter(waiting).length + extras.filter(waiting).length +
    photos.filter(function (p) { return !p.error; }).length;
  var attention = items.filter(m.needsAttention).length + extras.filter(m.needsAttention).length +
    photos.filter(function (p) { return p.error; }).length;
  // Logging out warns about everything not yet on the server, including items needing attention.
  await db.setMeta("pendingCount", pending + attention);
  return { pending: pending, attention: attention };
}
