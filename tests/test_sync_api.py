"""Phone-app sync API and the merge rules behind it."""

import uuid
from decimal import Decimal

import pytest

from app.sync import SyncError, clean_changes, item_to_json, merge, to_json_value
from tests.conftest import csrf, png_bytes


# -- merge rules (pure) ------------------------------------------------------------

def test_merge_applies_untouched_fields():
    merged, conflicts = merge({"name": "A", "storage_tub": "1"}, {"storage_tub": "1"}, {"storage_tub": "2"})
    assert conflicts == [] and merged == {"name": "A", "storage_tub": "2"}


def test_merge_same_value_on_both_sides_is_not_a_conflict():
    merged, conflicts = merge({"storage_tub": "2"}, {"storage_tub": "1"}, {"storage_tub": "2"})
    assert conflicts == [] and merged["storage_tub"] == "2"


def test_merge_reports_clash():
    _, conflicts = merge({"storage_tub": "3"}, {"storage_tub": "1"}, {"storage_tub": "2"})
    assert conflicts == [{"field": "storage_tub", "base": "1", "mine": "2", "theirs": "3"}]


def test_merge_treats_blank_and_null_alike():
    _, conflicts = merge({"notes": None}, {"notes": ""}, {"notes": "Chipped"})
    assert conflicts == []


def test_clean_changes_validation():
    with pytest.raises(SyncError, match="Unknown"):
        clean_changes({"base": {"evil": 1}, "changes": {"evil": 2}}, ("name",))
    with pytest.raises(SyncError, match="Missing base"):
        clean_changes({"base": {}, "changes": {"name": "x"}}, ("name",))
    with pytest.raises(SyncError, match="No changes"):
        clean_changes({"base": {}, "changes": {}}, ("name",))
    with pytest.raises(SyncError, match="must be"):
        clean_changes({"base": {"name": "a"}, "changes": {"name": {"$gt": 1}}}, ("name",))


def test_canonical_json_values():
    assert to_json_value("price_paid", Decimal("45.5")) == "45.50"
    assert to_json_value("height_in", Decimal("7.250")) == "7.25"
    assert to_json_value("on_display", 1) is True
    assert to_json_value("year_introduced", 1986) == 1986


# -- helpers -----------------------------------------------------------------------

def headers(client):
    return {"X-CSRF-Token": client.get("/api/v1/session").get_json()["csrf_token"]}


def new_uuid():
    return str(uuid.uuid4())


def add_piece(client, **fields):
    data = {"category": "building", "name": "Old Curiosity Shop", "csrf_token": csrf(client)}
    data.update(fields)
    client.post("/items/new", data=data)


# -- auth --------------------------------------------------------------------------

def test_api_requires_login_with_json_401(client):
    resp = client.get("/api/v1/snapshot")
    assert resp.status_code == 401 and resp.get_json()["error"] == "Not logged in."


def test_writes_require_csrf_header(logged_in):
    resp = logged_in.post("/api/v1/items", json={"client_uuid": new_uuid(), "fields": {}})
    assert resp.status_code == 403
    resp = logged_in.post("/api/v1/items", json={"client_uuid": new_uuid(), "fields": {}},
                          headers={"X-CSRF-Token": "wrong"})
    assert resp.status_code == 403


def test_session_endpoint(logged_in):
    data = logged_in.get("/api/v1/session").get_json()
    assert data["user"] == "gabriel" and len(data["csrf_token"]) > 20


def test_session_lasts_30_days(app):
    assert app.permanent_session_lifetime.days == 30


# -- snapshot and catalog ----------------------------------------------------------

def test_snapshot_shape_and_etag(logged_in, fake_repo):
    add_piece(logged_in, storage_tub="3", price_paid="12.5", height_in="7 1/4", on_display="1")
    item_id = next(iter(fake_repo.items))
    logged_in.post(f"/items/{item_id}/extras", data={"description": "Light cord", "csrf_token": csrf(logged_in)})
    resp = logged_in.get("/api/v1/snapshot")
    assert resp.status_code == 200 and resp.headers["ETag"]
    (item,) = resp.get_json()["items"]
    assert item["storage_tub"] == "3" and item["price_paid"] == "12.50" and item["height_in"] == "7.25"
    assert item["on_display"] is True and item["version"] == 1
    assert item["extras"][0]["description"] == "Light cord" and item["photos"] == []
    again = logged_in.get("/api/v1/snapshot", headers={"If-None-Match": resp.headers["ETag"]})
    assert again.status_code == 304
    add_piece(logged_in, name="Another")
    assert logged_in.get("/api/v1/snapshot", headers={"If-None-Match": resp.headers["ETag"]}).status_code == 200


