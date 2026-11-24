"""HTTP routes."""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from . import export, photos, scans
from .auth import (
    check_csrf,
    current_user,
    login_required,
    login_user,
    logout_user,
    safe_next_url,
    verify_credentials,
)
from .repository import PAGE_SIZE, VersionConflict
from .validation import (
    ITEM_BOOL_FIELDS,
    ITEM_COLUMNS,
    MEASUREMENT_FIELDS,
    format_inches,
    SORTS,
    STORAGE_COLUMNS,
    parse_caption,
    parse_extra_form,
    parse_id_list,
    parse_item_form,
    parse_list_filters,
    parse_positive_int,
)

log = logging.getLogger("dickens.views")

bp = Blueprint("main", __name__)


def repo() -> Any:
    if "repo" not in g:
        factory = current_app.extensions["dickens_repo_factory"]
        g.repo = factory(current_app.config["DICKENS"])
    return g.repo


def _upload_dir() -> str:
    return current_app.config["DICKENS"].upload_dir


def _max_photo_bytes() -> int:
    return current_app.config["DICKENS"].max_upload_mb * 1024 * 1024


def _item_or_404(item_id: int) -> dict[str, Any]:
    item = repo().get_item(item_id)
    if item is None:
        abort(404)
    return item


def _row_to_form(row: dict[str, Any]) -> dict[str, str]:
    """Turn a database row into the string values an HTML form expects."""
    form: dict[str, str] = {}
    for key in ITEM_COLUMNS:
        value = row.get(key)
        if key in ITEM_BOOL_FIELDS:
            form[key] = "1" if value else ""
        elif value is None:
            form[key] = ""
        elif key in MEASUREMENT_FIELDS and value is not None:
            form[key] = format_inches(value)
        elif isinstance(value, Decimal):
            form[key] = f"{value:.2f}"
        elif isinstance(value, dt.date):
            form[key] = value.isoformat()
        else:
            form[key] = str(value)
    return form


_FIELD_LABELS = {
    "category": "Category", "name": "Name", "item_number": "Item number", "series": "Series",
    "purchased_from": "Bought from", "storage_location": "Location", "storage_tub": "Tub",
    "storage_tray": "Tray", "condition_notes": "Condition notes", "notes": "Notes",
    "year_introduced": "Year introduced", "year_retired": "Year retired", "purchase_date": "Purchase date",
    "price_paid": "Price paid", "estimated_value": "Estimated value", "condition_grade": "Condition",
    "condition_checked_on": "Last checked", "has_original_box": "Original box", "has_sleeve": "Sleeve",
    "on_display": "On display", "height_in": "Height", "width_in": "Width", "depth_in": "Depth",
    "box_height_in": "Box height", "box_width_in": "Box width", "box_depth_in": "Box depth",
    "catalog_id": "Catalog link", "description": "Description", "quantity": "Quantity",
}


def _stale_fields(submitted_version: str | None, row: dict[str, Any], submitted: dict[str, Any],
                  saved: dict[str, Any]) -> list[tuple[str, str]] | None:
    """If the record changed since the form was opened, list the saved values that
    differ from what was submitted. None when the form is up to date (or has no version)."""
    if not submitted_version or not submitted_version.isdigit():
        return None
    if int(submitted_version) == int(row.get("version") or 1):
        return None
    return _changed_fields(submitted, saved)


def _changed_fields(submitted: dict[str, Any], saved: dict[str, Any]) -> list[tuple[str, str]]:
    """The saved values that differ from what was submitted, labelled for the conflict notice."""
    changed = []
    for key, value in saved.items():
        if key in submitted and submitted[key] != value:
            shown = value
            if key in ITEM_BOOL_FIELDS:
                shown = "Yes" if value else "No"
            changed.append((_FIELD_LABELS.get(key, key), "(blank)" if shown in (None, "") else str(shown)))
    return changed or [("(no visible fields)", "only the version number changed")]


def _submitted_version() -> int | None:
    """The version the edit form was opened at, or None for a form without one."""
    raw = request.form.get("version", "")
    return int(raw) if raw.isdigit() else None


