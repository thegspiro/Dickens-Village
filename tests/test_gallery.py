"""Photo galleries: order (cover first), captions, the move buttons and the list's cover thumbnail."""

from __future__ import annotations

import pytest

from app.validation import parse_caption, parse_id_list
from app.views import photo_moves
from tests.conftest import csrf


@pytest.fixture
def piece(logged_in, fake_repo):
    logged_in.post("/items/new", data={"category": "building", "name": "Mill", "csrf_token": csrf(logged_in)})
    item_id = next(iter(fake_repo.items))
    ids = [fake_repo.add_photo(item_id, f"{n:032x}.jpg", f"p{n}.jpg") for n in range(1, 4)]
    return item_id, ids


@pytest.fixture
def entry(fake_repo):
    catalog_id = fake_repo.add_catalog(name="Fezziwig's Warehouse", item_number="56.65005")
    ids = [fake_repo.add_catalog_photo(catalog_id, f"{n:032x}.jpg", None) for n in range(10, 13)]
    return catalog_id, ids


def order_of(fake_repo, item_id):
    return [p["id"] for p in fake_repo.list_photos(item_id)]


def test_parse_caption():
    assert parse_caption("  Chip on   the\nleft eave ") == ("Chip on the left eave", None)
    assert parse_caption("   ") == (None, None)
    assert parse_caption(None) == (None, None)
    assert parse_caption("x" * 300) == ("x" * 300, None)
    assert parse_caption("x" * 301)[1] == "Captions must be 300 characters or fewer."
    assert parse_caption("a\x00b\x1bc") == ("abc", None)


def test_parse_id_list():
    assert parse_id_list("3,1,2") == [3, 1, 2]
    assert parse_id_list(" 3 , 1 ") == [3, 1]
    for bad in (None, "", "1,,2", "1,1", "1,x", "-1", "0", "1;2"):
        assert parse_id_list(bad) is None, bad


def test_photo_moves_lists_the_new_order_for_each_button():
    photos = [{"id": 5}, {"id": 6}, {"id": 7}]
    assert photo_moves(photos, 0) == {"later": "6,5,7"}
    assert photo_moves(photos, 1) == {"earlier": "6,5,7", "cover": "6,5,7", "later": "5,7,6"}
    assert photo_moves(photos, 2) == {"earlier": "5,7,6", "cover": "7,5,6"}
    assert photo_moves([{"id": 5}], 0) == {}


def test_item_page_shows_photos_in_order_with_cover_and_tools(logged_in, fake_repo, piece):
    item_id, (a, b, c) = piece
    fake_repo.reorder_photos(item_id, [c, a, b])
    body = logged_in.get(f"/items/{item_id}").get_data(as_text=True)
    assert body.index(f"/photos/{c}/thumb") < body.index(f"/photos/{a}/thumb") < body.index(f"/photos/{b}/thumb")
    assert body.count("Cover</span>") == 1 and body.index("Cover</span>") < body.index(f"/photos/{a}/thumb")
    assert f'data-order-url="/items/{item_id}/photos/order"' in body
    assert f'value="{a},{c},{b}"' in body  # photo a's "Make cover" / "Earlier" order
    assert 'type="module" src="/static/gallery.mjs"' in body


def test_reorder_with_the_form_buttons(logged_in, fake_repo, piece):
    item_id, (a, b, c) = piece
    resp = logged_in.post(f"/items/{item_id}/photos/order", data={"order": f"{b},{a},{c}", "csrf_token": csrf(logged_in)},
                          follow_redirects=True)
    assert "Photo order saved." in resp.get_data(as_text=True)
    assert order_of(fake_repo, item_id) == [b, a, c]


@pytest.mark.parametrize("order", ["{a},{b}", "{a},{b},{c},999", "{a},{a},{b}", "nonsense", ""])
def test_reorder_rejects_anything_but_a_permutation(logged_in, fake_repo, piece, order):
    item_id, (a, b, c) = piece
    resp = logged_in.post(f"/items/{item_id}/photos/order", data={
        "order": order.format(a=a, b=b, c=c), "csrf_token": csrf(logged_in)}, follow_redirects=True)
    assert "The photos changed while you were arranging them" in resp.get_data(as_text=True)
    assert order_of(fake_repo, item_id) == [a, b, c]


def test_reorder_does_not_accept_another_pieces_photos(logged_in, fake_repo, piece):
    item_id, (a, b, c) = piece
    logged_in.post("/items/new", data={"category": "figure", "name": "Other", "csrf_token": csrf(logged_in)})
    other = max(fake_repo.items)
    resp = logged_in.post(f"/items/{other}/photos/order", data={"order": f"{a},{b},{c}", "csrf_token": csrf(logged_in)},
                          headers={"Accept": "application/json"})
    assert resp.status_code == 409 and resp.get_json()["error"]
    assert order_of(fake_repo, item_id) == [a, b, c]


