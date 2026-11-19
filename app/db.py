"""MySQL connection handling (PyMySQL)."""

from __future__ import annotations

from typing import Any

from .config import DatabaseConfig


def connect(cfg: DatabaseConfig) -> Any:
    """Open a new PyMySQL connection that returns rows as dicts.

    autocommit is off: the repository commits or rolls back each unit of work.
    """
    import pymysql  # imported lazily so unit tests run without the driver
    import pymysql.cursors

    return pymysql.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        database=cfg.name,
        charset="utf8mb4",
        connect_timeout=cfg.connect_timeout,
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )
