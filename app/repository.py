"""All SQL lives here. Every query is parameterized; the only strings ever
interpolated into SQL are fixed fragments chosen from whitelists in this file."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from .validation import ITEM_COLUMNS, STORAGE_COLUMNS

log = logging.getLogger("dickens.repository")

PAGE_SIZE = 50

_ORDER_BY: dict[str, str] = {
    "name": "i.name ASC, i.id ASC",
    "category": "i.category ASC, i.name ASC, i.id ASC",
    "year_introduced": "i.year_introduced IS NULL, i.year_introduced ASC, i.name ASC, i.id ASC",
    "storage": (
        "i.storage_location IS NULL, i.storage_location ASC, i.storage_tub ASC, "
        "i.storage_tray ASC, i.name ASC, i.id ASC"
    ),
    "estimated_value": "i.estimated_value IS NULL, i.estimated_value DESC, i.name ASC, i.id ASC",
    "updated": "i.updated_at DESC, i.id DESC",
}

_DISTINCT_COLUMNS = frozenset({*STORAGE_COLUMNS, "series", "purchased_from"})

_EXTRAS_COUNT = (
    "(SELECT COALESCE(SUM(e.quantity), 0) FROM item_extras e WHERE e.item_id = i.id) AS extras_count"
)


_CATALOG_FIELDS = (
    "c.id, c.list_name, c.item_number, c.name, c.details, c.series, c.year_issued, "
    "c.year_retired, c.is_current, c.srp_usd, c.srp_cad, c.default_category, "
    "p.name AS parent_name, p.item_number AS parent_item_number, "
    "(SELECT MIN(cp.id) FROM catalog_photos cp WHERE cp.catalog_id = c.id) AS photo_id, "
    "(SELECT COUNT(*) FROM catalog_photos cp WHERE cp.catalog_id = c.id) AS photo_count"
)


def _is_duplicate_key(exc: Exception) -> bool:
    """True for MySQL error 1062 (duplicate entry for a unique key)."""
    args = getattr(exc, "args", ())
    return bool(args) and args[0] == 1062


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class MySQLRepository:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            log.warning("Error closing database connection", exc_info=True)

    @contextmanager
    def _write(self) -> Iterator[Any]:
        """Run a unit of work in one transaction."""
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    def _all(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

    def _one(self, sql: str, params: Any = None) -> dict[str, Any] | None:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def _end_read(self) -> None:
        # Close the implicit read transaction so the next read sees fresh data.
        self._conn.commit()

    # -- health ---------------------------------------------------------------

    def ping(self) -> bool:
        row = self._one("SELECT 1 AS ok")
        self._end_read()
        return bool(row and row["ok"] == 1)

    # -- items ----------------------------------------------------------------

    def list_items(self, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        where: list[str] = []
        params: list[Any] = []

        if filters.get("q"):
            like = f"%{_like_escape(filters['q'])}%"
            where.append("(i.name LIKE %s OR i.item_number LIKE %s OR i.series LIKE %s)")
            params.extend([like, like, like])
        for column in ("category", "condition_grade", *STORAGE_COLUMNS):
            if filters.get(column):
                where.append(f"i.{column} = %s")  # column comes from a fixed tuple
                params.append(filters[column])
        if filters.get("on_display") == "yes":
            where.append("i.on_display = 1")
        elif filters.get("on_display") == "no":
            where.append("i.on_display = 0")
        if filters.get("has_extras") == "yes":
            where.append("EXISTS (SELECT 1 FROM item_extras e WHERE e.item_id = i.id)")
        elif filters.get("has_extras") == "no":
            where.append("NOT EXISTS (SELECT 1 FROM item_extras e WHERE e.item_id = i.id)")
        if filters.get("exact_storage"):
            for column in STORAGE_COLUMNS:
                if not filters.get(column):
                    where.append(f"i.{column} IS NULL")
        if filters.get("unstored"):
            where.append("i.storage_location IS NULL AND i.storage_tub IS NULL AND i.storage_tray IS NULL")

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        order_sql = _ORDER_BY.get(filters.get("sort", "name"), _ORDER_BY["name"])
        page = max(int(filters.get("page", 1)), 1)

        total_row = self._one(f"SELECT COUNT(*) AS n FROM items i {where_sql}", params)
        rows = self._all(
            f"SELECT i.*, {_EXTRAS_COUNT} FROM items i {where_sql} "
            f"ORDER BY {order_sql} LIMIT %s OFFSET %s",
            [*params, PAGE_SIZE, (page - 1) * PAGE_SIZE],
        )
        self._end_read()
        return rows, int(total_row["n"]) if total_row else 0

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        row = self._one(f"SELECT i.*, {_EXTRAS_COUNT} FROM items i WHERE i.id = %s", (item_id,))
        self._end_read()
        return row

    def create_item(self, data: dict[str, Any]) -> int:
        columns = list(ITEM_COLUMNS)
        sql = (
            f"INSERT INTO items ({', '.join(columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))})"
        )
        with self._write() as cur:
            cur.execute(sql, [data[c] for c in columns])
            new_id = int(cur.lastrowid)
        log.info("Created item id=%s", new_id)
        return new_id

    def update_item(self, item_id: int, data: dict[str, Any]) -> bool:
        columns = list(ITEM_COLUMNS)
        sql = f"UPDATE items SET {', '.join(f'{c} = %s' for c in columns)} WHERE id = %s"
        with self._write() as cur:
            cur.execute("SELECT id FROM items WHERE id = %s FOR UPDATE", (item_id,))
            if cur.fetchone() is None:
                return False
            cur.execute(sql.replace(" WHERE id", ", version = version + 1 WHERE id"),
                        [*[data[c] for c in columns], item_id])
        log.info("Updated item id=%s", item_id)
        return True

    # -- sync support (phone app) ----------------------------------------------

    def get_item_by_client_uuid(self, client_uuid: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM items WHERE client_uuid = %s", (client_uuid,))
        self._end_read()
        return row

    def create_item_from_client(self, data: dict[str, Any], client_uuid: str) -> tuple[int, bool]:
        """Create an item sent by the phone. Returns (id, created).

        Safe to retry: a second request with the same client_uuid returns the
        first item's id with created=False instead of inserting a duplicate.
        """
        existing = self.get_item_by_client_uuid(client_uuid)
        if existing:
            return int(existing["id"]), False
        columns = [*ITEM_COLUMNS, "client_uuid"]
        sql = f"INSERT INTO items ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})"
        try:
            with self._write() as cur:
                cur.execute(sql, [*[data[c] for c in ITEM_COLUMNS], client_uuid])
                new_id = int(cur.lastrowid)
        except Exception as exc:  # a concurrent retry won the race on the unique key
            if _is_duplicate_key(exc):
                existing = self.get_item_by_client_uuid(client_uuid)
                if existing:
                    return int(existing["id"]), False
            raise
        log.info("Created item id=%s from phone", new_id)
        return new_id, True

    def patch_item(self, item_id: int, resolve: Callable[[dict[str, Any]], tuple[str, Any]]
                   ) -> tuple[str, Any, dict[str, Any] | None]:
        """Lock the item, let resolve() decide, and write if it returns ("ok", data).

        resolve receives the current row and returns ("ok", full_item_data) or
        (status, payload) for any other outcome, which is passed back unchanged
        along with the current row. Returns ("missing", None, None) if the item
        does not exist.
        """
        columns = list(ITEM_COLUMNS)
        with self._write() as cur:
            cur.execute("SELECT * FROM items WHERE id = %s FOR UPDATE", (item_id,))
            row = cur.fetchone()
            if row is None:
                return "missing", None, None
            status, payload = resolve(row)
            if status != "ok":
                return status, payload, row
            cur.execute(
                f"UPDATE items SET {', '.join(f'{c} = %s' for c in columns)}, version = version + 1 "
                "WHERE id = %s",
                [*[payload[c] for c in columns], item_id],
            )
            cur.execute("SELECT * FROM items WHERE id = %s", (item_id,))
            fresh = cur.fetchone()
        log.info("Updated item id=%s from phone", item_id)
        return "ok", None, fresh

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        items = self._all("SELECT * FROM items ORDER BY id")
        extras = self._all("SELECT * FROM item_extras ORDER BY item_id, id")
        photos = self._all("SELECT id, item_id, client_uuid FROM item_photos ORDER BY item_id, id")
        scans = self._all("SELECT id, item_id FROM item_scans ORDER BY item_id, id")
        self._end_read()
        return {"items": items, "extras": extras, "photos": photos, "scans": scans}

    def snapshot_etag(self) -> str:
        row = self._one(
            "SELECT "
            "(SELECT CONCAT_WS(':', COUNT(*), COALESCE(SUM(version), 0), COALESCE(MAX(id), 0)) FROM items) AS i, "
            "(SELECT CONCAT_WS(':', COUNT(*), COALESCE(SUM(version), 0), COALESCE(MAX(id), 0)) FROM item_extras) AS e, "
            "(SELECT CONCAT_WS(':', COUNT(*), COALESCE(SUM(id), 0)) FROM item_photos) AS p, "
            "(SELECT CONCAT_WS(':', COUNT(*), COALESCE(SUM(id), 0)) FROM item_scans) AS s"
        )
        self._end_read()
        return f"{row['i']}/{row['e']}/{row['p']}/{row['s']}"

    def catalog_all(self) -> list[dict[str, Any]]:
        rows = self._all(
            f"SELECT {_CATALOG_FIELDS} FROM catalog_items c "
            "LEFT JOIN catalog_items p ON p.source_key = c.parent_key ORDER BY c.source_key"
        )
        self._end_read()
        return rows

    def catalog_etag(self) -> str:
        row = self._one(
            "SELECT (SELECT CONCAT_WS(':', COUNT(*), COALESCE(MAX(updated_at), '')) FROM catalog_items) AS c, "
            "(SELECT CONCAT_WS(':', COUNT(*), COALESCE(SUM(id), 0)) FROM catalog_photos) AS p"
        )
        self._end_read()
        return f"{row['c']}/{row['p']}"

    def delete_item(self, item_id: int) -> list[str] | None:
        """Delete an item and (via ON DELETE CASCADE) its extras, links and photo rows.

        Returns the photo and scan filenames to remove from disk, or None if the item did not exist.
        """
        with self._write() as cur:
            cur.execute("SELECT id FROM items WHERE id = %s FOR UPDATE", (item_id,))
            if cur.fetchone() is None:
                return None
            cur.execute("SELECT filename FROM item_photos WHERE item_id = %s "
                        "UNION ALL SELECT filename FROM item_scans WHERE item_id = %s", (item_id, item_id))
            filenames = [row["filename"] for row in cur.fetchall()]
            cur.execute("DELETE FROM items WHERE id = %s", (item_id,))
        log.info("Deleted item id=%s (%d photos)", item_id, len(filenames))
        return filenames

    def distinct_values(self, column: str) -> list[str]:
        if column not in _DISTINCT_COLUMNS:
            raise ValueError(f"Column not allowed: {column}")
        rows = self._all(
            f"SELECT DISTINCT {column} AS v FROM items WHERE {column} IS NOT NULL ORDER BY {column} LIMIT 500"
        )
        self._end_read()
        return [row["v"] for row in rows]

    # -- extras ---------------------------------------------------------------

    def list_extras(self, item_id: int) -> list[dict[str, Any]]:
        rows = self._all(
            "SELECT * FROM item_extras WHERE item_id = %s ORDER BY description, id", (item_id,)
        )
        self._end_read()
        return rows

    def get_extra(self, item_id: int, extra_id: int) -> dict[str, Any] | None:
        row = self._one(
            "SELECT * FROM item_extras WHERE id = %s AND item_id = %s", (extra_id, item_id)
        )
        self._end_read()
        return row

    def add_extra(self, item_id: int, data: dict[str, Any]) -> int | None:
        with self._write() as cur:
            cur.execute("SELECT id FROM items WHERE id = %s FOR UPDATE", (item_id,))
            if cur.fetchone() is None:
                return None
            cur.execute(
                "INSERT INTO item_extras (item_id, description, quantity, condition_grade, notes) "
                "VALUES (%s, %s, %s, %s, %s)",
                (item_id, data["description"], data["quantity"], data["condition_grade"], data["notes"]),
            )
            return int(cur.lastrowid)

    def add_extra_from_client(self, item_id: int, data: dict[str, Any], client_uuid: str
                              ) -> tuple[int | None, bool]:
        """Idempotent version of add_extra for the phone. Returns (id, created); id None if no item."""
        existing = self._one("SELECT id, item_id FROM item_extras WHERE client_uuid = %s", (client_uuid,))
        self._end_read()
        if existing:
            return int(existing["id"]), False
        try:
            with self._write() as cur:
                cur.execute("SELECT id FROM items WHERE id = %s FOR UPDATE", (item_id,))
                if cur.fetchone() is None:
                    return None, False
                cur.execute(
                    "INSERT INTO item_extras (item_id, description, quantity, condition_grade, notes, client_uuid) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (item_id, data["description"], data["quantity"], data["condition_grade"], data["notes"],
                     client_uuid),
                )
                return int(cur.lastrowid), True
        except Exception as exc:
            if _is_duplicate_key(exc):
                again = self._one("SELECT id FROM item_extras WHERE client_uuid = %s", (client_uuid,))
                self._end_read()
                if again:
                    return int(again["id"]), False
            raise

    def patch_extra(self, item_id: int, extra_id: int, resolve: Callable[[dict[str, Any]], tuple[str, Any]]
                    ) -> tuple[str, Any, dict[str, Any] | None]:
        """Same contract as patch_item, for one extra of one item."""
        with self._write() as cur:
            cur.execute("SELECT * FROM item_extras WHERE id = %s AND item_id = %s FOR UPDATE", (extra_id, item_id))
            row = cur.fetchone()
            if row is None:
                return "missing", None, None
            status, payload = resolve(row)
            if status != "ok":
                return status, payload, row
            cur.execute(
                "UPDATE item_extras SET description = %s, quantity = %s, condition_grade = %s, notes = %s, "
                "version = version + 1 WHERE id = %s",
                (payload["description"], payload["quantity"], payload["condition_grade"], payload["notes"],
                 extra_id),
            )
            cur.execute("SELECT * FROM item_extras WHERE id = %s", (extra_id,))
            fresh = cur.fetchone()
        return "ok", None, fresh

    def update_extra(self, item_id: int, extra_id: int, data: dict[str, Any]) -> bool:
        with self._write() as cur:
            cur.execute(
                "UPDATE item_extras SET description = %s, quantity = %s, condition_grade = %s, notes = %s, "
                "version = version + 1 WHERE id = %s AND item_id = %s",
                (data["description"], data["quantity"], data["condition_grade"], data["notes"],
                 extra_id, item_id),
            )
            return cur.rowcount > 0 or self._extra_exists(cur, item_id, extra_id)

    @staticmethod
    def _extra_exists(cur: Any, item_id: int, extra_id: int) -> bool:
        # rowcount is 0 when an UPDATE changes nothing, so check existence separately.
        cur.execute("SELECT 1 AS x FROM item_extras WHERE id = %s AND item_id = %s", (extra_id, item_id))
        return cur.fetchone() is not None

    def delete_extra(self, item_id: int, extra_id: int) -> bool:
        with self._write() as cur:
            cur.execute("DELETE FROM item_extras WHERE id = %s AND item_id = %s", (extra_id, item_id))
            return cur.rowcount > 0

    # -- links between buildings and figures/accessories ------------------------

    def list_links(self, item_id: int) -> list[dict[str, Any]]:
        """Items linked to this one: for a building, its pieces; otherwise, its buildings."""
        rows = self._all(
            "SELECT i.id, i.name, i.category, i.item_number FROM item_links l "
            "JOIN items i ON i.id = l.item_id WHERE l.building_id = %s "
            "UNION "
            "SELECT i.id, i.name, i.category, i.item_number FROM item_links l "
            "JOIN items i ON i.id = l.building_id WHERE l.item_id = %s "
            "ORDER BY name, id",
            (item_id, item_id),
        )
        self._end_read()
        return rows

    def link_candidates(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        if item["category"] == "building":
            sql = (
                "SELECT id, name, category, item_number FROM items WHERE category <> 'building' "
                "AND id NOT IN (SELECT item_id FROM item_links WHERE building_id = %s) "
                "ORDER BY name, id LIMIT 2000"
            )
        else:
            sql = (
                "SELECT id, name, category, item_number FROM items WHERE category = 'building' "
                "AND id NOT IN (SELECT building_id FROM item_links WHERE item_id = %s) "
                "ORDER BY name, id LIMIT 2000"
            )
        rows = self._all(sql, (item["id"],))
        self._end_read()
        return rows

    def add_link(self, id_a: int, id_b: int) -> bool:
        """Link a building with a non-building item, in either argument order."""
        if id_a == id_b:
            return False
        with self._write() as cur:
            cur.execute(
                "SELECT id, category FROM items WHERE id IN (%s, %s) FOR UPDATE", (id_a, id_b)
            )
            rows = {row["id"]: row["category"] for row in cur.fetchall()}
            if len(rows) != 2:
                return False
            buildings = [i for i, cat in rows.items() if cat == "building"]
            if len(buildings) != 1:
                return False
            building_id = buildings[0]
            other_id = id_b if building_id == id_a else id_a
            cur.execute(
                "INSERT IGNORE INTO item_links (building_id, item_id) VALUES (%s, %s)",
                (building_id, other_id),
            )
        return True

    def remove_link(self, id_a: int, id_b: int) -> bool:
        with self._write() as cur:
            cur.execute(
                "DELETE FROM item_links WHERE (building_id = %s AND item_id = %s) "
                "OR (building_id = %s AND item_id = %s)",
                (id_a, id_b, id_b, id_a),
            )
            return cur.rowcount > 0

    # -- photos ---------------------------------------------------------------

    def list_photos(self, item_id: int) -> list[dict[str, Any]]:
        rows = self._all(
            "SELECT * FROM item_photos WHERE item_id = %s ORDER BY created_at, id", (item_id,)
        )
        self._end_read()
        return rows

    def get_photo(self, photo_id: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM item_photos WHERE id = %s", (photo_id,))
        self._end_read()
        return row

    def add_photo(self, item_id: int, filename: str, original_name: str | None,
                  client_uuid: str | None = None) -> int | None:
        with self._write() as cur:
            cur.execute("SELECT id FROM items WHERE id = %s FOR UPDATE", (item_id,))
            if cur.fetchone() is None:
                return None
            cur.execute(
                "INSERT INTO item_photos (item_id, filename, original_name, client_uuid) VALUES (%s, %s, %s, %s)",
                (item_id, filename, original_name, client_uuid),
            )
            return int(cur.lastrowid)

    # -- 3D scans --------------------------------------------------------------

    def list_scans(self, item_id: int) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM item_scans WHERE item_id = %s ORDER BY created_at, id", (item_id,))
        self._end_read()
        return rows

    def get_scan(self, scan_id: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM item_scans WHERE id = %s", (scan_id,))
        self._end_read()
        return row

    def add_scan(self, item_id: int, filename: str, original_name: str | None, size_bytes: int) -> int | None:
        with self._write() as cur:
            cur.execute("SELECT id FROM items WHERE id = %s FOR UPDATE", (item_id,))
            if cur.fetchone() is None:
                return None
            cur.execute(
                "INSERT INTO item_scans (item_id, filename, original_name, size_bytes) VALUES (%s, %s, %s, %s)",
                (item_id, filename, original_name, size_bytes),
            )
            return int(cur.lastrowid)

    def delete_scan(self, scan_id: int) -> dict[str, Any] | None:
        with self._write() as cur:
            cur.execute("SELECT * FROM item_scans WHERE id = %s FOR UPDATE", (scan_id,))
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute("DELETE FROM item_scans WHERE id = %s", (scan_id,))
        return row

    def get_photo_by_client_uuid(self, client_uuid: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM item_photos WHERE client_uuid = %s", (client_uuid,))
        self._end_read()
        return row

    def delete_photo(self, photo_id: int) -> dict[str, Any] | None:
        with self._write() as cur:
            cur.execute("SELECT * FROM item_photos WHERE id = %s FOR UPDATE", (photo_id,))
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute("DELETE FROM item_photos WHERE id = %s", (photo_id,))
        return row

    # -- reference catalog ----------------------------------------------------

    def search_catalog(self, query: str, limit: int = 15) -> list[dict[str, Any]]:
        """Find catalog entries by item number (ranked first) or name."""
        query = " ".join(query.split())[:100]
        if len(query) < 2:
            return []
        like = f"%{_like_escape(query)}%"
        prefix = f"{_like_escape(query)}%"
        digits = "".join(ch for ch in query if ch.isdigit())
        where = ["c.item_number LIKE %s", "c.name LIKE %s"]
        params: list[Any] = [like, like]
        if len(digits) >= 2:
            # "5665005" or "56-65005" should still find 56.65005
            where.append("REPLACE(c.item_number, '.', '') LIKE %s")
            params.append(f"%{digits}%")
        sql = (
            f"SELECT {_CATALOG_FIELDS} FROM catalog_items c "
            "LEFT JOIN catalog_items p ON p.source_key = c.parent_key "
            f"WHERE {' OR '.join(where)} "
            "ORDER BY CASE "
            "  WHEN c.item_number = %s THEN 0 "
            "  WHEN c.item_number LIKE %s THEN 1 "
            "  WHEN c.item_number LIKE %s THEN 2 "
            "  WHEN c.name LIKE %s THEN 3 "
            "  ELSE 4 END, "
            "c.parent_key IS NOT NULL, c.item_number, c.source_key "
            "LIMIT %s"
        )
        rows = self._all(sql, [*params, query, prefix, like, prefix, limit])
        self._end_read()
        return rows

    def get_catalog_item(self, catalog_id: int) -> dict[str, Any] | None:
        row = self._one(
            f"SELECT {_CATALOG_FIELDS} FROM catalog_items c "
            "LEFT JOIN catalog_items p ON p.source_key = c.parent_key WHERE c.id = %s",
            (catalog_id,),
        )
        self._end_read()
        return row

    def list_catalog_photos(self, catalog_id: int) -> list[dict[str, Any]]:
        rows = self._all(
            "SELECT * FROM catalog_photos WHERE catalog_id = %s ORDER BY created_at, id", (catalog_id,)
        )
        self._end_read()
        return rows

    def get_catalog_photo(self, photo_id: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM catalog_photos WHERE id = %s", (photo_id,))
        self._end_read()
        return row

    def add_catalog_photo(self, catalog_id: int, filename: str, original_name: str | None) -> int | None:
        with self._write() as cur:
            cur.execute("SELECT id FROM catalog_items WHERE id = %s FOR UPDATE", (catalog_id,))
            if cur.fetchone() is None:
                return None
            cur.execute(
                "INSERT INTO catalog_photos (catalog_id, filename, original_name) VALUES (%s, %s, %s)",
                (catalog_id, filename, original_name),
            )
            return int(cur.lastrowid)

    def delete_catalog_photo(self, photo_id: int) -> dict[str, Any] | None:
        with self._write() as cur:
            cur.execute("SELECT * FROM catalog_photos WHERE id = %s FOR UPDATE", (photo_id,))
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute("DELETE FROM catalog_photos WHERE id = %s", (photo_id,))
        return row

    def items_for_catalog(self, catalog_id: int) -> list[dict[str, Any]]:
        """Pieces in the collection linked to a catalog entry."""
        rows = self._all(
            "SELECT id, name, category, condition_grade, storage_location, storage_tub, storage_tray "
            "FROM items WHERE catalog_id = %s ORDER BY name, id",
            (catalog_id,),
        )
        self._end_read()
        return rows

    # -- summaries ------------------------------------------------------------

    def storage_summary(self) -> list[dict[str, Any]]:
        rows = self._all(
            "SELECT storage_location, storage_tub, storage_tray, COUNT(*) AS item_count "
            "FROM items GROUP BY storage_location, storage_tub, storage_tray "
            "ORDER BY storage_location IS NULL, storage_location, storage_tub IS NULL, "
            "storage_tub, storage_tray IS NULL, storage_tray"
        )
        self._end_read()
        return rows

    def collection_summary(self) -> dict[str, Any]:
        by_category = self._all(
            "SELECT category, COUNT(*) AS item_count, COALESCE(SUM(price_paid), 0) AS total_paid, "
            "COALESCE(SUM(estimated_value), 0) AS total_value FROM items GROUP BY category ORDER BY category"
        )
        by_condition = self._all(
            "SELECT condition_grade, COUNT(*) AS item_count FROM items "
            "GROUP BY condition_grade ORDER BY condition_grade IS NULL, condition_grade"
        )
        totals = self._one(
            "SELECT COUNT(*) AS item_count, COALESCE(SUM(price_paid), 0) AS total_paid, "
            "COALESCE(SUM(estimated_value), 0) AS total_value, "
            "COALESCE(SUM(on_display), 0) AS on_display, "
            "COALESCE(SUM(has_original_box), 0) AS with_box, "
            "(SELECT COALESCE(SUM(quantity), 0) FROM item_extras) AS extras_count "
            "FROM items"
        )
        self._end_read()
        return {"by_category": by_category, "by_condition": by_condition, "totals": totals}
