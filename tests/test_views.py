from tests.conftest import add_place, csrf, png_bytes


def add_item(client, **fields):
    data = {"category": "building", "name": "Fezziwig's Warehouse", "csrf_token": csrf(client)}
    data.update(fields)
    resp = client.post("/items/new", data=data)
    assert resp.status_code == 302, resp.get_data(as_text=True)
    return int(resp.headers["Location"].rstrip("/").split("/")[-1])


def test_create_item_with_storage_and_condition(logged_in, fake_repo):
    shelf = add_place(fake_repo, "Basement shelf 2")
    tub = add_place(fake_repo, "3", parent=shelf, kind="Tub")
    tray = add_place(fake_repo, "Top", parent=tub, kind="Tray")
    item_id = add_item(
        logged_in, storage_place_id=str(tray),
        condition_grade="good", condition_checked_on="2026-11-01", condition_notes="Small chip on chimney",
        price_paid="45", has_original_box="1",
    )
    item = fake_repo.items[item_id]
    assert item["storage_place_id"] == tray
    # the old columns follow the place, for exports and older phone apps
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
    one, four = add_place(fake_repo, "Tub 1"), add_place(fake_repo, "Tub 4")
    item_id = add_item(logged_in, price_paid="12.5", storage_place_id=str(one))
    body = logged_in.get(f"/items/{item_id}/edit").get_data(as_text=True)
    assert 'value="12.50"' in body and f'<option value="{one}" selected>Tub 1</option>' in body
    resp = logged_in.post(f"/items/{item_id}/edit", data={
        "category": "building", "name": "Fezziwig's Warehouse", "storage_place_id": str(four),
        "csrf_token": csrf(logged_in)})
    assert resp.status_code == 302
    assert fake_repo.items[item_id]["storage_place_id"] == four
    assert fake_repo.items[item_id]["storage_location"] == "Tub 4"
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


def test_list_filters_by_place_including_places_inside(logged_in, fake_repo):
    attic = add_place(fake_repo, "Attic")
    tub1 = add_place(fake_repo, "1", parent=attic, kind="Tub")
    tub2 = add_place(fake_repo, "2", parent=attic, kind="Tub")
    add_item(logged_in, name="In tub 1", storage_place_id=str(tub1))
    add_item(logged_in, name="In tub 2", storage_place_id=str(tub2))
    add_item(logged_in, name="Loose piece")
    logged_in.get("/items")  # consume the "Added …" flash message
    body = logged_in.get(f"/items?place={tub1}").get_data(as_text=True)
    assert "In tub 1" in body and "In tub 2" not in body
    body = logged_in.get(f"/items?place={attic}").get_data(as_text=True)
    assert "In tub 1" in body and "In tub 2" in body and "Loose piece" not in body
    body = logged_in.get("/items?unstored=yes").get_data(as_text=True)
    assert "Loose piece" in body and "In tub 1" not in body


def test_old_location_tub_tray_links_still_filter(logged_in, fake_repo):
    attic = add_place(fake_repo, "Attic")
    tub7 = add_place(fake_repo, "7", parent=attic, kind="Tub")
    add_item(logged_in, name="Loose on shelf", storage_place_id=str(attic))
    add_item(logged_in, name="Packed in tub", storage_place_id=str(tub7))
    logged_in.get("/items")
    body = logged_in.get("/items?storage_tub=7").get_data(as_text=True)
    assert "Packed in tub" in body and "Loose on shelf" not in body
    body = logged_in.get("/items?storage_location=Attic&exact_storage=yes").get_data(as_text=True)
    assert "Loose on shelf" in body and "Packed in tub" not in body
    assert 'name="storage_location" value="Attic"' in body  # kept when the filters are changed


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


def test_oversized_photo_rejected_with_message(logged_in, fake_repo):
    import io
    item_id = add_item(logged_in)
    # MAX_UPLOAD_MB is 2 in the test config: this one file is too big, the other is fine
    resp = logged_in.post(f"/items/{item_id}/photos", data={
        "photo": [(io.BytesIO(b"0" * (3 * 1024 * 1024)), "big.jpg"), (png_bytes(), "ok.png")],
        "csrf_token": csrf(logged_in)}, content_type="multipart/form-data", follow_redirects=True)
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "big.jpg: That photo is larger than 2 MB." in body and "Added 1 photo." in body
    assert len(fake_repo.photos) == 1


def test_whole_request_limit_still_applies(tmp_path, fake_repo):
    import io
    from app import create_app
    from app.config import load_config
    from tests.conftest import make_env
    env = make_env(tmp_path)
    env["MAX_REQUEST_MB"] = "1"
    env["MAX_UPLOAD_MB"] = "1"
    env["MAX_SCAN_MB"] = "1"
    application = create_app(load_config(env), repo_factory=lambda _c: fake_repo)
    client = application.test_client()
    with client.session_transaction() as sess:
        sess["user"] = "gabriel"
        sess["csrf"] = "t"
    resp = client.post("/items/1/photos", data={"photo": (io.BytesIO(b"0" * (3 * 1024 * 1024)), "x.jpg")},
                       content_type="multipart/form-data")
    assert resp.status_code == 413


def test_repo_closed_after_request(logged_in, fake_repo):
    before = fake_repo.closed
    logged_in.get("/items")
    assert fake_repo.closed > before


