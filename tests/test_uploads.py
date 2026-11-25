"""python -m app.uploads: find and delete uploaded files nothing refers to."""

from __future__ import annotations

import os
import time

import pytest

from app import uploads

KEEP = "a" * 32
GONE = "b" * 32
SCAN_KEEP = "c" * 32
SCAN_GONE = "d" * 32
NEW = "e" * 32


def touch(folder, name, age_seconds=7200, content=b"x"):
    path = folder / name
    path.write_bytes(content)
    old = time.time() - age_seconds
    os.utime(path, (old, old))
    return path


@pytest.fixture
def folder(tmp_path):
    for name in (f"{KEEP}.jpg", f"{KEEP}.thumb.jpg", f"{GONE}.jpg", f"{GONE}.thumb.jpg",
                 f"{SCAN_KEEP}.glb", f"{SCAN_GONE}.gltf", ".upload-abc_123.tmp", ".scan-x9.tmp",
                 "notes.txt", "IMG_0001.jpg"):
        touch(tmp_path, name)
    touch(tmp_path, f"{NEW}.jpg", age_seconds=60)  # an upload that is still being recorded
    (tmp_path / "subdir").mkdir()
    return tmp_path


REFERENCED = {f"{KEEP}.jpg", f"{SCAN_KEEP}.glb", f"{'f' * 32}.jpg"}


def test_scan_finds_orphans_and_leaves_everything_else(folder):
    report = uploads.scan_folder(folder, REFERENCED)
    assert sorted(p.name for p in report.orphans) == sorted([
        f"{GONE}.jpg", f"{GONE}.thumb.jpg", f"{SCAN_GONE}.gltf", ".upload-abc_123.tmp", ".scan-x9.tmp"])
    assert report.recent == 1  # NEW is too young to judge
    assert report.missing == [f"{'f' * 32}.jpg"]
    assert report.other == 3  # notes.txt, IMG_0001.jpg, subdir
    assert report.orphan_bytes == 5


def test_delete_removes_only_the_orphans(folder):
    report = uploads.scan_folder(folder, REFERENCED)
    assert uploads.delete_orphans(report) == 5
    left = sorted(p.name for p in folder.iterdir())
    assert left == sorted([f"{KEEP}.jpg", f"{KEEP}.thumb.jpg", f"{SCAN_KEEP}.glb", f"{NEW}.jpg",
                           "notes.txt", "IMG_0001.jpg", "subdir"])
    assert uploads.delete_orphans(report) == 0  # already gone: no error


