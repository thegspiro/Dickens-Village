"""Catalog data, importer validation, search endpoint and form autofill."""

from decimal import Decimal
from pathlib import Path

import pytest

from app import catalog
from tests.conftest import csrf

DATA = Path(__file__).resolve().parent.parent / "data" / "catalog"


# -- committed data -------------------------------------------------------------

def test_committed_catalog_is_valid():
    rows = catalog.load_directory(DATA)
    assert len(rows) == 760
    lists = {r.values["list_name"] for r in rows}
    assert lists == {"lighted", "accessories"}


def test_committed_catalog_spot_checks():
    rows = {r.values["source_key"]: r.values for r in catalog.load_directory(DATA)}
    by_number = {}
    for v in rows.values():
        by_number.setdefault(v["item_number"], []).append(v)

    church = by_number["56.65161"][0]
    assert church["name"] == "Dickens' Village Church"
    assert (church["year_issued"], church["year_retired"]) == (1985, 1989)
    assert church["srp_usd"] == Decimal("35.00") and church["srp_cad"] is None
    assert church["default_category"] == "building"

    ccc = [v for v in by_number["56.65005"]]
    assert {v["name"] for v in ccc} == {
        "Christmas Carol Cottage", "Fezziwig's Warehouse", "Scrooge & Marley Counting House",
        "The Cottage Of Bob Cratchit & Tiny Tim",
    }
    fezziwig = next(v for v in ccc if v["name"] == "Fezziwig's Warehouse")
    assert fezziwig["parent_key"] and rows[fezziwig["parent_key"]]["name"] == "Christmas Carol Cottage"
    assert fezziwig["srp_usd"] == Decimal("25.00")

    carolers = by_number["56.65269"][0]
    assert carolers["name"] == "Carolers" and carolers["default_category"] == "accessory"
    assert carolers["details"] == "set of 3"

    current = by_number["6015650"][0]
    assert current["name"] == "Lord Lilliput's Cottage"
    assert current["is_current"] == 1 and current["year_retired"] is None
    assert current["srp_cad"] == Decimal("242.50")

    turkey = by_number["56.58539"][0]
    assert turkey["series"] == "A Christmas Carol"


def test_source_typo_years_are_blank_not_guessed():
    rows = [r.values for r in catalog.load_directory(DATA)]
    cheese = next(v for v in rows if v["item_number"] == "4050930")
    assert cheese["year_retired"] is None and cheese["is_current"] == 0
    issues = (DATA / "dickens_village_2025_source_issues.txt").read_text()
    assert "4050930" in issues and "2917" in issues


# -- importer validation --------------------------------------------------------

HEADER = ("source_key,list,item_number,name,details,series,year_issued,year_retired,is_current,"
          "srp_usd,srp_cad,default_category,parent_key,source_page,source_text\n")


def write_csv(tmp_path, *lines):
    path = tmp_path / "test.csv"
    path.write_text(HEADER + "".join(line + "\n" for line in lines), encoding="utf-8")
    return path


GOOD = "k-1,lighted,56.1,House,,,1990,1995,0,10.00,,building,,1,House"


def test_parse_good_row(tmp_path):
    (row,) = catalog.parse_rows(write_csv(tmp_path, GOOD))
    assert row.values["srp_usd"] == Decimal("10.00") and row.values["srp_cad"] is None
    assert row.values["details"] is None and row.values["parent_key"] is None


@pytest.mark.parametrize("bad, message", [
    ("k-1,lighted,56.1,,,,1990,1995,0,10.00,,building,,1,x", "name is required"),
    ("k-1,lighted,56.1,House,,,1990,1985,0,10.00,,building,,1,x", "before"),
    ("k-1,lighted,56.1,House,,,19x0,,0,10.00,,building,,1,x", "year_issued"),
    ("k-1,lighted,56.1,House,,,1990,,0,-1,,building,,1,x", "srp_usd"),
    ("k-1,lighted,56.1,House,,,1990,,0,abc,,building,,1,x", "srp_usd"),
    ("k-1,lighted,56.1,House,,,1990,,2,10,,building,,1,x", "is_current"),
    ("k-1,lighted,56.1,House,,,1990,,0,10,,castle,,1,x", "default_category"),
    ("K 1;DROP,lighted,56.1,House,,,1990,,0,10,,building,,1,x", "source_key"),
    ("k-1,lighted,56.1,House,,,1990,,0,10,,building,nope,1,x", "parent_key"),
])
def test_parse_rejects_bad_rows(tmp_path, bad, message):
    with pytest.raises(catalog.CatalogError, match=message):
        catalog.parse_rows(write_csv(tmp_path, bad))