def test_catalog_download(logged_in, fake_repo):
    fake_repo.add_catalog(item_number="56.65005", name="Fezziwig's Warehouse", default_category="building")
    resp = logged_in.get("/api/v1/catalog")
    (entry,) = resp.get_json()["entries"]
    assert entry["name"] == "Fezziwig's Warehouse" and entry["category"] == "building"
    assert logged_in.get("/api/v1/catalog", headers={"If-None-Match": resp.headers["ETag"]}).status_code == 304


# -- creating pieces ---------------------------------------------------------------

def test_create_is_idempotent(logged_in, fake_repo):
    h = headers(logged_in)
    body = {"client_uuid": new_uuid(), "fields": {"category": "figure", "name": "Carolers", "storage_tub": "2",
                                                   "on_display": True, "height_in": "3 1/2"}}
    first = logged_in.post("/api/v1/items", json=body, headers=h)
    assert first.status_code == 201
    item = first.get_json()["item"]
    assert item["client_uuid"] == body["client_uuid"] and item["height_in"] == "3.5" and item["on_display"] is True
    retry = logged_in.post("/api/v1/items", json=body, headers=h)
    assert retry.status_code == 200 and retry.get_json()["item"]["id"] == item["id"]
    assert len(fake_repo.items) == 1


def test_create_validation_and_bad_requests(logged_in, fake_repo):
    h = headers(logged_in)
    resp = logged_in.post("/api/v1/items", json={"client_uuid": new_uuid(), "fields": {"category": "building"}},
                          headers=h)
    assert resp.status_code == 422 and "name" in resp.get_json()["errors"]
    assert logged_in.post("/api/v1/items", json={"client_uuid": "nope", "fields": {}}, headers=h).status_code == 400
    assert logged_in.post("/api/v1/items", json={"client_uuid": new_uuid(), "fields": {"password": "x"}},
                          headers=h).status_code == 400
    assert logged_in.post("/api/v1/items", data="not json", headers=h).status_code == 400
    resp = logged_in.post("/api/v1/items", json={"client_uuid": new_uuid(), "fields": {
        "category": "building", "name": "X", "catalog_id": 999}}, headers=h)
    assert resp.status_code == 422 and "catalog_id" in resp.get_json()["errors"]
    assert fake_repo.items == {}


# -- editing pieces ----------------------------------------------------------------

def test_patch_merges_with_web_edit_to_other_field(logged_in, fake_repo):
    add_piece(logged_in, storage_tub="1", condition_grade="good")
    item_id = next(iter(fake_repo.items))
    # someone edits the condition on the website after the phone last synced
    logged_in.post(f"/items/{item_id}/edit", data={"category": "building", "name": "Old Curiosity Shop",
                                                   "storage_tub": "1", "condition_grade": "fair",
                                                   "csrf_token": csrf(logged_in)})
    resp = logged_in.patch(f"/api/v1/items/{item_id}", json={"base": {"storage_tub": "1"},
                                                              "changes": {"storage_tub": "4"}}, headers=headers(logged_in))
    assert resp.status_code == 200
    item = resp.get_json()["item"]
    assert item["storage_tub"] == "4" and item["condition_grade"] == "fair" and item["version"] == 3


def test_patch_conflict_applies_nothing(logged_in, fake_repo):
    add_piece(logged_in, storage_tub="1", storage_tray="A")
    item_id = next(iter(fake_repo.items))
    logged_in.post(f"/items/{item_id}/edit", data={"category": "building", "name": "Old Curiosity Shop",
                                                   "storage_tub": "3", "storage_tray": "A",
                                                   "csrf_token": csrf(logged_in)})
    resp = logged_in.patch(f"/api/v1/items/{item_id}", json={
        "base": {"storage_tub": "1", "storage_tray": "A"}, "changes": {"storage_tub": "2", "storage_tray": "B"}},
        headers=headers(logged_in))
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["conflicts"] == [{"field": "storage_tub", "base": "1", "mine": "2", "theirs": "3"}]
    assert body["item"]["storage_tub"] == "3"
    assert fake_repo.items[item_id]["storage_tray"] == "A"  # the non-conflicting field was not applied either


def test_patch_resolution_by_rebasing(logged_in, fake_repo):
    add_piece(logged_in, storage_tub="1")
    item_id = next(iter(fake_repo.items))
    fake_repo.items[item_id]["storage_tub"] = "3"
    # the person chose "keep mine": the phone re-sends with the server's value as base
    resp = logged_in.patch(f"/api/v1/items/{item_id}", json={"base": {"storage_tub": "3"},
                                                              "changes": {"storage_tub": "2"}}, headers=headers(logged_in))
    assert resp.status_code == 200 and resp.get_json()["item"]["storage_tub"] == "2"


