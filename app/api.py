"""JSON API used by the phone app for offline sync (/api/v1).

Same login as the website (session cookie). Every request that changes data
must send the session's CSRF token in the X-CSRF-Token header. Errors are
always JSON: {"error": "...", ...}.
"""

from __future__ import annotations

import functools
import hmac
import logging
from typing import Any, Callable, TypeVar

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.exceptions import HTTPException

from . import photos
from .auth import SESSION_CSRF, csrf_token, current_user
from .sync import (
    EXTRA_COLUMNS,
    UUID_RE,
    SyncError,
    clean_changes,
    extra_to_json,
    item_to_json,
    json_to_form,
    merge,
    place_to_json,
)
from .money import DEFAULT_CURRENCY
from .validation import (
    DEFAULT_VILLAGE,
    ITEM_COLUMNS,
    PLACE_FIELDS,
    STORAGE_COLUMNS,
    parse_extra_form,
    parse_item_form,
    parse_positive_int,
)

log = logging.getLogger("dickens.api")

api = Blueprint("api", __name__, url_prefix="/api/v1")

F = TypeVar("F", bound=Callable[..., Any])


def _error(status: int, message: str, **extra: Any) -> Any:
    response = jsonify({"error": message, **extra})
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _ok(payload: Any, status: int = 200) -> Any:
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def api_login_required(view: F) -> F:
    """401 JSON instead of a redirect; CSRF header check on anything but GET."""

    @functools.wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if current_user() is None:
            return _error(401, "Not logged in.")
        if request.method != "GET":
            expected = session.get(SESSION_CSRF)
            supplied = request.headers.get("X-CSRF-Token", "")
            if not expected or not hmac.compare_digest(expected, supplied):
                log.warning("API CSRF check failed for %s %s", request.method, request.path)
                return _error(403, "Security token missing or expired. Reload the app.")
        return view(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


@api.errorhandler(HTTPException)
def _http_error(exc: HTTPException) -> Any:
    return _error(exc.code or 500, exc.description or exc.name)


def _repo() -> Any:
    from .views import repo

    return repo()


def _json_body() -> dict[str, Any]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise SyncError("Request body must be a JSON object.")
    return data


def _client_uuid(value: Any) -> str:
    if not isinstance(value, str) or not UUID_RE.match(value):
        raise SyncError("client_uuid must be a lowercase UUID.")
    return value


@api.errorhandler(Exception)
def _unexpected(exc: Exception) -> Any:
    if isinstance(exc, HTTPException):
        return _http_error(exc)
    log.exception("Unhandled API error")
    return _error(500, "Something went wrong on the server. Your changes are kept on the phone.")


@api.errorhandler(SyncError)
def _sync_error(exc: SyncError) -> Any:
    return _error(400, str(exc))


def _with_etag(payload: dict[str, Any], etag: str) -> Any:
    if request.if_none_match and etag in request.if_none_match:
        response = current_app.response_class(status=304)
    else:
        response = jsonify(payload)
    response.set_etag(etag)
    response.headers["Cache-Control"] = "private, no-cache"
    return response


# -- reading -----------------------------------------------------------------------


@api.get("/session")
@api_login_required
def session_info() -> Any:
    from .money import CURRENCIES
    from .validation import CATEGORIES, CONDITIONS, VILLAGES
    from .views import money_settings

    settings = money_settings()
    return _ok({
        "user": current_user(),
        "csrf_token": csrf_token(),
        "choices": {"categories": CATEGORIES, "conditions": CONDITIONS, "villages": VILLAGES,
                    "currencies": CURRENCIES},
        "money": {"currency": settings.currency,
                  "usd_cad_rate": str(settings.usd_cad_rate) if settings.usd_cad_rate else None},
    })


@api.get("/snapshot")
@api_login_required
def snapshot() -> Any:
    repo = _repo()
    etag = repo.snapshot_etag()
    if request.if_none_match and etag in request.if_none_match:
        return _with_etag({}, etag)
    data = repo.snapshot()
    extras: dict[int, list[dict[str, Any]]] = {}
    for row in data["extras"]:
        extras.setdefault(row["item_id"], []).append(extra_to_json(row))
    photo_map: dict[int, list[dict[str, Any]]] = {}
    for row in data["photos"]:
        photo_map.setdefault(row["item_id"], []).append(
            {"id": row["id"], "client_uuid": row["client_uuid"], "caption": row.get("caption")})
    scan_map: dict[int, list[dict[str, Any]]] = {}
    for row in data.get("scans", []):
        scan_map.setdefault(row["item_id"], []).append({"id": row["id"]})
    items = []
    for row in data["items"]:
        item = item_to_json(row)
        item["extras"] = extras.get(row["id"], [])
        item["photos"] = photo_map.get(row["id"], [])
        item["scans"] = scan_map.get(row["id"], [])
        items.append(item)
    places = [place_to_json(row) for row in data.get("places", [])]
    return _with_etag({"etag": etag, "items": items, "places": places}, etag)


@api.get("/catalog")
@api_login_required
def catalog() -> Any:
    from .views import _catalog_json

    repo = _repo()
    etag = repo.catalog_etag()
    if request.if_none_match and etag in request.if_none_match:
        return _with_etag({}, etag)
    entries = [_catalog_json(row) for row in repo.catalog_all()]
    return _with_etag({"etag": etag, "entries": entries}, etag)


# -- pieces ------------------------------------------------------------------------


def _validate_item(values: dict[str, Any], check_links: bool = True, legacy_storage: bool = False,
                   default_village: str = DEFAULT_VILLAGE,
                   default_currency: str | None = None) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate piece fields. check_links also checks that the catalog entry and
    places exist and belong to the piece's village; it reads the database, so it
    can't run under a row lock. A piece sent without a village (by an older
    phone app) gets default_village; one without a currency gets default_currency,
    or the site currency (which reads the database: pass it under a row lock)."""
    from .views import _check_catalog_link, _check_places, money_settings

    data, errors = parse_item_form(json_to_form(values), legacy_storage=legacy_storage,
                                   default_village=default_village,
                                   default_currency=default_currency or money_settings().currency)
    if check_links:
        _check_catalog_link(data, errors)
        _check_places(data, errors, PLACE_FIELDS)
    return data, errors


def _sends_legacy_storage(fields: dict[str, Any]) -> bool:
    """An older phone app sends Location / Tub / Tray instead of a storage place."""
    return "storage_place_id" not in fields and any(c in fields for c in STORAGE_COLUMNS)


def _changed_places_problem(changes: dict[str, Any], keys: tuple[str, ...],
                            village: str | None = None) -> dict[str, str]:
    """Errors for places in changes that don't exist or are the wrong kind (checked
    before the row lock). With village, also for places in another village."""
    from .views import _check_places

    data: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for key in keys:
        if changes.get(key) not in (None, ""):
            data[key] = parse_positive_int(str(changes[key]))
            if data[key] is None:
                errors[key] = "Choose a place from the list."
    _check_places(data, errors, tuple(k for k in data if data[k] is not None), village=village,
                  check_village=village is not None)
    return errors


@api.post("/items")
@api_login_required
def create_item() -> Any:
    body = _json_body()
    client_uuid = _client_uuid(body.get("client_uuid"))
    fields = body.get("fields")
    if not isinstance(fields, dict) or set(fields) - set(ITEM_COLUMNS):
        raise SyncError('"fields" must be an object with item fields only.')
    repo = _repo()
    existing = repo.get_item_by_client_uuid(client_uuid)
    if existing:  # a retried upload: answer as if it just succeeded
        return _ok({"item": item_to_json(existing), "created": False})
    legacy = _sends_legacy_storage(fields)
    data, errors = _validate_item(fields, legacy_storage=legacy)
    if errors:
        return _error(422, "Some fields are not valid.", errors=errors)
    if legacy:
        data["_legacy_storage"] = True
    item_id, created = repo.create_item_from_client(data, client_uuid)
    row = repo.get_item(item_id)
    return _ok({"item": item_to_json(row), "created": created}, 201 if created else 200)


@api.patch("/items/<int:item_id>")
@api_login_required
def patch_item(item_id: int) -> Any:
    from .views import _place_tree, _place_village_errors, catalog_village_problem

    base, changes = clean_changes(_json_body(), ITEM_COLUMNS)
    repo = _repo()
    # Everything resolve() needs from the database is read before the row lock:
    # a query inside resolve() would end its transaction.
    catalog_villages: dict[int, str | None] = {}
    if changes.get("catalog_id") not in (None, ""):
        catalog_id = parse_positive_int(str(changes["catalog_id"]))
        entry = repo.get_catalog_item(catalog_id) if catalog_id is not None else None
        if entry is None:
            return _error(422, "Some fields are not valid.",
                          errors={"catalog_id": "That catalog entry no longer exists; pick the piece again."})
        catalog_villages[int(entry["id"])] = entry.get("village")
    place_errors = _changed_places_problem(changes, PLACE_FIELDS)
    if place_errors:
        return _error(422, "Some fields are not valid.", errors=place_errors)
    current_row = repo.get_item(item_id)
    if current_row and current_row.get("catalog_id") and int(current_row["catalog_id"]) not in catalog_villages:
        entry = repo.get_catalog_item(int(current_row["catalog_id"]))
        if entry is not None:
            catalog_villages[int(entry["id"])] = entry.get("village")
    tree = _place_tree()
    legacy = _sends_legacy_storage(changes)

    def resolve(row: dict[str, Any]) -> tuple[str, Any]:
        # Runs inside the repository's transaction: no other database calls here.
        current = item_to_json(row)
        merged, conflicts = merge(current, base, changes)
        if conflicts:
            return "conflict", conflicts
        data, errors = _validate_item({k: merged[k] for k in ITEM_COLUMNS}, check_links=False,
                                      legacy_storage=legacy, default_village=row.get("village") or DEFAULT_VILLAGE,
                                      default_currency=row.get("currency") or DEFAULT_CURRENCY)
        if not errors:
            # The piece's places and catalog entry must belong to its village.
            _place_village_errors(data, errors, PLACE_FIELDS, data["village"], tree)
            if data.get("catalog_id") is not None and int(data["catalog_id"]) in catalog_villages:
                problem = catalog_village_problem(catalog_villages[int(data["catalog_id"])], data["village"])
                if problem:
                    errors["catalog_id"] = problem
        if errors:
            return "invalid", errors
        # Move the piece only if the old-style values really changed: a piece in a
        # place deeper than three levels must stay where it is.
        if legacy and tuple(data[c] for c in STORAGE_COLUMNS) != tuple(row.get(c) for c in STORAGE_COLUMNS):
            data["_legacy_storage"] = True
        return "ok", data

    status, payload, row = repo.patch_item(item_id, resolve)
    if status == "missing":
        return _error(404, "That piece no longer exists.")
    if status == "conflict":
        return _error(409, "This piece was also changed somewhere else.", conflicts=payload,
                      item=item_to_json(row))
    if status == "invalid":
        return _error(422, "Some fields are not valid.", errors=payload)
    return _ok({"item": item_to_json(row)})


# -- extras ------------------------------------------------------------------------


@api.post("/items/<int:item_id>/extras")
@api_login_required
def create_extra(item_id: int) -> Any:
    body = _json_body()
    client_uuid = _client_uuid(body.get("client_uuid"))
    fields = body.get("fields")
    if not isinstance(fields, dict) or set(fields) - set(EXTRA_COLUMNS):
        raise SyncError('"fields" must be an object with extra fields only.')
    from .views import _check_places

    repo = _repo()
    item = repo.get_item(item_id)
    data, errors = parse_extra_form(json_to_form(fields))
    _check_places(data, errors, ("storage_place_id",), village=item["village"] if item else None,
                  check_village=item is not None)
    if errors:
        return _error(422, "Some fields are not valid.", errors=errors)
    extra_id, created = repo.add_extra_from_client(item_id, data, client_uuid)
    if extra_id is None:
        return _error(404, "That piece no longer exists.")
    row = repo.get_extra(item_id, extra_id)
    if row is None:  # the client_uuid belongs to an extra on a different piece
        return _error(409, "That extra belongs to a different piece.")
    return _ok({"extra": extra_to_json(row), "created": created}, 201 if created else 200)


@api.patch("/items/<int:item_id>/extras/<int:extra_id>")
@api_login_required
def patch_extra(item_id: int, extra_id: int) -> Any:
    base, changes = clean_changes(_json_body(), EXTRA_COLUMNS)
    item = _repo().get_item(item_id)
    place_errors = _changed_places_problem(changes, ("storage_place_id",), item["village"] if item else None)
    if place_errors:
        return _error(422, "Some fields are not valid.", errors=place_errors)

    def resolve(row: dict[str, Any]) -> tuple[str, Any]:
        current = extra_to_json(row)
        merged, conflicts = merge(current, base, changes)
        if conflicts:
            return "conflict", conflicts
        data, errors = parse_extra_form(json_to_form({k: merged[k] for k in EXTRA_COLUMNS}))
        if errors:
            return "invalid", errors
        return "ok", data

    status, payload, row = _repo().patch_extra(item_id, extra_id, resolve)
    if status == "missing":
        return _error(404, "That extra no longer exists.")
    if status == "conflict":
        return _error(409, "This extra was also changed somewhere else.", conflicts=payload,
                      extra=extra_to_json(row))
    if status == "invalid":
        return _error(422, "Some fields are not valid.", errors=payload)
    return _ok({"extra": extra_to_json(row)})


# -- photos ------------------------------------------------------------------------


@api.post("/items/<int:item_id>/photos")
@api_login_required
def upload_photo(item_id: int) -> Any:
    client_uuid = _client_uuid(request.form.get("client_uuid"))
    repo = _repo()
    existing = repo.get_photo_by_client_uuid(client_uuid)
    if existing:
        return _ok({"photo": {"id": existing["id"], "client_uuid": client_uuid}, "created": False})
    upload = request.files.get("photo")
    if upload is None or not upload.filename:
        raise SyncError("Attach the photo as the 'photo' field.")
    if repo.get_item(item_id) is None:
        return _error(404, "That piece no longer exists.")
    upload_dir = current_app.config["DICKENS"].upload_dir
    try:
        filename = photos.save_photo(upload.stream, upload_dir,
                                     current_app.config["DICKENS"].max_upload_mb * 1024 * 1024)
    except photos.PhotoError as exc:
        return _error(422, str(exc))
    try:
        photo_id = repo.add_photo(item_id, filename, photos.clean_original_name(upload.filename), client_uuid)
    except Exception as exc:
        photos.delete_photo_file(upload_dir, filename)
        from .repository import _is_duplicate_key

        if _is_duplicate_key(exc):  # a retry raced this request
            again = repo.get_photo_by_client_uuid(client_uuid)
            if again:
                return _ok({"photo": {"id": again["id"], "client_uuid": client_uuid}, "created": False})
        raise
    if photo_id is None:
        photos.delete_photo_file(upload_dir, filename)
        return _error(404, "That piece no longer exists.")
    return _ok({"photo": {"id": photo_id, "client_uuid": client_uuid}, "created": True}, 201)
