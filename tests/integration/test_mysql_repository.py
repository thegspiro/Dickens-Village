"""Integration tests against a real MySQL server.

Run with:  docker compose -f docker-compose.test.yml run --rm tests

They are skipped unless TEST_DB_HOST is set. The database name must end in
"_test" because these tests drop and recreate every table in it.
"""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal
from pathlib import Path

import pytest

pymysql = pytest.importorskip("pymysql")

from app import catalog, migrate  # noqa: E402
from app.config import DatabaseConfig  # noqa: E402
from app.db import connect  # noqa: E402
from app.repository import PAGE_SIZE, MySQLRepository  # noqa: E402
from app.validation import parse_extra_form, parse_item_form, parse_list_filters  # noqa: E402

pytestmark = pytest.mark.integration

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
TABLES = ("item_scans", "item_photos", "item_links", "item_extras", "items", "catalog_photos", "catalog_items")
CATALOG_DIR = Path(__file__).resolve().parents[2] / "data" / "catalog"


def _db_config() -> DatabaseConfig:
    if not os.environ.get("TEST_DB_HOST"):
        pytest.skip("TEST_DB_HOST not set; skipping MySQL integration tests")
    name = os.environ["TEST_DB_NAME"]
    if not name.endswith("_test"):
        pytest.fail("TEST_DB_NAME must end with _test; these tests drop tables")
    return DatabaseConfig(
        host=os.environ["TEST_DB_HOST"],
        port=int(os.environ.get("TEST_DB_PORT", "3306")),
        name=name,
        user=os.environ["TEST_DB_USER"],
        password=os.environ["TEST_DB_PASSWORD"],
        connect_timeout=10,
    )


@pytest.fixture(scope="module")
def db_config() -> DatabaseConfig:
    return _db_config()


@pytest.fixture(scope="module")
def migrated(db_config):
    conn = connect(db_config)
    migrations = migrate.discover(MIGRATIONS)
    try:
        while migrate.migrate_down(conn, migrations):
            pass
        assert migrate.migrate_up(conn, migrations) == [m.version for m in migrations]
        yield
    finally:
        conn.close()


@pytest.fixture
def repo(db_config, migrated):
    conn = connect(db_config)
    r = MySQLRepository(conn)
    yield r
    with conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in TABLES:
            cur.execute(f"TRUNCATE TABLE {table}")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    conn.commit()
    r.close()


def item(**fields):
    form = {"category": "building", "name": "Fezziwig's Warehouse"}
    form.update(fields)
    data, errors = parse_item_form(form)
    assert errors == {}
    return data


def filters(**args):
    return parse_list_filters(args)


def test_migrations_are_idempotent_and_reversible(db_config, migrated):
    conn = connect(db_config)
    migrations = migrate.discover(MIGRATIONS)
    try:
        assert migrate.migrate_up(conn, migrations) == []
        # Re-running the raw up SQL must also be harmless (IF NOT EXISTS).
        for m in migrations:
            migrate._execute_all(conn, migrate.split_statements(m.up_path.read_text()))
        conn.commit()
        assert migrate.migrate_down(conn, migrations) == "0008_catalog_listed"
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM catalog_items LIKE 'listed'")
            assert cur.fetchone() is None
        assert migrate.migrate_down(conn, migrations) == "0007_photo_order_captions"
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM item_photos LIKE 'sort_order'")
            assert cur.fetchone() is None
            cur.execute("SHOW COLUMNS FROM catalog_photos LIKE 'caption'")
            assert cur.fetchone() is None
        assert migrate.migrate_down(conn, migrations) == "0006_item_scans"
        assert migrate.migrate_down(conn, migrations) == "0005_sync"
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM items LIKE 'version'")
            assert cur.fetchone() is None
        assert migrate.migrate_down(conn, migrations) == "0004_measurements"
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM items LIKE 'height_in'")
            assert cur.fetchone() is None
        assert migrate.migrate_down(conn, migrations) == "0003_catalog_photos"
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'catalog_photos'")
            assert cur.fetchone() is None
        assert migrate.migrate_down(conn, migrations) == "0002_catalog"
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM items LIKE 'catalog_id'")
            assert cur.fetchone() is None
            cur.execute("SHOW TABLES LIKE 'catalog_items'")
            assert cur.fetchone() is None
        assert migrate.migrate_down(conn, migrations) == "0001_initial"
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'items'")
            assert cur.fetchone() is None
        assert migrate.migrate_up(conn, migrations) == [
            "0001_initial", "0002_catalog", "0003_catalog_photos", "0004_measurements", "0005_sync",
            "0006_item_scans", "0007_photo_order_captions", "0008_catalog_listed"]
        # The guarded ALTERs in 0002 must be harmless when run a second time.
        for m in migrations:
            migrate._execute_all(conn, migrate.split_statements(m.up_path.read_text()))
        conn.commit()
    finally:
        conn.close()


