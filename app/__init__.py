"""Dickens Village collection tracker - Flask application factory."""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal
from typing import Any, Callable

from flask import Flask, g, render_template
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from .auth import LoginThrottle, csrf_token, current_user
from .config import Config, load_config
from .validation import CATEGORIES, CONDITIONS

RepoFactory = Callable[[Config], Any]


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
        MAX_CONTENT_LENGTH=cfg.max_upload_mb * 1024 * 1024,
        SESSION_COOKIE_NAME="dickens_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=cfg.cookie_secure,
        PERMANENT_SESSION_LIFETIME=dt.timedelta(days=14),
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
    app.jinja_env.globals.update(
        csrf_token=csrf_token,
        current_user=current_user,
        CATEGORIES=CATEGORIES,
        CONDITIONS=CONDITIONS,
    )

    from .views import bp

    app.register_blueprint(bp)

    @app.teardown_appcontext
    def _close_repo(_exc: BaseException | None) -> None:
        repo = g.pop("repo", None)
        if repo is not None:
            repo.close()

    @app.after_request
    def _security_headers(response: Any) -> Any:
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException) -> Any:
        return render_template("error.html", error=exc), exc.code

    @app.errorhandler(Exception)
    def _unhandled(exc: Exception) -> Any:
        app.logger.exception("Unhandled error")
        return render_template("error.html", error=None), 500

    return app
