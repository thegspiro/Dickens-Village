"""Export the collection: JSON, CSV and a zip of every photo and scan.

Pure functions over the rows MySQLRepository.export_data() returns, so they can
be tested without a server. The zip is produced as a stream of chunks: files
are read from disk 1 MB at a time and never held in memory as a whole.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Iterator

from . import photos, scans
from .validation import ITEM_COLUMNS

FORMAT_VERSION = 1

EXTRA_COLUMNS = ("description", "quantity", "condition_grade", "notes")

# Spreadsheet apps treat a cell starting with one of these as a formula.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _plain(value: Any) -> Any:
    """A JSON-friendly copy of a database value."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return value


def _slug(text: str | None, fallback: str) -> str:
    """A safe file or folder name: letters, digits, spaces, dots, dashes; at most 60 characters."""
    slug = re.sub(r"[^A-Za-z0-9 ._-]+", "", text or "").strip(" .")[:60].strip(" .")
    return slug or fallback


def _photo_path(photo: dict[str, Any], folder: str) -> str:
    return f"{folder}/{int(photo['sort_order']) + 1:02d}-{photo['filename']}"


def item_folder(item: dict[str, Any]) -> str:
    return f"pieces/{int(item['id'])}-{_slug(item.get('name'), 'piece')}"


def catalog_folder(row: dict[str, Any]) -> str:
    return f"catalog/{int(row['catalog_id'])}-{_slug(row.get('item_number') or row.get('name'), 'entry')}"


def collection_json(data: dict[str, list[dict[str, Any]]], exported_at: dt.datetime) -> dict[str, Any]:
    """The whole collection as one JSON document (paths point into the zip export)."""
    extras: dict[int, list[dict[str, Any]]] = {}
    for row in data["extras"]:
        extras.setdefault(row["item_id"], []).append({"id": row["id"], **{c: _plain(row[c]) for c in EXTRA_COLUMNS}})
    links: dict[int, list[int]] = {}
    for row in data["links"]:
        links.setdefault(row["building_id"], []).append(row["item_id"])
        links.setdefault(row["item_id"], []).append(row["building_id"])
    items_by_id = {row["id"]: row for row in data["items"]}
    photo_rows: dict[int, list[dict[str, Any]]] = {}
    for row in data["photos"]:
        item = items_by_id.get(row["item_id"])
        if item is None:
            continue
        photo_rows.setdefault(row["item_id"], []).append({
            "id": row["id"], "caption": row["caption"], "original_name": row["original_name"],
            "added": _plain(row["created_at"]), "file": _photo_path(row, item_folder(item)),
        })
    scan_rows: dict[int, list[dict[str, Any]]] = {}
    for row in data["scans"]:
        item = items_by_id.get(row["item_id"])
        if item is None:
            continue
        scan_rows.setdefault(row["item_id"], []).append({
            "id": row["id"], "original_name": row["original_name"], "size_bytes": int(row["size_bytes"]),
            "added": _plain(row["created_at"]), "file": f"{item_folder(item)}/scans/{row['filename']}",
        })
    pieces = []
    for row in data["items"]:
        piece = {"id": row["id"], **{c: _plain(row[c]) for c in ITEM_COLUMNS}}
        for flag in ("has_original_box", "has_sleeve", "on_display"):
            piece[flag] = bool(piece[flag])
        piece.update({
            "catalog_item_number": row.get("catalog_item_number"),
            "catalog_name": row.get("catalog_name"),
            "added": _plain(row.get("created_at")),
            "updated": _plain(row.get("updated_at")),
            "extras": extras.get(row["id"], []),
            "linked_piece_ids": sorted(set(links.get(row["id"], []))),
            "photos": photo_rows.get(row["id"], []),
            "scans": scan_rows.get(row["id"], []),
        })
        pieces.append(piece)
    catalog_photos = [{
        "id": row["id"], "catalog_id": row["catalog_id"], "catalog_item_number": row["item_number"],
        "catalog_name": row["name"], "caption": row["caption"], "original_name": row["original_name"],
        "added": _plain(row["created_at"]), "file": _photo_path(row, catalog_folder(row)),
    } for row in data["catalog_photos"]]
    return {
        "format": "dickens-village-collection",
        "format_version": FORMAT_VERSION,
        "exported_at": exported_at.isoformat(timespec="seconds"),
        "pieces": pieces,
        "catalog_photos": catalog_photos,
    }


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = str(_plain(value))
    return "'" + text if text.startswith(_FORMULA_START) else text