def test_create_get_update_roundtrip(repo):
    item_id = repo.create_item(item(
        price_paid="45.50", purchase_date="2025-12-01", storage_location="Basement",
        storage_tub="3", storage_tray="Top", condition_grade="good", has_original_box="1",
    ))
    row = repo.get_item(item_id)
    assert row["price_paid"] == Decimal("45.50")
    assert row["purchase_date"] == dt.date(2025, 12, 1)
    assert row["storage_tub"] == "3" and row["has_original_box"] == 1
    assert row["extras_count"] == 0

    assert repo.update_item(item_id, item(name="Renamed", storage_tub="4"))
    row = repo.get_item(item_id)
    assert row["name"] == "Renamed" and row["storage_tub"] == "4" and row["price_paid"] is None
    assert repo.update_item(999999, item()) is False


def test_check_constraint_rejects_bad_years(repo):
    data = item()
    data["year_introduced"], data["year_retired"] = 1995, 1990
    with pytest.raises(pymysql.err.OperationalError):
        repo.create_item(data)
    assert repo.list_items(filters())[1] == 0


def test_search_escapes_like_wildcards(repo):
    repo.create_item(item(name="100% Cotton Tree"))
    repo.create_item(item(name="Plain Tree"))
    rows, total = repo.list_items(filters(q="100%"))
    assert total == 1 and rows[0]["name"] == "100% Cotton Tree"
    assert repo.list_items(filters(q="%"))[1] == 1
    assert repo.list_items(filters(q="_"))[1] == 0


def test_injection_attempt_is_just_text(repo):
    repo.create_item(item())
    rows, total = repo.list_items(filters(q="' OR 1=1 --", storage_tub="x' OR '1'='1"))
    assert total == 0
    assert repo.list_items(filters())[1] == 1


def test_storage_filters_and_summary(repo):
    repo.create_item(item(name="A", storage_location="Attic", storage_tub="1", storage_tray="Top"))
    repo.create_item(item(name="B", storage_location="Attic", storage_tub="1", storage_tray="Top"))
    repo.create_item(item(name="C", storage_location="Attic", storage_tub="2"))
    repo.create_item(item(name="D"))
    assert repo.list_items(filters(storage_tub="1", storage_tray="Top"))[1] == 2
    assert repo.list_items(filters(unstored="yes"))[1] == 1
    assert repo.list_items(filters(storage_location="Attic"))[1] == 3
    assert repo.list_items(filters(storage_location="Attic", storage_tub="2", exact_storage="yes"))[1] == 1
    assert repo.list_items(filters(storage_location="Attic", exact_storage="yes"))[1] == 0
    summary = {(r["storage_location"], r["storage_tub"], r["storage_tray"]): r["item_count"]
               for r in repo.storage_summary()}
    assert summary == {("Attic", "1", "Top"): 2, ("Attic", "2", None): 1, (None, None, None): 1}
    assert repo.distinct_values("storage_tub") == ["1", "2"]
    with pytest.raises(ValueError):
        repo.distinct_values("name; DROP TABLE items")


def test_all_sorts_and_pagination(repo):
    for n in range(PAGE_SIZE + 3):
        repo.create_item(item(name=f"Piece {n:03d}", estimated_value=str(n)))
    for sort in ("name", "category", "year_introduced", "storage", "estimated_value", "updated"):
        rows, total = repo.list_items(filters(sort=sort))
        assert total == PAGE_SIZE + 3 and len(rows) == PAGE_SIZE
    rows, _ = repo.list_items(filters(page="2"))
    assert len(rows) == 3