def test_parse_rejects_duplicate_keys_and_missing_columns(tmp_path):
    with pytest.raises(catalog.CatalogError, match="duplicate"):
        catalog.parse_rows(write_csv(tmp_path, GOOD, GOOD))
    path = tmp_path / "short.csv"
    path.write_text("source_key,name\nk-1,House\n")
    with pytest.raises(catalog.CatalogError, match="missing columns"):
        catalog.parse_rows(path)


def test_check_command(tmp_path, capsys):
    assert catalog.main(["check", str(DATA)]) == 0
    assert "760 catalog rows are valid" in capsys.readouterr().out
    write_csv(tmp_path, GOOD.replace("House", ""))
    assert catalog.main(["check", str(tmp_path)]) == 1


class LockConn:
    """Minimal fake connection recording executemany batches."""

    def __init__(self, lock_ok=True):
        self.lock_ok, self.batches, self.committed, self.rolled_back = lock_ok, [], False, False
        self.statements = []

    def cursor(self):
        conn = self

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            rowcount = 0

            def execute(self, sql, params=None):
                self.last = sql
                conn.statements.append(sql)

            def fetchone(self):
                return {"got": 1 if conn.lock_ok else 0, "released": 1}

            def executemany(self, sql, rows):
                assert "ON DUPLICATE KEY UPDATE" in sql and "source_key = new.source_key" not in sql
                conn.batches.append(len(rows))

        return Cur()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def test_sync_batches_and_commits():
    rows = catalog.load_directory(DATA)
    conn = LockConn()
    assert catalog.sync(conn, rows, batch_size=300) == 760
    assert conn.batches == [300, 300, 160] and conn.committed
    unlist = [s for s in conn.statements if s.startswith("UPDATE catalog_items SET listed = 0")]
    assert len(unlist) == 1 and unlist[0].count("%s") == 760


def test_sync_requires_lock():
    with pytest.raises(catalog.CatalogError, match="lock"):
        catalog.sync(LockConn(lock_ok=False), catalog.load_directory(DATA)[:1])


def test_sync_refuses_to_import_nothing():
    conn = LockConn()
    with pytest.raises(catalog.CatalogError, match="No catalog rows"):
        catalog.sync(conn, [])
    assert conn.statements == [] and not conn.committed


# -- search endpoint ------------------------------------------------------------

@pytest.fixture
def seeded(fake_repo):
    ids = {
        "set": fake_repo.add_catalog(item_number="56.65005", name="Christmas Carol Cottage", details="Set of 3",
                                     year_issued=1986, year_retired=1995, srp_usd=Decimal("75.00"),
                                     default_category="building"),
        "piece": fake_repo.add_catalog(item_number="56.65005", name="Fezziwig's Warehouse",
                                       details='Part of "Christmas Carol Cottage"', year_issued=1986,
                                       year_retired=1995, srp_usd=Decimal("25.00"), default_category="building",
                                       parent_name="Christmas Carol Cottage", parent_item_number="56.65005"),
        "acc": fake_repo.add_catalog(list_name="accessories", item_number="56.58539", name="The Big Prize Turkey",
                                     series="A Christmas Carol", year_issued=2001, year_retired=2008,
                                     srp_usd=Decimal("27.50"), srp_cad=Decimal("44.00"), default_category="accessory"),
        "current": fake_repo.add_catalog(item_number="6015650", name="Lord Lilliput's Cottage", year_issued=2025,
                                         is_current=1, srp_usd=Decimal("180.00"), default_category="building"),
    }
    return ids


def test_search_requires_login(client):
    assert client.get("/catalog/search?q=56").status_code == 302


def test_search_by_item_number_variants(logged_in, seeded):
    for q in ("56.65005", "65005", "5665005", "56-65005"):
        data = logged_in.get(f"/catalog/search?q={q}").get_json()
        names = [r["name"] for r in data["results"]]
        assert "Fezziwig's Warehouse" in names and "Christmas Carol Cottage" in names, q


