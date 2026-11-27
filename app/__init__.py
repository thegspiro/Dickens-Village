"""Dickens Village collection tracker - Flask application factory."""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from pathlib import Path
from decimal import Decimal
from typing import Any, Callable

from flask import Flask, g, render_template, request
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from .auth import LoginThrottle, csrf_token, current_user
from .config import Config, load_config
from .places import label as place_label
from .validation import CATEGORIES, CONDITIONS, VILLAGES, format_inches

RepoFactory = Callable[[Config], Any]

# Inline stylesheet(s) added by the pinned @google/model-viewer (scripts/fetch_vendor.sh).
MODEL_VIEWER_STYLE_HASHES = ("'sha256-yhlpZVZMy2vXExwTGihUWVSrOxyhMuvj+Ygg7pyBWek='",)


def _mysql_repo_factory(cfg: Config) -> Any:
    from .db import connect
    from .repository import MySQLRepository

    return MySQLRepository(connect(cfg.database))


def _money(value: Any) -> str:
    if value is None or value == "":
        return ""
    return f"${Decimal(value):,.2f}"


def _date(value: Any) -> str:
    if isinstance(value, (dt.date, dt.datetime)):
        return f"{value:%b} {value.day}, {value.year}"
    return "" if value is None else str(value)


# Files the service worker caches for offline use, besides app/static itself.
_OFFLINE_TEMPLATES = ("offline_app.html", "offline.html")


def static_build_id(static_dir: Path, template_dir: Path) -> str:
    """A short hash of the app's own static files and offline templates.

    The service worker is served with it as its cache version, so every deploy
    that changes one of these files makes phones fetch a fresh, consistent copy.
    The vendored 3D viewer is left out: the service worker doesn't cache it.
    """
    digest = hashlib.sha256()
    files = [p for p in static_dir.rglob("*") if p.is_file() and "vendor" not in p.relative_to(static_dir).parts]
    files += [template_dir / name for name in _OFFLINE_TEMPLATES]
    for path in sorted(files):
        base = static_dir if path.is_relative_to(static_dir) else template_dir
        digest.update(str(path.relative_to(base)).encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def create_app(config: Config | None = None, repo_factory: RepoFactory | None = None) -> Flask:
    cfg = config or load_config()
    factory = repo_factory or _mysql_repo_factory

    logging.basicConfig(
        level=cfg.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=cfg.secret_key,
        DICKENS=cfg,
        # Whole-request cap (several photos at once); each photo is also checked
        # against MAX_UPLOAD_MB on its own.
        MAX_CONTENT_LENGTH=(max(cfg.max_request_mb, cfg.max_upload_mb, cfg.max_scan_mb) + 1) * 1024 * 1024,
        SESSION_COOKIE_NAME="dickens_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=cfg.cookie_secure,
        PERMANENT_SESSION_LIFETIME=dt.timedelta(days=30),
    )
    app.extensions["dickens_throttle"] = LoginThrottle()
    app.extensions["dickens_repo_factory"] = factory

    if cfg.trusted_proxies:
        app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
            app.wsgi_app,
            x_for=cfg.trusted_proxies,
            x_proto=cfg.trusted_proxies,
            x_host=cfg.trusted_proxies,
        )

    app.jinja_env.filters["money"] = _money
    app.jinja_env.filters["nicedate"] = _date
    app.jinja_env.filters["inches"] = format_inches
    app.jinja_env.filters["place_label"] = place_label
    # Imported here, not at the top: the entrypoint runs app.catalog as a script
    # (python -m app.catalog), which warns if importing the app package loaded it first.
    from .catalog import list_label

    app.jinja_env.filters["catalog_list"] = list_label
    app.jinja_env.globals.update(
        csrf_token=csrf_token,
        current_user=current_user,
        CATEGORIES=CATEGORIES,
        VILLAGES=VILLAGES,
        CONDITIONS=CONDITIONS,
    )

    app.config["STATIC_BUILD"] = static_build_id(Path(app.static_folder), Path(app.root_path) / "templates")

    viewer = Path(app.static_folder) / "vendor" / "model-viewer" / "model-viewer.min.js"
    app.config["MODEL_VIEWER_AVAILABLE"] = viewer.is_file()
    if not viewer.is_file():
        app.logger.info("3D viewer files not installed (run scripts/fetch_vendor.sh); scans offer downloads only")

    from .api import api
    from .views import bp

    app.register_blueprint(bp)
    app.register_blueprint(api)

    @app.teardown_appcontext
    def _close_repo(_exc: BaseException | None) -> None:
        repo = g.pop("repo", None)
        if repo is not None:
            repo.close()

    @app.after_request
    def _security_headers(response: Any) -> Any:
        script_src = "'self'"
        style_src = "'self'"
        extra = ""
        if g.get("needs_3d_viewer"):
            # model-viewer 4.0.0 inserts one fixed inline <style>; allow exactly
            # that stylesheet by its hash (not inline styles in general). If the
            # pinned version changes, the browser tests report the new hash.
            style_src += " " + " ".join(MODEL_VIEWER_STYLE_HASHES)
            # Only on pages that show a 3D scan: the viewer compiles WebAssembly
            # decoders and runs them in workers created from blob: URLs, and
            # reads textures embedded in models as blob:/data: URLs.
            script_src += " 'wasm-unsafe-eval'"
            extra = "; worker-src 'self' blob:; connect-src 'self' blob: data:"
        response.headers.setdefault(
            "Content-Security-Policy",
            f"default-src 'self'; img-src 'self' blob: data:; style-src {style_src}; script-src {script_src}; "
            f"form-action 'self'; frame-ancestors 'none'; base-uri 'none'{extra}",
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException) -> Any:
        if request.path == "/api/v1" or request.path.startswith("/api/v1/"):
            # No API route matched (unknown path or method), so the API blueprint's
            # own handler never ran; the phone app still expects a JSON error.
            from .api import _error

            return _error(exc.code or 500, exc.description or exc.name)
        return render_template("error.html", error=exc), exc.code

    @app.errorhandler(Exception)
    def _unhandled(exc: Exception) -> Any:
        app.logger.exception("Unhandled error")
        return render_template("error.html", error=None), 500

    return app