def test_edit_form_detects_change_made_elsewhere(logged_in, fake_repo):
    garage = add_place(fake_repo, "Garage")
    tub1 = add_place(fake_repo, "1", parent=garage, kind="Tub")
    tub7 = add_place(fake_repo, "7", parent=garage, kind="Tub")
    item_id = add_item(logged_in, storage_place_id=str(tub1), condition_grade="good")
    page = logged_in.get(f"/items/{item_id}/edit").get_data(as_text=True)
    assert 'name="version" value="1"' in page
    # the phone syncs a change while the edit page is open
    fake_repo.items[item_id].update(storage_place_id=tub7, storage_tub="7", version=2)
    form = {"category": "building", "name": "Fezziwig's Warehouse", "storage_place_id": str(tub1),
            "condition_grade": "fair", "version": "1", "csrf_token": csrf(logged_in)}
    resp = logged_in.post(f"/items/{item_id}/edit", data=form)
    body = resp.get_data(as_text=True)
    assert resp.status_code == 409 and "changed somewhere else" in body
    assert "Storage place: <strong>Garage › Tub 7</strong>" in body
    assert "Tub:" not in body  # the old columns follow the place and aren't listed
    assert fake_repo.items[item_id]["condition_grade"] == "good"  # nothing saved
    assert 'name="version" value="2"' in body  # saving again is a deliberate overwrite
    form["version"] = "2"
    form["csrf_token"] = csrf(logged_in)
    assert logged_in.post(f"/items/{item_id}/edit", data=form).status_code == 302
    assert fake_repo.items[item_id]["condition_grade"] == "fair"


def test_extra_edit_detects_change_made_elsewhere(logged_in, fake_repo):
    item_id = add_item(logged_in)
    logged_in.post(f"/items/{item_id}/extras", data={"description": "Bulb", "csrf_token": csrf(logged_in)})
    (extra_id,) = fake_repo.extras
    assert 'name="version" value="1"' in logged_in.get(f"/items/{item_id}/extras/{extra_id}/edit").get_data(as_text=True)
    fake_repo.extras[extra_id].update({"quantity": 3, "version": 2})
    resp = logged_in.post(f"/items/{item_id}/extras/{extra_id}/edit", data={
        "description": "Bulb", "quantity": "1", "version": "1", "csrf_token": csrf(logged_in)})
    assert resp.status_code == 409 and "Quantity: <strong>3</strong>" in resp.get_data(as_text=True)
    assert fake_repo.extras[extra_id]["quantity"] == 3


def _save_elsewhere_first(monkeypatch, fake_repo, method, rows, row_id, change):
    """Make `method` behave as if another device saved `change` just before it ran
    (after the view's own version check passed)."""
    original = getattr(fake_repo, method)

    def racing(*args, **kwargs):
        rows[row_id].update(change)
        rows[row_id]["version"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(fake_repo, method, racing)


def test_edit_does_not_overwrite_a_save_that_lands_during_the_request(logged_in, fake_repo, monkeypatch):
    tub1, tub7 = add_place(fake_repo, "Tub 1"), add_place(fake_repo, "Tub 7")
    item_id = add_item(logged_in, storage_place_id=str(tub1), condition_grade="good")
    _save_elsewhere_first(monkeypatch, fake_repo, "update_item", fake_repo.items, item_id,
                          {"storage_place_id": tub7})
    form = {"category": "building", "name": "Fezziwig's Warehouse", "storage_place_id": str(tub1),
            "condition_grade": "fair", "version": "1", "csrf_token": csrf(logged_in)}
    resp = logged_in.post(f"/items/{item_id}/edit", data=form)
    body = resp.get_data(as_text=True)
    assert resp.status_code == 409 and "Storage place: <strong>Tub 7</strong>" in body
    assert fake_repo.items[item_id]["storage_place_id"] == tub7
    assert fake_repo.items[item_id]["condition_grade"] == "good"  # nothing from the form was saved
    assert 'name="version" value="2"' in body  # the next save is a deliberate overwrite


def test_extra_edit_does_not_overwrite_a_save_that_lands_during_the_request(logged_in, fake_repo, monkeypatch):
    item_id = add_item(logged_in)
    logged_in.post(f"/items/{item_id}/extras", data={"description": "Bulb", "csrf_token": csrf(logged_in)})
    (extra_id,) = fake_repo.extras
    _save_elsewhere_first(monkeypatch, fake_repo, "update_extra", fake_repo.extras, extra_id, {"quantity": 3})
    resp = logged_in.post(f"/items/{item_id}/extras/{extra_id}/edit", data={
        "description": "Bulb", "quantity": "1", "version": "1", "csrf_token": csrf(logged_in)})
    body = resp.get_data(as_text=True)
    assert resp.status_code == 409 and "Quantity: <strong>3</strong>" in body
    assert 'name="version" value="2"' in body
    assert fake_repo.extras[extra_id]["quantity"] == 3


def test_edit_form_without_a_version_still_saves(logged_in, fake_repo):
    item_id = add_item(logged_in, condition_grade="good")
    fake_repo.items[item_id]["version"] = 5
    resp = logged_in.post(f"/items/{item_id}/edit", data={
        "category": "building", "name": "Fezziwig's Warehouse", "condition_grade": "fair",
        "csrf_token": csrf(logged_in)})
    assert resp.status_code == 302 and fake_repo.items[item_id]["condition_grade"] == "fair"