def test_reorder_by_script_gets_json(logged_in, fake_repo, piece):
    item_id, (a, b, c) = piece
    resp = logged_in.post(f"/items/{item_id}/photos/order", data={"order": f"{c},{b},{a}", "csrf_token": csrf(logged_in)},
                          headers={"Accept": "application/json"})
    assert resp.status_code == 200 and resp.get_json() == {"ok": True}
    assert order_of(fake_repo, item_id) == [c, b, a]


def test_gallery_writes_need_csrf_and_login(client, logged_in, fake_repo, piece):
    item_id, (a, b, c) = piece
    assert logged_in.post(f"/items/{item_id}/photos/order", data={"order": f"{b},{a},{c}"}).status_code == 400
    assert logged_in.post(f"/photos/{a}/caption", data={"caption": "x"}).status_code == 400
    assert logged_in.post("/items/999/photos/order", data={"order": "1", "csrf_token": csrf(logged_in)}).status_code == 404
    assert logged_in.post("/photos/999/caption", data={"caption": "x", "csrf_token": csrf(logged_in)}).status_code == 404
    assert order_of(fake_repo, item_id) == [a, b, c]


def test_caption_save_clear_and_limit(logged_in, fake_repo, piece):
    item_id, (a, _, _) = piece
    resp = logged_in.post(f"/photos/{a}/caption", data={"caption": " Chip on the  eave ", "csrf_token": csrf(logged_in)},
                          follow_redirects=True)
    assert "Caption saved." in resp.get_data(as_text=True)
    assert fake_repo.photos[a]["caption"] == "Chip on the eave"
    resp = logged_in.post(f"/photos/{a}/caption", data={"caption": "x" * 301, "csrf_token": csrf(logged_in)},
                          follow_redirects=True)
    assert "300 characters or fewer" in resp.get_data(as_text=True)
    assert fake_repo.photos[a]["caption"] == "Chip on the eave"
    logged_in.post(f"/photos/{a}/caption", data={"caption": "", "csrf_token": csrf(logged_in)})
    assert fake_repo.photos[a]["caption"] is None


def test_captions_are_escaped(logged_in, fake_repo, piece):
    item_id, (a, _, _) = piece
    fake_repo.set_photo_caption(a, '<img src=x onerror=alert(1)> "quoted"')
    body = logged_in.get(f"/items/{item_id}").get_data(as_text=True)
    assert "<img src=x onerror" not in body
    assert "&lt;img src=x onerror=alert(1)&gt;" in body
    assert 'data-caption="&lt;img src=x onerror=alert(1)&gt; &#34;quoted&#34;"' in body


def test_collection_list_shows_each_pieces_cover(logged_in, fake_repo, piece):
    item_id, (a, b, c) = piece
    fake_repo.reorder_photos(item_id, [b, a, c])
    body = logged_in.get("/items").get_data(as_text=True)
    assert f'class="cover-thumb" src="/photos/{b}/thumb"' in body


def test_snapshot_lists_photos_in_order_with_captions(logged_in, fake_repo, piece):
    item_id, (a, b, c) = piece
    fake_repo.reorder_photos(item_id, [c, a, b])
    fake_repo.set_photo_caption(a, "Back")
    photos = logged_in.get("/api/v1/snapshot").get_json()["items"][0]["photos"]
    assert [(p["id"], p["caption"]) for p in photos] == [(c, None), (a, "Back"), (b, None)]


def test_catalog_photos_reorder_and_caption(logged_in, fake_repo, entry):
    catalog_id, (a, b, c) = entry
    logged_in.post(f"/catalog/{catalog_id}/photos/order", data={"order": f"{c},{a},{b}", "csrf_token": csrf(logged_in)})
    assert [p["id"] for p in fake_repo.list_catalog_photos(catalog_id)] == [c, a, b]
    assert fake_repo.get_catalog_item(catalog_id)["photo_id"] == c  # the lookup's thumbnail follows the cover
    logged_in.post(f"/catalog-photos/{a}/caption", data={"caption": "Box art", "csrf_token": csrf(logged_in)})
    body = logged_in.get(f"/catalog/{catalog_id}").get_data(as_text=True)
    assert "Box art" in body and f'data-order-url="/catalog/{catalog_id}/photos/order"' in body
    bad = logged_in.post(f"/catalog/{catalog_id}/photos/order", data={"order": f"{a}", "csrf_token": csrf(logged_in)},
                         headers={"Accept": "application/json"})
    assert bad.status_code == 409


def test_linked_catalog_photos_are_shown_without_edit_tools(logged_in, fake_repo, entry):
    catalog_id, (a, _, _) = entry
    logged_in.post("/items/new", data={"category": "building", "name": "Mill", "catalog_id": str(catalog_id),
                                       "csrf_token": csrf(logged_in)})
    item_id = next(iter(fake_repo.items))
    body = logged_in.get(f"/items/{item_id}").get_data(as_text=True)
    assert f"/catalog-photos/{a}/thumb" in body
    assert f"/catalog-photos/{a}/caption" not in body
