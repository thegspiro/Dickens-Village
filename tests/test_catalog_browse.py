"""The Catalog page's filters: entries without photos, entries linked to pieces you own."""

from __future__ import annotations

import pytest

from tests.conftest import csrf


@pytest.fixture
def catalog(logged_in, fake_repo):
    ids = {
        "pictured": fake_repo.add_catalog(item_number="56.65001", name="Pictured Cottage"),
        "bare": fake_repo.add_catalog(item_number="56.65002", name="Bare Cottage"),
        "owned_bare": fake_repo.add_catalog(item_number="56.65003", name="Owned Bare Mill"),
        "owned_pictured": fake_repo.add_catalog(item_number="56.65004", name="Owned Pictured Mill"),
        "unlisted_owned": fake_repo.add_catalog(item_number="56.65009", name="Withdrawn Owned Inn", listed=0),
        "unlisted": fake_repo.add_catalog(item_number="56.65010", name="Withdrawn Inn", listed=0),
    }
    for key in ("pictured", "owned_pictured"):
        fake_repo.add_catalog_photo(ids[key], f"{ids[key]:032x}.jpg", None)
    for key in ("owned_bare", "owned_pictured", "unlisted_owned", "owned_bare"):
        logged_in.post("/items/new", data={"category": "building", "name": f"Mine {key}",
                                           "catalog_id": str(ids[key]), "csrf_token": csrf(logged_in)})
    return ids


def names(body, fake_repo):
    return [r["name"] for r in fake_repo.catalog.values() if f">{r['name']}</a>" in body]


def test_no_photos_yet_lists_entries_without_photos(logged_in, fake_repo, catalog):
    body = logged_in.get("/catalog?missing=1").get_data(as_text=True)
    assert sorted(names(body, fake_repo)) == ["Bare Cottage", "Owned Bare Mill", "Withdrawn Owned Inn"]
    assert "3 entries without photos." in body
    assert f'href="/catalog/{catalog["bare"]}#photos"' in body  # the + placeholder goes straight to uploading


def test_only_pieces_i_own_with_and_without_the_photo_filter(logged_in, fake_repo, catalog):
    body = logged_in.get("/catalog?owned=1").get_data(as_text=True)
    assert sorted(names(body, fake_repo)) == ["Owned Bare Mill", "Owned Pictured Mill", "Withdrawn Owned Inn"]
    assert "You own 2" in body  # two of my pieces are linked to Owned Bare Mill
    body = logged_in.get("/catalog?owned=1&missing=1").get_data(as_text=True)
    assert sorted(names(body, fake_repo)) == ["Owned Bare Mill", "Withdrawn Owned Inn"]
    assert "without photos that linked to pieces you own" in body


def test_filters_combine_with_a_search(logged_in, fake_repo, catalog):
    body = logged_in.get("/catalog?q=Mill&missing=1").get_data(as_text=True)
    assert names(body, fake_repo) == ["Owned Bare Mill"]
    assert "matching “Mill”" in body


def test_plain_search_is_unchanged(logged_in, fake_repo, catalog):
    body = logged_in.get("/catalog?q=Cottage").get_data(as_text=True)
    assert sorted(names(body, fake_repo)) == ["Bare Cottage", "Pictured Cottage"]
    assert "Withdrawn" not in logged_in.get("/catalog?q=Inn").get_data(as_text=True)
    assert "Type at least two characters." in logged_in.get("/catalog?q=C").get_data(as_text=True)


def test_everything_has_photos_message(logged_in, fake_repo):
    entry = fake_repo.add_catalog(item_number="56.1", name="Only One")
    fake_repo.add_catalog_photo(entry, "e" * 32 + ".jpg", None)
    body = logged_in.get("/catalog?missing=1").get_data(as_text=True)
    assert "Every catalog entry has at least one reference photo." in body


def test_pages_of_100(logged_in, fake_repo):
    for n in range(230):
        fake_repo.add_catalog(item_number=f"56.{n:05d}", name=f"Entry {n:03d}")
    first = logged_in.get("/catalog?missing=1").get_data(as_text=True)
    assert "230 entries without photos." in first and "Page 1 of 3." in first
    assert ">Entry 000</a>" in first and ">Entry 100</a>" not in first
    assert 'href="/catalog?missing=1&amp;page=2"' in first
    third = logged_in.get("/catalog?missing=1&page=3").get_data(as_text=True)
    assert ">Entry 229</a>" in third and ">Entry 129</a>" not in third
    assert 'href="/catalog?missing=1&amp;page=2">← Previous' in third
