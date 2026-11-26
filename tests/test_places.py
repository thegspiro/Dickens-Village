"""Storage places: the pure tree rules (app/places.py) and the website pages."""

from app.places import MAX_DEPTH, PlaceTree, label, legacy_path
from tests.conftest import add_place, csrf


def rows(*specs):
    """(id, parent_id, name, kind[, is_display]) tuples -> place rows."""
    out = []
    for spec in specs:
        pid, parent, name, kind, *display = spec
        out.append({"id": pid, "parent_id": parent, "name": name, "kind": kind,
                    "is_display": bool(display and display[0])})
    return out


TREE = rows(
    (1, None, "Garage", None),
    (2, 1, "2", "Cabinet"),
    (3, 2, "Drawer 3", "Drawer"),
    (4, 3, "B", "Spot"),
    (5, 1, "10", "Cabinet"),
    (6, None, "Living room", None, True),
    (7, 6, "Mantel", None, True),
)


# -- pure rules --------------------------------------------------------------------

def test_label_puts_the_type_before_a_short_name():
    assert label({"name": "3", "kind": "Tub"}) == "Tub 3"
    assert label({"name": "Tub 3", "kind": "Tub"}) == "Tub 3"
    assert label({"name": "tub 3", "kind": "Tub"}) == "tub 3"
    assert label({"name": "Garage", "kind": None}) == "Garage"
    assert label({"name": "Garage", "kind": "  "}) == "Garage"


def test_paths_and_old_columns():
    tree = PlaceTree(TREE)
    assert tree.path_text(4) == "Garage › Cabinet 2 › Drawer 3 › Spot B"
    assert tree.path_text(4, start=2) == "Drawer 3 › Spot B"
    assert tree.depth(4) == 4 and tree.depth(1) == 1
    # Location / Tub / Tray hold the first three names, not the labels
    assert tree.legacy(4) == ("Garage", "2", "Drawer 3")
    assert tree.legacy(1) == ("Garage", None, None)
    assert tree.legacy(None) == (None, None, None) and tree.legacy(999) == (None, None, None)
    assert tree.path_text(None) == "" and tree.path(999) == []


def test_children_sort_naturally_and_descendants_include_self():
    tree = PlaceTree(TREE)
    assert tree.children[1] == [2, 5]  # Cabinet 2 before Cabinet 10
    assert tree.descendants(1) == [1, 2, 3, 4, 5]
    assert tree.descendants(4) == [4]
    assert [(p["id"], d) for p, d in tree.walk()] == [(1, 0), (2, 1), (3, 2), (4, 3), (5, 1), (6, 0), (7, 1)]


def test_options_separate_storage_and_display_places():
    tree = PlaceTree(TREE)
    assert [i for i, _ in tree.options(False)] == [1, 2, 3, 4, 5]
    assert tree.options(True) == [(6, "Living room"), (7, "Living room › Mantel")]
    assert len(tree.options()) == 7


def test_move_rules():
    tree = PlaceTree(TREE)
    assert tree.move_problem(2, None) is None
    assert tree.move_problem(2, 6) is None
    assert "inside itself" in tree.move_problem(2, 2)
    assert "inside itself" in tree.move_problem(2, 4)
    assert "no longer exists" in tree.move_problem(2, 999)


def test_depth_limit():
    chain = [{"id": n, "parent_id": n - 1 if n > 1 else None, "name": str(n), "kind": None}
             for n in range(1, MAX_DEPTH + 1)]
    chain.append({"id": 100, "parent_id": None, "name": "Box", "kind": None})
    chain.append({"id": 101, "parent_id": 100, "name": "Inner", "kind": None})
    tree = PlaceTree(chain)
    assert tree.move_problem(100, MAX_DEPTH - 2) is None  # Box and Inner fit at the bottom
    assert "at most" in tree.move_problem(100, MAX_DEPTH - 1)


def test_a_cycle_in_bad_data_does_not_hang():
    tree = PlaceTree(rows((1, 2, "A", None), (2, 1, "B", None)))
    assert len(tree.path(1)) == 2
    assert set(tree.descendants(1)) <= {1, 2}


def test_legacy_path_matches_the_migration():
    assert legacy_path("Attic", "3", "Top") == [("Attic", None), ("3", "Tub"), ("Top", "Tray")]
    assert legacy_path(None, "3", None) == [("3", "Tub")]
    assert legacy_path(None, None, None) == []


# -- website -----------------------------------------------------------------------

def add_item(client, **fields):
    data = {"category": "building", "name": "Fezziwig's Warehouse", "csrf_token": csrf(client)}
    data.update(fields)
    resp = client.post("/items/new", data=data)
    assert resp.status_code == 302, resp.get_data(as_text=True)
    return int(resp.headers["Location"].rstrip("/").split("/")[-1])


def post_place(client, **fields):
    return client.post("/places/new", data={"csrf_token": csrf(client), **fields})


