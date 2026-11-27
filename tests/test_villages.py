"""Villages: Dickens' Village and the Holy Land Village kept apart (pieces, storage
places, catalog lookups, summary) on the website and in the phone API."""

import uuid

from app.places import PlaceTree
from app.validation import DEFAULT_VILLAGE, VILLAGES, parse_item_form, parse_village
from tests.conftest import add_place, csrf


def switch(client, village, next_path="/items"):
    return client.post("/village", data={"village": village, "next": next_path, "csrf_token": csrf(client)})


def add_piece(client, **fields):
    data = {"category": "building", "name": "Herod's Temple", "csrf_token": csrf(client)}
    data.update(fields)
    return client.post("/items/new", data=data)


def api_headers(client):
    return {"X-CSRF-Token": client.get("/api/v1/session").get_json()["csrf_token"]}


def only_item(fake_repo, name):
    return next(i for i in fake_repo.items.values() if i["name"] == name)


# -- validation and the pure tree rules -------------------------------------------

def test_parse_village_and_item_form_default():
    assert set(VILLAGES) == {"dickens", "holy-land"} and DEFAULT_VILLAGE == "dickens"
    assert parse_village("holy-land") == "holy-land"
    assert parse_village("narnia") is None and parse_village(None) is None
    data, errors = parse_item_form({"category": "building", "name": "Nativity"})
    assert errors == {} and data["village"] == "dickens"
    data, errors = parse_item_form({"category": "building", "name": "Nativity"}, default_village="holy-land")
    assert data["village"] == "holy-land"
    data, errors = parse_item_form({"category": "building", "name": "Nativity", "village": "holy-land"})
    assert data["village"] == "holy-land"
    _, errors = parse_item_form({"category": "building", "name": "Nativity", "village": "narnia"})
    assert "village" in errors


def test_place_tree_villages():
    tree = PlaceTree([
        {"id": 1, "parent_id": None, "name": "Garage", "kind": None, "is_display": False},  # from before villages
        {"id": 2, "parent_id": 1, "name": "1", "kind": "Tub", "is_display": False, "village": "dickens"},
        {"id": 3, "parent_id": None, "name": "Garage", "kind": None, "is_display": False, "village": "holy-land"},
        {"id": 4, "parent_id": 3, "name": "1", "kind": "Tub", "is_display": False, "village": "holy-land"},
    ])
    assert tree.village_of(1) == "dickens" and tree.village_of(4) == "holy-land" and tree.village_of(99) is None
    assert tree.roots("holy-land") == [3] and tree.roots("dickens") == [1] and sorted(tree.roots()) == [1, 3]
    assert [i for i, _ in tree.options(False, "holy-land")] == [3, 4]
    assert [i for i, _ in tree.options(False, "dickens")] == [1, 2]
    assert "same village" in tree.move_problem(2, 3)
    assert tree.move_problem(4, None) is None


# -- switching ----------------------------------------------------------------------

def test_switcher_on_every_page_and_defaults_to_dickens(logged_in):
    html = logged_in.get("/items").get_data(as_text=True)
    assert 'action="/village"' in html
    assert 'value="dickens" class="current" aria-pressed="true"' in html
    assert "Holy Land Village" in html
    assert '<h1>Collection <span class="muted">· Dickens&#39; Village (0 pieces)</span></h1>' in html


def test_switching_lands_in_the_same_section_only(logged_in):
    resp = switch(logged_in, "holy-land", "/storage")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/storage")
    assert 'value="holy-land" class="current"' in logged_in.get("/storage").get_data(as_text=True)
    # a piece or place of the other village, or anything else, goes to the collection list
    for target in ("/items/5", "/places/3", "//evil.example", ""):
        assert switch(logged_in, "dickens", target).headers["Location"].endswith("/items")


def test_switching_rejects_unknown_villages_and_needs_csrf(logged_in):
    assert switch(logged_in, "narnia").status_code == 400
    assert logged_in.post("/village", data={"village": "holy-land"}).status_code == 400


# -- pieces ---------------------------------------------------------------------------

def test_new_pieces_go_in_the_current_village_and_lists_are_separate(logged_in, fake_repo):
    add_piece(logged_in, name="Dickens Church")
    switch(logged_in, "holy-land")
    form = logged_in.get("/items/new").get_data(as_text=True)
    assert '<option value="holy-land" selected>' in form
    add_piece(logged_in, name="Nativity")
    assert only_item(fake_repo, "Nativity")["village"] == "holy-land"
    assert only_item(fake_repo, "Dickens Church")["village"] == "dickens"

    html = logged_in.get("/items").get_data(as_text=True)
    assert "Nativity" in html and "Dickens Church" not in html
    switch(logged_in, "dickens")
    html = logged_in.get("/items").get_data(as_text=True)
    assert "Dickens Church" in html and "Nativity" not in html


