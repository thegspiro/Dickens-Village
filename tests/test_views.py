from tests.conftest import csrf, png_bytes


def add_item(client, **fields):
    data = {"category": "building", "name": "Fezziwig's Warehouse", "csrf_token": csrf(client)}
    data.update(fields)
    resp = client.post("/items/new", data=data)
    assert resp.status_code == 302, resp.get_data(as_text=True)
    return int(resp.headers["Location"].rstrip("/").split("/")[-1])


def test_create_item_with_storage_and_condition(logged_in, fake_repo):
    item_id = add_item(
        logged_in, storage_location="Basement shelf 2", storage_tub="3", storage_tray="Top",
        condition_grade="good", condition_checked_on="2026-11-01", condition_notes="Small chip on chimney",
        price_paid="45", has_original_box="1",
    )
    item = fake_repo.items[item_id]
    assert (item["storage_location"], item["storage_tub"], item["storage_tray"]) == ("Basement shelf 2", "3", "Top")
    page = logged_in.get(f"/items/{item_id}").get_data(as_text=True)
    assert "Basement shelf 2 › Tub 3 › Tray Top" in page
    assert "Good" in page and "Small chip on chimney" in page and "$45.00" in page


def test_create_item_validation_errors_rerender(logged_in, fake_repo):
    resp = logged_in.post("/items/new", data={"category": "building", "name": "", "price_paid": "abc",
                                              "csrf_token": csrf(logged_in)})
    assert resp.status_code == 400
    body = resp.get_data(as_text=True)
    assert "Name is required." in body and "Enter an amount" in body
    assert fake_repo.items == {}


def test_create_requires_csrf(logged_in, fake_repo):
    resp = logged_in.post("/items/new", data={"category": "building", "name": "x"})
    assert resp.status_code == 400 and fake_repo.items == {}


def test_user_input_is_escaped(logged_in):
    item_id = add_item(logged_in, name="<script>alert(1)</script>", notes="<b>bold</b>")
    body = logged_in.get(f"/items/{item_id}").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body and "&lt;b&gt;bold" in body


def test_edit_item_prefills_and_saves(logged_in, fake_repo):
    item_id = add_item(logged_in, price_paid="12.5", storage_tub="1")
    body = logged_in.get(f"/items/{item_id}/edit").get_data(as_text=True)
    assert 'value="12.50"' in body and 'value="1"' in body
    resp = logged_in.post(f"/items/{item_id}/edit", data={
        "category": "building", "name": "Fezziwig's Warehouse", "storage_tub": "4", "csrf_token": csrf(logged_in)})
    assert resp.status_code == 302
    assert fake_repo.items[item_id]["storage_tub"] == "4"
    assert fake_repo.items[item_id]["price_paid"] is None  # cleared field is saved as NULL


def test_missing_item_404(logged_in):
    assert logged_in.get("/items/999").status_code == 404
    assert logged_in.get("/items/999/edit").status_code == 404


def test_delete_needs_confirmation_post(logged_in, fake_repo):
    item_id = add_item(logged_in)
    assert logged_in.get(f"/items/{item_id}/delete").status_code == 200
    assert item_id in fake_repo.items
    resp = logged_in.post(f"/items/{item_id}/delete", data={"csrf_token": csrf(logged_in)})
    assert resp.status_code == 302 and item_id not in fake_repo.items


def test_extras_add_edit_delete(logged_in, fake_repo):
    item_id = add_item(logged_in)
    resp = logged_in.post(f"/items/{item_id}/extras", data={
        "description": "Light cord", "quantity": "1", "condition_grade": "excellent", "csrf_token": csrf(logged_in)})
    assert resp.status_code == 302
    (extra_id,) = fake_repo.extras
    body = logged_in.get(f"/items/{item_id}").get_data(as_text=True)
    assert "Light cord" in body and "Excellent" in body

    resp = logged_in.post(f"/items/{item_id}/extras/{extra_id}/edit", data={
        "description": "Light cord", "quantity": "2", "condition_grade": "fair", "csrf_token": csrf(logged_in)})
    assert resp.status_code == 302
    assert fake_repo.extras[extra_id]["quantity"] == 2 and fake_repo.extras[extra_id]["condition_grade"] == "fair"

    listing = logged_in.get("/items?has_extras=yes").get_data(as_text=True)
    assert "Fezziwig" in listing
    assert "Fezziwig" not in logged_in.get("/items?has_extras=no").get_data(as_text=True)

    resp = logged_in.post(f"/items/{item_id}/extras/{extra_id}/delete", data={"csrf_token": csrf(logged_in)})
    assert resp.status_code == 302 and fake_repo.extras == {}


def test_extra_validation_error(logged_in, fake_repo):
    item_id = add_item(logged_in)
    resp = logged_in.post(f"/items/{item_id}/extras", data={"description": "", "csrf_token": csrf(logged_in)})
    assert resp.status_code == 400 and "Describe the extra piece." in resp.get_data(as_text=True)


def test_extra_belongs_to_item(logged_in):
    a = add_item(logged_in, name="A")
    b = add_item(logged_in, name="B")
    logged_in.post(f"/items/{a}/extras", data={"description": "Bulb", "csrf_token": csrf(logged_in)})
    resp = logged_in.post(f"/items/{b}/extras/3/delete", data={"csrf_token": csrf(logged_in)})
    assert resp.status_code == 404


