"""Use one of a piece's photos as a reference photo of its catalog entry."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import photos
from tests.conftest import csrf, png_bytes


@pytest.fixture
def linked(logged_in, fake_repo, app):
    entry = fake_repo.add_catalog(item_number="56.65005", name="Fezziwig's Warehouse")
    logged_in.post("/items/new", data={"category": "building", "name": "My Fezziwig", "catalog_id": str(entry),
                                       "csrf_token": csrf(logged_in)})
    item_id = next(iter(fake_repo.items))
    logged_in.post(f"/items/{item_id}/photos", data={"photo": (png_bytes((300, 200), mode="RGB"), "front.png"),
                                                      "csrf_token": csrf(logged_in)},
                   content_type="multipart/form-data")
    (photo_id,) = fake_repo.photos
    fake_repo.set_photo_caption(photo_id, "Front, with the lamp lit")
    return {"entry": entry, "item": item_id, "photo": photo_id, "dir": Path(app.config["DICKENS"].upload_dir)}


def copy(client, photo_id):
    return client.post(f"/photos/{photo_id}/to-catalog", data={"csrf_token": csrf(client)}, follow_redirects=True)


def test_copies_the_photo_and_thumbnail_with_its_caption(logged_in, fake_repo, linked):
    resp = copy(logged_in, linked["photo"])
    assert "Added to the catalog entry&#39;s reference photos." in resp.get_data(as_text=True)
    (copied,) = fake_repo.list_catalog_photos(linked["entry"])
    source = fake_repo.photos[linked["photo"]]
    assert copied["filename"] != source["filename"] and photos.FILENAME_RE.match(copied["filename"])
    assert copied["caption"] == "Front, with the lamp lit" and copied["original_name"] == "front.png"
    d = linked["dir"]
    assert (d / copied["filename"]).read_bytes() == (d / source["filename"]).read_bytes()
    assert (d / photos.thumb_name(copied["filename"])).is_file()
    assert not list(d.glob(".upload-*.tmp"))


def test_the_copies_are_independent(logged_in, fake_repo, linked):
    copy(logged_in, linked["photo"])
    (copied,) = fake_repo.list_catalog_photos(linked["entry"])
    source_file = fake_repo.photos[linked["photo"]]["filename"]
    logged_in.post(f"/photos/{linked['photo']}/delete", data={"csrf_token": csrf(logged_in)})
    assert not (linked["dir"] / source_file).exists()
    assert (linked["dir"] / copied["filename"]).is_file()
    assert logged_in.get(f"/catalog-photos/{copied['id']}").status_code == 200


def test_the_same_photo_is_not_added_twice(logged_in, fake_repo, linked):
    copy(logged_in, linked["photo"])
    resp = copy(logged_in, linked["photo"])
    assert "already one of the catalog entry" in resp.get_data(as_text=True)
    assert len(fake_repo.list_catalog_photos(linked["entry"])) == 1


def test_needs_a_catalog_link(logged_in, fake_repo, linked):
    fake_repo.items[linked["item"]]["catalog_id"] = None
    resp = copy(logged_in, linked["photo"])
    assert "Link this piece to a catalog entry first" in resp.get_data(as_text=True)
    assert fake_repo.list_catalog_photos(linked["entry"]) == []


def test_a_missing_file_is_reported_not_recorded(logged_in, fake_repo, linked):
    (linked["dir"] / fake_repo.photos[linked["photo"]]["filename"]).unlink()
    resp = copy(logged_in, linked["photo"])
    assert "The photo file is missing on the server." in resp.get_data(as_text=True)
    assert fake_repo.list_catalog_photos(linked["entry"]) == []


def test_needs_csrf_and_an_existing_photo(logged_in, fake_repo, linked):
    assert logged_in.post(f"/photos/{linked['photo']}/to-catalog").status_code == 400
    assert logged_in.post("/photos/999/to-catalog", data={"csrf_token": csrf(logged_in)}).status_code == 404
    assert fake_repo.list_catalog_photos(linked["entry"]) == []


def test_button_only_on_pieces_with_a_catalog_link(logged_in, fake_repo, linked):
    page = logged_in.get(f"/items/{linked['item']}").get_data(as_text=True)
    assert f'action="/photos/{linked["photo"]}/to-catalog"' in page and "Use as catalog photo" in page
    fake_repo.items[linked["item"]]["catalog_id"] = None
    assert "Use as catalog photo" not in logged_in.get(f"/items/{linked['item']}").get_data(as_text=True)


def test_copy_photo_refuses_unexpected_names(tmp_path):
    with pytest.raises(photos.PhotoError):
        photos.copy_photo(str(tmp_path), "../secret.jpg")
