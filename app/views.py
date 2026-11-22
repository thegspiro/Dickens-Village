"""HTTP routes."""

from __future__ import annotations

import datetime as dt
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

from . import photos
from .auth import (
    check_csrf,
    current_user,
    login_required,
    login_user,
    logout_user,
    safe_next_url,
    verify_credentials,
)
from .repository import PAGE_SIZE
from .validation import (
    ITEM_BOOL_FIELDS,
    ITEM_COLUMNS,
    MEASUREMENT_FIELDS,
    format_inches,
    SORTS,
    STORAGE_COLUMNS,
    parse_extra_form,
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


@bp.get("/sw.js")
def service_worker() -> Any:
    response = send_from_directory(current_app.static_folder, "sw.js", mimetype="text/javascript", max_age=0)
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
        if not errors:
            if not repo().update_item(item_id, data):
                abort(404)
            flash("Changes saved.", "success")
            return redirect(url_for("main.item_detail", item_id=item_id))
        return (
            render_template(
                "item_form.html", form=request.form, errors=errors, item=item, catalog=catalog,
                suggestions=_storage_suggestions(),
            ),
            400,
        )
    catalog = repo().get_catalog_item(item["catalog_id"]) if item.get("catalog_id") else None
    return render_template(
        "item_form.html", form=_row_to_form(item), errors={}, item=item, catalog=catalog,
        suggestions=_storage_suggestions(),
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
        if not errors:
            if not repo().update_extra(item_id, extra_id, data):
                abort(404)
            flash("Extra updated.", "success")
            return redirect(url_for("main.item_detail", item_id=item_id) + "#extras")
        return render_template("extra_form.html", item=item, extra=extra, form=request.form, errors=errors), 400
    form = {
        "description": extra["description"],
        "quantity": str(extra["quantity"]),
        "condition_grade": extra["condition_grade"] or "",
        "notes": extra["notes"] or "",
    }
    return render_template("extra_form.html", item=item, extra=extra, form=form, errors={})


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
            filename = photos.save_photo(upload.stream, _upload_dir())
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
