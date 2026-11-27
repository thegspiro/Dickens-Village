"""All SQL lives here. Every query is parameterized; the only strings ever
interpolated into SQL are fixed fragments chosen from whitelists in this file."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from .places import PlaceTree, legacy_path
from .validation import DEFAULT_VILLAGE, ITEM_COLUMNS, STORAGE_COLUMNS

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

# The cover is the first photo in the chosen order.
_COVER_PHOTO = (
    "(SELECT p.id FROM item_photos p WHERE p.item_id = i.id ORDER BY p.sort_order, p.id LIMIT 1) AS cover_photo_id"
)

# Fingerprint of photo rows for ETags: changes when a photo is added, removed,
# moved (id weighted by position) or re-captioned.
_PHOTOS_FINGERPRINT = (
    "CONCAT_WS(':', COUNT(*), COALESCE(SUM(id), 0), COALESCE(SUM(id * (sort_order + 1)), 0), "
    "COALESCE(SUM(CRC32(COALESCE(caption, ''))), 0))"
)


_CATALOG_FIELDS = (
    "c.id, c.list_name, c.village, c.item_number, c.name, c.details, c.series, c.year_issued, "
    "c.year_retired, c.is_current, c.listed, c.srp_usd, c.srp_cad, c.default_category, "
    "p.name AS parent_name, p.item_number AS parent_item_number, "
    "(SELECT cp.id FROM catalog_photos cp WHERE cp.catalog_id = c.id ORDER BY cp.sort_order, cp.id LIMIT 1) AS photo_id, "
    "(SELECT COUNT(*) FROM catalog_photos cp WHERE cp.catalog_id = c.id) AS photo_count"
)


def _is_duplicate_key(exc: Exception) -> bool:
    """True for MySQL error 1062 (duplicate entry for a unique key)."""
    args = getattr(exc, "args", ())
    return bool(args) and args[0] == 1062


class VersionConflict(Exception):
    """The row's version is not the one the caller expected: someone else saved it first."""


class DuplicatePlace(Exception):
    """A place with that name already exists in the same parent place."""


EXTRA_COLUMNS = ("description", "quantity", "condition_grade", "notes", "storage_place_id")

_PLACE_COLUMNS = ("parent_id", "name", "kind", "is_display", "notes")  # what an edit can change
_NEW_PLACE_COLUMNS = ("village", *_PLACE_COLUMNS)  # a place's village is set when it is created