def test_patch_invalid_and_missing(logged_in, fake_repo):
    add_piece(logged_in)
    item_id = next(iter(fake_repo.items))
    h = headers(logged_in)
    resp = logged_in.patch(f"/api/v1/items/{item_id}", json={"base": {"name": "Old Curiosity Shop"},
                                                              "changes": {"name": ""}}, headers=h)
    assert resp.status_code == 422 and "name" in resp.get_json()["errors"]
    resp = logged_in.patch(f"/api/v1/items/{item_id}", json={"base": {"height_in": None},
                                                              "changes": {"height_in": "tall"}}, headers=h)
    assert resp.status_code == 422 and "height_in" in resp.get_json()["errors"]
    resp = logged_in.patch("/api/v1/items/999", json={"base": {"name": "a"}, "changes": {"name": "b"}}, headers=h)
    assert resp.status_code == 404
    resp = logged_in.patch(f"/api/v1/items/{item_id}", json={"base": {"catalog_id": None},
                                                              "changes": {"catalog_id": 999}}, headers=h)
    assert resp.status_code == 422


def test_web_edit_bumps_version(logged_in, fake_repo):
    add_piece(logged_in)
    item_id = next(iter(fake_repo.items))
    logged_in.post(f"/items/{item_id}/edit", data={"category": "building", "name": "Renamed",
                                                   "csrf_token": csrf(logged_in)})
    assert item_to_json(fake_repo.items[item_id])["version"] == 2


# -- extras ------------------------------------------------------------------------

def test_extras_create_idempotent_and_patch(logged_in, fake_repo):
    add_piece(logged_in)
    item_id = next(iter(fake_repo.items))
    h = headers(logged_in)
    body = {"client_uuid": new_uuid(), "fields": {"description": "Light cord", "quantity": 1,
                                                   "condition_grade": "good"}}
    first = logged_in.post(f"/api/v1/items/{item_id}/extras", json=body, headers=h)
    assert first.status_code == 201
    extra = first.get_json()["extra"]
    assert logged_in.post(f"/api/v1/items/{item_id}/extras", json=body, headers=h).status_code == 200
    assert len(fake_repo.extras) == 1
    resp = logged_in.patch(f"/api/v1/items/{item_id}/extras/{extra['id']}", json={
        "base": {"quantity": 1}, "changes": {"quantity": 2}}, headers=h)
    assert resp.status_code == 200 and resp.get_json()["extra"]["quantity"] == 2
    resp = logged_in.patch(f"/api/v1/items/{item_id}/extras/{extra['id']}", json={
        "base": {"quantity": 1}, "changes": {"quantity": 5}}, headers=h)
    assert resp.status_code == 409 and resp.get_json()["conflicts"][0]["theirs"] == 2
    assert logged_in.post("/api/v1/items/999/extras", json={"client_uuid": new_uuid(), "fields": {
        "description": "x"}}, headers=h).status_code == 404
    resp = logged_in.post(f"/api/v1/items/{item_id}/extras", json={"client_uuid": new_uuid(), "fields": {
        "description": ""}}, headers=h)
    assert resp.status_code == 422


# -- photos ------------------------------------------------------------------------

def test_photo_upload_idempotent(logged_in, fake_repo, app):
    add_piece(logged_in)
    item_id = next(iter(fake_repo.items))
    h = headers(logged_in)
    cu = new_uuid()
    first = logged_in.post(f"/api/v1/items/{item_id}/photos", data={"client_uuid": cu, "photo": (png_bytes(), "p.jpg")},
                           headers=h, content_type="multipart/form-data")
    assert first.status_code == 201
    retry = logged_in.post(f"/api/v1/items/{item_id}/photos", data={"client_uuid": cu, "photo": (png_bytes(), "p.jpg")},
                           headers=h, content_type="multipart/form-data")
    assert retry.status_code == 200 and retry.get_json()["photo"]["id"] == first.get_json()["photo"]["id"]
    assert len(fake_repo.photos) == 1
    snap = logged_in.get("/api/v1/snapshot").get_json()
    assert snap["items"][0]["photos"] == [{"id": first.get_json()["photo"]["id"], "client_uuid": cu}]
    bad = logged_in.post(f"/api/v1/items/{item_id}/photos", data={"client_uuid": new_uuid(),
                         "photo": (png_bytes().__class__(b"nope"), "x.jpg")}, headers=h,
                         content_type="multipart/form-data")
    assert bad.status_code == 422


def test_api_errors_are_json(logged_in):
    resp = logged_in.patch("/api/v1/items/1", data="x", headers=headers(logged_in))
    assert resp.status_code == 400 and resp.is_json