def test_search_result_shape(logged_in, seeded):
    resp = logged_in.get("/catalog/search?q=58539")
    assert resp.headers["Cache-Control"] == "private, no-store"
    (r,) = resp.get_json()["results"]
    assert r == {
        "id": seeded["acc"], "list": "accessories", "item_number": "56.58539", "name": "The Big Prize Turkey",
        "details": None, "series": "A Christmas Carol", "year_issued": 2001, "year_retired": 2008,
        "is_current": False, "listed": True, "srp_usd": "27.50", "srp_cad": "44.00", "category": "accessory", "parent_name": None,
        "url": f"/catalog/{seeded['acc']}", "photo_count": 0, "thumb_url": None,
    }


def test_search_by_name_and_short_query(logged_in, seeded):
    assert [r["name"] for r in logged_in.get("/catalog/search?q=turkey").get_json()["results"]] == ["The Big Prize Turkey"]
    assert logged_in.get("/catalog/search?q=5").get_json() == {"results": []}
    assert logged_in.get("/catalog/search").get_json() == {"results": []}


# -- form and detail page -------------------------------------------------------

def test_form_includes_lookup_and_script(logged_in):
    body = logged_in.get("/items/new").get_data(as_text=True)
    assert 'id="catalog-lookup"' in body and "catalog-lookup.js" in body
    assert 'data-search-url="/catalog/search"' in body
    assert "<script>" not in body  # no inline script; CSP allows only files from the app


def test_create_item_with_catalog_link_shows_retail(logged_in, fake_repo, seeded):
    resp = logged_in.post("/items/new", data={
        "category": "building", "name": "Fezziwig's Warehouse", "item_number": "56.65005",
        "catalog_id": str(seeded["piece"]), "csrf_token": csrf(logged_in)})
    assert resp.status_code == 302
    (item,) = fake_repo.items.values()
    assert item["catalog_id"] == seeded["piece"]
    body = logged_in.get(f"/items/{item['id']}").get_data(as_text=True)
    assert "Catalog listing" in body and "$25.00 US" in body
    assert "part of “Christmas Carol Cottage”" in body


def test_catalog_link_must_exist(logged_in, fake_repo):
    resp = logged_in.post("/items/new", data={
        "category": "building", "name": "X", "catalog_id": "999", "csrf_token": csrf(logged_in)})
    assert resp.status_code == 400 and "no longer exists" in resp.get_data(as_text=True)
    resp = logged_in.post("/items/new", data={
        "category": "building", "name": "X", "catalog_id": "abc", "csrf_token": csrf(logged_in)})
    assert resp.status_code == 400 and fake_repo.items == {}


def test_edit_shows_link_and_can_unlink(logged_in, fake_repo, seeded):
    logged_in.post("/items/new", data={"category": "building", "name": "Cottage",
                                       "catalog_id": str(seeded["current"]), "csrf_token": csrf(logged_in)})
    (item_id,) = fake_repo.items
    body = logged_in.get(f"/items/{item_id}/edit").get_data(as_text=True)
    assert "Linked to catalog" in body and "Lord Lilliput" in body
    assert f'name="catalog_id" value="{seeded["current"]}"' in body
    logged_in.post(f"/items/{item_id}/edit", data={
        "category": "building", "name": "Cottage", "catalog_id": str(seeded["current"]),
        "catalog_unlink": "1", "csrf_token": csrf(logged_in)})
    assert fake_repo.items[item_id]["catalog_id"] is None


def test_unlisted_entries_are_hidden_from_search_but_kept(logged_in, fake_repo):
    from tests.conftest import csrf

    gone = fake_repo.add_catalog(item_number="56.99999", name="Withdrawn Cottage", listed=0)
    fake_repo.add_catalog(item_number="56.99998", name="Withdrawn Cottage Two")
    hits = logged_in.get("/catalog/search?q=Withdrawn").get_json()["results"]
    assert [h["name"] for h in hits] == ["Withdrawn Cottage Two"] and hits[0]["listed"] is True
    page = logged_in.get(f"/catalog/{gone}").get_data(as_text=True)
    assert "no longer in the reference catalog lists" in page
    logged_in.post("/items/new", data={"category": "building", "name": "Mine", "catalog_id": str(gone),
                                       "csrf_token": csrf(logged_in)})
    assert next(iter(fake_repo.items.values()))["catalog_id"] == gone  # linking still works