def _extra_values(extra: dict[str, Any]) -> dict[str, Any]:
    return {"description": extra["description"], "quantity": extra["quantity"],
            "condition_grade": extra["condition_grade"], "notes": extra["notes"]}


def _check_catalog_link(data: dict[str, Any], errors: dict[str, str]) -> dict[str, Any] | None:
    """Return the linked catalog entry, or record an error if the link points nowhere."""
    if data.get("catalog_id") is None:
        return None
    entry = repo().get_catalog_item(data["catalog_id"])
    if entry is None:
        errors["catalog_id"] = "That catalog entry no longer exists; pick the piece again."
        data["catalog_id"] = None
    return entry


def _form_from_catalog(row: dict[str, Any]) -> dict[str, str]:
    """Form values for a new piece, prefilled from a catalog entry (same fields as the lookup script)."""
    values = {
        "catalog_id": str(row["id"]),
        "name": row["name"],
        "item_number": row["item_number"] or "",
        "category": row["default_category"] or "",
        "series": row["series"] or "",
        "year_introduced": str(row["year_issued"]) if row["year_issued"] else "",
        "year_retired": str(row["year_retired"]) if row["year_retired"] else "",
    }
    return values


def _catalog_json(row: dict[str, Any]) -> dict[str, Any]:
    def money(value: Any) -> str | None:
        return None if value is None else f"{Decimal(value):.2f}"

    return {
        "id": row["id"],
        "list": row["list_name"],
        "item_number": row["item_number"],
        "name": row["name"],
        "details": row["details"],
        "series": row["series"],
        "year_issued": row["year_issued"],
        "year_retired": row["year_retired"],
        "is_current": bool(row["is_current"]),
        "listed": bool(row.get("listed", 1)),
        "srp_usd": money(row["srp_usd"]),
        "srp_cad": money(row["srp_cad"]),
        "category": row["default_category"],
        "parent_name": row["parent_name"],
        "url": url_for("main.catalog_entry", catalog_id=row["id"]),
        "photo_count": int(row.get("photo_count") or 0),
        "thumb_url": (
            url_for("main.catalog_photo_thumb", photo_id=row["photo_id"]) if row.get("photo_id") else None
        ),
    }


def _storage_suggestions() -> dict[str, list[str]]:
    r = repo()
    return {column: r.distinct_values(column) for column in (*STORAGE_COLUMNS, "series")}


# -- auth --------------------------------------------------------------------


@bp.route("/login", methods=["GET", "POST"])
def login() -> Any:
    if current_user():
        return redirect(url_for("main.item_list"))
    next_url = request.values.get("next", "")
    if request.method == "POST":
        check_csrf()
        throttle = current_app.extensions["dickens_throttle"]
        client = request.remote_addr or "unknown"
        if throttle.is_blocked(client):
            log.warning("Login blocked by throttle for %s", client)
            flash("Too many failed attempts. Wait 15 minutes and try again.", "error")
            return render_template("login.html", next_url=next_url), 429
        username = request.form.get("username", "")[:200]
        password = request.form.get("password", "")[:1000]
        if verify_credentials(username, password):
            throttle.reset(client)
            login_user(username)
            log.info("Login succeeded from %s", client)
            return redirect(safe_next_url(next_url))
        throttle.record_failure(client)
        log.warning("Login failed from %s", client)
        flash("Incorrect username or password.", "error")
        return render_template("login.html", next_url=next_url), 401
    return render_template("login.html", next_url=next_url)


@bp.post("/logout")
@login_required
def logout() -> Any:
    check_csrf()
    logout_user()
    return redirect(url_for("main.login"))


# -- installable app (PWA) ----------------------------------------------------
# These are public: the manifest, service worker and offline page hold no
# collection data, and browsers fetch the manifest without cookies.


@bp.get("/manifest.webmanifest")
def manifest() -> Any:
    response = send_from_directory(current_app.static_folder, "manifest.webmanifest",
                                   mimetype="application/manifest+json", max_age=3600)
    return response


SW_BUILD_PLACEHOLDER = '"__BUILD__"'  # the quoted string literal in sw.js


