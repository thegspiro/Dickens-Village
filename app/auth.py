"""Single-admin authentication, CSRF protection and login throttling."""

from __future__ import annotations

import functools
import hmac
import logging
import secrets
import threading
import time
from collections import deque
from typing import Any, Callable, TypeVar
from urllib.parse import urlsplit

from flask import abort, current_app, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

log = logging.getLogger("dickens.auth")

F = TypeVar("F", bound=Callable[..., Any])

SESSION_USER = "user"
SESSION_CSRF = "csrf"

# Hash of a random throwaway password, used when the username is wrong so a
# failed login costs the same time either way. Generated once per process.
_DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(32))


class LoginThrottle:
    """In-process sliding-window limiter on failed logins per client address.

    State is per worker process, so with N gunicorn workers the effective limit
    is up to N times higher. That is acceptable for a single-admin app; put a
    reverse proxy with rate limiting in front if it is exposed to the internet.
    """

    def __init__(self, max_failures: int = 5, window_seconds: int = 900) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        attempts = self._failures.setdefault(key, deque())
        while attempts and now - attempts[0] > self.window_seconds:
            attempts.popleft()
        return attempts

    def is_blocked(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            return len(self._prune(key, now)) >= self.max_failures

    def record_failure(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._prune(key, now).append(now)
            if len(self._failures) > 10_000:  # bound memory under a spray of addresses
                self._failures = {k: v for k, v in self._failures.items() if v}

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


def verify_credentials(username: str, password: str) -> bool:
    cfg = current_app.config["DICKENS"]
    user_ok = hmac.compare_digest(username.encode("utf-8"), cfg.admin_username.encode("utf-8"))
    # Always run one hash check so timing does not reveal whether the username matched.
    pw_ok = check_password_hash(cfg.admin_password_hash if user_ok else _DUMMY_HASH, password)
    return user_ok and pw_ok


def login_user(username: str) -> None:
    session.clear()  # new session on login prevents session fixation
    session[SESSION_USER] = username
    session[SESSION_CSRF] = secrets.token_urlsafe(32)
    session.permanent = True


def logout_user() -> None:
    session.clear()


def current_user() -> str | None:
    return session.get(SESSION_USER)


def csrf_token() -> str:
    token = session.get(SESSION_CSRF)
    if not token:
        token = secrets.token_urlsafe(32)
        session[SESSION_CSRF] = token
    return token


def check_csrf() -> None:
    """Abort with 400 unless the form's csrf_token matches the session's."""
    expected = session.get(SESSION_CSRF)
    supplied = request.form.get("csrf_token", "")
    if not expected or not hmac.compare_digest(expected, supplied):
        log.warning("CSRF check failed for %s %s", request.method, request.path)
        abort(400, description="Your form expired. Go back, reload the page and try again.")


def login_required(view: F) -> F:
    @functools.wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if current_user() is None:
            return redirect(url_for("main.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


def safe_next_url(target: str | None) -> str:
    """Only allow redirects to local paths (blocks open redirects like //evil.example)."""
    if not target:
        return url_for("main.item_list")
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return url_for("main.item_list")
    return target
