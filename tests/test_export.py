"""Collection export: JSON, CSV (with formula-injection protection) and the streamed zip."""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import zipfile

import pytest

from app import export
from tests.conftest import csrf, png_bytes

NOW = dt.datetime(2026, 11, 23, 9, 30)


@pytest.fixture
def collection(logged_in, fake_repo, app):
    """Two pieces (one linked to the other), extras, photos with captions, a scan and a catalog photo."""
    entry = fake_repo.add_catalog(item_number="56.65005", name="Fezziwig's Warehouse")
    logged_in.post("/items/new", data={
        "category": "building", "name": "Fezziwig's Warehouse", "item_number": "56.65005",
        "catalog_id": str(entry), "price_paid": "45.50", "height_in": "7 1/4", "has_original_box": "1",
        "notes": "=HYPERLINK(\"http://evil.example\")", "csrf_token": csrf(logged_in)})
    logged_in.post("/items/new", data={"category": "figure", "name": "Mr. & Mrs. Fezziwig",
                                       "csrf_token": csrf(logged_in)})
    building, figure = sorted(fake_repo.items)
    fake_repo.add_link(building, figure)
    logged_in.post(f"/items/{building}/extras", data={"description": "Light cord", "quantity": "2",
                                                      "csrf_token": csrf(logged_in)})
    logged_in.post(f"/items/{building}/extras", data={"description": "-bulb", "csrf_token": csrf(logged_in)})
    upload_dir = app.config["DICKENS"].upload_dir
    logged_in.post(f"/items/{building}/photos", data={
        "photo": [(png_bytes(), "front.png"), (png_bytes((60, 40)), "side.png")], "csrf_token": csrf(logged_in)},
        content_type="multipart/form-data")
    first, second = [p["id"] for p in fake_repo.list_photos(building)]
    fake_repo.reorder_photos(building, [second, first])
    fake_repo.set_photo_caption(first, "Chip on the eave")
    from pathlib import Path

    scan_name = "c" * 32 + ".glb"
    (Path(upload_dir) / scan_name).write_bytes(b"glTF" + b"\0" * 60)
    fake_repo.add_scan(building, scan_name, "house scan.glb", 64)
    catalog_photo = fake_repo.add_catalog_photo(entry, "d" * 32 + ".jpg", None)
    (Path(upload_dir) / ("d" * 32 + ".jpg")).write_bytes(b"\xff\xd8catalog")
    return {"building": building, "figure": figure, "first": first, "second": second, "entry": entry,
            "catalog_photo": catalog_photo, "upload_dir": upload_dir}


def test_exports_need_login(client):
    for name in ("collection.json", "pieces.csv", "extras.csv", "photos-and-scans.zip"):
        resp = client.get(f"/export/{name}")
        assert resp.status_code == 302 and "/login" in resp.headers["Location"]


def test_unknown_export_is_404(logged_in):
    assert logged_in.get("/export/passwords.txt").status_code == 404


def test_summary_page_links_every_export(logged_in):
    body = logged_in.get("/summary").get_data(as_text=True)
    for name in ("pieces.csv", "extras.csv", "collection.json", "photos-and-scans.zip"):
        assert f'href="/export/{name}"' in body


def test_json_export(logged_in, collection):
    resp = logged_in.get("/export/collection.json")
    assert resp.status_code == 200 and resp.mimetype == "application/json"
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert resp.headers["Content-Disposition"].startswith('attachment; filename="dickens-village-collection-')
    doc = json.loads(resp.get_data(as_text=True))
    assert doc["format"] == "dickens-village-collection" and doc["format_version"] == 1
    pieces = {p["name"]: p for p in doc["pieces"]}
    building = pieces["Fezziwig's Warehouse"]
    assert building["price_paid"] == "45.50" and building["height_in"] == "7.250"
    assert building["has_original_box"] is True and building["catalog_item_number"] == "56.65005"
    assert [e["description"] for e in building["extras"]] == ["Light cord", "-bulb"]
    assert building["linked_piece_ids"] == [collection["figure"]]
    assert pieces["Mr. & Mrs. Fezziwig"]["linked_piece_ids"] == [collection["building"]]
    assert [(p["id"], p["caption"]) for p in building["photos"]] == [
        (collection["second"], None), (collection["first"], "Chip on the eave")]
    folder = f"pieces/{collection['building']}-Fezziwigs Warehouse"
    assert building["photos"][0]["file"].startswith(f"{folder}/01-")
    assert building["scans"][0]["file"] == f"{folder}/scans/{'c' * 32}.glb"
    assert doc["catalog_photos"][0]["file"] == f"catalog/{collection['entry']}-56.65005/01-{'d' * 32}.jpg"


