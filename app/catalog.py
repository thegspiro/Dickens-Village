"""Load the reference catalog (data/catalog/*.csv) into the catalog_items table.

Usage:
    python -m app.catalog sync [DIRECTORY]   # import every CSV in the directory
    python -m app.catalog check [DIRECTORY]  # validate the CSVs without a database

The import is an upsert keyed on source_key, so running it again (it runs on
every container start) only updates rows whose CSV values changed. Rows that
disappear from a CSV are left in place, because collection items may link to
them. Every row is validated before anything is written; one bad row aborts
the whole import and nothing is changed.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("dickens.catalog")

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data" / "catalog"
LOCK_NAME = "dickens_tracker_catalog"
LOCK_TIMEOUT_SECONDS = 60
CATEGORIES = {"building", "figure", "accessory"}

COLUMNS = (
    "source_key",
    "list_name",
    "item_number",
    "name",
    "details",
    "series",
    "year_issued",
    "year_retired",
    "is_current",
    "srp_usd",
    "srp_cad",
    "default_category",
    "parent_key",
    "source_page",
    "source_text",
)
REQUIRED_HEADERS = {
    "source_key", "list", "item_number", "name", "details", "series", "year_issued",
    "year_retired", "is_current", "srp_usd", "srp_cad", "default_category", "parent_key",
    "source_page", "source_text",
}
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_LENGTHS = {
    "item_number": 32, "name": 200, "details": 1000, "series": 120, "source_text": 1000, "list": 32,
}


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class CatalogRow:
    values: dict[str, Any]

    def as_tuple(self) -> tuple[Any, ...]:
        return tuple(self.values[c] for c in COLUMNS)


def _opt(value: str) -> str | None:
    value = value.strip()
    return value or None


def _year(value: str, field: str, where: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    if not value.isdigit() or not 1900 <= int(value) <= 2100:
        raise CatalogError(f"{where}: {field} must be a year, got {value!r}")
    return int(value)


def _money(value: str, field: str, where: str) -> Decimal | None:
    value = value.strip()
    if not value:
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise CatalogError(f"{where}: {field} must be an amount, got {value!r}") from None
    if not amount.is_finite() or amount < 0 or amount > Decimal("99999999.99"):
        raise CatalogError(f"{where}: {field} is out of range: {value!r}")
    return amount.quantize(Decimal("0.01"))


def parse_rows(path: Path) -> list[CatalogRow]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = REQUIRED_HEADERS - set(reader.fieldnames or [])
        if missing:
            raise CatalogError(f"{path.name}: missing columns {sorted(missing)}")
        rows: list[CatalogRow] = []
        keys: set[str] = set()
        for line_no, raw in enumerate(reader, start=2):
            where = f"{path.name} line {line_no}"
            key = raw["source_key"].strip()
            if not _KEY_RE.match(key):
                raise CatalogError(f"{where}: invalid source_key {key!r}")
            if key in keys:
                raise CatalogError(f"{where}: duplicate source_key {key!r}")
            keys.add(key)
            for field, limit in _LENGTHS.items():
                if len(raw[field].strip()) > limit:
                    raise CatalogError(f"{where}: {field} is longer than {limit} characters")
            name = raw["name"].strip()
            if not name:
                raise CatalogError(f"{where}: name is required")
            category = raw["default_category"].strip()
            if category and category not in CATEGORIES:
                raise CatalogError(f"{where}: unknown default_category {category!r}")
            if raw["is_current"].strip() not in {"0", "1"}:
                raise CatalogError(f"{where}: is_current must be 0 or 1")
            parent = raw["parent_key"].strip()
            if parent and not _KEY_RE.match(parent):
                raise CatalogError(f"{where}: invalid parent_key {parent!r}")
            page = raw["source_page"].strip()
            if page and not (page.isdigit() and int(page) < 10_000):
                raise CatalogError(f"{where}: invalid source_page {page!r}")
            issued = _year(raw["year_issued"], "year_issued", where)
            retired = _year(raw["year_retired"], "year_retired", where)
            if issued and retired and retired < issued:
                raise CatalogError(f"{where}: year_retired is before year_issued")
            rows.append(CatalogRow({
                "source_key": key,
                "list_name": raw["list"].strip() or path.stem,
                "item_number": _opt(raw["item_number"]),
                "name": name,
                "details": _opt(raw["details"]),
                "series": _opt(raw["series"]),
                "year_issued": issued,
                "year_retired": retired,
                "is_current": int(raw["is_current"].strip()),
                "srp_usd": _money(raw["srp_usd"], "srp_usd", where),
                "srp_cad": _money(raw["srp_cad"], "srp_cad", where),
                "default_category": category or None,
                "parent_key": parent or None,
                "source_page": int(page) if page else None,
                "source_text": _opt(raw["source_text"]),
            }))
    missing_parents = {r.values["parent_key"] for r in rows if r.values["parent_key"]} - keys
    if missing_parents:
        raise CatalogError(f"{path.name}: parent_key values with no matching row: {sorted(missing_parents)}")
    return rows


def load_directory(directory: Path) -> list[CatalogRow]:
    files = sorted(directory.glob("*.csv"))
    rows: list[CatalogRow] = []
    seen: set[str] = set()
    for path in files:
        for row in parse_rows(path):
            key = row.values["source_key"]
            if key in seen:
                raise CatalogError(f"{path.name}: source_key {key!r} also appears in another file")
            seen.add(key)
            rows.append(row)
    return rows


_UPSERT_SQL = (
    f"INSERT INTO catalog_items ({', '.join(COLUMNS)}) "
    f"VALUES ({', '.join(['%s'] * len(COLUMNS))}) AS new "
    "ON DUPLICATE KEY UPDATE "
    + ", ".join(f"{c} = new.{c}" for c in COLUMNS if c != "source_key")
)


def sync(conn: Any, rows: Iterable[CatalogRow], batch_size: int = 200) -> int:
    """Upsert rows in one transaction, under a named lock. Returns rows sent."""
    rows = list(rows)
    with conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s, %s) AS got", (LOCK_NAME, LOCK_TIMEOUT_SECONDS))
        got = cur.fetchone()
    if not got or got.get("got") != 1:
        raise CatalogError("Could not acquire the catalog import lock")
    try:
        with conn.cursor() as cur:
            for start in range(0, len(rows), batch_size):
                cur.executemany(_UPSERT_SQL, [r.as_tuple() for r in rows[start:start + batch_size]])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK(%s) AS released", (LOCK_NAME,))
                cur.fetchone()
        except Exception:  # noqa: BLE001 - also released when the connection closes
            log.warning("Failed to release catalog lock explicitly", exc_info=True)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.catalog")
    parser.add_argument("command", choices=["sync", "check"])
    parser.add_argument("directory", nargs="?", default=os.environ.get("CATALOG_DIR", str(DEFAULT_DIR)))
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    directory = Path(args.directory)
    if not directory.is_dir():
        print(f"Catalog directory not found: {directory}", file=sys.stderr)
        return 1
    try:
        rows = load_directory(directory)
    except CatalogError as exc:
        print(f"Catalog data is invalid: {exc}", file=sys.stderr)
        return 1
    if args.command == "check":
        print(f"{len(rows)} catalog rows are valid")
        return 0

    from .config import load_database_config
    from .db import connect

    conn = connect(load_database_config())
    try:
        count = sync(conn, rows)
    finally:
        conn.close()
    log.info("Catalog sync complete: %d rows from %s", count, directory)
    print(f"Synced {count} catalog rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
