"""Installable app: manifest, service worker, offline page, icons, phone markup."""

import json
from pathlib import Path

from PIL import Image

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


def test_manifest_is_public_and_valid(client):
    resp = client.get("/manifest.webmanifest")
    assert resp.status_code == 200 and resp.mimetype == "application/manifest+json"
    data = json.loads(resp.get_data(as_text=True))
    resp.close()
    assert data["display"] == "standalone" and data["scope"] == "/" and data["start_url"].startswith("/items")
    sizes = {(i["sizes"], i.get("purpose", "any")) for i in data["icons"]}
    assert ("192x192", "any") in sizes and ("512x512", "any") in sizes and ("512x512", "maskable") in sizes
    for icon in data["icons"]:
        path = STATIC / icon["src"].removeprefix("/static/")
        with Image.open(path) as img:
            assert f"{img.width}x{img.height}" == icon["sizes"]


def test_service_worker_served_from_root_without_caching(client):
    resp = client.get("/sw.js")
    assert resp.status_code == 200 and resp.mimetype == "text/javascript"
    assert resp.headers["Cache-Control"] == "no-cache" and resp.headers["Service-Worker-Allowed"] == "/"
    body = resp.get_data(as_text=True)
    resp.close()
    assert 'request.method !== "GET"' in body  # never intercepts form posts


def test_service_worker_precache_files_exist(client):
    body = (STATIC / "sw.js").read_text()
    start = body.index("var PRECACHE = [")
    urls = [line.strip().strip('",') for line in body[start:body.index("];", start)].splitlines()[1:]]
    urls = [u for u in urls if u.startswith("/")]
    assert "/static/style.css" in urls
    for url in urls:
        resp = client.get(url)
        assert resp.status_code == 200, url
        resp.close()


def test_offline_page_is_public_and_has_no_user_data(client, logged_in):
    resp = logged_in.get("/offline")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200 and "You're offline" in body
    assert "csrf_token" not in body and "Log out" not in body


def test_favicon(client):
    resp = client.get("/favicon.ico")
    assert resp.status_code == 200 and resp.mimetype == "image/png"
    resp.close()


def test_pages_link_manifest_icons_and_script(logged_in):
    body = logged_in.get("/items").get_data(as_text=True)
    for needle in ('rel="manifest" href="/manifest.webmanifest"', 'rel="apple-touch-icon"', 'name="theme-color"',
                   "/static/app.js", 'id="nav-toggle"', 'viewport-fit=cover'):
        assert needle in body, needle


def test_tables_have_phone_labels(logged_in, fake_repo):
    from tests.conftest import csrf
    logged_in.post("/items/new", data={"category": "building", "name": "Mill", "storage_tub": "3",
                                       "csrf_token": csrf(logged_in)})
    body = logged_in.get("/items").get_data(as_text=True)
    assert 'class="responsive"' in body and 'data-label="Storage"' in body and 'class="primary"' in body
    assert 'data-label="Tub"' in logged_in.get("/storage").get_data(as_text=True)


def test_photo_forms_offer_camera(logged_in, fake_repo):
    from tests.conftest import csrf
    logged_in.post("/items/new", data={"category": "building", "name": "Mill", "csrf_token": csrf(logged_in)})
    item_id = next(iter(fake_repo.items))
    body = logged_in.get(f"/items/{item_id}").get_data(as_text=True)
    assert 'capture="environment"' in body and "Take a photo" in body and "multiple" in body


def test_camera_and_library_files_upload_together(logged_in, fake_repo):
    from tests.conftest import csrf, png_bytes
    logged_in.post("/items/new", data={"category": "building", "name": "Mill", "csrf_token": csrf(logged_in)})
    item_id = next(iter(fake_repo.items))
    # the camera input and the library input share name="photo"
    resp = logged_in.post(f"/items/{item_id}/photos", data={
        "photo": [(png_bytes(), "camera.jpg"), (png_bytes(), "library.png")], "csrf_token": csrf(logged_in)},
        content_type="multipart/form-data", follow_redirects=True)
    assert "Added 2 photos." in resp.get_data(as_text=True)
