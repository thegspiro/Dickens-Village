"""Matching bulk-upload filenames to catalog entries by item number."""

from __future__ import annotations

import pytest

from app import catalog_match as cm

ENTRIES = [
    {"id": 1, "item_number": "56.65005", "name": "Christmas Carol Cottage"},
    {"id": 2, "item_number": "56.65005", "name": "Fezziwig's Warehouse"},  # a set and its piece share a number
    {"id": 3, "item_number": "56.65161", "name": "Ivy Glen Church"},
    {"id": 4, "item_number": "6000123", "name": "Seven Digit"},
    {"id": 5, "item_number": "6000124FE", "name": "With Letters"},
    {"id": 6, "item_number": None, "name": "No Number"},
]
INDEX = cm.index_entries(ENTRIES)


@pytest.mark.parametrize("filename, ids", [
    ("56.65161.jpg", [3]),
    ("56-65161.JPG", [3]),
    ("5665161.png", [3]),
    ("56 65161.webp", [3]),
    ("65161.jpg", [3]),            # short form
    ("56.65161-2.jpg", [3]),       # photo number
    ("5665161_3.jpg", [3]),
    ("65161 (2).jpg", [3]),
    ("56.65161 front.jpg", [3]),   # words after the number
    ("folder/56.65161.jpg", [3]),
    ("6000123.jpg", [4]),
    ("6000123-1.jpg", [4]),
    ("6000124FE.jpg", [5]),
    ("6000124fe back.jpg", [5]),
    ("56.65005.jpg", [1, 2]),      # shared number: both, for the person to choose
])
def test_matches(filename, ids):
    key, found = cm.match(filename, INDEX)
    assert key and [e["id"] for e in found] == ids


@pytest.mark.parametrize("filename", [
    "IMG_1234.jpg", "photo.jpg", "front 56.65161.jpg", "99999.jpg", "56.99999.jpg", "12.jpg", ".jpg", "",
    "6000123-123.jpg",  # a 3-digit suffix isn't a photo number, so this is a different (unknown) number
])
def test_no_match(filename):
    assert cm.match(filename, INDEX)[1] == []


def test_filename_key():
    assert cm.filename_key("56.65005-2.jpg") == "5665005"
    assert cm.filename_key("IMG_1234.jpg") is None
    assert cm.filename_key("123.jpg") is None  # too short to be an item number
    assert cm.normalise("56.65005") == "5665005" and cm.normalise(None) == ""


def test_every_real_catalog_number_matches_its_own_entry():
    from pathlib import Path

    from app import catalog

    rows = [dict(r.values, id=i) for i, r in enumerate(catalog.load_directory(
        Path(__file__).resolve().parent.parent / "data" / "catalog"))]
    index = cm.index_entries(rows)
    for row in rows:
        if not row["item_number"]:
            continue
        for spelling in (row["item_number"], row["item_number"].replace(".", "-"), row["item_number"].replace(".", "")):
            assert row["id"] in [e["id"] for e in cm.match(f"{spelling}-2.jpg", index)[1]], spelling