def test_extras_counts_and_filters(repo):
    a = repo.create_item(item(name="A"))
    repo.create_item(item(name="B"))
    extra, errors = parse_extra_form({"description": "Light cord", "quantity": "2", "condition_grade": "fair"})
    assert errors == {}
    extra_id = repo.add_extra(a, extra)
    assert repo.get_item(a)["extras_count"] == 2
    assert [r["name"] for r in repo.list_items(filters(has_extras="yes"))[0]] == ["A"]
    assert [r["name"] for r in repo.list_items(filters(has_extras="no"))[0]] == ["B"]

    assert repo.update_extra(a, extra_id, extra) is True  # unchanged values still count as found
    assert repo.update_extra(a + 1, extra_id, extra) is False
    assert repo.add_extra(999999, extra) is None
    assert repo.delete_extra(a, extra_id) and not repo.delete_extra(a, extra_id)


def test_links_only_between_building_and_piece(repo):
    building = repo.create_item(item(name="Cottage"))
    figure = repo.create_item(item(category="figure", name="Tiny Tim"))
    other = repo.create_item(item(name="Shop"))
    assert repo.add_link(figure, building)
    assert repo.add_link(building, figure)  # duplicate is ignored, not an error
    assert not repo.add_link(building, other)
    assert not repo.add_link(building, building)
    assert [r["name"] for r in repo.list_links(building)] == ["Tiny Tim"]
    assert [r["name"] for r in repo.list_links(figure)] == ["Cottage"]
    assert [r["name"] for r in repo.link_candidates(repo.get_item(figure))] == ["Shop"]
    assert repo.remove_link(figure, building) and repo.list_links(building) == []


def test_delete_cascades_and_returns_photo_files(repo):
    building = repo.create_item(item())
    figure = repo.create_item(item(category="figure", name="Fig"))
    repo.add_link(building, figure)
    extra, _ = parse_extra_form({"description": "Bulb"})
    repo.add_extra(building, extra)
    photo_id = repo.add_photo(building, "a" * 32 + ".jpg", "front.jpg")
    assert repo.get_photo(photo_id)["item_id"] == building

    assert repo.delete_item(building) == ["a" * 32 + ".jpg"]
    assert repo.get_item(building) is None
    assert repo.list_links(figure) == []
    assert repo.get_photo(photo_id) is None
    assert repo.delete_item(building) is None


def test_collection_summary(repo):
    repo.create_item(item(price_paid="10", estimated_value="25", condition_grade="mint", on_display="1"))
    repo.create_item(item(category="figure", name="Fig", price_paid="5"))
    s = repo.collection_summary()
    assert s["totals"]["item_count"] == 2
    assert s["totals"]["total_paid"] == Decimal("15.00")
    assert int(s["totals"]["on_display"]) == 1
    assert {r["category"] for r in s["by_category"]} == {"building", "figure"}
    assert repo.ping()


# -- catalog ------------------------------------------------------------------------


def test_catalog_sync_is_idempotent_and_updates(db_config, repo):
    rows = catalog.load_directory(CATALOG_DIR)
    conn = connect(db_config)
    try:
        assert catalog.sync(conn, rows) == 760
        assert catalog.sync(conn, rows) == 760
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM catalog_items")
            assert cur.fetchone()["n"] == 760
        changed = [catalog.CatalogRow({**r.values, "name": "Renamed"}) if r.values["source_key"] == "lighted-2025-0002"
                   else r for r in rows]
        catalog.sync(conn, changed)
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM catalog_items WHERE source_key = 'lighted-2025-0002'")
            assert cur.fetchone()["name"] == "Renamed"
        conn.commit()
    finally:
        conn.close()


