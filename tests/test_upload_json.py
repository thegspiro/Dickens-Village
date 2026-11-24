"""Photo uploads answered in JSON for the upload script, and the ?uploaded=N message."""

from __future__ import annotations

import io
import re

import pytest

from tests.conftest import csrf, png_bytes

JSON = {"Accept": "application/json"}


@pytest.fixture
def piece(logged_in, fake_repo):
    logged_in.post("/items/new", data={"category": "building", "name": "Mill", "csrf_token": csrf(logged_in)})
    return next(iter(fake_repo.items))


def post(client, url, files):
    return client.post(url, data={"photo": files, "csrf_token": csrf(client)}, headers=JSON,
                       content_type="multipart/form-data")


def test_json_upload_reports_added_and_adds_no_flash(logged_in, fake_repo, piece):
    resp = post(logged_in, f"/items/{piece}/photos", [(png_bytes(), "a.png")])
    assert resp.status_code == 200 and resp.get_json() == {"added": 1, "errors": []}
    assert len(fake_repo.photos) == 1
    page = logged_in.get(f"/items/{piece}").get_data(as_text=True)
    assert "Added 1 photo" not in page  # the script shows progress itself; no stray message later


def test_json_upload_reports_each_refused_file(logged_in, fake_repo, piece):
    resp = post(logged_in, f"/items/{piece}/photos", [(io.BytesIO(b"<?php"), "bad.jpg"), (png_bytes(), "ok.png")])
    assert resp.status_code == 200
    assert resp.get_json() == {"added": 1, "errors": [{"name": "bad.jpg", "message": "That file is not a supported image."}]}
    resp = post(logged_in, f"/items/{piece}/photos", [(io.BytesIO(b"<?php"), "bad.jpg")])
    assert resp.status_code == 400 and resp.get_json()["added"] == 0


def test_json_upload_with_no_file(logged_in, piece):
    resp = logged_in.post(f"/items/{piece}/photos", data={"csrf_token": csrf(logged_in)}, headers=JSON)
    assert resp.status_code == 400
    assert resp.get_json()["errors"] == [{"name": "", "message": "Choose one or more photos to upload."}]


def test_json_catalog_upload(logged_in, fake_repo):
    entry = fake_repo.add_catalog(item_number="56.1", name="Entry")
    resp = post(logged_in, f"/catalog/{entry}/photos", [(png_bytes(), "a.png")])
    assert resp.get_json() == {"added": 1, "errors": []}
    assert len(fake_repo.list_catalog_photos(entry)) == 1
    assert post(logged_in, "/catalog/999/photos", [(png_bytes(), "a.png")]).status_code == 404


def test_form_uploads_still_flash_and_redirect(logged_in, fake_repo, piece):
    resp = logged_in.post(f"/items/{piece}/photos", data={
        "photo": [(io.BytesIO(b"<?php"), "bad.jpg"), (png_bytes(), "ok.png")], "csrf_token": csrf(logged_in)},
        content_type="multipart/form-data")
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/items/{piece}#photos")
    page = logged_in.get(f"/items/{piece}").get_data(as_text=True)
    assert "Added 1 photo." in page and "bad.jpg: That file is not a supported image." in page


@pytest.mark.parametrize("value, shown", [("3", "Added 3 photos."), ("1", "Added 1 photo."),
                                           ("0", None), ("abc", None), ("5000", None)])
def test_uploaded_message_after_the_script_reloads(logged_in, fake_repo, piece, value, shown):
    logged_in.get(f"/items/{piece}")  # shows (and clears) the "Added “Mill”." message from creating it
    page = logged_in.get(f"/items/{piece}?uploaded={value}").get_data(as_text=True)
    if shown:
        assert shown in page
    else:
        assert re.search(r"Added \d+ photos?\.", page) is None


def test_upload_forms_are_marked_for_the_script(logged_in, fake_repo, piece, app):
    page = logged_in.get(f"/items/{piece}").get_data(as_text=True)
    assert f'data-uploader data-max-mb="{app.config["DICKENS"].max_upload_mb}"' in page
    assert 'src="/static/uploader.mjs"' in page