@bp.get("/sw.js")
def service_worker() -> Any:
    # Served with this deploy's build id as its cache version: any change to the
    # app's files changes sw.js, so phones install it and re-download everything.
    source = (Path(current_app.static_folder) / "sw.js").read_text(encoding="utf-8")
    body = source.replace(SW_BUILD_PLACEHOLDER, '"' + current_app.config["STATIC_BUILD"] + '"', 1)
    response = current_app.response_class(body, mimetype="text/javascript")
    # Always check for a new version so updates reach installed phones promptly.
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@bp.get("/app")
def offline_app() -> Any:
    """The phone app shell. Public: it contains no data and is cached for offline use;
    the app asks the API for data, which needs a login."""
    response = current_app.make_response(render_template("offline_app.html"))
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.get("/offline")
def offline() -> Any:
    return render_template("offline.html")


@bp.get("/favicon.ico")
def favicon() -> Any:
    return send_from_directory(current_app.static_folder, "icons/favicon-32.png", mimetype="image/png", max_age=86400)


@bp.get("/healthz")
def healthz() -> Any:
    try:
        ok = repo().ping()
    except Exception:  # noqa: BLE001
        log.exception("Health check failed")
        ok = False
    return ("ok", 200) if ok else ("database unavailable", 503)


# -- items -------------------------------------------------------------------


@bp.get("/")
@login_required
def index() -> Any:
    return redirect(url_for("main.item_list"))


@bp.get("/items")
@login_required
def item_list() -> Any:
    filters = parse_list_filters(request.args)
    items, total = repo().list_items(filters)
    pages = max(math.ceil(total / PAGE_SIZE), 1)

    def page_url(page: int) -> str:
        args = {k: v for k, v in request.args.items() if k != "page" and v}
        args["page"] = str(page)
        return f"{url_for('main.item_list')}?{urlencode(args)}"

    return render_template(
        "items.html",
        items=items,
        total=total,
        filters=filters,
        pages=pages,
        page_url=page_url,
        sorts=SORTS,
        suggestions=_storage_suggestions(),
    )


@bp.route("/items/new", methods=["GET", "POST"])
@login_required
def item_new() -> Any:
    if request.method == "POST":
        check_csrf()
        data, errors = parse_item_form(request.form)
        catalog = _check_catalog_link(data, errors)
        if not errors:
            item_id = repo().create_item(data)
            flash(f"Added “{data['name']}”.", "success")
            return redirect(url_for("main.item_detail", item_id=item_id))
        return (
            render_template(
                "item_form.html", form=request.form, errors=errors, item=None, catalog=catalog,
                suggestions=_storage_suggestions(),
            ),
            400,
        )
    preset: dict[str, str] = {"category": request.args.get("category", "")}
    catalog = None
    catalog_id = parse_positive_int(request.args.get("catalog_id"))
    if catalog_id is not None:
        catalog = repo().get_catalog_item(catalog_id)
        if catalog is not None:
            preset.update(_form_from_catalog(catalog))
    return render_template(
        "item_form.html", form=preset, errors={}, item=None, catalog=catalog,
        suggestions=_storage_suggestions(),
    )


@bp.get("/items/<int:item_id>")
@login_required
def item_detail(item_id: int) -> Any:
    item = _item_or_404(item_id)
    r = repo()
    return render_template(
        "item_detail.html",
        item=item,
        extras=r.list_extras(item_id),
        links=r.list_links(item_id),
        link_candidates=r.link_candidates(item),
        photos=r.list_photos(item_id),
        catalog=r.get_catalog_item(item["catalog_id"]) if item.get("catalog_id") else None,
        catalog_photos=r.list_catalog_photos(item["catalog_id"]) if item.get("catalog_id") else [],
        scans=_scans_for_page(item_id),
        extra_form={},
        extra_errors={},
    )