def test_form_village_choice_wins_and_detail_page_shows_it(logged_in, fake_repo):
    add_piece(logged_in, name="Tower Of David", village="holy-land")
    piece = only_item(fake_repo, "Tower Of David")
    assert piece["village"] == "holy-land"
    html = logged_in.get(f"/items/{piece['id']}").get_data(as_text=True)
    assert "<dt>Village</dt><dd>Holy Land Village" in html and "(you're viewing Dickens&#39; Village)" in html


def test_editing_without_a_village_field_keeps_the_village(logged_in, fake_repo):
    add_piece(logged_in, name="Potter's Shop", village="holy-land")
    piece = only_item(fake_repo, "Potter's Shop")
    resp = logged_in.post(f"/items/{piece['id']}/edit", data={
        "category": "building", "name": "Potter's Shop", "notes": "Boxed", "csrf_token": csrf(logged_in)})
    assert resp.status_code == 302
    assert fake_repo.items[piece["id"]]["village"] == "holy-land"


def test_places_must_belong_to_the_pieces_village(logged_in, fake_repo):
    dickens_tub = add_place(fake_repo, "Tub 1")
    holy_tub = add_place(fake_repo, "Tub 1", village="holy-land")
    resp = add_piece(logged_in, name="Nativity", village="holy-land", storage_place_id=str(dickens_tub))
    assert resp.status_code == 400
    assert "That place is in Dickens&#39; Village. Choose one in Holy Land Village." in resp.get_data(as_text=True)
    assert not fake_repo.items
    assert add_piece(logged_in, name="Nativity", village="holy-land", storage_place_id=str(holy_tub)).status_code == 302

    # The form offers only the village's own places.
    switch(logged_in, "holy-land")
    form = logged_in.get("/items/new").get_data(as_text=True)
    assert f'<option value="{holy_tub}"' in form and f'<option value="{dickens_tub}"' not in form


def test_moving_a_piece_to_the_other_village_asks_for_new_places(logged_in, fake_repo):
    tub = add_place(fake_repo, "Tub 1")
    add_piece(logged_in, name="Gate House", storage_place_id=str(tub))
    piece = only_item(fake_repo, "Gate House")
    data = {"category": "building", "name": "Gate House", "village": "holy-land", "storage_place_id": str(tub),
            "csrf_token": csrf(logged_in)}
    resp = logged_in.post(f"/items/{piece['id']}/edit", data=data)
    assert resp.status_code == 400 and fake_repo.items[piece["id"]]["village"] == "dickens"
    data["storage_place_id"] = ""
    assert logged_in.post(f"/items/{piece['id']}/edit", data=data).status_code == 302
    assert fake_repo.items[piece["id"]]["village"] == "holy-land"


def test_extras_use_their_pieces_village_places(logged_in, fake_repo):
    dickens_bin = add_place(fake_repo, "Cords")
    add_piece(logged_in, name="Herod's Temple", village="holy-land")
    piece = only_item(fake_repo, "Herod's Temple")
    resp = logged_in.post(f"/items/{piece['id']}/extras", data={
        "description": "High Priests", "storage_place_id": str(dickens_bin), "csrf_token": csrf(logged_in)})
    assert resp.status_code == 400 and not fake_repo.extras


def test_links_stay_within_a_village(logged_in, fake_repo):
    add_piece(logged_in, name="Carpenter's Shop", village="holy-land")
    add_piece(logged_in, name="Carolers", category="figure")
    add_piece(logged_in, name="Carpenter & Son", category="figure", village="holy-land")
    shop = only_item(fake_repo, "Carpenter's Shop")
    html = logged_in.get(f"/items/{shop['id']}").get_data(as_text=True)
    assert "Carpenter &amp; Son" in html and "Carolers" not in html
    carolers = only_item(fake_repo, "Carolers")
    logged_in.post(f"/items/{shop['id']}/links", data={"other_id": str(carolers["id"]), "csrf_token": csrf(logged_in)})
    assert not fake_repo.links


# -- storage, summary ---------------------------------------------------------------

