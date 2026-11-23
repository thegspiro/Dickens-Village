/*
 * Logging out and erasing the phone's offline copy. Used by the website's
 * Log out button (app.js) and by the phone app's own buttons (main.mjs).
 *
 * Logging out ends the server session first and only then erases the copy, so
 * a sync running in another tab can't download the collection again: its
 * requests are no longer logged in, and db.mjs won't recreate the database
 * once the "offline enabled" marker is gone.
 */

import * as db from "./db.mjs";

/* Changes on this phone not yet on the server, without creating the database if there isn't one. */
export function pendingCount() {
  return new Promise(function (resolve) {
    if (!window.indexedDB) { resolve(0); return; }
    var req = indexedDB.open(db.DB_NAME);
    req.onupgradeneeded = function () { req.transaction.abort(); resolve(0); }; // nothing stored yet
    req.onerror = function () { resolve(0); };
    req.onsuccess = function () {
      var conn = req.result;
      try {
        var get = conn.transaction("meta").objectStore("meta").get("pendingCount");
        get.onsuccess = function () { conn.close(); resolve(get.result ? get.result.v : 0); };
        get.onerror = function () { conn.close(); resolve(0); };
      } catch (e) { conn.close(); resolve(0); }
    };
  });
}

/*
 * The warning to show before erasing, or null when nothing would be lost.
 * action: "Logging out" / "Erasing"; question: "Log out anyway?" / "Erase anyway?"
 */
export function lossWarning(count, action, question) {
  if (!count) return null;
  return count + (count === 1 ? " change on this phone hasn't" : " changes on this phone haven't") +
    " synced yet. " + action + " deletes " + (count === 1 ? "it" : "them") + ". " + question;
}

/* Erase the offline copy: the database, cached photos and the markers that turn offline use on. */
export async function erase() {
  db.disableOffline();
  try { localStorage.removeItem("dickens-last-bg-sync"); } catch (e) { /* blocked */ }
  var jobs = [db.wipe()];
  if (window.caches) {
    jobs.push(caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k.indexOf("dickens-data-") === 0; })
        .map(function (k) { return caches.delete(k); }));
    }));
  }
  if (navigator.serviceWorker && navigator.serviceWorker.controller) {
    navigator.serviceWorker.controller.postMessage("clear-data");
  }
  await Promise.all(jobs).catch(function () {});
}

export class LogoutError extends Error {}

/*
 * End the server session, then erase the offline copy. `csrfToken` is the
 * session's form token. Throws LogoutError (and erases nothing) if the server
 * can't be reached or refuses.
 */
export async function logout(csrfToken) {
  var form = new FormData();
  form.append("csrf_token", csrfToken || "");
  var response;
  try {
    response = await fetch("/logout", { method: "POST", body: form, credentials: "same-origin", cache: "no-store" });
  } catch (err) {
    throw new LogoutError("You're offline, so you can't log out right now. Nothing was erased. Try again when you're online.");
  }
  if (!response.ok) {
    throw new LogoutError("The server didn't log you out (HTTP " + response.status + "). Nothing was erased. Reload the page and try again.");
  }
  await erase();
}
