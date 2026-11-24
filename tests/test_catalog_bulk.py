"""The 'Add catalog photos by item number' page and its matching endpoint."""

from __future__ import annotations

import pytest

from tests.conftest import csrf


@pytest.fixture
def entries(fake_repo):
    return {
        "church": fake_repo.add_catalog(item_number="56.65161", name="Ivy Glen Church"),
        "set": fake_repo.add_catalog(item_number="56.65005", name="Christmas Carol Cottage"),
        "piece": fake_repo.add_catalog(item_number="56.65005", name="Fezziwig's Warehouse",
                                       parent_name="Christmas Carol Cottage"),
        "gone": fake_repo.add_catalog(item_number="56.65999", name="Withdrawn", listed=0),
    }


def match(client, *names):
    return client.post("/catalog/bulk/match", data={"name": list(names), "csrf_token": csrf(client)})


def test_page_is_linked_from_the_catalog(logged_in):
    page = logged_in.get("/catalog/bulk").get_data(as_text=True)
    assert 'data-match-url="/catalog/bulk/match"' in page and "catalog-bulk.mjs" in page
    assert 'href="/catalog/bulk"' in logged_in.get("/catalog").get_data(as_text=True)


def test_anonymous_requests_are_refused(app):
    anon = app.test_client()
    assert anon.get("/catalog/bulk").status_code == 302
    assert anon.post("/catalog/bulk/match", data={"name": ["56.65161.jpg"]}).status_code == 302


def test_match_returns_entries_per_file_in_order(logged_in, entries):
    resp = match(logged_in, "56.65161-2.jpg", "65005.jpg", "IMG_0001.jpg", "56.99999.jpg", "56.65999.jpg")
    assert resp.status_code == 200 and resp.headers["Cache-Control"] == "private, no-store"
    got = resp.get_json()["matches"]
    assert [m["name"] for m in got] == ["56.65161-2.jpg", "65005.jpg", "IMG_0001.jpg", "56.99999.jpg", "56.65999.jpg"]
    assert [e["id"] for e in got[0]["entries"]] == [entries["church"]]
    assert {e["id"] for e in got[1]["entries"]} == {entries["set"], entries["piece"]}  # shared number: both
    assert got[2] == {"name": "IMG_0001.jpg", "key": None, "entries": []}
    assert got[3]["key"] == "5699999" and got[3]["entries"] == []
    assert got[4]["entries"] == []  # removed from the catalog lists and not linked to a piece
    assert got[0]["entries"][0]["url"] == f"/catalog/{entries['church']}"


def test_an_unlisted_entry_you_own_still_matches(logged_in, fake_repo, entries):
    logged_in.post("/items/new", data={"category": "building", "name": "Mine", "catalog_id": str(entries["gone"]),
                                       "csrf_token": csrf(logged_in)})
    got = match(logged_in, "56.65999.jpg").get_json()["matches"]
    assert [e["id"] for e in got[0]["entries"]] == [entries["gone"]]


def test_match_needs_csrf_and_caps_the_number_of_files(logged_in, entries):
    assert logged_in.post("/catalog/bulk/match", data={"name": ["56.65161.jpg"]}).status_code == 400
    got = match(logged_in, *[f"{n}.jpg" for n in range(600)]).get_json()["matches"]
    assert len(got) == 500