# Fingerprint of the places table for ETags.
_PLACES_FINGERPRINT = (
    "CONCAT_WS(':', COUNT(*), COALESCE(SUM(CRC32(CONCAT_WS('|', id, COALESCE(parent_id, 0), name, "
    "COALESCE(kind, ''), is_display))), 0))"
)


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

        if filters.get("village"):
            where.append("i.village = %s")
            params.append(filters["village"])
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
            where.append("i.storage_place_id IS NULL")
        if filters.get("place"):
            tree = PlaceTree(self._all("SELECT id, village, parent_id, name, kind FROM storage_places"))
            ids = tree.descendants(filters["place"]) if filters["place"] in tree else []
            if ids:
                where.append(f"i.storage_place_id IN ({', '.join(['%s'] * len(ids))})")
                params.extend(ids)
            else:
                where.append("1 = 0")

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        order_sql = _ORDER_BY.get(filters.get("sort", "name"), _ORDER_BY["name"])
        order_params: list[Any] = []
        if filters.get("sort") == "estimated_value" and filters.get("usd_cad_rate"):
            # Compare values entered in different currencies in US dollars.
            order_sql = ("i.estimated_value IS NULL, CASE WHEN i.currency = 'CAD' THEN i.estimated_value / %s "
                         "ELSE i.estimated_value END DESC, i.name ASC, i.id ASC")
            order_params = [filters["usd_cad_rate"]]
        page = max(int(filters.get("page", 1)), 1)

        total_row = self._one(f"SELECT COUNT(*) AS n FROM items i {where_sql}", params)
        rows = self._all(
            f"SELECT i.*, {_EXTRAS_COUNT}, {_COVER_PHOTO} FROM items i {where_sql} "
            f"ORDER BY {order_sql} LIMIT %s OFFSET %s",
            [*params, *order_params, PAGE_SIZE, (page - 1) * PAGE_SIZE],
        )
        self._end_read()
        return rows, int(total_row["n"]) if total_row else 0

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        row = self._one(f"SELECT i.*, {_EXTRAS_COUNT} FROM items i WHERE i.id = %s", (item_id,))
        self._end_read()
        return row

    def create_item(self, data: dict[str, Any]) -> int:
        """Insert a piece. Its Location / Tub / Tray columns are set from its storage place."""
        columns = list(ITEM_COLUMNS)
        sql = (
            f"INSERT INTO items ({', '.join(columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))})"
        )
        with self._write() as cur:
            data = self._apply_storage(cur, data)
            cur.execute(sql, [data[c] for c in columns])
            new_id = int(cur.lastrowid)
        log.info("Created item id=%s", new_id)
        return new_id

    def update_item(self, item_id: int, data: dict[str, Any], expected_version: int | None = None) -> bool:
        """Save every item field. Returns False if the item does not exist.

        With expected_version, raises VersionConflict (and writes nothing) if
        the row was saved by someone else since that version was read. The
        check runs under the row lock, so it cannot race another writer.
        """
        columns = list(ITEM_COLUMNS)
        sql = f"UPDATE items SET {', '.join(f'{c} = %s' for c in columns)} WHERE id = %s"
        with self._write() as cur:
            # Places are read (and share-locked) before the item row, the same
            # order a place rename takes its locks in.
            data = self._apply_storage(cur, data)
            cur.execute("SELECT id, version FROM items WHERE id = %s FOR UPDATE", (item_id,))
            row = cur.fetchone()
            if row is None:
                return False
            if expected_version is not None and int(row["version"]) != expected_version:
                log.info("Item id=%s not saved: version %s, expected %s", item_id, row["version"], expected_version)
                raise VersionConflict(item_id)
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
                data = self._apply_storage(cur, data)
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
        does not exist. The data may carry "_legacy_storage": see _apply_storage.
        """
        columns = list(ITEM_COLUMNS)
        with self._write() as cur:
            self._read_places(cur)  # lock order: places, then the item
            cur.execute("SELECT * FROM items WHERE id = %s FOR UPDATE", (item_id,))
            row = cur.fetchone()
            if row is None:
                return "missing", None, None
            status, payload = resolve(row)
            if status != "ok":
                return status, payload, row
            payload = self._apply_storage(cur, payload)
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
        photos = self._all("SELECT id, item_id, client_uuid, caption FROM item_photos "
                           "ORDER BY item_id, sort_order, id")
        scans = self._all("SELECT id, item_id FROM item_scans ORDER BY item_id, id")
        places = self._all("SELECT id, village, parent_id, name, kind, is_display FROM storage_places ORDER BY id")
        self._end_read()
        return {"items": items, "extras": extras, "photos": photos, "scans": scans, "places": places}

    def snapshot_etag(self) -> str:
        row = self._one(
            "SELECT "
            "(SELECT CONCAT_WS(':', COUNT(*), COALESCE(SUM(version), 0), COALESCE(MAX(id), 0)) FROM items) AS i, "
            "(SELECT CONCAT_WS(':', COUNT(*), COALESCE(SUM(version), 0), COALESCE(MAX(id), 0)) FROM item_extras) AS e, "
            f"(SELECT {_PHOTOS_FINGERPRINT} FROM item_photos) AS p, "
            "(SELECT CONCAT_WS(':', COUNT(*), COALESCE(SUM(id), 0)) FROM item_scans) AS s, "
            f"(SELECT {_PLACES_FINGERPRINT} FROM storage_places) AS pl"
        )
        self._end_read()
        return f"{row['i']}/{row['e']}/{row['p']}/{row['s']}/{row['pl']}"

    def catalog_all(self) -> list[dict[str, Any]]:
        rows = self._all(
            f"SELECT {_CATALOG_FIELDS} FROM catalog_items c "
            "LEFT JOIN catalog_items p ON p.source_key = c.parent_key ORDER BY c.source_key"
        )
        self._end_read()
        return rows

    def catalog_etag(self) -> str:
        row = self._one(
            "SELECT (SELECT CONCAT_WS(':', COUNT(*), COALESCE(MAX(updated_at), ''), COALESCE(SUM(listed), 0)) "
            "FROM catalog_items) AS c, "
            f"(SELECT {_PHOTOS_FINGERPRINT} FROM catalog_photos) AS p"
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
                "INSERT INTO item_extras (item_id, description, quantity, condition_grade, notes, storage_place_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (item_id, *[data.get(c) for c in EXTRA_COLUMNS]),
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
                    "INSERT INTO item_extras (item_id, description, quantity, condition_grade, notes, "
                    "storage_place_id, client_uuid) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (item_id, *[data.get(c) for c in EXTRA_COLUMNS], client_uuid),
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
                "storage_place_id = %s, version = version + 1 WHERE id = %s",
                (*[payload.get(c) for c in EXTRA_COLUMNS], extra_id),
            )
            cur.execute("SELECT * FROM item_extras WHERE id = %s", (extra_id,))
            fresh = cur.fetchone()
        return "ok", None, fresh

    def update_extra(self, item_id: int, extra_id: int, data: dict[str, Any],
                     expected_version: int | None = None) -> bool:
        """Save an extra. Returns False if it does not exist; same expected_version
        contract as update_item."""
        with self._write() as cur:
            cur.execute("SELECT id, version FROM item_extras WHERE id = %s AND item_id = %s FOR UPDATE",
                        (extra_id, item_id))
            row = cur.fetchone()
            if row is None:
                return False
            if expected_version is not None and int(row["version"]) != expected_version:
                log.info("Extra id=%s not saved: version %s, expected %s", extra_id, row["version"], expected_version)
                raise VersionConflict(extra_id)
            cur.execute(
                "UPDATE item_extras SET description = %s, quantity = %s, condition_grade = %s, notes = %s, "
                "storage_place_id = %s, version = version + 1 WHERE id = %s AND item_id = %s",
                (*[data.get(c) for c in EXTRA_COLUMNS], extra_id, item_id),
            )
            return True

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
        """Pieces this one can be linked to: in the same village, buildings for a figure
        or accessory and the other way round."""
        if item["category"] == "building":
            sql = (
                "SELECT id, name, category, item_number FROM items WHERE category <> 'building' AND village = %s "
                "AND id NOT IN (SELECT item_id FROM item_links WHERE building_id = %s) "
                "ORDER BY name, id LIMIT 2000"
            )
        else:
            sql = (
                "SELECT id, name, category, item_number FROM items WHERE category = 'building' AND village = %s "
                "AND id NOT IN (SELECT building_id FROM item_links WHERE item_id = %s) "
                "ORDER BY name, id LIMIT 2000"
            )
        rows = self._all(sql, (item["village"], item["id"]))
        self._end_read()
        return rows

    def add_link(self, id_a: int, id_b: int) -> bool:
        """Link a building with a non-building item of the same village, in either argument order."""
        if id_a == id_b:
            return False
        with self._write() as cur:
            cur.execute(
                "SELECT id, category, village FROM items WHERE id IN (%s, %s) FOR UPDATE", (id_a, id_b)
            )
            found = cur.fetchall()
            rows = {row["id"]: row["category"] for row in found}
            if len(rows) != 2 or len({row["village"] for row in found}) != 1:
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
            "SELECT * FROM item_photos WHERE item_id = %s ORDER BY sort_order, id", (item_id,)
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
            # New photos go last; the item row lock keeps two uploads from taking the same place.
            cur.execute(
                "INSERT INTO item_photos (item_id, filename, original_name, client_uuid, sort_order) "
                "SELECT %s, %s, %s, %s, COALESCE(MAX(sort_order) + 1, 0) FROM item_photos WHERE item_id = %s",
                (item_id, filename, original_name, client_uuid, item_id),
            )
            return int(cur.lastrowid)

    def reorder_photos(self, item_id: int, photo_ids: list[int]) -> bool:
        """Put an item's photos in this order (the first is the cover).

        photo_ids must list every photo of the item exactly once; otherwise
        nothing changes and False is returned (also when the item is gone).
        """
        return self._reorder("items", "item_photos", "item_id", item_id, photo_ids)

    def set_photo_caption(self, photo_id: int, caption: str | None) -> dict[str, Any] | None:
        """Set or clear a photo's caption. Returns the photo row, or None if it doesn't exist."""
        return self._set_caption("item_photos", photo_id, caption)

    def _reorder(self, parent_table: str, photo_table: str, parent_column: str, parent_id: int,
                 photo_ids: list[int]) -> bool:
        # Table and column names come from the two fixed callers above.
        with self._write() as cur:
            cur.execute(f"SELECT id FROM {parent_table} WHERE id = %s FOR UPDATE", (parent_id,))
            if cur.fetchone() is None:
                return False
            cur.execute(f"SELECT id FROM {photo_table} WHERE {parent_column} = %s FOR UPDATE", (parent_id,))
            current = sorted(int(row["id"]) for row in cur.fetchall())
            if sorted(photo_ids) != current:
                return False
            for position, photo_id in enumerate(photo_ids):
                cur.execute(f"UPDATE {photo_table} SET sort_order = %s WHERE id = %s", (position, photo_id))
        log.info("Reordered %d photos of %s id=%s", len(photo_ids), parent_table, parent_id)
        return True

    def _set_caption(self, photo_table: str, photo_id: int, caption: str | None) -> dict[str, Any] | None:
        with self._write() as cur:
            cur.execute(f"SELECT * FROM {photo_table} WHERE id = %s FOR UPDATE", (photo_id,))
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute(f"UPDATE {photo_table} SET caption = %s WHERE id = %s", (caption, photo_id))
        row["caption"] = caption
        return row

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

    @staticmethod
    def _village_filter(village: str | None) -> tuple[str, list[Any]]:
        """Catalog entries for a village: its own lists and the lists for every village."""
        if village is None:
            return "1 = 1", []
        return "(c.village IS NULL OR c.village = %s)", [village]

    @staticmethod
    def _catalog_match(query: str) -> tuple[str, list[Any], str, list[Any]]:
        """WHERE fragment and ORDER BY fragment (with their parameters) that find
        catalog entries by item number (ranked first) or name."""
        like = f"%{_like_escape(query)}%"
        prefix = f"{_like_escape(query)}%"
        digits = "".join(ch for ch in query if ch.isdigit())
        where = ["c.item_number LIKE %s", "c.name LIKE %s"]
        params: list[Any] = [like, like]
        if len(digits) >= 2:
            # "5665005" or "56-65005" should still find 56.65005
            where.append("REPLACE(c.item_number, '.', '') LIKE %s")
            params.append(f"%{digits}%")
        order = (
            "CASE "
            "  WHEN c.item_number = %s THEN 0 "
            "  WHEN c.item_number LIKE %s THEN 1 "
            "  WHEN c.item_number LIKE %s THEN 2 "
            "  WHEN c.name LIKE %s THEN 3 "
            "  ELSE 4 END, "
        )
        return f"({' OR '.join(where)})", params, order, [query, prefix, like, prefix]

    def search_catalog(self, query: str, limit: int = 15, village: str | None = None) -> list[dict[str, Any]]:
        """Find catalog entries by item number (ranked first) or name; entries
        removed from the CSVs (listed = 0) are left out. With village, only
        entries for that village or for every village."""
        query = " ".join(query.split())[:100]
        if len(query) < 2:
            return []
        match, params, order, order_params = self._catalog_match(query)
        village_sql, village_params = self._village_filter(village)
        sql = (
            f"SELECT {_CATALOG_FIELDS} FROM catalog_items c "
            "LEFT JOIN catalog_items p ON p.source_key = c.parent_key "
            f"WHERE c.listed = 1 AND {village_sql} AND {match} "
            f"ORDER BY {order}c.parent_key IS NOT NULL, c.item_number, c.source_key "
            "LIMIT %s"
        )
        rows = self._all(sql, [*village_params, *params, *order_params, limit])
        self._end_read()
        return rows

    def browse_catalog(self, query: str = "", missing_photos: bool = False, owned_only: bool = False,
                       limit: int = 100, offset: int = 0, village: str | None = None
                       ) -> tuple[list[dict[str, Any]], int]:
        """The Catalog page: optional search, optionally only entries with no
        reference photo and/or only entries a piece in the collection is linked
        to. Returns (rows, total). Each row also has owned_count. With village,
        only entries for that village or for every village (and owned_only and
        owned_count count that village's pieces).

        Entries removed from the CSVs are left out, as in the lookup. With
        either filter on, those a piece is linked to are included: they still
        need photos for that piece.
        """
        query = " ".join(query.split())[:100]
        owner_sql = "i.catalog_id = c.id" + (" AND i.village = %s" if village else "")
        owner_params: list[Any] = [village] if village else []
        village_sql, village_params = self._village_filter(village)
        where = [village_sql]
        params: list[Any] = [*village_params]
        if missing_photos or owned_only:
            where.append(f"(c.listed = 1 OR EXISTS (SELECT 1 FROM items i WHERE {owner_sql}))")
            params += owner_params
        else:
            where.append("c.listed = 1")
        order, order_params = "", []
        if query:
            match, match_params, order, order_params = self._catalog_match(query)
            where.append(match)
            params += match_params
        if missing_photos:
            where.append("NOT EXISTS (SELECT 1 FROM catalog_photos cp WHERE cp.catalog_id = c.id)")
        if owned_only:
            where.append(f"EXISTS (SELECT 1 FROM items i WHERE {owner_sql})")
            params += owner_params
        where_sql = " AND ".join(where)
        total = self._one(f"SELECT COUNT(*) AS n FROM catalog_items c WHERE {where_sql}", params)
        rows = self._all(
            f"SELECT {_CATALOG_FIELDS}, "
            f"(SELECT COUNT(*) FROM items i WHERE {owner_sql}) AS owned_count "
            "FROM catalog_items c LEFT JOIN catalog_items p ON p.source_key = c.parent_key "
            f"WHERE {where_sql} "
            f"ORDER BY {order}c.parent_key IS NOT NULL, c.item_number IS NULL, c.item_number, c.source_key "
            "LIMIT %s OFFSET %s",
            [*owner_params, *params, *order_params, limit, offset],
        )
        self._end_read()
        return rows, int(total["n"]) if total else 0

    def catalog_for_matching(self) -> list[dict[str, Any]]:
        """Every catalog entry with a number, for matching bulk-upload filenames: entries removed
        from the CSVs only if a piece is linked to them."""
        rows = self._all(
            f"SELECT {_CATALOG_FIELDS} FROM catalog_items c "
            "LEFT JOIN catalog_items p ON p.source_key = c.parent_key "
            "WHERE c.item_number IS NOT NULL "
            "AND (c.listed = 1 OR EXISTS (SELECT 1 FROM items i WHERE i.catalog_id = c.id)) "
            "ORDER BY c.parent_key IS NOT NULL, c.source_key"
        )
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
            "SELECT * FROM catalog_photos WHERE catalog_id = %s ORDER BY sort_order, id", (catalog_id,)
        )
        self._end_read()
        return rows

    def get_catalog_photo(self, photo_id: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM catalog_photos WHERE id = %s", (photo_id,))
        self._end_read()
        return row

    def add_catalog_photo(self, catalog_id: int, filename: str, original_name: str | None,
                          caption: str | None = None) -> int | None:
        with self._write() as cur:
            cur.execute("SELECT id FROM catalog_items WHERE id = %s FOR UPDATE", (catalog_id,))
            if cur.fetchone() is None:
                return None
            cur.execute(
                "INSERT INTO catalog_photos (catalog_id, filename, original_name, caption, sort_order) "
                "SELECT %s, %s, %s, %s, COALESCE(MAX(sort_order) + 1, 0) FROM catalog_photos WHERE catalog_id = %s",
                (catalog_id, filename, original_name, caption, catalog_id),
            )
            return int(cur.lastrowid)

    def reorder_catalog_photos(self, catalog_id: int, photo_ids: list[int]) -> bool:
        """Same contract as reorder_photos, for a catalog entry's reference photos."""
        return self._reorder("catalog_items", "catalog_photos", "catalog_id", catalog_id, photo_ids)

    def set_catalog_photo_caption(self, photo_id: int, caption: str | None) -> dict[str, Any] | None:
        return self._set_caption("catalog_photos", photo_id, caption)

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
            "SELECT id, name, category, village, condition_grade, storage_location, storage_tub, storage_tray, "
            "storage_place_id, display_place_id, on_display FROM items WHERE catalog_id = %s ORDER BY name, id",
            (catalog_id,),
        )
        self._end_read()
        return rows

    # -- storage places ---------------------------------------------------------

    def _read_places(self, cur: Any) -> PlaceTree:
        """Every place, share-locked until the transaction ends, so a rename or move
        can't change a path while a piece is being saved against it."""
        cur.execute("SELECT id, village, parent_id, name, kind, is_display FROM storage_places FOR SHARE")
        return PlaceTree(cur.fetchall())

    def _apply_storage(self, cur: Any, data: dict[str, Any]) -> dict[str, Any]:
        """Set a piece's Location / Tub / Tray columns from its storage place.

        With "_legacy_storage" set (Location / Tub / Tray sent by an older
        phone app), the place is first found, or created, from those values.
        Returns a copy of data without the flag.
        """
        data = dict(data)
        legacy = data.pop("_legacy_storage", False)
        tree = self._read_places(cur)
        if legacy:
            steps = legacy_path(data.get("storage_location"), data.get("storage_tub"), data.get("storage_tray"))
            data["storage_place_id"] = self._find_or_create_path(cur, steps, data["village"])
            tree = self._read_places(cur)
        place_id = data.get("storage_place_id")
        data["storage_location"], data["storage_tub"], data["storage_tray"] = tree.legacy(place_id)
        return data

    def _find_or_create_path(self, cur: Any, steps: list[tuple[str, str | None]], village: str) -> int | None:
        parent: int | None = None
        for name, kind in steps:
            cur.execute("SELECT id FROM storage_places WHERE village = %s AND parent_key = %s AND name = %s FOR UPDATE",
                        (village, parent or 0, name))
            row = cur.fetchone()
            if row is None:
                cur.execute("INSERT INTO storage_places (village, parent_id, name, kind) VALUES (%s, %s, %s, %s)",
                            (village, parent, name, kind))
                parent = int(cur.lastrowid)
                log.info("Created storage place id=%s from an older phone app's Location/Tub/Tray", parent)
            else:
                parent = int(row["id"])
        return parent

    def list_places(self) -> list[dict[str, Any]]:
        """Every place (of every village), with how many pieces are stored in it
        (items), displayed in it (displayed) and how many extras are stored in it (extras)."""
        rows = self._all(
            "SELECT p.*, "
            "(SELECT COUNT(*) FROM items i WHERE i.storage_place_id = p.id) AS items, "
            "(SELECT COUNT(*) FROM items i WHERE i.display_place_id = p.id) AS displayed, "
            "(SELECT COUNT(*) FROM item_extras e WHERE e.storage_place_id = p.id) AS extras "
            "FROM storage_places p ORDER BY p.id"
        )
        self._end_read()
        return rows

    def get_place(self, place_id: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM storage_places WHERE id = %s", (place_id,))
        self._end_read()
        return row

    def create_place(self, data: dict[str, Any]) -> int | None:
        """Add a place in data["village"]; a place inside another takes its parent's
        village. Returns None if its parent no longer exists; raises DuplicatePlace
        if the parent already has a place with that name."""
        data = dict(data)
        data["village"] = data.get("village") or DEFAULT_VILLAGE
        try:
            with self._write() as cur:
                if data.get("parent_id") is not None:
                    cur.execute("SELECT id, village FROM storage_places WHERE id = %s FOR UPDATE",
                                (data["parent_id"],))
                    parent = cur.fetchone()
                    if parent is None:
                        return None
                    data["village"] = parent["village"]
                cur.execute(
                    f"INSERT INTO storage_places ({', '.join(_NEW_PLACE_COLUMNS)}) "
                    f"VALUES ({', '.join(['%s'] * len(_NEW_PLACE_COLUMNS))})",
                    [data.get(c) for c in _NEW_PLACE_COLUMNS],
                )
                new_id = int(cur.lastrowid)
        except Exception as exc:
            if _is_duplicate_key(exc):
                raise DuplicatePlace() from exc
            raise
        log.info("Created storage place id=%s", new_id)
        return new_id

    def update_place(self, place_id: int, data: dict[str, Any]) -> str | None:
        """Rename, retype or move a place. Returns None when saved, "missing" if the
        place is gone, or a message saying why it can't be moved there. Raises
        DuplicatePlace for a name already used in the new parent.

        The Location / Tub / Tray columns of every piece stored in it, or in a
        place inside it, are updated in the same transaction.
        """
        try:
            with self._write() as cur:
                cur.execute("SELECT id, village, parent_id, name, kind, is_display FROM storage_places FOR UPDATE")
                tree = PlaceTree(cur.fetchall())
                if place_id not in tree:
                    return "missing"
                problem = tree.move_problem(place_id, data.get("parent_id"))
                if problem:
                    return problem
                if bool(data.get("is_display")) != bool(tree.by_id[place_id].get("is_display")):
                    problem = self._role_change_problem(cur, place_id, bool(data.get("is_display")))
                    if problem:
                        return problem
                cur.execute(
                    f"UPDATE storage_places SET {', '.join(f'{c} = %s' for c in _PLACE_COLUMNS)} WHERE id = %s",
                    [*[data.get(c) for c in _PLACE_COLUMNS], place_id],
                )
                cur.execute("SELECT id, village, parent_id, name, kind, is_display FROM storage_places")
                self._refresh_legacy(cur, PlaceTree(cur.fetchall()), place_id)
        except Exception as exc:
            if _is_duplicate_key(exc):
                raise DuplicatePlace() from exc
            raise
        log.info("Updated storage place id=%s", place_id)
        return None

    @staticmethod
    def _role_change_problem(cur: Any, place_id: int, to_display: bool) -> str | None:
        """A place can only become a display spot when nothing is stored in it, and
        stop being one when no piece has it as its display spot."""
        if to_display:
            cur.execute("SELECT (SELECT COUNT(*) FROM items WHERE storage_place_id = %s) "
                        "+ (SELECT COUNT(*) FROM item_extras WHERE storage_place_id = %s) AS n", (place_id, place_id))
            if cur.fetchone()["n"]:
                return "Pieces or extras are stored here. Move them before making this a display spot."
        else:
            cur.execute("SELECT COUNT(*) AS n FROM items WHERE display_place_id = %s", (place_id,))
            if cur.fetchone()["n"]:
                return "Pieces have this as their display spot. Change theirs before making it a storage place."
        return None

    @staticmethod
    def _refresh_legacy(cur: Any, tree: PlaceTree, place_id: int) -> None:
        for pid in tree.descendants(place_id):
            cur.execute(
                "UPDATE items SET storage_location = %s, storage_tub = %s, storage_tray = %s "
                "WHERE storage_place_id = %s",
                (*tree.legacy(pid), pid),
            )

    def delete_place(self, place_id: int) -> str | None:
        """Delete an empty place. Returns None when deleted, "missing" if it was
        already gone, or a message saying what is still in it."""
        with self._write() as cur:
            cur.execute("SELECT id FROM storage_places WHERE id = %s FOR UPDATE", (place_id,))
            if cur.fetchone() is None:
                return "missing"
            cur.execute(
                "SELECT (SELECT COUNT(*) FROM storage_places WHERE parent_id = %s) AS places, "
                "(SELECT COUNT(*) FROM items WHERE storage_place_id = %s OR display_place_id = %s) AS items, "
                "(SELECT COUNT(*) FROM item_extras WHERE storage_place_id = %s) AS extras",
                (place_id, place_id, place_id, place_id),
            )
            counts = cur.fetchone()
            if counts["places"] or counts["items"] or counts["extras"]:
                return "Only an empty place can be deleted. Move what's in it first."
            cur.execute("DELETE FROM storage_places WHERE id = %s", (place_id,))
        log.info("Deleted storage place id=%s", place_id)
        return None

    def place_contents(self, place_ids: list[int]) -> dict[str, list[dict[str, Any]]]:
        """What belongs in these places: pieces stored there, pieces displayed
        there, and extras stored there (with their piece's name)."""
        if not place_ids:
            return {"stored": [], "displayed": [], "extras": []}
        marks = ", ".join(["%s"] * len(place_ids))
        fields = "i.id, i.name, i.category, i.item_number, i.on_display, i.storage_place_id, i.display_place_id"
        data = {
            "stored": self._all(f"SELECT {fields} FROM items i WHERE i.storage_place_id IN ({marks}) "
                                "ORDER BY i.name, i.id", place_ids),
            "displayed": self._all(f"SELECT {fields} FROM items i WHERE i.display_place_id IN ({marks}) "
                                   "ORDER BY i.name, i.id", place_ids),
            "extras": self._all(
                f"SELECT e.id, e.item_id, e.description, e.quantity, e.storage_place_id, i.name AS item_name, "
                f"i.on_display FROM item_extras e JOIN items i ON i.id = e.item_id "
                f"WHERE e.storage_place_id IN ({marks}) ORDER BY i.name, e.description, e.id", place_ids),
        }
        self._end_read()
        return data

    # -- export ------------------------------------------------------------------

    def export_data(self) -> dict[str, list[dict[str, Any]]]:
        """Everything a full export needs, read in one transaction so it is consistent."""
        self._conn.commit()  # start a fresh snapshot
        with self._conn.cursor() as cur:
            cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        try:
            data = {
                "items": self._all(
                    "SELECT i.*, c.item_number AS catalog_item_number, c.name AS catalog_name "
                    "FROM items i LEFT JOIN catalog_items c ON c.id = i.catalog_id ORDER BY i.id"),
                "extras": self._all("SELECT * FROM item_extras ORDER BY item_id, id"),
                "links": self._all("SELECT building_id, item_id FROM item_links ORDER BY building_id, item_id"),
                "photos": self._all(
                    "SELECT id, item_id, filename, original_name, sort_order, caption, created_at "
                    "FROM item_photos ORDER BY item_id, sort_order, id"),
                "scans": self._all(
                    "SELECT id, item_id, filename, original_name, size_bytes, created_at "
                    "FROM item_scans ORDER BY item_id, id"),
                "places": self._all(
                    "SELECT id, village, parent_id, name, kind, is_display, notes FROM storage_places ORDER BY id"),
                "catalog_photos": self._all(
                    "SELECT p.id, p.catalog_id, p.filename, p.original_name, p.sort_order, p.caption, p.created_at, "
                    "c.item_number, c.name FROM catalog_photos p JOIN catalog_items c ON c.id = p.catalog_id "
                    "ORDER BY p.catalog_id, p.sort_order, p.id"),
            }
        finally:
            self._end_read()
        return data

    # -- upload housekeeping ------------------------------------------------------

    def upload_filenames(self) -> set[str]:
        """Every photo and scan filename the database refers to."""
        rows = self._all(
            "SELECT filename FROM item_photos UNION ALL SELECT filename FROM catalog_photos "
            "UNION ALL SELECT filename FROM item_scans"
        )
        self._end_read()
        return {row["filename"] for row in rows}

    # -- summaries ------------------------------------------------------------

    def count_unplaced(self, village: str | None = None) -> int:
        """Pieces with no storage place (in one village, or in all)."""
        if village is None:
            row = self._one("SELECT COUNT(*) AS n FROM items WHERE storage_place_id IS NULL")
        else:
            row = self._one("SELECT COUNT(*) AS n FROM items WHERE storage_place_id IS NULL AND village = %s",
                            (village,))
        self._end_read()
        return int(row["n"]) if row else 0

    def collection_summary(self, village: str | None = None) -> dict[str, Any]:
        """Totals for one village's pieces (or every piece with village None)."""
        where, params = ("WHERE village = %s", [village]) if village else ("", [])
        by_category = self._all(
            "SELECT category, COUNT(*) AS item_count, COALESCE(SUM(price_paid), 0) AS total_paid, "
            f"COALESCE(SUM(estimated_value), 0) AS total_value FROM items {where} GROUP BY category ORDER BY category",
            params,
        )
        by_condition = self._all(
            f"SELECT condition_grade, COUNT(*) AS item_count FROM items {where} "
            "GROUP BY condition_grade ORDER BY condition_grade IS NULL, condition_grade",
            params,
        )
        totals = self._one(
            "SELECT COUNT(*) AS item_count, COALESCE(SUM(price_paid), 0) AS total_paid, "
            "COALESCE(SUM(estimated_value), 0) AS total_value, "
            "COALESCE(SUM(on_display), 0) AS on_display, "
            "COALESCE(SUM(has_original_box), 0) AS with_box, "
            "(SELECT COALESCE(SUM(e.quantity), 0) FROM item_extras e JOIN items x ON x.id = e.item_id "
            + ("WHERE x.village = %s" if village else "") + ") AS extras_count "
            f"FROM items {where}",
            [*params, *params],
        )
        # Money is added up per currency; the caller converts to the site currency.
        money = self._all(
            "SELECT category, currency, COALESCE(SUM(price_paid), 0) AS total_paid, "
            f"COALESCE(SUM(estimated_value), 0) AS total_value FROM items {where} GROUP BY category, currency "
            "ORDER BY category, currency",
            params,
        )
        self._end_read()
        return {"by_category": by_category, "by_condition": by_condition, "totals": totals, "money": money}

    # -- site settings ------------------------------------------------------------

    def get_settings(self) -> dict[str, str]:
        rows = self._all("SELECT name, value FROM site_settings")
        self._end_read()
        return {row["name"]: row["value"] for row in rows}

    def save_settings(self, values: dict[str, str | None]) -> None:
        """Set several settings in one transaction; a None value removes the setting."""
        with self._write() as cur:
            for name, value in values.items():
                if value is None:
                    cur.execute("DELETE FROM site_settings WHERE name = %s", (name,))
                else:
                    cur.execute("INSERT INTO site_settings (name, value) VALUES (%s, %s) AS new "
                                "ON DUPLICATE KEY UPDATE value = new.value", (name, value))
        log.info("Saved settings: %s", ", ".join(sorted(values)))
