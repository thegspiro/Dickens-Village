/* Small fetch wrapper for /api/v1. Throws OfflineError when the server can't be reached. */

export class OfflineError extends Error {}
export class AuthError extends Error {}
export class ServerError extends Error {}

let csrfToken = null;

export function setToken(token) {
  csrfToken = token;
}

export async function call(method, path, body, options) {
  var opts = options || {};
  var headers = { Accept: "application/json" };
  var init = { method: method, credentials: "same-origin", headers: headers, cache: "no-store" };
  if (method !== "GET") headers["X-CSRF-Token"] = csrfToken || "";
  if (opts.etag) headers["If-None-Match"] = opts.etag;
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  var response;
  try {
    response = await fetch("/api/v1" + path, init);
  } catch (err) {
    throw new OfflineError("Can't reach the server.");
  }
  if (response.status === 401) throw new AuthError("Your login has expired.");
  if (response.status === 403) throw new AuthError("Your login needs refreshing.");
  if (response.status === 304) return { status: 304, data: null, etag: opts.etag };
  var type = response.headers.get("Content-Type") || "";
  if (type.indexOf("application/json") === -1) {
    // e.g. a proxy error page or a captive portal: treat as unreachable
    throw new OfflineError("The server sent an unexpected reply (" + response.status + ").");
  }
  var data = await response.json();
  if (response.status >= 500) throw new ServerError(data.error || "The server had a problem.");
  return { status: response.status, data: data, etag: response.headers.get("ETag") };
}
