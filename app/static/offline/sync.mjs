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
    var session = await api.call("GET", "/session");
    api.setToken(session.data.csrf_token);
    await db.setMeta("session", { user: session.data.user, choices: session.data.choices });
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
    if (err instanceof api.OfflineError) {
      emit("offline", counts2);
    } else if (err instanceof api.AuthError) {
      await db.setMeta("authExpired", true);
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

async function pushItems() {
  var items = await db.all("items");
  for (var i = 0; i < items.length; i++) {
    var rec = items[i];
    if (m.needsAttention(rec)) continue; // waits for the person to fix or resolve it
    if (rec.id == null) {
      var fields = Object.assign({}, rec.local);
      var res = await api.call("POST", "/items", { client_uuid: rec.client_uuid, fields: fields });
      var fresh = (await db.get("items", rec.key)) || rec;
      if (res.status === 200 || res.status === 201) {
        await db.put("items", Object.assign(m.afterCreate(fresh, fields, res.data.item, m.ITEM_FIELDS),
          { photos: fresh.photos || [] }));
      } else {
        fresh.error = validationError(res);
        await db.put("items", fresh);
      }
    } else if (Object.keys(rec.dirty).length) {
      var payload = m.patchPayload(rec);
      var res2 = await api.call("PATCH", "/items/" + rec.id, payload);
      var fresh2 = (await db.get("items", rec.key)) || rec;
      if (res2.status === 200) {
        await db.put("items", m.afterPatch(fresh2, payload.changes, res2.data.item, m.ITEM_FIELDS));
      } else if (res2.status === 409) {
        await db.put("items", m.afterConflict(fresh2, res2.data.conflicts, res2.data.item, m.ITEM_FIELDS));
      } else {
        fresh2.error = validationError(res2);
        await db.put("items", fresh2);
      }
    }
  }
}

async function itemIdFor(itemKey) {
  var item = await db.get("items", itemKey);
  return item ? item.id : null;
}

async function pushExtras() {
  var extras = await db.all("extras");
  for (var i = 0; i < extras.length; i++) {
    var rec = extras[i];
    if (m.needsAttention(rec)) continue;
    var itemId = await itemIdFor(rec.itemKey);
    if (itemId == null) continue; // its piece hasn't been uploaded yet
    if (rec.id == null) {
      var fields = Object.assign({}, rec.local);
      var res = await api.call("POST", "/items/" + itemId + "/extras", { client_uuid: rec.client_uuid, fields: fields });
      var fresh = (await db.get("extras", rec.key)) || rec;
      if (res.status === 200 || res.status === 201) {
        await db.put("extras", m.afterCreate(fresh, fields, res.data.extra, m.EXTRA_FIELDS));
      } else {
        fresh.error = validationError(res);
        await db.put("extras", fresh);
      }
    } else if (Object.keys(rec.dirty).length) {
      var payload = m.patchPayload(rec);
      var res2 = await api.call("PATCH", "/items/" + itemId + "/extras/" + rec.id, payload);
      var fresh2 = (await db.get("extras", rec.key)) || rec;
      if (res2.status === 200) {
        await db.put("extras", m.afterPatch(fresh2, payload.changes, res2.data.extra, m.EXTRA_FIELDS));
      } else if (res2.status === 409) {
        await db.put("extras", m.afterConflict(fresh2, res2.data.conflicts, res2.data.extra, m.EXTRA_FIELDS));
      } else {
        fresh2.error = validationError(res2);
        await db.put("extras", fresh2);
      }
    }
  }
}

async function pushPhotos() {
  var pending = await db.all("photos");
  for (var i = 0; i < pending.length; i++) {
    var p = pending[i];
    if (p.error) continue;
    var itemId = await itemIdFor(p.itemKey);
    if (itemId == null) continue;
    var form = new FormData();
    form.append("client_uuid", p.client_uuid);
    form.append("photo", p.blob, p.name || "photo.jpg");
    var res = await api.call("POST", "/items/" + itemId + "/photos", form);
    if (res.status === 200 || res.status === 201) {
      var item = await db.get("items", p.itemKey);
      if (item) {
        item.photos = (item.photos || []).filter(function (x) { return x.id !== res.data.photo.id; });
        item.photos.push({ id: res.data.photo.id, client_uuid: p.client_uuid });
        await db.put("items", item);
      }
      await db.del("photos", p.key);
    } else {
      p.error = validationError(res);
      await db.put("photos", p);
    }
  }
}

async function pullSnapshot() {
  var etag = await db.getMeta("snapshotEtag", null);
  var res = await api.call("GET", "/snapshot", undefined, { etag: etag });
  if (res.status === 304) return;
  var rows = res.data.items;
  var items = await db.all("items");
  var merged = m.mergeSnapshot(items, rows, m.ITEM_FIELDS, "", function (row) { return { photos: row.photos }; });
  await db.apply("items", merged.put, merged.remove);

  var keyById = {};
  merged.put.forEach(function (rec) { if (rec.id != null) keyById[rec.id] = rec.key; });
  var extraRows = [];
  rows.forEach(function (row) { (row.extras || []).forEach(function (e) { extraRows.push(e); }); });
  var extras = await db.all("extras");
  var mergedExtras = m.mergeSnapshot(extras, extraRows, m.EXTRA_FIELDS, "e",
    function (row) { return { itemKey: keyById[row.item_id] }; });
  await db.apply("extras", mergedExtras.put, mergedExtras.remove);
  await db.setMeta("snapshotEtag", res.etag);
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