def test_add_nested_places_and_see_the_tree(logged_in, fake_repo):
    resp = post_place(logged_in, name="Garage")
    assert resp.status_code == 302
    garage = int(resp.headers["Location"].rsplit("/", 1)[-1])
    form = logged_in.get(f"/places/new?parent={garage}").get_data(as_text=True)
    assert f'<option value="{garage}" selected>Garage</option>' in form
    resp = post_place(logged_in, name="2", kind="Cabinet", parent_id=str(garage))
    cabinet = int(resp.headers["Location"].rsplit("/", 1)[-1])
    assert fake_repo.places[cabinet]["parent_id"] == garage
    add_item(logged_in, storage_place_id=str(cabinet))
    add_item(logged_in, name="Second", storage_place_id=str(cabinet))
    page = logged_in.get("/storage").get_data(as_text=True)
    assert "Garage" in page and "Cabinet 2" in page
    assert page.count("2 pieces") == 2 and "built-in" not in page  # Garage counts what's inside Cabinet 2
    detail = logged_in.get(f"/places/{cabinet}").get_data(as_text=True)
    assert f'href="/places/{garage}">Garage</a>' in detail and "<h1>Cabinet 2" in detail


def test_place_validation(logged_in, fake_repo):
    garage = add_place(fake_repo, "Garage")
    resp = post_place(logged_in, name="")
    assert resp.status_code == 400 and "Name is required." in resp.get_data(as_text=True)
    resp = post_place(logged_in, name="Garage")
    assert resp.status_code == 400 and "already a place with this name" in resp.get_data(as_text=True)
    resp = post_place(logged_in, name="Shelf", parent_id="999")
    assert resp.status_code == 400 and "no longer exists" in resp.get_data(as_text=True)
    assert post_place(logged_in, name="Garage", parent_id=str(garage)).status_code == 302  # same name elsewhere is fine
    assert logged_in.post("/places/new", data={"name": "No token"}).status_code == 400


def test_rename_and_move_update_pieces_and_refuse_loops(logged_in, fake_repo):
    garage = add_place(fake_repo, "Garage")
    attic = add_place(fake_repo, "Attic")
    tub = add_place(fake_repo, "3", parent=garage, kind="Tub")
    tray = add_place(fake_repo, "Top", parent=tub, kind="Tray")
    item_id = add_item(logged_in, storage_place_id=str(tray))
    resp = logged_in.post(f"/places/{tub}/edit", data={"name": "4", "kind": "Tub", "parent_id": str(attic),
                                                      "csrf_token": csrf(logged_in)})
    assert resp.status_code == 302
    item = fake_repo.items[item_id]
    assert (item["storage_location"], item["storage_tub"], item["storage_tray"]) == ("Attic", "4", "Top")
    assert "Attic › Tub 4 › Tray Top" in logged_in.get(f"/items/{item_id}").get_data(as_text=True)
    resp = logged_in.post(f"/places/{tub}/edit", data={"name": "4", "parent_id": str(tray),
                                                      "csrf_token": csrf(logged_in)})
    assert resp.status_code == 400 and "inside itself" in resp.get_data(as_text=True)
    form = logged_in.get(f"/places/{tub}/edit").get_data(as_text=True)
    assert f'<option value="{tray}"' not in form  # not offered as a parent either
    assert logged_in.get("/places/999/edit").status_code == 404


def test_only_empty_places_can_be_deleted(logged_in, fake_repo):
    garage = add_place(fake_repo, "Garage")
    shelf = add_place(fake_repo, "Shelf", parent=garage)
    item_id = add_item(logged_in, storage_place_id=str(shelf))
    resp = logged_in.post(f"/places/{garage}/delete", data={"csrf_token": csrf(logged_in)}, follow_redirects=True)
    assert "Only an empty place can be deleted" in resp.get_data(as_text=True) and garage in fake_repo.places
    page = logged_in.get(f"/places/{shelf}").get_data(as_text=True)
    assert "Delete this place" not in page
    logged_in.post(f"/items/{item_id}/edit", data={"category": "building", "name": "Moved out",
                                                  "csrf_token": csrf(logged_in)})
    assert "Delete this place" in logged_in.get(f"/places/{shelf}").get_data(as_text=True)
    resp = logged_in.post(f"/places/{shelf}/delete", data={"csrf_token": csrf(logged_in)})
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/places/{garage}")
    assert shelf not in fake_repo.places
    assert logged_in.post(f"/places/{shelf}/delete", data={"csrf_token": csrf(logged_in)}).status_code == 404


