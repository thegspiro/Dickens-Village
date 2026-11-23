/*
 * IndexedDB storage for the phone's offline copy.
 *
 * Stores: items, extras (index itemKey), pending photos (index itemKey),
 * catalog, meta (key/value). Everything is erased on logout (wipe()).
 */

export const DB_NAME = "dickens-offline";
const DB_VERSION = 1;
const ENABLED_KEY = "dickens-offline-enabled";
let dbPromise = null;
let enabledHere = false; // fallback for when localStorage can't be used

/* The phone's copy was erased (logged out) and offline use hasn't been turned on again. */
export class StorageErased extends Error {}

/*
 * Offline use is turned on when the phone app is opened and off when the copy
 * is erased. The database is only ever created while it is on, so a sync that
 * was still running in another tab during logout can't bring the data back.
 */
export function enableOffline() {
  enabledHere = true;
  try { localStorage.setItem(ENABLED_KEY, "1"); } catch (e) { /* private mode: enabledHere covers this page */ }
}

export function disableOffline() {
  enabledHere = false;
  try { localStorage.removeItem(ENABLED_KEY); } catch (e) { /* blocked */ }
}

export function offlineEnabled() {
  try {
    return localStorage.getItem(ENABLED_KEY) === "1"; // shared by every tab, so a logout elsewhere counts
  } catch (e) {
    return enabledHere;
  }
}

/*
 * Stores and indexes, by the database version that added them. An upgrade
 * creates only what is missing, so raising DB_VERSION to add a store (with a
 * new entry here) works on phones that already have the older ones.
 */
const SCHEMA = [
  ["items", { keyPath: "key" }, []],
  ["extras", { keyPath: "key" }, ["itemKey"]],
  ["photos", { keyPath: "key" }, ["itemKey"]],
  ["catalog", { keyPath: "id" }, []],
  ["meta", { keyPath: "k" }, []],
];

export function upgrade(db, tx) {
  SCHEMA.forEach(function (spec) {
    var store = db.objectStoreNames.contains(spec[0]) ? tx.objectStore(spec[0]) : db.createObjectStore(spec[0], spec[1]);
    spec[2].forEach(function (index) {
      if (!store.indexNames.contains(index)) store.createIndex(index, index);
    });
  });
}

function open() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise(function (resolve, reject) {
    var settled = false;
    var req = indexedDB.open(DB_NAME, DB_VERSION);
    var refused = false;
    req.onupgradeneeded = function (event) {
      if (event.oldVersion === 0 && !offlineEnabled()) {
        refused = true; // would create the database: not while offline use is off
        req.transaction.abort();
        return;
      }
      upgrade(req.result, req.transaction);
    };
    req.onsuccess = function () {
      var db = req.result;
      if (settled) { db.close(); return; } // gave up on this open (blocked); the next call opens again
      settled = true;
      db.onversionchange = function () { db.close(); dbPromise = null; };
      resolve(db);
    };
    req.onerror = function (event) {
      if (event && event.preventDefault) event.preventDefault();
      settled = true;
      dbPromise = null;
      reject(refused ? new StorageErased("This phone's copy was erased.") : req.error);
    };
    req.onblocked = function () {
      // Another tab still has the older version open. Don't leave a failed open
      // cached: the next storage call tries again.
      settled = true;
      dbPromise = null;
      reject(new Error("Close other tabs of the tracker and try again."));
    };
  });
  return dbPromise;
}

function done(tx) {
  return new Promise(function (resolve, reject) {
    tx.oncomplete = function () { resolve(); };
    tx.onerror = function () { reject(tx.error); };
    tx.onabort = function () { reject(tx.error || new Error("Storage transaction aborted")); };
  });
}

function request(req) {
  return new Promise(function (resolve, reject) {
    req.onsuccess = function () { resolve(req.result); };
    req.onerror = function () { reject(req.error); };
  });
}

export async function get(store, key) {
  var db = await open();
  return request(db.transaction(store).objectStore(store).get(key));
}

export async function all(store) {
  var db = await open();
  return request(db.transaction(store).objectStore(store).getAll());
}

export async function byItem(store, itemKey) {
  var db = await open();
  return request(db.transaction(store).objectStore(store).index("itemKey").getAll(itemKey));
}

export async function put(store, value) {
  var db = await open();
  var tx = db.transaction(store, "readwrite");
  tx.objectStore(store).put(value);
  return done(tx);
}

/* Several puts and deletes in one transaction: either all happen or none. */
export async function apply(store, puts, removes) {
  var db = await open();
  var tx = db.transaction(store, "readwrite");
  var os = tx.objectStore(store);
  (puts || []).forEach(function (v) { os.put(v); });
  (removes || []).forEach(function (k) { os.delete(k); });
  return done(tx);
}

/*
 * Read one record and write its replacement in a single transaction, so nothing
 * saved in between is overwritten. fn(current) runs synchronously and returns
 * the record to store, null to delete it, or undefined to leave it unchanged.
 * Resolves to what fn returned.
 */
export async function update(store, key, fn) {
  var db = await open();
  var tx = db.transaction(store, "readwrite");
  var os = tx.objectStore(store);
  var result;
  var failure = null;
  os.get(key).onsuccess = function (event) {
    try {
      result = fn(event.target.result);
      if (result === null) os.delete(key);
      else if (result !== undefined) os.put(result);
    } catch (err) {
      failure = err;
      tx.abort();
    }
  };
  try {
    await done(tx);
  } catch (err) {
    throw failure || err;
  }
  return result;
}

/*
 * Read every record in a store and apply fn's changes in the same transaction.
 * fn(records) runs synchronously and returns { put: [...], remove: [keys] }.
 * Resolves to what fn returned.
 */
export async function mergeAll(store, fn) {
  var db = await open();
  var tx = db.transaction(store, "readwrite");
  var os = tx.objectStore(store);
  var result;
  var failure = null;
  os.getAll().onsuccess = function (event) {
    try {
      result = fn(event.target.result) || {};
      (result.put || []).forEach(function (v) { os.put(v); });
      (result.remove || []).forEach(function (k) { os.delete(k); });
    } catch (err) {
      failure = err;
      tx.abort();
    }
  };
  try {
    await done(tx);
  } catch (err) {
    throw failure || err;
  }
  return result;
}

export async function replaceAll(store, values) {
  var db = await open();
  var tx = db.transaction(store, "readwrite");
  var os = tx.objectStore(store);
  os.clear();
  values.forEach(function (v) { os.put(v); });
  return done(tx);
}

export async function del(store, key) {
  var db = await open();
  var tx = db.transaction(store, "readwrite");
  tx.objectStore(store).delete(key);
  return done(tx);
}

export async function getMeta(k, fallback) {
  var row = await get("meta", k);
  return row ? row.v : fallback;
}

export function setMeta(k, v) {
  return put("meta", { k: k, v: v });
}

export async function wipe() {
  if (dbPromise) {
    try { (await dbPromise).close(); } catch (e) { /* already closed */ }
    dbPromise = null;
  }
  await new Promise(function (resolve) {
    var req = indexedDB.deleteDatabase(DB_NAME);
    req.onsuccess = req.onerror = req.onblocked = function () { resolve(); };
  });
}