def test_catalog_search_ranks_item_numbers(db_config, repo):
    conn = connect(db_config)
    try:
        catalog.sync(conn, catalog.load_directory(CATALOG_DIR))
    finally:
        conn.close()
    names = [r["name"] for r in repo.search_catalog("56.65005")]
    assert names[0] == "Christmas Carol Cottage"  # the set itself before its pieces
    assert set(names) == {"Christmas Carol Cottage", "Fezziwig's Warehouse",
                          "Scrooge & Marley Counting House", "The Cottage Of Bob Cratchit & Tiny Tim"}
    for query in ("65005", "5665005", "56-65005"):
        assert "Fezziwig's Warehouse" in [r["name"] for r in repo.search_catalog(query)], query
    piece = next(r for r in repo.search_catalog("65005") if r["name"] == "Fezziwig's Warehouse")
    assert piece["parent_name"] == "Christmas Carol Cottage" and piece["srp_usd"] == Decimal("25.00")
    assert {r["name"] for r in repo.search_catalog("58576")} == {"Christmas Morning Parade", "London Gas Worker"}
    assert repo.search_catalog("prize turkey")[0]["item_number"] == "56.58539"
    assert repo.search_catalog("%%") == []  # LIKE wildcards are matched literally
    assert repo.search_catalog("x") == []
    assert repo.get_catalog_item(piece["id"])["name"] == "Fezziwig's Warehouse"


def test_item_catalog_link_and_on_delete_set_null(db_config, repo):
    conn = connect(db_config)
    try:
        catalog.sync(conn, catalog.load_directory(CATALOG_DIR))
    finally:
        conn.close()
    entry = repo.search_catalog("56.65161")[0]
    data = item(name="Dickens' Village Church")
    data["catalog_id"] = entry["id"]
    item_id = repo.create_item(data)
    assert repo.get_item(item_id)["catalog_id"] == entry["id"]
    bad = item()
    bad["catalog_id"] = 99_999_999
    with pytest.raises(pymysql.err.IntegrityError):
        repo.create_item(bad)
    conn = connect(db_config)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM catalog_items WHERE id = %s", (entry["id"],))
        conn.commit()
    finally:
        conn.close()
    assert repo.get_item(item_id)["catalog_id"] is None


def test_catalog_photos_and_linked_items(db_config, repo):
    conn = connect(db_config)
    try:
        catalog.sync(conn, catalog.load_directory(CATALOG_DIR))
    finally:
        conn.close()
    entry = repo.search_catalog("56.65161")[0]
    assert entry["photo_id"] is None and entry["photo_count"] == 0
    first = repo.add_catalog_photo(entry["id"], "a" * 32 + ".jpg", "front.jpg")
    repo.add_catalog_photo(entry["id"], "b" * 32 + ".jpg", None)
    assert repo.add_catalog_photo(99_999_999, "c" * 32 + ".jpg", None) is None
    found = repo.search_catalog("56.65161")[0]
    assert found["photo_id"] == first and found["photo_count"] == 2
    assert repo.get_catalog_item(entry["id"])["photo_count"] == 2
    assert [p["original_name"] for p in repo.list_catalog_photos(entry["id"])] == ["front.jpg", None]
    assert repo.get_catalog_photo(first)["catalog_id"] == entry["id"]
    assert repo.delete_catalog_photo(first)["filename"] == "a" * 32 + ".jpg"
    assert repo.delete_catalog_photo(first) is None

    data = item(name="My church")
    data["catalog_id"] = entry["id"]
    item_id = repo.create_item(data)
    assert [i["id"] for i in repo.items_for_catalog(entry["id"])] == [item_id]

    conn = connect(db_config)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM catalog_items WHERE id = %s", (entry["id"],))
        conn.commit()
    finally:
        conn.close()
    assert repo.list_catalog_photos(entry["id"]) == []  # ON DELETE CASCADE


def test_measurements_roundtrip_and_check(repo):
    item_id = repo.create_item(item(height_in="7 1/8", box_depth_in="11.75"))
    row = repo.get_item(item_id)
    assert row["height_in"] == Decimal("7.125") and row["box_depth_in"] == Decimal("11.750")
    assert row["width_in"] is None
    bad = item()
    bad["depth_in"] = Decimal("-1")
    with pytest.raises(pymysql.err.OperationalError):
        repo.create_item(bad)


# -- offline sync support ---------------------------------------------------------

UUID_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
UUID_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def test_versions_bump_on_every_update(repo):
    item_id = repo.create_item(item())
    assert repo.get_item(item_id)["version"] == 1
    repo.update_item(item_id, item(name="Renamed"))
    assert repo.get_item(item_id)["version"] == 2
    extra, _ = parse_extra_form({"description": "Bulb"})
    extra_id = repo.add_extra(item_id, extra)
    repo.update_extra(item_id, extra_id, extra)
    assert repo.get_extra(item_id, extra_id)["version"] == 2