def test_storage_page_and_new_places_are_per_village(logged_in, fake_repo):
    add_place(fake_repo, "Garage")
    switch(logged_in, "holy-land")
    html = logged_in.get("/storage").get_data(as_text=True)
    assert ">Garage</a>" not in html and "No storage places in Holy Land Village yet" in html
    resp = logged_in.post("/places/new", data={"name": "Garage", "csrf_token": csrf(logged_in)})
    assert resp.status_code == 302  # the same name as a Dickens' Village place is fine
    holy_garage = next(p for p in fake_repo.places.values() if p["village"] == "holy-land")
    assert ">Garage</a>" in logged_in.get("/storage").get_data(as_text=True)

    # A place inside another takes its village, whichever village is being viewed.
    switch(logged_in, "dickens")
    logged_in.post("/places/new", data={"name": "1", "kind": "Tub", "parent_id": str(holy_garage["id"]),
                                        "csrf_token": csrf(logged_in)})
    tub = next(p for p in fake_repo.places.values() if p["name"] == "1")
    assert tub["village"] == "holy-land"
    form = logged_in.get(f"/places/new?parent={holy_garage['id']}").get_data(as_text=True)
    assert "Holy Land Village" in form


def test_places_cannot_move_to_the_other_village(logged_in, fake_repo):
    dickens = add_place(fake_repo, "Attic")
    holy = add_place(fake_repo, "Closet", village="holy-land")
    resp = logged_in.post(f"/places/{holy}/edit", data={"name": "Closet", "parent_id": str(dickens),
                                                      "csrf_token": csrf(logged_in)})
    assert resp.status_code == 400 and "same village" in resp.get_data(as_text=True)


def test_summary_counts_one_village(logged_in, fake_repo):
    add_piece(logged_in, name="One")
    add_piece(logged_in, name="Two")
    add_piece(logged_in, name="Three", village="holy-land")
    switch(logged_in, "holy-land")
    html = logged_in.get("/summary").get_data(as_text=True)
    assert "Holy Land Village" in html
    assert '<span class="stat-value">1</span><span class="stat-label">Pieces</span>' in html


# -- catalog --------------------------------------------------------------------------

def seed_catalog(fake_repo):
    return {
        "dickens": fake_repo.add_catalog(list_name="lighted", village="dickens", item_number="56.65005",
                                         name="Christmas Carol Cottage", default_category="building"),
        "holy": fake_repo.add_catalog(list_name="holy-land-lighted", village="holy-land", item_number="56.59757",
                                      name="Little Town Of Bethlehem", default_category="building"),
        "general": fake_repo.add_catalog(list_name="general-accessories", village=None, item_number="56.59960",
                                         name="Village Double Street Lamps", default_category="accessory"),
    }


def test_lookup_offers_the_village_lists_and_the_general_list(logged_in, fake_repo):
    ids = seed_catalog(fake_repo)

    def found(**args):
        return {r["id"] for r in logged_in.get("/catalog/search", query_string={"q": "56.", **args})
                .get_json()["results"]}

    assert found() == {ids["dickens"], ids["general"]}
    assert found(village="holy-land") == {ids["holy"], ids["general"]}  # the form's choice
    switch(logged_in, "holy-land")
    assert found() == {ids["holy"], ids["general"]}
    assert found(village="narnia") == {ids["holy"], ids["general"]}
    result = logged_in.get("/catalog/search?q=59757").get_json()["results"][0]
    assert result["village"] == "holy-land" and result["list_label"] == "Holy Land Lighted"


def test_catalog_page_shows_the_current_village(logged_in, fake_repo):
    seed_catalog(fake_repo)
    switch(logged_in, "holy-land")
    html = logged_in.get("/catalog?q=56.").get_data(as_text=True)
    assert "Little Town Of Bethlehem" in html and "Village Double Street Lamps" in html
    assert "Christmas Carol Cottage" not in html


def test_catalog_link_must_suit_the_village(logged_in, fake_repo):
    ids = seed_catalog(fake_repo)
    resp = add_piece(logged_in, name="Bethlehem", village="dickens", catalog_id=str(ids["holy"]))
    assert resp.status_code == 400 and "Holy Land Village lists" in resp.get_data(as_text=True)
    assert add_piece(logged_in, name="Lamps", catalog_id=str(ids["general"])).status_code == 302
    assert add_piece(logged_in, name="Bethlehem", village="holy-land",
                     catalog_id=str(ids["holy"])).status_code == 302


def test_add_from_catalog_entry_uses_its_village(logged_in, fake_repo):
    ids = seed_catalog(fake_repo)
    form = logged_in.get(f"/items/new?catalog_id={ids['holy']}").get_data(as_text=True)
    assert '<option value="holy-land" selected>' in form


# -- phone API -------------------------------------------------------------------------

def test_api_session_lists_villages(logged_in):
    choices = logged_in.get("/api/v1/session").get_json()["choices"]
    assert choices["villages"] == VILLAGES


