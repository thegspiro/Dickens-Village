"""Catalog pages, catalog reference photos, multi-photo upload and thumbnails."""

import io
from decimal import Decimal
from pathlib import Path

import pytest

from app import photos
from tests.conftest import csrf, png_bytes


@pytest.fixture
def entry(fake_repo):
    return fake_repo.add_catalog(item_number="56.65005", name="Fezziwig's Warehouse", details='Part of "X"',
                                 year_issued=1986, year_retired=1995, srp_usd=Decimal("25.00"),
                                 default_category="building", series=None, parent_name="Christmas Carol Cottage",
                                 parent_item_number="56.65005")


def upload_dir(app):
    return Path(app.config["DICKENS"].upload_dir)


def post_photos(client, url, files):
    return client.post(url, data={"photo": files, "csrf_token": csrf(client)}, content_type="multipart/form-data",
                       follow_redirects=True)


def test_catalog_pages_require_login(client, entry):
    for path in ("/catalog", f"/catalog/{entry}", "/catalog-photos/1", "/catalog-photos/1/thumb"):
        assert client.get(path).status_code == 302, path


def test_catalog_browse(logged_in, entry):
    body = logged_in.get("/catalog?q=65005").get_data(as_text=True)
    assert "Fezziwig&#39;s Warehouse" in body and f'href="/catalog/{entry}"' in body
    assert "No catalog entries match" in logged_in.get("/catalog?q=zzzz").get_data(as_text=True)
    assert "at least two" in logged_in.get("/catalog?q=5").get_data(as_text=True)
    assert logged_in.get("/catalog").status_code == 200


def test_catalog_entry_page(logged_in, entry, fake_repo):
    body = logged_in.get(f"/catalog/{entry}").get_data(as_text=True)
    assert "Christmas Carol Cottage" in body and "$25.00 US" in body
    assert f"/items/new?catalog_id={entry}" in body
    assert 'multiple' in body and "None of your pieces" in body
    assert logged_in.get("/catalog/999").status_code == 404


def test_add_to_collection_prefills_form(logged_in, entry):
    body = logged_in.get(f"/items/new?catalog_id={entry}").get_data(as_text=True)
    assert 'value="Fezziwig&#39;s Warehouse"' in body and 'value="56.65005"' in body
    assert 'value="1986"' in body and 'value="1995"' in body
    assert f'name="catalog_id" value="{entry}"' in body
    assert '<option value="building" selected>' in body
    # unknown or junk ids are ignored
    assert logged_in.get("/items/new?catalog_id=999").status_code == 200
    assert logged_in.get("/items/new?catalog_id=abc").status_code == 200


def test_upload_several_catalog_photos_with_thumbnails(logged_in, entry, fake_repo, app):
    resp = post_photos(logged_in, f"/catalog/{entry}/photos",
                       [(png_bytes(), "front.png"), (png_bytes((900, 600), "RGB"), "back.png")])
    assert "Added 2 photos." in resp.get_data(as_text=True)
    stored = list(fake_repo.catalog_photos.values())
    assert [p["original_name"] for p in stored] == ["front.png", "back.png"]
    for p in stored:
        assert (upload_dir(app) / p["filename"]).is_file()
        assert (upload_dir(app) / photos.thumb_name(p["filename"])).is_file()

    first = stored[0]["id"]
    thumb = logged_in.get(f"/catalog-photos/{first}/thumb")
    full = logged_in.get(f"/catalog-photos/{first}")
    assert thumb.status_code == full.status_code == 200 and thumb.mimetype == "image/jpeg"
    thumb.close(); full.close()

    # lookup results carry the first photo as a thumbnail
    (result,) = logged_in.get("/catalog/search?q=65005").get_json()["results"]
    assert result["photo_count"] == 2 and result["thumb_url"] == f"/catalog-photos/{first}/thumb"


def test_bad_files_reported_and_good_ones_kept(logged_in, entry, fake_repo):
    resp = post_photos(logged_in, f"/catalog/{entry}/photos",
                       [(io.BytesIO(b"not an image"), "notes.txt"), (png_bytes(), "ok.png")])
    body = resp.get_data(as_text=True)
    assert "notes.txt: That file is not a supported image." in body and "Added 1 photo." in body
    assert len(fake_repo.catalog_photos) == 1


def test_upload_limits(logged_in, entry, fake_repo):
    resp = logged_in.post(f"/catalog/{entry}/photos", data={"csrf_token": csrf(logged_in)},
                          content_type="multipart/form-data", follow_redirects=True)
    assert "Choose one or more photos" in resp.get_data(as_text=True)
    too_many = [(png_bytes((4, 4), "RGB"), f"{n}.png") for n in range(21)]
    resp = post_photos(logged_in, f"/catalog/{entry}/photos", too_many)
    assert "at most 20" in resp.get_data(as_text=True) and fake_repo.catalog_photos == {}
    assert logged_in.post(f"/catalog/{entry}/photos", data={"photo": (png_bytes(), "a.png")},
                          content_type="multipart/form-data").status_code == 400  # no CSRF token
    assert post_photos(logged_in, "/catalog/999/photos", [(png_bytes(), "a.png")]).status_code == 404


def test_delete_catalog_photo_removes_files(logged_in, entry, fake_repo, app):
    post_photos(logged_in, f"/catalog/{entry}/photos", [(png_bytes(), "a.png")])
    (photo,) = fake_repo.catalog_photos.values()
    main = upload_dir(app) / photo["filename"]
    thumb = upload_dir(app) / photos.thumb_name(photo["filename"])
    resp = logged_in.post(f"/catalog-photos/{photo['id']}/delete", data={"csrf_token": csrf(logged_in)})
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/catalog/{entry}#photos")
    assert not main.exists() and not thumb.exists() and fake_repo.catalog_photos == {}
    assert logged_in.post(f"/catalog-photos/{photo['id']}/delete",
                          data={"csrf_token": csrf(logged_in)}).status_code == 404


def test_linked_piece_shows_catalog_photos_and_entry_lists_it(logged_in, entry, fake_repo):
    post_photos(logged_in, f"/catalog/{entry}/photos", [(png_bytes(), "a.png")])
    (photo_id,) = fake_repo.catalog_photos
    logged_in.post("/items/new", data={"category": "building", "name": "My Fezziwig", "catalog_id": str(entry),
                                       "csrf_token": csrf(logged_in)})
    item_id = next(i for i in fake_repo.items)
    body = logged_in.get(f"/items/{item_id}").get_data(as_text=True)
    assert f"/catalog-photos/{photo_id}/thumb" in body and "Catalog reference photos" in body
    assert "My Fezziwig" in logged_in.get(f"/catalog/{entry}").get_data(as_text=True)


def test_item_multi_upload_and_thumb_fallback(logged_in, fake_repo, app):
    logged_in.post("/items/new", data={"category": "figure", "name": "Carolers", "csrf_token": csrf(logged_in)})
    item_id = next(iter(fake_repo.items))
    resp = post_photos(logged_in, f"/items/{item_id}/photos", [(png_bytes(), "a.png"), (png_bytes(), "b.png")])
    assert "Added 2 photos." in resp.get_data(as_text=True) and len(fake_repo.photos) == 2
    photo = next(iter(fake_repo.photos.values()))
    # a photo stored before thumbnails existed is served full size from the thumb URL
    (upload_dir(app) / photos.thumb_name(photo["filename"])).unlink()
    resp = logged_in.get(f"/photos/{photo['id']}/thumb")
    assert resp.status_code == 200
    resp.close()