def test_update_with_stale_expected_version_writes_nothing(repo):
    from app.repository import VersionConflict

    item_id = repo.create_item(item(name="Original"))
    assert repo.update_item(item_id, item(name="First save"), expected_version=1)
    with pytest.raises(VersionConflict):
        repo.update_item(item_id, item(name="Stale form"), expected_version=1)
    row = repo.get_item(item_id)
    assert (row["name"], row["version"]) == ("First save", 2)
    assert repo.update_item(item_id, item(name="Fresh form"), expected_version=2)
    assert repo.update_item(999999, item(), expected_version=1) is False

    extra, _ = parse_extra_form({"description": "Bulb"})
    extra_id = repo.add_extra(item_id, extra)
    assert repo.update_extra(item_id, extra_id, {**extra, "quantity": 2}, expected_version=1)
    with pytest.raises(VersionConflict):
        repo.update_extra(item_id, extra_id, {**extra, "quantity": 9}, expected_version=1)
    row = repo.get_extra(item_id, extra_id)
    assert (row["quantity"], row["version"]) == (2, 2)
    assert repo.update_extra(item_id + 1, extra_id, extra, expected_version=2) is False


def test_create_from_client_is_idempotent(repo):
    first, created = repo.create_item_from_client(item(name="Carolers"), UUID_A)
    again, created_again = repo.create_item_from_client(item(name="Carolers"), UUID_A)
    assert created and not created_again and first == again
    assert repo.list_items(filters())[1] == 1
    assert repo.get_item_by_client_uuid(UUID_A)["id"] == first


def test_patch_item_paths(repo):
    item_id = repo.create_item(item(storage_tub="1"))
    seen = {}

    def conflict(row):
        seen["tub"] = row["storage_tub"]
        return "conflict", ["details"]

    status, payload, row = repo.patch_item(item_id, conflict)
    assert (status, payload, row["version"], seen["tub"]) == ("conflict", ["details"], 1, "1")

    def ok(row):
        return "ok", item(storage_tub="2")

    status, _, row = repo.patch_item(item_id, ok)
    assert status == "ok" and row["storage_tub"] == "2" and row["version"] == 2
    assert repo.patch_item(999_999, ok)[0] == "missing"


def test_extras_and_photos_from_client(repo):
    item_id = repo.create_item(item())
    extra, _ = parse_extra_form({"description": "Light cord", "quantity": "2"})
    extra_id, created = repo.add_extra_from_client(item_id, extra, UUID_B)
    assert created and repo.add_extra_from_client(item_id, extra, UUID_B) == (extra_id, False)
    assert repo.add_extra_from_client(999_999, extra, "cccccccc-cccc-4ccc-8ccc-cccccccccccc") == (None, False)
    status, _, row = repo.patch_extra(item_id, extra_id, lambda r: ("ok", {**extra, "quantity": 3}))
    assert status == "ok" and row["quantity"] == 3 and row["version"] == 2

    photo_id = repo.add_photo(item_id, "d" * 32 + ".jpg", "x.jpg", UUID_A)
    assert repo.get_photo_by_client_uuid(UUID_A)["id"] == photo_id
    with pytest.raises(pymysql.err.IntegrityError):
        repo.add_photo(item_id, "e" * 32 + ".jpg", "y.jpg", UUID_A)


def test_snapshot_and_etag(repo):
    empty = repo.snapshot_etag()
    item_id = repo.create_item(item())
    after_create = repo.snapshot_etag()
    assert after_create != empty
    repo.update_item(item_id, item(name="Renamed"))
    assert repo.snapshot_etag() != after_create
    data = repo.snapshot()
    assert [i["name"] for i in data["items"]] == ["Renamed"] and data["extras"] == [] and data["photos"] == []
    before_delete = repo.snapshot_etag()
    repo.delete_item(item_id)
    assert repo.snapshot_etag() != before_delete