def test_csv_exports_open_cleanly_and_never_run_formulas(logged_in, collection):
    resp = logged_in.get("/export/pieces.csv")
    assert resp.mimetype == "text/csv" and "charset=utf-8" in resp.headers["Content-Type"]
    text = resp.get_data(as_text=True)
    assert text.startswith("﻿")
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    building = next(r for r in rows if r["name"] == "Fezziwig's Warehouse")
    assert building["notes"] == "'=HYPERLINK(\"http://evil.example\")"
    assert building["has_original_box"] == "yes" and building["has_sleeve"] == "no"
    assert (building["extras"], building["photos"], building["scans"]) == ("3", "2", "1")
    assert building["catalog_item_number"] == "56.65005"
    extras = list(csv.DictReader(io.StringIO(logged_in.get("/export/extras.csv").get_data(as_text=True).lstrip("﻿"))))
    assert [(e["piece_name"], e["description"], e["quantity"]) for e in extras] == [
        ("Fezziwig's Warehouse", "Light cord", "2"), ("Fezziwig's Warehouse", "'-bulb", "1")]


def test_zip_export_contains_every_file_and_the_data(logged_in, collection):
    resp = logged_in.get("/export/photos-and-scans.zip")
    assert resp.mimetype == "application/zip" and resp.is_streamed
    archive = zipfile.ZipFile(io.BytesIO(resp.get_data()))
    assert archive.testzip() is None
    names = archive.namelist()
    assert {"collection.json", "pieces.csv", "extras.csv"} <= set(names)
    folder = f"pieces/{collection['building']}-Fezziwigs Warehouse"
    photo_files = sorted(n for n in names if n.startswith(folder) and n.endswith(".jpg"))
    assert [n.split("/")[-1][:3] for n in photo_files] == ["01-", "02-"]
    stored = {p["id"]: p["filename"] for p in [*collection_photos(logged_in)]}
    assert archive.read(f"{folder}/01-{stored[collection['second']]}")[:2] == b"\xff\xd8"
    assert archive.read(f"{folder}/scans/{'c' * 32}.glb").startswith(b"glTF")
    assert archive.read(f"catalog/{collection['entry']}-56.65005/01-{'d' * 32}.jpg") == b"\xff\xd8catalog"
    assert "MISSING.txt" not in names
    assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())


def collection_photos(client):
    doc = json.loads(client.get("/export/collection.json").get_data(as_text=True))
    return [{"id": p["id"], "filename": p["file"].rsplit("-", 1)[-1]} for piece in doc["pieces"] for p in piece["photos"]]


def test_zip_lists_missing_files_instead_of_failing(logged_in, collection):
    from pathlib import Path

    (Path(collection["upload_dir"]) / ("c" * 32 + ".glb")).unlink()
    archive = zipfile.ZipFile(io.BytesIO(logged_in.get("/export/photos-and-scans.zip").get_data()))
    assert "scans/" + "c" * 32 + ".glb" in archive.read("MISSING.txt").decode()
    assert archive.testzip() is None


def test_zip_paths_are_safe_whatever_the_names(tmp_path):
    data = {"items": [{"id": 7, "name": "../../etc/passwd <Évil>\\x", "category": "building"}],
            "extras": [], "links": [], "scans": [],
            "photos": [{"id": 1, "item_id": 7, "filename": "a" * 32 + ".jpg", "original_name": "../x.jpg",
                        "sort_order": 0, "caption": None, "created_at": NOW}],
            "catalog_photos": [{"id": 2, "catalog_id": 3, "filename": "../../boom.jpg", "original_name": None,
                                "sort_order": 0, "caption": None, "created_at": NOW, "item_number": "../..",
                                "name": "x"}]}
    for column in export.ITEM_COLUMNS:
        data["items"][0].setdefault(column, None)
    (tmp_path / ("a" * 32 + ".jpg")).write_bytes(b"x")
    archive = zipfile.ZipFile(io.BytesIO(b"".join(export.zip_stream(data, str(tmp_path), NOW))))
    for name in archive.namelist():
        assert ".." not in name.split("/") and not name.startswith("/") and "\\" not in name
    assert "pieces/7-etcpasswd vilx/01-" + "a" * 32 + ".jpg" in archive.namelist()
    assert not any("boom" in n for n in archive.namelist())  # an unexpected stored name is never read


def test_zip_is_produced_in_chunks(tmp_path):
    big = "b" * 32 + ".glb"
    (tmp_path / big).write_bytes(b"glTF" + b"\0" * (3 * 1024 * 1024))
    data = {"items": [{**{c: None for c in export.ITEM_COLUMNS}, "id": 1, "name": "Big"}], "extras": [],
            "links": [], "photos": [], "catalog_photos": [],
            "scans": [{"id": 1, "item_id": 1, "filename": big, "original_name": None, "size_bytes": 3 * 1024 * 1024 + 4,
                       "created_at": NOW}]}
    chunks = list(export.zip_stream(data, str(tmp_path), NOW, chunk_size=256 * 1024))
    assert max(len(c) for c in chunks) < 600 * 1024  # never the whole file at once
    archive = zipfile.ZipFile(io.BytesIO(b"".join(chunks)))
    assert len(archive.read(f"pieces/1-Big/scans/{big}")) == 3 * 1024 * 1024 + 4