def test_api_create_defaults_to_dickens_for_older_phones(logged_in, fake_repo):
    headers = api_headers(logged_in)
    resp = logged_in.post("/api/v1/items", headers=headers, json={
        "client_uuid": str(uuid.uuid4()), "fields": {"category": "building", "name": "Old phone piece"}})
    assert resp.status_code == 201 and resp.get_json()["item"]["village"] == "dickens"
    resp = logged_in.post("/api/v1/items", headers=headers, json={
        "client_uuid": str(uuid.uuid4()),
        "fields": {"category": "building", "name": "New phone piece", "village": "holy-land"}})
    assert resp.status_code == 201 and resp.get_json()["item"]["village"] == "holy-land"


def test_api_create_rejects_a_place_of_another_village(logged_in, fake_repo):
    tub = add_place(fake_repo, "Tub 1")
    resp = logged_in.post("/api/v1/items", headers=api_headers(logged_in), json={
        "client_uuid": str(uuid.uuid4()),
        "fields": {"category": "building", "name": "Nativity", "village": "holy-land", "storage_place_id": tub}})
    assert resp.status_code == 422 and "storage_place_id" in resp.get_json()["errors"]


def test_api_patch_checks_places_against_the_pieces_village(logged_in, fake_repo):
    dickens_tub = add_place(fake_repo, "Tub 1")
    holy_tub = add_place(fake_repo, "Tub 1", village="holy-land")
    add_piece(logged_in, name="Nativity", village="holy-land")
    piece = only_item(fake_repo, "Nativity")
    headers = api_headers(logged_in)
    url = f"/api/v1/items/{piece['id']}"

    resp = logged_in.patch(url, headers=headers, json={"base": {"storage_place_id": None},
                                                      "changes": {"storage_place_id": dickens_tub}})
    assert resp.status_code == 422 and "Holy Land Village" in resp.get_json()["errors"]["storage_place_id"]
    resp = logged_in.patch(url, headers=headers, json={"base": {"storage_place_id": None},
                                                      "changes": {"storage_place_id": holy_tub}})
    assert resp.status_code == 200 and resp.get_json()["item"]["storage_place_id"] == holy_tub

    # Moving it to Dickens' Village while it's in a Holy Land tub is refused.
    resp = logged_in.patch(url, headers=headers, json={"base": {"village": "holy-land"},
                                                      "changes": {"village": "dickens"}})
    assert resp.status_code == 422 and fake_repo.items[piece["id"]]["village"] == "holy-land"


def test_api_patch_checks_the_catalog_link_against_the_village(logged_in, fake_repo):
    ids = seed_catalog(fake_repo)
    add_piece(logged_in, name="Bethlehem", village="holy-land", catalog_id=str(ids["holy"]))
    piece = only_item(fake_repo, "Bethlehem")
    resp = logged_in.patch(f"/api/v1/items/{piece['id']}", headers=api_headers(logged_in),
                           json={"base": {"village": "holy-land"}, "changes": {"village": "dickens"}})
    assert resp.status_code == 422 and "catalog_id" in resp.get_json()["errors"]
    resp = logged_in.patch(f"/api/v1/items/{piece['id']}", headers=api_headers(logged_in),
                           json={"base": {"catalog_id": ids["holy"]}, "changes": {"catalog_id": ids["dickens"]}})
    assert resp.status_code == 422 and "catalog_id" in resp.get_json()["errors"]


def test_api_extras_check_their_pieces_village(logged_in, fake_repo):
    dickens_bin = add_place(fake_repo, "Cords")
    add_piece(logged_in, name="Herod's Temple", village="holy-land")
    piece = only_item(fake_repo, "Herod's Temple")
    resp = logged_in.post(f"/api/v1/items/{piece['id']}/extras", headers=api_headers(logged_in), json={
        "client_uuid": str(uuid.uuid4()), "fields": {"description": "High Priests", "storage_place_id": dickens_bin}})
    assert resp.status_code == 422 and "storage_place_id" in resp.get_json()["errors"]


def test_snapshot_and_export_carry_villages(logged_in, fake_repo):
    add_place(fake_repo, "Closet", village="holy-land")
    add_piece(logged_in, name="Nativity", village="holy-land")
    snap = logged_in.get("/api/v1/snapshot").get_json()
    assert snap["items"][0]["village"] == "holy-land" and snap["places"][0]["village"] == "holy-land"
    doc = logged_in.get("/export/collection.json").get_json()
    assert doc["pieces"][0]["village"] == "holy-land" and doc["places"][0]["village"] == "holy-land"
    header = logged_in.get("/export/pieces.csv").get_data(as_text=True).lstrip("\ufeff").splitlines()[0]
    assert header.split(",")[:3] == ["id", "category", "village"]