def test_symlinks_are_never_followed_or_deleted(folder, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "precious.jpg"
    outside.write_bytes(b"keep me")
    (folder / f"{'9' * 32}.jpg").symlink_to(outside)
    report = uploads.scan_folder(folder, REFERENCED)
    assert f"{'9' * 32}.jpg" not in [p.name for p in report.orphans]
    uploads.delete_orphans(report)
    assert outside.read_bytes() == b"keep me"


class FakeRepo:
    def __init__(self, conn):
        pass

    def upload_filenames(self):
        return REFERENCED

    def close(self):
        pass


@pytest.fixture
def cli(monkeypatch, folder):
    import app.db
    import app.repository

    monkeypatch.setenv("UPLOAD_DIR", str(folder))
    for key, value in {"DB_HOST": "db", "DB_NAME": "d", "DB_USER": "u", "DB_PASSWORD": "p"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(app.db, "connect", lambda cfg: object())
    monkeypatch.setattr(app.repository, "MySQLRepository", FakeRepo)
    return folder


def test_status_and_clean_without_yes_delete_nothing(cli, capsys):
    before = sorted(p.name for p in cli.iterdir())
    assert uploads.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "5 orphaned file(s)" in out and f"{GONE}.jpg" in out and "1 file(s) the database refers to are missing" in out
    assert uploads.main(["clean"]) == 0
    assert "Nothing was deleted" in capsys.readouterr().out
    assert sorted(p.name for p in cli.iterdir()) == before


def test_clean_yes_deletes(cli, capsys):
    assert uploads.main(["clean", "--yes"]) == 0
    assert "Deleted 5 orphaned file(s)" in capsys.readouterr().out
    assert not (cli / f"{GONE}.jpg").exists() and (cli / f"{KEEP}.jpg").exists()


def test_needs_an_existing_upload_dir(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("UPLOAD_DIR", raising=False)
    assert uploads.main(["status"]) == 1
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "nope"))
    assert uploads.main(["status"]) == 1
    assert "Upload folder not found" in capsys.readouterr().err


# -- thumbs: rebuild thumbnails smaller than the gallery uses ------------------------

def _store(folder, name, size, thumb_edge=None):
    """A stored photo as the app writes it, with a thumbnail of thumb_edge (None: no thumbnail)."""
    from PIL import Image

    from app import photos

    image = Image.new("RGB", size, (120, 80, 60))
    image.save(folder / f"{name}.jpg", format="JPEG")
    if thumb_edge:
        thumb = image.copy()
        thumb.thumbnail((thumb_edge, thumb_edge))
        thumb.save(folder / photos.thumb_name(f"{name}.jpg"), format="JPEG")


def _thumb_size(folder, name):
    from PIL import Image

    from app import photos

    with Image.open(folder / photos.thumb_name(f"{name}.jpg")) as img:
        return img.size


@pytest.fixture
def photo_folder(tmp_path):
    _store(tmp_path, "1" * 32, (2000, 1500), thumb_edge=320)   # old small thumbnail
    _store(tmp_path, "2" * 32, (1200, 1600), thumb_edge=None)  # from before thumbnails existed
    _store(tmp_path, "3" * 32, (2000, 1000), thumb_edge=480)   # already current
    _store(tmp_path, "4" * 32, (300, 200), thumb_edge=320)     # photo smaller than 480: thumbnail is full size
    (tmp_path / ("5" * 32 + ".jpg")).write_bytes(b"not a jpeg")  # damaged
    (tmp_path / "holiday.jpg").write_bytes(b"x")                 # not ours
    return tmp_path


def test_finds_only_thumbnails_that_are_too_small_or_missing(photo_folder):
    assert uploads.stale_thumbnails(photo_folder) == ["1" * 32 + ".jpg", "2" * 32 + ".jpg", "5" * 32 + ".jpg"]


def test_report_changes_nothing(photo_folder, capsys):
    before = {p.name: p.read_bytes() for p in photo_folder.iterdir()}
    assert uploads.thumbs_command(photo_folder, apply=False) == 0
    assert "3 photo(s) have a thumbnail smaller than 480 px" in capsys.readouterr().out
    assert {p.name: p.read_bytes() for p in photo_folder.iterdir()} == before


def test_rebuild_makes_480px_thumbnails_and_reports_damaged_photos(photo_folder, capsys):
    untouched = (photo_folder / ("3" * 32 + ".thumb.jpg")).read_bytes()
    assert uploads.thumbs_command(photo_folder, apply=True) == 1  # one photo couldn't be read
    out = capsys.readouterr().out
    assert "Rebuilt 2 thumbnail(s); 1 could not be rebuilt" in out and "5" * 32 in out
    assert _thumb_size(photo_folder, "1" * 32) == (480, 360)
    assert _thumb_size(photo_folder, "2" * 32) == (360, 480)
    assert (photo_folder / ("3" * 32 + ".thumb.jpg")).read_bytes() == untouched
    assert _thumb_size(photo_folder, "4" * 32) == (300, 200)
    assert not list(photo_folder.glob(".upload-*.tmp"))  # written atomically, nothing left over
    assert uploads.stale_thumbnails(photo_folder) == ["5" * 32 + ".jpg"]  # running again only retries the damaged one


def test_thumbs_needs_no_database(monkeypatch, photo_folder, capsys):
    for key in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("UPLOAD_DIR", str(photo_folder))
    (photo_folder / ("5" * 32 + ".jpg")).unlink()
    assert uploads.main(["thumbs", "--yes"]) == 0
    assert "Rebuilt 2 thumbnail(s)" in capsys.readouterr().out
    assert uploads.main(["thumbs"]) == 0
    assert "0 photo(s)" in capsys.readouterr().out