@bp.route("/items/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def item_edit(item_id: int) -> Any:
    item = _item_or_404(item_id)
    if request.method == "POST":
        check_csrf()
        data, errors = parse_item_form(request.form)
        catalog = _check_catalog_link(data, errors)
        stale = None if errors else _stale_fields(
            request.form.get("version"), item, _row_to_form(data), _row_to_form(item))
        if not errors and not stale:
            try:
                saved = repo().update_item(item_id, data, expected_version=_submitted_version())
            except VersionConflict:
                # Saved elsewhere between reading the item above and taking the row lock.
                item = _item_or_404(item_id)
                stale = _changed_fields(_row_to_form(data), _row_to_form(item))
            else:
                if not saved:
                    abort(404)
                flash("Changes saved.", "success")
                return redirect(url_for("main.item_detail", item_id=item_id))
        return (
            render_template(
                "item_form.html", form=request.form, errors=errors, item=item, catalog=catalog,
                suggestions=_storage_suggestions(), version=item.get("version"), stale=stale,
            ),
            409 if stale else 400,
        )
    catalog = repo().get_catalog_item(item["catalog_id"]) if item.get("catalog_id") else None
    return render_template(
        "item_form.html", form=_row_to_form(item), errors={}, item=item, catalog=catalog,
        suggestions=_storage_suggestions(), version=item.get("version"),
    )


@bp.route("/items/<int:item_id>/delete", methods=["GET", "POST"])
@login_required
def item_delete(item_id: int) -> Any:
    item = _item_or_404(item_id)
    if request.method == "POST":
        check_csrf()
        filenames = repo().delete_item(item_id)
        if filenames is None:
            abort(404)
        # Files are removed only after the database commit succeeded.
        for filename in filenames:
            if scans.FILENAME_RE.match(filename):
                scans.delete_scan_file(_upload_dir(), filename)
            else:
                photos.delete_photo_file(_upload_dir(), filename)
        flash(f"Deleted “{item['name']}”.", "success")
        return redirect(url_for("main.item_list"))
    return render_template("item_delete.html", item=item)


# -- extras ------------------------------------------------------------------


@bp.post("/items/<int:item_id>/extras")
@login_required
def extra_add(item_id: int) -> Any:
    check_csrf()
    item = _item_or_404(item_id)
    data, errors = parse_extra_form(request.form)
    if errors:
        r = repo()
        return (
            render_template(
                "item_detail.html",
                item=item,
                extras=r.list_extras(item_id),
                links=r.list_links(item_id),
                link_candidates=r.link_candidates(item),
                photos=r.list_photos(item_id),
                catalog=r.get_catalog_item(item["catalog_id"]) if item.get("catalog_id") else None,
                catalog_photos=r.list_catalog_photos(item["catalog_id"]) if item.get("catalog_id") else [],
                scans=_scans_for_page(item_id),
                extra_form=request.form,
                extra_errors=errors,
            ),
            400,
        )
    if repo().add_extra(item_id, data) is None:
        abort(404)
    flash(f"Added extra: {data['description']}.", "success")
    return redirect(url_for("main.item_detail", item_id=item_id) + "#extras")


@bp.route("/items/<int:item_id>/extras/<int:extra_id>/edit", methods=["GET", "POST"])
@login_required
def extra_edit(item_id: int, extra_id: int) -> Any:
    item = _item_or_404(item_id)
    extra = repo().get_extra(item_id, extra_id)
    if extra is None:
        abort(404)
    if request.method == "POST":
        check_csrf()
        data, errors = parse_extra_form(request.form)
        saved = _extra_values(extra)
        stale = None if errors else _stale_fields(request.form.get("version"), extra, data, saved)
        if not errors and not stale:
            try:
                found = repo().update_extra(item_id, extra_id, data, expected_version=_submitted_version())
            except VersionConflict:
                extra = repo().get_extra(item_id, extra_id)
                if extra is None:
                    abort(404)
                stale = _changed_fields(data, _extra_values(extra))
            else:
                if not found:
                    abort(404)
                flash("Extra updated.", "success")
                return redirect(url_for("main.item_detail", item_id=item_id) + "#extras")
        return render_template("extra_form.html", item=item, extra=extra, form=request.form, errors=errors,
                               version=extra.get("version"), stale=stale), 409 if stale else 400
    form = {
        "description": extra["description"],
        "quantity": str(extra["quantity"]),
        "condition_grade": extra["condition_grade"] or "",
        "notes": extra["notes"] or "",
    }
    return render_template("extra_form.html", item=item, extra=extra, form=form, errors={},
                           version=extra.get("version"))


@bp.post("/items/<int:item_id>/extras/<int:extra_id>/delete")
@login_required
def extra_delete(item_id: int, extra_id: int) -> Any:
    check_csrf()
    if not repo().delete_extra(item_id, extra_id):
        abort(404)
    flash("Extra removed.", "success")
    return redirect(url_for("main.item_detail", item_id=item_id) + "#extras")


# -- links -------------------------------------------------------------------


@bp.post("/items/<int:item_id>/links")
@login_required
def link_add(item_id: int) -> Any:
    check_csrf()
    _item_or_404(item_id)
    other_id = parse_positive_int(request.form.get("other_id"))
    if other_id is None or not repo().add_link(item_id, other_id):
        flash("Pick a piece to link. A building can only be linked to figures or accessories.", "error")
    else:
        flash("Linked.", "success")
    return redirect(url_for("main.item_detail", item_id=item_id) + "#links")


@bp.post("/items/<int:item_id>/links/<int:other_id>/delete")
@login_required
def link_delete(item_id: int, other_id: int) -> Any:
    check_csrf()
    if not repo().remove_link(item_id, other_id):
        abort(404)
    flash("Link removed.", "success")
    return redirect(url_for("main.item_detail", item_id=item_id) + "#links")


# -- photos ------------------------------------------------------------------


MAX_PHOTOS_PER_UPLOAD = 20


def _store_uploads(add_record: Callable[[str, str | None], int | None]) -> bool:
    """Save every file in the "photo" field and record each with add_record.

    Flashes one summary message plus one message per rejected file. Returns
    False if add_record reported that the parent row no longer exists.
    """
    uploads = [f for f in request.files.getlist("photo") if f and f.filename]
    if not uploads:
        flash("Choose one or more photos to upload.", "error")
        return True
    if len(uploads) > MAX_PHOTOS_PER_UPLOAD:
        flash(f"Upload at most {MAX_PHOTOS_PER_UPLOAD} photos at a time.", "error")
        return True
    added = 0
    for upload in uploads:
        original = photos.clean_original_name(upload.filename)
        try:
            filename = photos.save_photo(upload.stream, _upload_dir(), _max_photo_bytes())
        except photos.PhotoError as exc:
            flash(f"{original or 'A file'}: {exc}", "error")
            continue
        try:
            record_id = add_record(filename, original)
        except Exception:
            photos.delete_photo_file(_upload_dir(), filename)  # don't leave an orphaned file
            raise
        if record_id is None:
            photos.delete_photo_file(_upload_dir(), filename)
            return False
        added += 1
    if added:
        flash(f"Added {added} photo{'' if added == 1 else 's'}.", "success")
    return True


def _send_photo(filename: str, thumb: bool) -> Any:
    if not photos.FILENAME_RE.match(filename):
        abort(404)
    name = filename
    if thumb and (Path(_upload_dir()) / photos.thumb_name(filename)).is_file():
        name = photos.thumb_name(filename)  # photos uploaded before thumbnails existed fall back to full size
    response = send_from_directory(_upload_dir(), name, mimetype="image/jpeg", max_age=3600)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@bp.post("/items/<int:item_id>/photos")
@login_required
def photo_add(item_id: int) -> Any:
    check_csrf()
    _item_or_404(item_id)
    if not _store_uploads(lambda filename, original: repo().add_photo(item_id, filename, original)):
        abort(404)
    return redirect(url_for("main.item_detail", item_id=item_id) + "#photos")


@bp.app_template_global()
def photo_moves(photos: list[dict[str, Any]], index: int) -> dict[str, str]:
    """The new order (comma-separated ids) for each move button on photo `index`."""
    ids = [int(p["id"]) for p in photos]
    moves: dict[str, str] = {}
    if index > 0:
        earlier = ids[:]
        earlier[index - 1], earlier[index] = earlier[index], earlier[index - 1]
        moves["earlier"] = ",".join(map(str, earlier))
        moves["cover"] = ",".join(map(str, [ids[index], *ids[:index], *ids[index + 1:]]))
    if index < len(ids) - 1:
        later = ids[:]
        later[index + 1], later[index] = later[index], later[index + 1]
        moves["later"] = ",".join(map(str, later))
    return moves


def _wants_json() -> bool:
    """The gallery script posts with fetch and asks for JSON instead of a redirect."""
    return request.accept_mimetypes.best == "application/json"


def _reordered(ok: bool, back: str) -> Any:
    if _wants_json():
        return (jsonify({"ok": True}), 200) if ok else (jsonify({"error": _REORDER_FAILED}), 409)
    flash("Photo order saved." if ok else _REORDER_FAILED, "success" if ok else "error")
    return redirect(back)


_REORDER_FAILED = "The photos changed while you were arranging them. Reload the page and try again."


def _captioned(row: dict[str, Any] | None, error: str | None, back: Callable[[dict[str, Any]], str]) -> Any:
    if row is None:
        abort(404)
    if _wants_json():
        return (jsonify({"error": error}), 400) if error else (jsonify({"caption": row.get("caption")}), 200)
    flash(error or "Caption saved.", "error" if error else "success")
    return redirect(back(row))


@bp.post("/items/<int:item_id>/photos/order")
@login_required
def photo_order(item_id: int) -> Any:
    check_csrf()
    _item_or_404(item_id)
    ids = parse_id_list(request.form.get("order"))
    ok = ids is not None and repo().reorder_photos(item_id, ids)
    return _reordered(ok, url_for("main.item_detail", item_id=item_id) + "#photos")


@bp.post("/photos/<int:photo_id>/caption")
@login_required
def photo_caption(photo_id: int) -> Any:
    check_csrf()
    caption, error = parse_caption(request.form.get("caption"))
    row = repo().get_photo(photo_id) if error else repo().set_photo_caption(photo_id, caption)
    return _captioned(row, error, lambda r: url_for("main.item_detail", item_id=r["item_id"]) + "#photos")


@bp.post("/photos/<int:photo_id>/to-catalog")
@login_required
def photo_to_catalog(photo_id: int) -> Any:
    """Copy one of a piece's photos to the reference photos of its catalog entry."""
    check_csrf()
    r = repo()
    photo = r.get_photo(photo_id)
    if photo is None:
        abort(404)
    item = _item_or_404(photo["item_id"])
    back = url_for("main.item_detail", item_id=item["id"]) + "#photos"
    catalog_id = item.get("catalog_id")
    if not catalog_id or r.get_catalog_item(catalog_id) is None:
        flash("Link this piece to a catalog entry first (Edit → Look up in catalog).", "error")
        return redirect(back)
    if any(photos.same_content(_upload_dir(), photo["filename"], existing["filename"])
           for existing in r.list_catalog_photos(catalog_id)):
        flash("That photo is already one of the catalog entry's reference photos.", "error")
        return redirect(back)
    try:
        filename = photos.copy_photo(_upload_dir(), photo["filename"])
    except photos.PhotoError as exc:
        flash(str(exc), "error")
        return redirect(back)
    try:
        new_id = r.add_catalog_photo(catalog_id, filename, photo.get("original_name"), photo.get("caption"))
    except Exception:
        photos.delete_photo_file(_upload_dir(), filename)
        raise
    if new_id is None:
        photos.delete_photo_file(_upload_dir(), filename)
        abort(404)
    log.info("Copied photo id=%s of item id=%s to catalog id=%s", photo_id, item["id"], catalog_id)
    flash("Added to the catalog entry's reference photos.", "success")
    return redirect(back)


@bp.get("/photos/<int:photo_id>")
@login_required
def photo_file(photo_id: int) -> Any:
    photo = repo().get_photo(photo_id)
    if photo is None:
        abort(404)
    return _send_photo(photo["filename"], thumb=False)


@bp.get("/photos/<int:photo_id>/thumb")
@login_required
def photo_thumb(photo_id: int) -> Any:
    photo = repo().get_photo(photo_id)
    if photo is None:
        abort(404)
    return _send_photo(photo["filename"], thumb=True)


@bp.post("/photos/<int:photo_id>/delete")
@login_required
def photo_delete(photo_id: int) -> Any:
    check_csrf()
    photo = repo().delete_photo(photo_id)
    if photo is None:
        abort(404)
    photos.delete_photo_file(_upload_dir(), photo["filename"])
    flash("Photo removed.", "success")
    return redirect(url_for("main.item_detail", item_id=photo["item_id"]) + "#photos")


# -- 3D scans ----------------------------------------------------------------


def _scans_for_page(item_id: int) -> list[dict[str, Any]]:
    rows = repo().list_scans(item_id)
    if rows and current_app.config.get("MODEL_VIEWER_AVAILABLE"):
        g.needs_3d_viewer = True
    for row in rows:
        row["size_text"] = scans.human_size(int(row["size_bytes"]))
    return rows


@bp.post("/items/<int:item_id>/scans")
@login_required
def scan_add(item_id: int) -> Any:
    check_csrf()
    _item_or_404(item_id)
    uploads = [f for f in request.files.getlist("scan") if f and f.filename]
    if not uploads:
        flash("Choose a 3D scan file to upload.", "error")
        return redirect(url_for("main.item_detail", item_id=item_id) + "#scans")
    max_bytes = current_app.config["DICKENS"].max_scan_mb * 1024 * 1024
    added = 0
    for upload in uploads[:5]:
        original = photos.clean_original_name(upload.filename)
        try:
            filename, size = scans.save_scan(upload.stream, original, _upload_dir(), max_bytes)
        except scans.ScanError as exc:
            flash(f"{original or 'A file'}: {exc}", "error")
            continue
        try:
            scan_id = repo().add_scan(item_id, filename, original, size)
        except Exception:
            scans.delete_scan_file(_upload_dir(), filename)
            raise
        if scan_id is None:
            scans.delete_scan_file(_upload_dir(), filename)
            abort(404)
        added += 1
    if len(uploads) > 5:
        flash("Upload at most 5 scans at a time; the rest were skipped.", "error")
    if added:
        flash(f"Added {added} 3D scan{'' if added == 1 else 's'}.", "success")
    return redirect(url_for("main.item_detail", item_id=item_id) + "#scans")


def _send_scan(scan_id: int, download: bool) -> Any:
    scan = repo().get_scan(scan_id)
    if scan is None or not scans.FILENAME_RE.match(scan["filename"]):
        abort(404)
    response = send_from_directory(
        _upload_dir(), scan["filename"], mimetype=scans.mime_type(scan["filename"]), max_age=3600,
        as_attachment=download, download_name=scans.download_name(scan["original_name"], scan["filename"]),
    )
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@bp.get("/scans/<int:scan_id>")
@login_required
def scan_file(scan_id: int) -> Any:
    return _send_scan(scan_id, download=False)


@bp.get("/scans/<int:scan_id>/download")
@login_required
def scan_download(scan_id: int) -> Any:
    return _send_scan(scan_id, download=True)


@bp.post("/scans/<int:scan_id>/delete")
@login_required
def scan_delete(scan_id: int) -> Any:
    check_csrf()
    scan = repo().delete_scan(scan_id)
    if scan is None:
        abort(404)
    scans.delete_scan_file(_upload_dir(), scan["filename"])
    flash("3D scan removed.", "success")
    return redirect(url_for("main.item_detail", item_id=scan["item_id"]) + "#scans")


# -- reference catalog -------------------------------------------------------


@bp.get("/catalog/search")
@login_required
def catalog_search() -> Any:
    query = request.args.get("q", "")
    results = repo().search_catalog(query) if len(query.strip()) >= 2 else []
    response = jsonify({"results": [_catalog_json(row) for row in results]})
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.get("/catalog")
@login_required
def catalog_browse() -> Any:
    query = " ".join(request.args.get("q", "").split())[:100]
    results = repo().search_catalog(query, limit=100) if len(query) >= 2 else []
    return render_template("catalog.html", query=query, results=results)


@bp.get("/catalog/<int:catalog_id>")
@login_required
def catalog_entry(catalog_id: int) -> Any:
    r = repo()
    entry = r.get_catalog_item(catalog_id)
    if entry is None:
        abort(404)
    return render_template(
        "catalog_entry.html",
        entry=entry,
        photos=r.list_catalog_photos(catalog_id),
        owned=r.items_for_catalog(catalog_id),
        max_photos=MAX_PHOTOS_PER_UPLOAD,
    )


@bp.post("/catalog/<int:catalog_id>/photos")
@login_required
def catalog_photo_add(catalog_id: int) -> Any:
    check_csrf()
    if repo().get_catalog_item(catalog_id) is None:
        abort(404)
    if not _store_uploads(lambda filename, original: repo().add_catalog_photo(catalog_id, filename, original)):
        abort(404)
    return redirect(url_for("main.catalog_entry", catalog_id=catalog_id) + "#photos")


@bp.post("/catalog/<int:catalog_id>/photos/order")
@login_required
def catalog_photo_order(catalog_id: int) -> Any:
    check_csrf()
    if repo().get_catalog_item(catalog_id) is None:
        abort(404)
    ids = parse_id_list(request.form.get("order"))
    ok = ids is not None and repo().reorder_catalog_photos(catalog_id, ids)
    return _reordered(ok, url_for("main.catalog_entry", catalog_id=catalog_id) + "#photos")


@bp.post("/catalog-photos/<int:photo_id>/caption")
@login_required
def catalog_photo_caption(photo_id: int) -> Any:
    check_csrf()
    caption, error = parse_caption(request.form.get("caption"))
    row = repo().get_catalog_photo(photo_id) if error else repo().set_catalog_photo_caption(photo_id, caption)
    return _captioned(row, error, lambda r: url_for("main.catalog_entry", catalog_id=r["catalog_id"]) + "#photos")


@bp.get("/catalog-photos/<int:photo_id>")
@login_required
def catalog_photo_file(photo_id: int) -> Any:
    photo = repo().get_catalog_photo(photo_id)
    if photo is None:
        abort(404)
    return _send_photo(photo["filename"], thumb=False)


@bp.get("/catalog-photos/<int:photo_id>/thumb")
@login_required
def catalog_photo_thumb(photo_id: int) -> Any:
    photo = repo().get_catalog_photo(photo_id)
    if photo is None:
        abort(404)
    return _send_photo(photo["filename"], thumb=True)


@bp.post("/catalog-photos/<int:photo_id>/delete")
@login_required
def catalog_photo_delete(photo_id: int) -> Any:
    check_csrf()
    photo = repo().delete_catalog_photo(photo_id)
    if photo is None:
        abort(404)
    photos.delete_photo_file(_upload_dir(), photo["filename"])
    flash("Photo removed.", "success")
    return redirect(url_for("main.catalog_entry", catalog_id=photo["catalog_id"]) + "#photos")


# -- summaries ---------------------------------------------------------------


@bp.get("/storage")
@login_required
def storage() -> Any:
    rows = repo().storage_summary()
    for row in rows:
        args = {c: row[c] for c in STORAGE_COLUMNS if row[c]}
        args = {**args, "exact_storage": "yes"} if args else {"unstored": "yes"}
        args["sort"] = "storage"
        row["href"] = f"{url_for('main.item_list')}?{urlencode(args)}"
    return render_template("storage.html", rows=rows)


@bp.get("/summary")
@login_required
def summary() -> Any:
    return render_template("summary.html", summary=repo().collection_summary())


# -- export ------------------------------------------------------------------

_EXPORTS = {
    "collection.json": "application/json",
    "pieces.csv": "text/csv",
    "extras.csv": "text/csv",
    "photos-and-scans.zip": "application/zip",
}


@bp.get("/export/<name>")
@login_required
def export_file(name: str) -> Any:
    mimetype = _EXPORTS.get(name)
    if mimetype is None:
        abort(404)
    now = dt.datetime.now().replace(microsecond=0)
    data = repo().export_data()
    stem, ext = name.rsplit(".", 1)
    download = f"dickens-village-{stem}-{now:%Y-%m-%d}.{ext}"
    if name == "collection.json":
        body: Any = json.dumps(export.collection_json(data, now), indent=2, ensure_ascii=False)
    elif name == "pieces.csv":
        body = export.pieces_csv(data)
    elif name == "extras.csv":
        body = export.extras_csv(data)
    else:
        body = export.zip_stream(data, _upload_dir(), now)
    log.info("Export %s: %d pieces", name, len(data["items"]))
    response = current_app.response_class(body, mimetype=mimetype)
    if name.endswith(".csv") or name.endswith(".json"):
        response.charset = "utf-8"
    response.headers["Content-Disposition"] = f'attachment; filename="{download}"'
    response.headers["Cache-Control"] = "private, no-store"
    return response