def test_place_page_lists_what_belongs_there(logged_in, fake_repo):
    garage = add_place(fake_repo, "Garage")
    drawer = add_place(fake_repo, "3", parent=garage, kind="Drawer")
    cords = add_place(fake_repo, "Cords", parent=garage, kind="Bin")
    mantel = add_place(fake_repo, "Mantel", display=True)
    shop = add_item(logged_in, name="Curiosity Shop", storage_place_id=str(drawer),
                    display_place_id=str(mantel), on_display="1")
    add_item(logged_in, name="Bakery", storage_place_id=str(garage))
    logged_in.post(f"/items/{shop}/extras", data={"description": "Light cord", "storage_place_id": str(cords),
                                                 "csrf_token": csrf(logged_in)})
    page = logged_in.get(f"/places/{garage}").get_data(as_text=True)
    assert "Curiosity Shop" in page and "Bakery" in page and "Light cord" in page
    assert "Drawer 3" in page and "On display" in page and "Put away" in page
    display = logged_in.get(f"/places/{mantel}").get_data(as_text=True)
    assert "Displayed here" in display and "Curiosity Shop" in display and "Stored here" not in display
    detail = logged_in.get(f"/items/{shop}").get_data(as_text=True)
    assert "Garage › Bin Cords" in detail and "Display spot" in detail
    assert logged_in.get("/places/999").status_code == 404


def test_storage_and_display_places_cannot_be_swapped(logged_in, fake_repo):
    shelf = add_place(fake_repo, "Shelf")
    mantel = add_place(fake_repo, "Mantel", display=True)
    resp = logged_in.post("/items/new", data={"category": "building", "name": "X", "storage_place_id": str(mantel),
                                              "display_place_id": str(shelf), "csrf_token": csrf(logged_in)})
    body = resp.get_data(as_text=True)
    assert resp.status_code == 400 and "not a display spot" in body and "Choose a display spot." in body
    resp = logged_in.post("/items/new", data={"category": "building", "name": "X", "storage_place_id": "999",
                                              "csrf_token": csrf(logged_in)})
    assert resp.status_code == 400 and "no longer exists" in resp.get_data(as_text=True)
    item_id = add_item(logged_in)
    resp = logged_in.post(f"/items/{item_id}/extras", data={"description": "Cord", "storage_place_id": str(mantel),
                                                           "csrf_token": csrf(logged_in)})
    assert resp.status_code == 400 and fake_repo.extras == {}
    form = logged_in.get("/items/new").get_data(as_text=True)
    assert form.count(f'<option value="{mantel}"') == 1 and form.count(f'<option value="{shelf}"') == 1


def test_extra_edit_keeps_and_changes_its_place(logged_in, fake_repo):
    bin_ = add_place(fake_repo, "Cord bin")
    item_id = add_item(logged_in)
    logged_in.post(f"/items/{item_id}/extras", data={"description": "Cord", "csrf_token": csrf(logged_in)})
    (extra_id,) = fake_repo.extras
    assert "With the piece" in logged_in.get(f"/items/{item_id}").get_data(as_text=True)
    resp = logged_in.post(f"/items/{item_id}/extras/{extra_id}/edit", data={
        "description": "Cord", "storage_place_id": str(bin_), "version": "1", "csrf_token": csrf(logged_in)})
    assert resp.status_code == 302 and fake_repo.extras[extra_id]["storage_place_id"] == bin_
    form = logged_in.get(f"/items/{item_id}/extras/{extra_id}/edit").get_data(as_text=True)
    assert f'<option value="{bin_}" selected>' in form


def test_add_a_piece_here_presets_the_place(logged_in, fake_repo):
    shelf = add_place(fake_repo, "Shelf")
    mantel = add_place(fake_repo, "Mantel", display=True)
    form = logged_in.get(f"/items/new?place={shelf}").get_data(as_text=True)
    assert f'<option value="{shelf}" selected>Shelf</option>' in form
    form = logged_in.get(f"/items/new?display={mantel}").get_data(as_text=True)
    assert f'<option value="{mantel}" selected>Mantel</option>' in form


def test_place_names_are_escaped(logged_in, fake_repo):
    evil = add_place(fake_repo, "<script>alert(1)</script>", kind="<b>")
    add_item(logged_in, storage_place_id=str(evil))
    for path in ("/storage", f"/places/{evil}", "/items", "/items/new"):
        body = logged_in.get(path).get_data(as_text=True)
        assert "<script>alert(1)</script>" not in body and "&lt;script&gt;" in body, path


def test_place_pages_need_login(client):
    for path in ("/storage", "/places/1", "/places/new", "/places/1/edit"):
        assert client.get(path).status_code == 302, path


def test_a_place_in_use_keeps_its_role(logged_in, fake_repo):
    shelf = add_place(fake_repo, "Shelf")
    mantel = add_place(fake_repo, "Mantel", display=True)
    add_item(logged_in, storage_place_id=str(shelf), display_place_id=str(mantel))
    resp = logged_in.post(f"/places/{shelf}/edit", data={"name": "Shelf", "is_display": "1",
                                                        "csrf_token": csrf(logged_in)})
    assert resp.status_code == 400 and "Move them before making this a display spot" in resp.get_data(as_text=True)
    resp = logged_in.post(f"/places/{mantel}/edit", data={"name": "Mantel", "csrf_token": csrf(logged_in)})
    assert resp.status_code == 400 and "Change theirs before" in resp.get_data(as_text=True)
    assert not fake_repo.places[shelf]["is_display"] and fake_repo.places[mantel]["is_display"]