def _csv(header: list[str], rows: Iterable[list[Any]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([_cell(v) for v in row])
    # A byte-order mark lets Excel open the file as UTF-8 (names with accents, ¼, ½).
    return "﻿" + out.getvalue()


def pieces_csv(data: dict[str, list[dict[str, Any]]]) -> str:
    counts: dict[str, dict[int, int]] = {"extras": {}, "photos": {}, "scans": {}}
    for kind in counts:
        for row in data[kind]:
            counts[kind][row["item_id"]] = counts[kind].get(row["item_id"], 0) + (
                int(row["quantity"]) if kind == "extras" else 1)
    header = ["id", *ITEM_COLUMNS, "catalog_item_number", "catalog_name", "extras", "photos", "scans", "added", "updated"]
    rows = []
    for item in data["items"]:
        values: list[Any] = [item["id"]]
        for column in ITEM_COLUMNS:
            value = item[column]
            values.append(bool(value) if column in ("has_original_box", "has_sleeve", "on_display") else value)
        values += [item.get("catalog_item_number"), item.get("catalog_name"),
                   counts["extras"].get(item["id"], 0), counts["photos"].get(item["id"], 0),
                   counts["scans"].get(item["id"], 0), item.get("created_at"), item.get("updated_at")]
        rows.append(values)
    return _csv(header, rows)


def extras_csv(data: dict[str, list[dict[str, Any]]]) -> str:
    names = {item["id"]: item["name"] for item in data["items"]}
    return _csv(["id", "piece_id", "piece_name", *EXTRA_COLUMNS],
                ([e["id"], e["item_id"], names.get(e["item_id"]), *(e[c] for c in EXTRA_COLUMNS)]
                 for e in data["extras"]))


class _Chunks(io.RawIOBase):
    """A write-only, unseekable stream whose output is collected and handed out in pieces."""

    def __init__(self) -> None:
        self._parts: list[bytes] = []
        self._written = 0

    def writable(self) -> bool:
        return True

    def write(self, b: Any) -> int:
        data = bytes(b)
        self._parts.append(data)
        self._written += len(data)
        return len(data)

    def tell(self) -> int:
        return self._written

    def take(self) -> bytes:
        out = b"".join(self._parts)
        self._parts.clear()
        return out


def zip_stream(data: dict[str, list[dict[str, Any]]], upload_dir: str, exported_at: dt.datetime,
               chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    """Yield a zip archive of every photo and scan, plus the JSON and CSV exports.

    Files are stored, not compressed (JPEG and GLB are compressed already).
    A file missing from disk is listed in MISSING.txt instead of failing the
    whole download.
    """
    sink = _Chunks()
    folder = Path(upload_dir)
    items_by_id = {row["id"]: row for row in data["items"]}
    missing: list[str] = []
    stamp = exported_at.timetuple()[:6]

    def entry(name: str) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(name, date_time=stamp)
        info.compress_type = zipfile.ZIP_STORED
        info.external_attr = 0o644 << 16
        return info

    files: list[tuple[str, str]] = []  # (path in zip, stored filename)
    for row in data["photos"]:
        item = items_by_id.get(row["item_id"])
        if item is not None and photos.FILENAME_RE.match(row["filename"]):
            files.append((_photo_path(row, item_folder(item)), row["filename"]))
    for row in data["scans"]:
        item = items_by_id.get(row["item_id"])
        if item is not None and scans.FILENAME_RE.match(row["filename"]):
            files.append((f"{item_folder(item)}/scans/{row['filename']}", row["filename"]))
    for row in data["catalog_photos"]:
        if photos.FILENAME_RE.match(row["filename"]):
            files.append((_photo_path(row, catalog_folder(row)), row["filename"]))

    with zipfile.ZipFile(sink, mode="w", allowZip64=True) as archive:
        archive.writestr(entry("collection.json"),
                         json.dumps(collection_json(data, exported_at), indent=2, ensure_ascii=False))
        archive.writestr(entry("pieces.csv"), pieces_csv(data))
        archive.writestr(entry("extras.csv"), extras_csv(data))
        yield sink.take()
        for zip_path, filename in files:
            source = folder / filename
            try:
                handle = source.open("rb")
            except OSError:
                missing.append(zip_path)
                continue
            with handle, archive.open(entry(zip_path), mode="w", force_zip64=True) as target:
                while True:
                    block = handle.read(chunk_size)
                    if not block:
                        break
                    target.write(block)
                    yield sink.take()
            yield sink.take()
        if missing:
            archive.writestr(entry("MISSING.txt"),
                             "These files are recorded but were not found in the upload folder:\n"
                             + "\n".join(missing) + "\n")
    yield sink.take()
