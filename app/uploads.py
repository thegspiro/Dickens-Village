"""Find (and optionally delete) uploaded files the database no longer refers to.

Usage:
    python -m app.uploads status          # report only (the default)
    python -m app.uploads clean           # same report; deletes nothing
    python -m app.uploads clean --yes     # delete the orphaned files listed
    python -m app.uploads thumbs          # count thumbnails smaller than the gallery uses
    python -m app.uploads thumbs --yes    # rebuild them from the stored photos
    docker compose run --rm app python -m app.uploads status

thumbs needs no database: it rebuilds the thumbnail of every stored photo
(<32 hex>.jpg) whose thumbnail is missing or smaller than photos.THUMB_EDGE
(photos uploaded before the gallery used 320 px), and is safe to run while
the app is serving them and to run again.

Files can be left behind when the server stops between saving a file and
recording it, or between deleting a row and its file. Only files named the way
this app names them are considered: photos (<32 hex>.jpg), their thumbnails
(<32 hex>.thumb.jpg), scans (<32 hex>.glb / .gltf) and the temporary files of
interrupted uploads (.upload-*.tmp, .scan-*.tmp). Anything else in the folder
is left alone. Files changed in the last hour are skipped, because an upload
in progress writes its file just before its database row.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import photos, scans

log = logging.getLogger("dickens.uploads")

GRACE_SECONDS = 60 * 60
_THUMB_RE = re.compile(r"^([0-9a-f]{32})\.thumb\.jpg$")
_TEMP_RE = re.compile(r"^\.(upload|scan)-[A-Za-z0-9_]+\.tmp$")


@dataclass
class Report:
    orphans: list[Path] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # referenced by the database, not on disk
    recent: int = 0  # orphan-looking files skipped because they are under an hour old
    other: int = 0  # files this app doesn't name; never touched

    @property
    def orphan_bytes(self) -> int:
        total = 0
        for path in self.orphans:
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total


def scan_folder(upload_dir: Path, referenced: Iterable[str], now: float | None = None,
                grace_seconds: int = GRACE_SECONDS) -> Report:
    """Compare the upload folder with the filenames the database refers to."""
    now = time.time() if now is None else now
    referenced = set(referenced)
    report = Report()
    present: set[str] = set()
    for path in sorted(upload_dir.iterdir()) if upload_dir.is_dir() else []:
        if not path.is_file() or path.is_symlink():
            report.other += 1
            continue
        name = path.name
        thumb = _THUMB_RE.match(name)
        if photos.FILENAME_RE.match(name) or scans.FILENAME_RE.match(name):
            present.add(name)
            orphan = name not in referenced
        elif thumb:
            orphan = f"{thumb.group(1)}.jpg" not in referenced
        elif _TEMP_RE.match(name):
            orphan = True
        else:
            report.other += 1
            continue
        if not orphan:
            continue
        try:
            age = now - path.stat().st_mtime
        except OSError:
            continue
        if age < grace_seconds:
            report.recent += 1
        else:
            report.orphans.append(path)
    report.missing = sorted(referenced - present)
    return report


def delete_orphans(report: Report) -> int:
    """Delete the files listed as orphans. Returns how many were deleted."""
    deleted = 0
    for path in report.orphans:
        try:
            path.unlink()
            deleted += 1
            log.info("Deleted orphaned upload %s", path.name)
        except FileNotFoundError:
            pass
        except OSError:
            log.exception("Could not delete %s", path.name)
    return deleted


def stale_thumbnails(upload_dir: Path) -> list[str]:
    """Stored photos whose thumbnail is missing or smaller than photos.THUMB_EDGE."""
    if not upload_dir.is_dir():
        return []
    return [path.name for path in sorted(upload_dir.iterdir())
            if path.is_file() and not path.is_symlink() and photos.FILENAME_RE.match(path.name)
            and not photos.thumbnail_is_current(str(upload_dir), path.name)]


def thumbs_command(folder: Path, apply: bool) -> int:
    stale = stale_thumbnails(folder)
    print(f"{len(stale)} photo(s) have a thumbnail smaller than {photos.THUMB_EDGE} px (or none)")
    if not apply:
        if stale:
            print("Nothing was changed. Run 'python -m app.uploads thumbs --yes' to rebuild them.")
        return 0
    rebuilt = failed = 0
    for name in stale:
        try:
            photos.regenerate_thumbnail(str(folder), name)
            rebuilt += 1
        except photos.PhotoError as exc:
            failed += 1
            print(f"  {name}: {exc}")
    print(f"Rebuilt {rebuilt} thumbnail(s)" + (f"; {failed} could not be rebuilt" if failed else ""))
    return 1 if failed else 0


def _size(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB" if n >= 1024 * 1024 else f"{n // 1024} KB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.uploads")
    parser.add_argument("command", nargs="?", default="status", choices=["status", "clean", "thumbs"])
    parser.add_argument("--yes", action="store_true",
                        help="with clean: really delete the orphaned files; with thumbs: rebuild the thumbnails")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    upload_dir = os.environ.get("UPLOAD_DIR", "").strip()
    if not upload_dir:
        print("UPLOAD_DIR is not set", file=sys.stderr)
        return 1
    folder = Path(upload_dir)
    if not folder.is_dir():
        print(f"Upload folder not found: {folder}", file=sys.stderr)
        return 1
    if args.command == "thumbs":
        return thumbs_command(folder, args.yes)

    from .config import load_database_config
    from .db import connect
    from .repository import MySQLRepository

    repo = MySQLRepository(connect(load_database_config()))
    try:
        referenced = repo.upload_filenames()
    finally:
        repo.close()
    report = scan_folder(folder, referenced)

    print(f"{len(report.orphans)} orphaned file(s), {_size(report.orphan_bytes)}")
    for path in report.orphans:
        print(f"  {path.name}")
    if report.recent:
        print(f"{report.recent} unreferenced file(s) changed in the last hour were skipped (uploads may be in progress)")
    if report.missing:
        print(f"{len(report.missing)} file(s) the database refers to are missing from {folder}:")
        for name in report.missing:
            print(f"  {name}")
    if report.other:
        print(f"{report.other} other item(s) in the folder were left alone")

    if args.command == "clean":
        if not args.yes:
            print("Nothing was deleted. Run 'python -m app.uploads clean --yes' to delete the orphaned files.")
            return 0
        deleted = delete_orphans(report)
        print(f"Deleted {deleted} orphaned file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