def test_catalog_all_and_etag(db_config, repo):
    conn = connect(db_config)
    try:
        catalog.sync(conn, catalog.load_directory(CATALOG_DIR))
    finally:
        conn.close()
    rows = repo.catalog_all()
    assert len(rows) == 760 and "photo_id" in rows[0]
    tag = repo.catalog_etag()
    repo.add_catalog_photo(rows[0]["id"], "f" * 32 + ".jpg", None)
    assert repo.catalog_etag() != tag


def test_scans_add_list_delete_and_cascade(repo):
    item_id = repo.create_item(item())
    scan_id = repo.add_scan(item_id, "a" * 32 + ".glb", "mill.glb", 12345)
    assert repo.add_scan(999_999, "b" * 32 + ".glb", None, 1) is None
    (row,) = repo.list_scans(item_id)
    assert row["id"] == scan_id and row["size_bytes"] == 12345 and row["original_name"] == "mill.glb"
    assert repo.get_scan(scan_id)["item_id"] == item_id
    tag = repo.snapshot_etag()
    repo.add_scan(item_id, "c" * 32 + ".gltf", None, 10)
    assert repo.snapshot_etag() != tag
    assert {s["item_id"] for s in repo.snapshot()["scans"]} == {item_id}
    assert repo.delete_scan(scan_id)["filename"] == "a" * 32 + ".glb"
    assert repo.delete_scan(scan_id) is None
    # deleting the piece returns its scan files too, so they can be removed from disk
    assert repo.delete_item(item_id) == ["c" * 32 + ".gltf"]
    assert repo.list_scans(item_id) == []


def test_photo_order_backfill_numbers_old_photos_and_keeps_a_chosen_order(db_config, migrated):
    """0007 numbers existing photos by upload order; re-running it leaves an order chosen since."""
    by_version = {m.version: m for m in migrate.discover(MIGRATIONS)}
    target = by_version["0007_photo_order_captions"]
    conn = connect(db_config)
    try:
        assert migrate.migrate_down(conn, list(by_version.values())) == "0008_catalog_listed"
        assert migrate.migrate_down(conn, list(by_version.values())) == "0007_photo_order_captions"
        repo = MySQLRepository(conn)
        a = repo.create_item(item(name="Backfill A"))
        b = repo.create_item(item(name="Backfill B"))
        with conn.cursor() as cur:
            for item_id, name in ((a, "a1"), (a, "a2"), (a, "a3"), (b, "b1"), (b, "b2")):
                cur.execute("INSERT INTO item_photos (item_id, filename) VALUES (%s, %s)",
                            (item_id, f"{name:0<32}.jpg"))
        conn.commit()
        assert migrate.migrate_up(conn, list(by_version.values())) == ["0007_photo_order_captions",
                                                                       "0008_catalog_listed"]

        def order(item_id):
            with conn.cursor() as cur:
                cur.execute("SELECT filename, sort_order FROM item_photos WHERE item_id = %s ORDER BY id", (item_id,))
                return [(r["filename"][:2], r["sort_order"]) for r in cur.fetchall()]

        assert order(a) == [("a1", 0), ("a2", 1), ("a3", 2)]
        assert order(b) == [("b1", 0), ("b2", 1)]
        with conn.cursor() as cur:  # the person moves a3 to the front
            cur.execute("UPDATE item_photos SET sort_order = CASE LEFT(filename, 2) "
                        "WHEN 'a3' THEN 0 WHEN 'a1' THEN 1 ELSE 2 END WHERE item_id = %s", (a,))
        conn.commit()
        migrate._execute_all(conn, migrate.split_statements(target.up_path.read_text()))
        conn.commit()
        assert order(a) == [("a1", 1), ("a2", 2), ("a3", 0)]
    finally:
        conn.close()