def test_links_between_building_and_figure(logged_in, fake_repo):
    building = add_item(logged_in, name="Cratchit's Cottage")
    figure = add_item(logged_in, category="figure", name="Bob Cratchit & Tiny Tim")
    other_building = add_item(logged_in, name="Old Curiosity Shop")

    resp = logged_in.post(f"/items/{figure}/links", data={"other_id": str(building), "csrf_token": csrf(logged_in)})
    assert resp.status_code == 302 and (building, figure) in fake_repo.links
    assert "Bob Cratchit" in logged_in.get(f"/items/{building}").get_data(as_text=True)

    logged_in.post(f"/items/{building}/links", data={"other_id": str(other_building), "csrf_token": csrf(logged_in)})
    assert (building, other_building) not in fake_repo.links and (other_building, building) not in fake_repo.links

    resp = logged_in.post(f"/items/{building}/links/{figure}/delete", data={"csrf_token": csrf(logged_in)})
    assert resp.status_code == 302 and fake_repo.links == set()


def test_list_filters_by_tub(logged_in):
    add_item(logged_in, name="In tub 1", storage_tub="1")
    add_item(logged_in, name="In tub 2", storage_tub="2")
    logged_in.get("/items")  # consume the "Added …" flash message
    body = logged_in.get("/items?storage_tub=1").get_data(as_text=True)
    assert "In tub 1" in body and "In tub 2" not in body


def test_storage_page_links_to_filtered_list(logged_in):
    add_item(logged_in, storage_location="Attic", storage_tub="7", storage_tray="B")
    add_item(logged_in, name="Loose piece")
    body = logged_in.get("/storage").get_data(as_text=True)
    assert "storage_location=Attic&amp;storage_tub=7&amp;storage_tray=B" in body
    assert "unstored=yes" in body


def test_storage_exact_match_excludes_deeper_levels(logged_in):
    add_item(logged_in, name="Loose on shelf", storage_location="Attic")
    add_item(logged_in, name="Packed in tub", storage_location="Attic", storage_tub="7")
    logged_in.get("/items")
    body = logged_in.get("/items?storage_location=Attic&exact_storage=yes").get_data(as_text=True)
    assert "Loose on shelf" in body and "Packed in tub" not in body


def test_summary_renders(logged_in):
    add_item(logged_in)
    assert logged_in.get("/summary").status_code == 200


def test_photo_upload_serve_delete(logged_in, fake_repo, app):
    item_id = add_item(logged_in)
    resp = logged_in.post(f"/items/{item_id}/photos", data={
        "photo": (png_bytes(), "front.png"), "csrf_token": csrf(logged_in)}, content_type="multipart/form-data")
    assert resp.status_code == 302
    (photo_id,) = fake_repo.photos
    photo = fake_repo.photos[photo_id]
    assert photo["original_name"] == "front.png" and photo["filename"].endswith(".jpg")

    served = logged_in.get(f"/photos/{photo_id}")
    assert served.status_code == 200 and served.mimetype == "image/jpeg"
    assert served.data[:3] == b"\xff\xd8\xff"
    served.close()

    from pathlib import Path
    stored = Path(app.config["DICKENS"].upload_dir) / photo["filename"]
    assert stored.exists()
    logged_in.post(f"/photos/{photo_id}/delete", data={"csrf_token": csrf(logged_in)})
    assert not stored.exists() and fake_repo.photos == {}


def test_photo_rejects_non_image(logged_in, fake_repo):
    import io
    item_id = add_item(logged_in)
    resp = logged_in.post(f"/items/{item_id}/photos", data={
        "photo": (io.BytesIO(b"<?php echo 1; ?>"), "x.jpg"), "csrf_token": csrf(logged_in)},
        content_type="multipart/form-data", follow_redirects=True)
    assert "not a supported image" in resp.get_data(as_text=True)
    assert fake_repo.photos == {}


def test_deleting_item_removes_photo_files(logged_in, fake_repo, app):
    from pathlib import Path
    item_id = add_item(logged_in)
    logged_in.post(f"/items/{item_id}/photos", data={
        "photo": (png_bytes(), "a.png"), "csrf_token": csrf(logged_in)}, content_type="multipart/form-data")
    stored = Path(app.config["DICKENS"].upload_dir) / next(iter(fake_repo.photos.values()))["filename"]
    assert stored.exists()
    logged_in.post(f"/items/{item_id}/delete", data={"csrf_token": csrf(logged_in)})
    assert not stored.exists()


def test_oversized_upload_rejected(logged_in):
    import io
    item_id = add_item(logged_in)
    resp = logged_in.post(f"/items/{item_id}/photos", data={
        "photo": (io.BytesIO(b"0" * (3 * 1024 * 1024)), "big.jpg"), "csrf_token": csrf(logged_in)},
        content_type="multipart/form-data")
    assert resp.status_code == 413


def test_repo_closed_after_request(logged_in, fake_repo):
    before = fake_repo.closed
    logged_in.get("/items")
    assert fake_repo.closed > before
