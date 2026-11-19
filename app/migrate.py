"""Plain-SQL migration runner.

Usage:
    python -m app.migrate status
    python -m app.migrate up            # apply all pending migrations
    python -m app.migrate down --yes    # roll back the most recent migration

Migrations live in ./migrations as NNNN_name.up.sql / NNNN_name.down.sql.
Statements are split on a semicolon at the end of a line, so a semicolon
must not end a line inside a string literal. Lines starting with "--" are
comments.

A MySQL named lock (GET_LOCK) makes concurrent runs (e.g. two containers
starting at once) wait for each other instead of racing. MySQL DDL commits
implicitly, so a migration that fails part way is not rolled back; every
up migration is written idempotently (IF NOT EXISTS) so it can be re-run
after the cause is fixed.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("dickens.migrate")

LOCK_NAME = "dickens_tracker_migrate"
LOCK_TIMEOUT_SECONDS = 60
DEFAULT_DIR = Path(__file__).resolve().parent.parent / "migrations"
_FILE_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.(up|down)\.sql$")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    up_path: Path
    down_path: Path | None


def discover(directory: Path = DEFAULT_DIR) -> list[Migration]:
    ups: dict[str, tuple[str, Path]] = {}
    downs: dict[str, Path] = {}
    for path in sorted(directory.iterdir()):
        match = _FILE_RE.match(path.name)
        if not match:
            continue
        number, name, direction = match.groups()
        version = f"{number}_{name}"
        if direction == "up":
            ups[version] = (name, path)
        else:
            downs[version] = path
    orphans = set(downs) - set(ups)
    if orphans:
        raise MigrationError(f"Down migration without matching up: {sorted(orphans)}")
    return [
        Migration(version=v, name=ups[v][0], up_path=ups[v][1], down_path=downs.get(v))
        for v in sorted(ups)
    ]


def split_statements(sql: str) -> list[str]:
    statements: list[str] = []
    buffer: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(buffer).strip().rstrip(";").strip()
            if statement:
                statements.append(statement)
            buffer = []
    if buffer:
        raise MigrationError("Migration file ends with a statement that has no terminating ';'")
    return statements


def _execute_all(conn: Any, statements: Iterable[str]) -> None:
    with conn.cursor() as cur:
        for statement in statements:
            cur.execute(statement)


def ensure_tracking_table(conn: Any) -> None:
    _execute_all(
        conn,
        [
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version VARCHAR(128) NOT NULL PRIMARY KEY,"
            " applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ") ENGINE = InnoDB DEFAULT CHARSET = utf8mb4"
        ],
    )
    conn.commit()


def applied_versions(conn: Any) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations ORDER BY version")
        return [row["version"] for row in cur.fetchall()]


def _acquire_lock(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s, %s) AS got", (LOCK_NAME, LOCK_TIMEOUT_SECONDS))
        row = cur.fetchone()
    if not row or row.get("got") != 1:
        raise MigrationError("Could not acquire migration lock; another migration may be running")


def _release_lock(conn: Any) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT RELEASE_LOCK(%s) AS released", (LOCK_NAME,))
            cur.fetchone()
    except Exception:  # noqa: BLE001 - lock is also released when the connection closes
        log.warning("Failed to release migration lock explicitly", exc_info=True)


def migrate_up(conn: Any, migrations: list[Migration]) -> list[str]:
    """Apply pending migrations in order. Returns the versions applied."""
    _acquire_lock(conn)
    try:
        ensure_tracking_table(conn)
        done = set(applied_versions(conn))
        known = {m.version for m in migrations}
        unknown = done - known
        if unknown:
            raise MigrationError(
                f"Database has migrations this code does not know about: {sorted(unknown)}. "
                "Refusing to continue (are you running an older image?)"
            )
        applied: list[str] = []
        for migration in migrations:
            if migration.version in done:
                continue
            log.info("Applying migration %s", migration.version)
            _execute_all(conn, split_statements(migration.up_path.read_text(encoding="utf-8")))
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)", (migration.version,)
                )
            conn.commit()
            applied.append(migration.version)
            log.info("Applied migration %s", migration.version)
        return applied
    except Exception:
        conn.rollback()
        raise
    finally:
        _release_lock(conn)


def migrate_down(conn: Any, migrations: list[Migration]) -> str | None:
    """Roll back the most recently applied migration. Returns its version."""
    _acquire_lock(conn)
    try:
        ensure_tracking_table(conn)
        done = applied_versions(conn)
        if not done:
            return None
        latest = done[-1]
        by_version = {m.version: m for m in migrations}
        migration = by_version.get(latest)
        if migration is None or migration.down_path is None:
            raise MigrationError(f"No down migration available for {latest}")
        log.warning("Rolling back migration %s", latest)
        _execute_all(conn, split_statements(migration.down_path.read_text(encoding="utf-8")))
        with conn.cursor() as cur:
            cur.execute("DELETE FROM schema_migrations WHERE version = %s", (latest,))
        conn.commit()
        log.warning("Rolled back migration %s", latest)
        return latest
    except Exception:
        conn.rollback()
        raise
    finally:
        _release_lock(conn)


def main(argv: list[str] | None = None) -> int:
    from .config import load_database_config
    from .db import connect

    parser = argparse.ArgumentParser(prog="python -m app.migrate")
    parser.add_argument("command", choices=["status", "up", "down"])
    parser.add_argument("--yes", action="store_true", help="confirm a rollback (down)")
    parser.add_argument("--dir", default=os.environ.get("MIGRATIONS_DIR", str(DEFAULT_DIR)))
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.command == "down" and not args.yes:
        print("Rolling back can drop tables and data. Re-run with --yes to confirm.", file=sys.stderr)
        return 2

    migrations = discover(Path(args.dir))
    conn = connect(load_database_config())
    try:
        if args.command == "status":
            ensure_tracking_table(conn)
            done = set(applied_versions(conn))
            for m in migrations:
                print(f"{'applied ' if m.version in done else 'pending '} {m.version}")
        elif args.command == "up":
            applied = migrate_up(conn, migrations)
            print(f"Applied {len(applied)} migration(s)" + (f": {', '.join(applied)}" if applied else ""))
        else:
            rolled = migrate_down(conn, migrations)
            print(f"Rolled back {rolled}" if rolled else "Nothing to roll back")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