def test_photo_order_captions_cover_and_etags(db_config, repo):
    item_id = repo.create_item(item(name="Gallery piece"))
    a = repo.add_photo(item_id, "1" * 32 + ".jpg", "a.jpg")
    b = repo.add_photo(item_id, "2" * 32 + ".jpg", "b.jpg")
    c = repo.add_photo(item_id, "3" * 32 + ".jpg", "c.jpg")
    assert [(p["id"], p["sort_order"]) for p in repo.list_photos(item_id)] == [(a, 0), (b, 1), (c, 2)]
    listed = {r["id"]: r for r in repo.list_items(filters(q="Gallery piece"))[0]}
    assert listed[item_id]["cover_photo_id"] == a

    etag = repo.snapshot_etag()
    assert repo.reorder_photos(item_id, [c, a, b])
    assert [p["id"] for p in repo.list_photos(item_id)] == [c, a, b]
    assert repo.list_items(filters(q="Gallery piece"))[0][0]["cover_photo_id"] == c
    moved = repo.snapshot_etag()
    assert moved != etag
    assert repo.reorder_photos(item_id, [a, c, b]) and repo.snapshot_etag() != moved  # a swap changes it too

    for bad in ([a, b], [a, b, c, c], [a, b, c, 999_999], []):
        assert repo.reorder_photos(item_id, bad) is False
    assert [p["id"] for p in repo.list_photos(item_id)] == [a, c, b]
    assert repo.reorder_photos(999_999, [a]) is False

    before = repo.snapshot_etag()
    assert repo.set_photo_caption(b, "Chip on the eave")["caption"] == "Chip on the eave"
    assert repo.get_photo(b)["caption"] == "Chip on the eave"
    assert repo.snapshot_etag() != before
    snap = [p for p in repo.snapshot()["photos"] if p["item_id"] == item_id]
    assert [(p["id"], p["caption"]) for p in snap] == [(a, None), (c, None), (b, "Chip on the eave")]
    assert repo.set_photo_caption(b, None)["caption"] is None
    assert repo.set_photo_caption(999_999, "x") is None

    d = repo.add_photo(item_id, "4" * 32 + ".jpg", "d.jpg")  # new photos go last
    assert [p["id"] for p in repo.list_photos(item_id)][-1] == d


def test_catalog_photo_order_moves_the_lookup_thumbnail(db_config, repo):
    conn = connect(db_config)
    try:
        catalog.sync(conn, catalog.load_directory(CATALOG_DIR))
    finally:
        conn.close()
    entry = repo.search_catalog("56.65161")[0]
    a = repo.add_catalog_photo(entry["id"], "5" * 32 + ".jpg", None)
    b = repo.add_catalog_photo(entry["id"], "6" * 32 + ".jpg", None)
    assert repo.search_catalog("56.65161")[0]["photo_id"] == a
    etag = repo.catalog_etag()
    assert repo.reorder_catalog_photos(entry["id"], [b, a])
    assert repo.search_catalog("56.65161")[0]["photo_id"] == b
    assert repo.catalog_etag() != etag
    assert repo.reorder_catalog_photos(entry["id"], [b]) is False
    assert repo.set_catalog_photo_caption(a, "Box art")["caption"] == "Box art"
    assert [p["caption"] for p in repo.list_catalog_photos(entry["id"])] == [None, "Box art"]


def test_upload_filenames_lists_photos_catalog_photos_and_scans(db_config, repo):
    conn = connect(db_config)
    try:
        catalog.sync(conn, catalog.load_directory(CATALOG_DIR))
    finally:
        conn.close()
    item_id = repo.create_item(item(name="Files"))
    repo.add_photo(item_id, "7" * 32 + ".jpg", None)
    repo.add_scan(item_id, "8" * 32 + ".glb", None, 10)
    repo.add_catalog_photo(repo.search_catalog("56.65161")[0]["id"], "9" * 32 + ".jpg", None)
    names = repo.upload_filenames()
    assert {"7" * 32 + ".jpg", "8" * 32 + ".glb", "9" * 32 + ".jpg"} <= names


