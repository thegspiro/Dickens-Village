"""Configuration loaded from environment variables only.

Nothing here has a secret default. Missing required values raise ConfigError
at startup so a misconfigured container fails fast instead of half-working.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _get(env: Mapping[str, str], key: str, default: str | None = None) -> str | None:
    value = env.get(key)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _require(env: Mapping[str, str], key: str) -> str:
    value = _get(env, key)
    if value is None:
        raise ConfigError(f"Environment variable {key} is required")
    return value


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    value = _get(env, key)
    if value is None:
        return default
    lowered = value.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigError(f"{key} must be true or false, got {value!r}")


def _int(env: Mapping[str, str], key: str, default: int, minimum: int, maximum: int) -> int:
    value = _get(env, key)
    if value is None:
        return default
    try:
        number = int(value)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {value!r}") from exc
    if not minimum <= number <= maximum:
        raise ConfigError(f"{key} must be between {minimum} and {maximum}, got {number}")
    return number


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    name: str
    user: str
    password: str
    connect_timeout: int


@dataclass(frozen=True)
class Config:
    secret_key: str
    admin_username: str
    admin_password_hash: str
    cookie_secure: bool
    upload_dir: str
    max_upload_mb: int
    max_request_mb: int
    trusted_proxies: int
    log_level: str
    database: DatabaseConfig


def load_database_config(env: Mapping[str, str] | None = None) -> DatabaseConfig:
    env = os.environ if env is None else env
    return DatabaseConfig(
        host=_require(env, "DB_HOST"),
        port=_int(env, "DB_PORT", 3306, 1, 65535),
        name=_require(env, "DB_NAME"),
        user=_require(env, "DB_USER"),
        password=_require(env, "DB_PASSWORD"),
        connect_timeout=_int(env, "DB_CONNECT_TIMEOUT", 10, 1, 120),
    )


def load_config(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env

    secret_key = _require(env, "SECRET_KEY")
    if len(secret_key) < 32:
        raise ConfigError("SECRET_KEY must be at least 32 characters")

    password_hash = _require(env, "ADMIN_PASSWORD_HASH")
    if ":" not in password_hash or "$" not in password_hash:
        raise ConfigError(
            "ADMIN_PASSWORD_HASH does not look like a Werkzeug password hash; "
            "generate one with: python -m app.hashpw"
        )

    log_level = (_get(env, "LOG_LEVEL", "INFO") or "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError(f"LOG_LEVEL is not a valid level: {log_level!r}")

    return Config(
        secret_key=secret_key,
        admin_username=_require(env, "ADMIN_USERNAME"),
        admin_password_hash=password_hash,
        cookie_secure=_bool(env, "COOKIE_SECURE", True),
        upload_dir=_require(env, "UPLOAD_DIR"),
        max_upload_mb=_int(env, "MAX_UPLOAD_MB", 25, 1, 200),
        max_request_mb=_int(env, "MAX_REQUEST_MB", 200, 1, 2000),
        trusted_proxies=_int(env, "TRUSTED_PROXIES", 0, 0, 5),
        log_level=log_level,
        database=load_database_config(env),
    )