def test_entries_removed_from_the_csv_are_unlisted_not_deleted(db_config, repo):
    rows = catalog.load_directory(CATALOG_DIR)
    conn = connect(db_config)
    try:
        catalog.sync(conn, rows)
    finally:
        conn.close()
    entry = repo.search_catalog("56.65161")[0]
    key = next(r.values["source_key"] for r in rows if r.values["item_number"] == "56.65161")
    data = item(name="Linked to a withdrawn entry")
    data["catalog_id"] = entry["id"]
    item_id = repo.create_item(data)
    photo = repo.add_catalog_photo(entry["id"], "4" * 32 + ".jpg", None)
    etag = repo.catalog_etag()

    conn = connect(db_config)
    try:
        catalog.sync(conn, [r for r in rows if r.values["source_key"] != key])
    finally:
        conn.close()
    assert [e for e in repo.search_catalog("56.65161") if e["id"] == entry["id"]] == []
    kept = repo.get_catalog_item(entry["id"])
    assert kept is not None and kept["listed"] == 0
    assert repo.get_item(item_id)["catalog_id"] == entry["id"]
    assert repo.get_catalog_photo(photo) is not None
    assert {e["id"]: e for e in repo.catalog_all()}[entry["id"]]["listed"] == 0
    assert repo.catalog_etag() != etag  # phones fetch the catalog again
    with connect(db_config) as check, check.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM catalog_items WHERE listed = 0")
        assert cur.fetchone()["n"] == 1

    conn = connect(db_config)
    try:
        catalog.sync(conn, rows)  # it came back
    finally:
        conn.close()
    assert repo.get_catalog_item(entry["id"])["listed"] == 1
    assert any(e["id"] == entry["id"] for e in repo.search_catalog("56.65161"))


def test_export_data_reads_everything_consistently(db_config, repo):
    building = repo.create_item(item(name="Export building"))
    figure_data = item(name="Export figure")
    figure_data["category"] = "figure"
    figure = repo.create_item(figure_data)
    assert repo.add_link(building, figure)
    extra, _ = parse_extra_form({"description": "Bulb", "quantity": "2"})
    repo.add_extra(building, extra)
    photo = repo.add_photo(building, "e" * 32 + ".jpg", "front.jpg")
    repo.set_photo_caption(photo, "Front")
    repo.add_scan(building, "f" * 32 + ".glb", "scan.glb", 99)
    data = repo.export_data()
    assert {"items", "extras", "links", "photos", "scans", "catalog_photos"} == set(data)
    names = {r["id"]: r["name"] for r in data["items"]}
    assert names[building] == "Export building" and "catalog_item_number" in data["items"][0]
    assert {"building_id": building, "item_id": figure} in data["links"]
    assert any(p["id"] == photo and p["caption"] == "Front" and p["sort_order"] == 0 for p in data["photos"])
    assert any(s["filename"] == "f" * 32 + ".glb" for s in data["scans"])
    assert any(e["item_id"] == building and e["quantity"] == 2 for e in data["extras"])
    # the connection is usable for writes afterwards (the read-only snapshot ended)
    repo.update_item(building, item(name="Renamed after export"))
    assert repo.get_item(building)["name"] == "Renamed after export"


def test_browse_catalog_filters(db_config, repo):
    conn = connect(db_config)
    try:
        catalog.sync(conn, catalog.load_directory(CATALOG_DIR))
    finally:
        conn.close()
    all_rows, total = repo.browse_catalog(limit=1000)
    with_photo = all_rows[0]["id"]
    linked = all_rows[1]["id"]
    repo.add_catalog_photo(with_photo, "a1" * 16 + ".jpg", None)
    data = item(name="Browse linked")
    data["catalog_id"] = linked
    repo.create_item(data)
    repo.create_item(data)

    missing, missing_total = repo.browse_catalog(missing_photos=True, limit=1000)
    assert with_photo not in {r["id"] for r in missing} and linked in {r["id"] for r in missing}
    assert missing_total == total - 1 - sum(1 for r in all_rows if r["photo_id"] and r["id"] != with_photo)
    owned, owned_total = repo.browse_catalog(owned_only=True)
    assert [r["id"] for r in owned] == [linked] and owned_total == 1 and owned[0]["owned_count"] == 2
    both, _ = repo.browse_catalog(missing_photos=True, owned_only=True)
    assert [r["id"] for r in both] == [linked]
    number = all_rows[1]["item_number"]
    found, found_total = repo.browse_catalog(number, owned_only=True)
    assert [r["id"] for r in found] == [linked] and found_total == 1
    page2, page_total = repo.browse_catalog(missing_photos=True, limit=50, offset=50)
    assert len(page2) == 50 and page_total == missing_total
    assert not {r["id"] for r in page2} & {r["id"] for r in repo.browse_catalog(missing_photos=True, limit=50)[0]}
    assert repo.search_catalog(number)  # the lookup still works the same way
