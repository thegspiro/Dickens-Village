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
TABLES = ("item_scans", "item_photos", "item_links", "item_extras", "items", "catalog_photos", "catalog_items",
          "storage_places", "site_settings")
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
    _truncate_all(conn)
    r.close()


def _truncate_all(conn):
    with conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in TABLES:
            cur.execute(f"TRUNCATE TABLE {table}")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    conn.commit()


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
        assert migrate.migrate_down(conn, migrations) == "0011_currency"
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'site_settings'")
            assert cur.fetchone() is None
            cur.execute("SHOW COLUMNS FROM items LIKE 'currency'")
            assert cur.fetchone() is None
        assert migrate.migrate_down(conn, migrations) == "0010_villages"
        with conn.cursor() as cur:
            for table in ("items", "storage_places", "catalog_items"):
                cur.execute(f"SHOW COLUMNS FROM {table} LIKE 'village'")
                assert cur.fetchone() is None, table
            cur.execute("SHOW INDEX FROM storage_places WHERE Key_name = 'uq_storage_places_parent_name'")
            assert cur.fetchall()
        assert migrate.migrate_down(conn, migrations) == "0009_storage_places"
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'storage_places'")
            assert cur.fetchone() is None
            cur.execute("SHOW COLUMNS FROM items LIKE 'storage_place_id'")
            assert cur.fetchone() is None
            cur.execute("SHOW COLUMNS FROM item_extras LIKE 'storage_place_id'")
            assert cur.fetchone() is None
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
            "0006_item_scans", "0007_photo_order_captions", "0008_catalog_listed", "0009_storage_places",
            "0010_villages", "0011_currency"]
        # The guarded ALTERs in 0002 must be harmless when run a second time.
        for m in migrations:
            migrate._execute_all(conn, migrate.split_statements(m.up_path.read_text()))
        conn.commit()
    finally:
        conn.close()


def test_create_get_update_roundtrip(repo):
    basement = _place(repo, "Basement")
    tub3 = _place(repo, "3", basement, "Tub")
    tray = _place(repo, "Top", tub3, "Tray")
    tub4 = _place(repo, "4", basement, "Tub")
    item_id = repo.create_item(item(
        price_paid="45.50", purchase_date="2025-12-01", storage_place_id=str(tray),
        condition_grade="good", has_original_box="1",
    ))
    row = repo.get_item(item_id)
    assert row["price_paid"] == Decimal("45.50")
    assert row["purchase_date"] == dt.date(2025, 12, 1)
    assert row["storage_place_id"] == tray and row["has_original_box"] == 1
    assert (row["storage_location"], row["storage_tub"], row["storage_tray"]) == ("Basement", "3", "Top")
    assert row["extras_count"] == 0

    assert repo.update_item(item_id, item(name="Renamed", storage_place_id=str(tub4)))
    row = repo.get_item(item_id)
    assert row["name"] == "Renamed" and row["price_paid"] is None
    assert (row["storage_location"], row["storage_tub"], row["storage_tray"]) == ("Basement", "4", None)
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


def test_storage_filters_and_counts(repo):
    attic = _place(repo, "Attic")
    tub1 = _place(repo, "1", attic, "Tub")
    top = _place(repo, "Top", tub1, "Tray")
    tub2 = _place(repo, "2", attic, "Tub")
    repo.create_item(item(name="A", storage_place_id=str(top)))
    repo.create_item(item(name="B", storage_place_id=str(top)))
    repo.create_item(item(name="C", storage_place_id=str(tub2)))
    repo.create_item(item(name="D"))
    # the old Location / Tub / Tray filters still work on the columns kept in step
    assert repo.list_items(filters(storage_tub="1", storage_tray="Top"))[1] == 2
    assert repo.list_items(filters(unstored="yes"))[1] == 1
    assert repo.list_items(filters(storage_location="Attic"))[1] == 3
    assert repo.list_items(filters(storage_location="Attic", storage_tub="2", exact_storage="yes"))[1] == 1
    assert repo.list_items(filters(storage_location="Attic", exact_storage="yes"))[1] == 0
    assert repo.list_items(filters(place=str(attic)))[1] == 3
    counts = {p["id"]: p["items"] for p in repo.list_places()}
    assert counts == {attic: 0, tub1: 0, top: 2, tub2: 1}
    assert repo.count_unplaced() == 1
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
        assert catalog.sync(conn, rows) == len(rows) == 1993
        assert catalog.sync(conn, rows) == 1993
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM catalog_items")
            assert cur.fetchone()["n"] == 1993
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
    tub1, tub2 = _place(repo, "1", None, "Tub"), _place(repo, "2", None, "Tub")
    item_id = repo.create_item(item(storage_place_id=str(tub1)))
    seen = {}

    def conflict(row):
        seen["tub"] = row["storage_location"]
        return "conflict", ["details"]

    status, payload, row = repo.patch_item(item_id, conflict)
    assert (status, payload, row["version"], seen["tub"]) == ("conflict", ["details"], 1, "1")

    def ok(row):
        return "ok", item(storage_place_id=str(tub2))

    status, _, row = repo.patch_item(item_id, ok)
    assert status == "ok" and row["storage_place_id"] == tub2 and row["storage_location"] == "2"
    assert row["version"] == 2
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
    assert len(rows) == 1993 and "photo_id" in rows[0]
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
        assert migrate.migrate_down(conn, list(by_version.values())) == "0011_currency"
        assert migrate.migrate_down(conn, list(by_version.values())) == "0010_villages"
        assert migrate.migrate_down(conn, list(by_version.values())) == "0009_storage_places"
        assert migrate.migrate_down(conn, list(by_version.values())) == "0008_catalog_listed"
        assert migrate.migrate_down(conn, list(by_version.values())) == "0007_photo_order_captions"
        # Plain SQL: today's repository expects the newer tables.
        with conn.cursor() as cur:
            cur.execute("INSERT INTO items (category, name) VALUES ('building', 'Backfill A')")
            a = cur.lastrowid
            cur.execute("INSERT INTO items (category, name) VALUES ('building', 'Backfill B')")
            b = cur.lastrowid
            for item_id, name in ((a, "a1"), (a, "a2"), (a, "a3"), (b, "b1"), (b, "b2")):
                cur.execute("INSERT INTO item_photos (item_id, filename) VALUES (%s, %s)",
                            (item_id, f"{name:0<32}.jpg"))
        conn.commit()
        assert migrate.migrate_up(conn, list(by_version.values())) == ["0007_photo_order_captions",
                                                                       "0008_catalog_listed", "0009_storage_places",
                                                                       "0010_villages", "0011_currency"]

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
    assert {"items", "extras", "links", "photos", "scans", "catalog_photos", "places"} == set(data)
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


def test_add_catalog_photo_with_a_caption(db_config, repo):
    conn = connect(db_config)
    try:
        catalog.sync(conn, catalog.load_directory(CATALOG_DIR))
    finally:
        conn.close()
    entry = repo.search_catalog("56.65161")[0]
    with_caption = repo.add_catalog_photo(entry["id"], "c1" * 16 + ".jpg", "a.jpg", "Box art")
    without = repo.add_catalog_photo(entry["id"], "c2" * 16 + ".jpg", None)
    assert repo.get_catalog_photo(with_caption)["caption"] == "Box art"
    assert repo.get_catalog_photo(without)["caption"] is None


def test_catalog_for_matching(db_config, repo):
    from app import catalog_match

    conn = connect(db_config)
    try:
        catalog.sync(conn, catalog.load_directory(CATALOG_DIR))
    finally:
        conn.close()
    rows = repo.catalog_for_matching()
    assert rows and all(r["item_number"] for r in rows)
    index = catalog_match.index_entries(rows)
    assert [e["item_number"] for e in catalog_match.match("65161-2.jpg", index)[1]] == ["56.65161"]


# -- storage places (0009) -----------------------------------------------------------


def _legacy_items(conn, rows):
    """Insert pieces the way the app stored them before 0009 (Location / Tub / Tray text)."""
    ids = {}
    with conn.cursor() as cur:
        for name, loc, tub, tray in rows:
            cur.execute("INSERT INTO items (category, name, storage_location, storage_tub, storage_tray) "
                        "VALUES ('building', %s, %s, %s, %s)", (name, loc, tub, tray))
            ids[name] = cur.lastrowid
    conn.commit()
    return ids


def test_storage_places_migration_turns_old_values_into_places(db_config, migrated):
    migrations = migrate.discover(MIGRATIONS)
    by_version = {m.version: m for m in migrations}
    conn = connect(db_config)
    try:
        assert migrate.migrate_down(conn, migrations) == "0011_currency"
        assert migrate.migrate_down(conn, migrations) == "0010_villages"
        assert migrate.migrate_down(conn, migrations) == "0009_storage_places"
        ids = _legacy_items(conn, [
            ("full", "Attic", "3", "Top"),
            ("full twin", "attic", "3", "Top"),     # different case: same places
            ("tub only", "Attic", "3", None),
            ("tray no tub", "Attic", None, "B"),
            ("no location", None, "9", "A"),
            ("tray alone", None, None, "Z"),
            ("location only", "Basement", None, None),
            ("nowhere", None, None, None),
        ])
        assert migrate.migrate_up(conn, migrations) == ["0009_storage_places", "0010_villages", "0011_currency"]
        repo = MySQLRepository(conn)
        from app.places import PlaceTree

        tree = PlaceTree(repo.list_places())
        rows = {r["id"]: r for r in repo.list_items(filters())[0]}

        def path(name):
            return tree.path_text(rows[ids[name]]["storage_place_id"])

        assert path("full") == "Attic › Tub 3 › Tray Top"
        assert rows[ids["full"]]["storage_place_id"] == rows[ids["full twin"]]["storage_place_id"]
        assert path("tub only") == "Attic › Tub 3"
        assert path("tray no tub") == "Attic › Tray B"
        assert path("no location") == "Tub 9 › Tray A"
        assert path("tray alone") == "Tray Z"
        assert path("location only") == "Basement"
        assert rows[ids["nowhere"]]["storage_place_id"] is None
        # Attic, Attic › Tub 3, … › Tray Top, Attic › Tray B, Tub 9, Tub 9 › Tray A, Tray Z, Basement
        assert len(tree) == 8
        # the old values themselves are untouched
        assert (rows[ids["full twin"]]["storage_location"], rows[ids["no location"]]["storage_tub"]) == ("attic", "9")

        # Re-running the SQL changes nothing, even after a piece has been moved since.
        repo.update_item(ids["full"], {**item(name="full"), "storage_place_id": rows[ids["tray alone"]]["storage_place_id"]})
        migrate._execute_all(conn, migrate.split_statements(by_version["0009_storage_places"].up_path.read_text()))
        conn.commit()
        assert len(PlaceTree(repo.list_places())) == 8
        assert repo.get_item(ids["full"])["storage_place_id"] == rows[ids["tray alone"]]["storage_place_id"]

        # Rolling back leaves Location / Tub / Tray as the app last kept them.
        assert migrate.migrate_down(conn, migrations) == "0011_currency"
        assert migrate.migrate_down(conn, migrations) == "0010_villages"
        assert migrate.migrate_down(conn, migrations) == "0009_storage_places"
        with conn.cursor() as cur:
            cur.execute("SELECT storage_location, storage_tub, storage_tray FROM items WHERE id = %s", (ids["full"],))
            # the place's name; "Tray" is its type
            assert cur.fetchone() == {"storage_location": "Z", "storage_tub": None, "storage_tray": None}
        assert migrate.migrate_up(conn, migrations) == ["0009_storage_places", "0010_villages", "0011_currency"]
    finally:
        migrate.migrate_up(conn, migrations)  # back to the latest schema even if an assertion failed
        _truncate_all(conn)
        conn.close()


def _place(repo, name, parent=None, kind=None, display=False):
    place_id = repo.create_place({"parent_id": parent, "name": name, "kind": kind, "is_display": display,
                                  "notes": None})
    assert place_id
    return place_id


def test_places_crud_keep_old_columns_in_step(repo):
    from app.repository import DuplicatePlace

    garage = _place(repo, "Garage")
    attic = _place(repo, "Attic")
    cabinet = _place(repo, "2", garage, "Cabinet")
    drawer = _place(repo, "3", cabinet, "Drawer")
    spot = _place(repo, "B", drawer, "Spot")
    assert repo.create_place({"parent_id": 999_999, "name": "x", "kind": None, "is_display": False,
                              "notes": None}) is None
    with pytest.raises(DuplicatePlace):
        _place(repo, "garage")  # names are compared without case
    _place(repo, "Garage", attic)  # the same name in another place is fine

    item_id = repo.create_item({**item(), "storage_place_id": spot})
    row = repo.get_item(item_id)
    assert (row["storage_location"], row["storage_tub"], row["storage_tray"]) == ("Garage", "2", "3")

    # renaming or moving a place updates every piece inside it
    assert repo.update_place(cabinet, {"parent_id": attic, "name": "7", "kind": "Cabinet",
                                       "is_display": False, "notes": None}) is None
    row = repo.get_item(item_id)
    assert (row["storage_location"], row["storage_tub"], row["storage_tray"]) == ("Attic", "7", "3")
    assert row["version"] == 1  # not an edit of the piece itself
    assert "inside itself" in repo.update_place(cabinet, {"parent_id": spot, "name": "7", "kind": None,
                                                          "is_display": False, "notes": None})
    assert repo.update_place(999_999, {"parent_id": None, "name": "x"}) == "missing"
    with pytest.raises(DuplicatePlace):
        repo.update_place(attic, {"parent_id": None, "name": "Garage", "kind": None, "is_display": False,
                                  "notes": None})

    counts = {p["id"]: p for p in repo.list_places()}
    assert counts[spot]["items"] == 1 and counts[drawer]["items"] == 0

    # only empty places can be deleted; the foreign keys back that up
    assert "empty" in repo.delete_place(spot)
    assert "empty" in repo.delete_place(drawer)
    repo.update_item(item_id, item())
    assert repo.get_item(item_id)["storage_location"] is None
    assert repo.delete_place(spot) is None
    assert repo.delete_place(spot) == "missing"
    with pytest.raises(Exception):
        repo.create_item({**item(), "storage_place_id": 999_999})


def test_place_filter_contents_extras_and_snapshot(repo):
    garage = _place(repo, "Garage")
    tub = _place(repo, "1", garage, "Tub")
    bin_ = _place(repo, "Cords", garage)
    mantel = _place(repo, "Mantel", display=True)
    a = repo.create_item({**item(name="A"), "storage_place_id": tub, "display_place_id": mantel})
    b = repo.create_item({**item(name="B"), "storage_place_id": garage})
    repo.create_item(item(name="C"))
    extra = repo.add_extra(a, {**parse_extra_form({"description": "Cord"})[0], "storage_place_id": bin_})
    assert repo.get_extra(a, extra)["storage_place_id"] == bin_

    def names(**args):
        return sorted(r["name"] for r in repo.list_items(filters(**args))[0])

    assert names(place=str(garage)) == ["A", "B"]
    assert names(place=str(tub)) == ["A"]
    assert names(place="999999") == []
    assert names(unstored="yes") == ["C"]
    assert names(storage_tub="1") == ["A"]  # old links keep working
    assert repo.count_unplaced() == 1

    contents = repo.place_contents([garage, tub, bin_])
    assert [r["name"] for r in contents["stored"]] == ["A", "B"]
    assert [(e["description"], e["item_name"]) for e in contents["extras"]] == [("Cord", "A")]
    assert [r["name"] for r in repo.place_contents([mantel])["displayed"]] == ["A"]
    assert repo.place_contents([]) == {"stored": [], "displayed": [], "extras": []}

    etag = repo.snapshot_etag()
    snap = repo.snapshot()
    assert {p["id"] for p in snap["places"]} == {garage, tub, bin_, mantel}
    repo.update_place(bin_, {"parent_id": garage, "name": "Cord bin", "kind": None, "is_display": False,
                             "notes": None})
    assert repo.snapshot_etag() != etag
    assert {p["id"] for p in repo.export_data()["places"]} == {garage, tub, bin_, mantel}
    assert "empty" in repo.delete_place(mantel)  # still some piece's display spot
    # a place in use keeps its role
    assert "display spot" in repo.update_place(tub, {"parent_id": garage, "name": "1", "kind": "Tub",
                                                     "is_display": True, "notes": None})
    assert "display spot" in repo.update_place(mantel, {"parent_id": None, "name": "Mantel", "kind": None,
                                                        "is_display": False, "notes": None})
    assert repo.update_place(bin_, {"parent_id": garage, "name": "Cord bin", "kind": None, "is_display": False,
                                    "notes": "Under the bench"}) is None  # unchanged role: fine


def test_old_phone_values_find_or_create_places(repo):
    attic = _place(repo, "Attic")
    tub = _place(repo, "3", attic, "Tub")
    data = item(storage_location="Attic", storage_tub="3")
    data = {**data, "storage_location": "Attic", "storage_tub": "3", "_legacy_storage": True}
    item_id, created = repo.create_item_from_client(data, "11111111-1111-4111-8111-111111111111")
    assert created and repo.get_item(item_id)["storage_place_id"] == tub

    def resolve(row):
        return "ok", {**item(), "storage_location": "Attic", "storage_tub": "4", "storage_tray": "Top",
                      "_legacy_storage": True}

    status, _payload, row = repo.patch_item(item_id, resolve)
    assert status == "ok"
    from app.places import PlaceTree

    tree = PlaceTree(repo.list_places())
    assert tree.path_text(row["storage_place_id"]) == "Attic › Tub 4 › Tray Top"
    assert (row["storage_tub"], row["storage_tray"]) == ("4", "Top")


# -- villages -----------------------------------------------------------------------


def test_villages_migration_keeps_existing_rows_in_dickens_village(db_config, migrated):
    migrations = migrate.discover(MIGRATIONS)
    conn = connect(db_config)
    try:
        assert migrate.migrate_down(conn, migrations) == "0011_currency"
        assert migrate.migrate_down(conn, migrations) == "0010_villages"
        ids = _legacy_items(conn, [("before villages", "Attic", "3", None)])
        with conn.cursor() as cur:
            cur.execute("INSERT INTO storage_places (parent_id, name, kind) VALUES (NULL, 'Garage', NULL)")
        conn.commit()
        assert migrate.migrate_up(conn, migrations) == ["0010_villages", "0011_currency"]
        repo = MySQLRepository(conn)
        assert repo.get_item(ids["before villages"])["village"] == "dickens"
        assert {p["village"] for p in repo.list_places()} == {"dickens"}
        # Both villages can now have a top-level Garage; one village still can't have two.
        from app.repository import DuplicatePlace

        assert _place_in(repo, "Garage", "holy-land")
        with pytest.raises(DuplicatePlace):
            _place_in(repo, "garage", "holy-land")
        # ...which is what stops a rollback until one is renamed.
        assert migrate.migrate_down(conn, migrations) == "0011_currency"
        with pytest.raises(pymysql.err.IntegrityError):
            migrate.migrate_down(conn, migrations)
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM storage_places LIKE 'village'")
            assert cur.fetchone() is not None  # nothing was dropped
            cur.execute("UPDATE storage_places SET name = 'Garage 2' WHERE village = 'holy-land'")
        conn.commit()
        assert migrate.migrate_down(conn, migrations) == "0010_villages"
        assert migrate.migrate_up(conn, migrations) == ["0010_villages", "0011_currency"]
    finally:
        migrate.migrate_up(conn, migrations)
        _truncate_all(conn)
        conn.close()


def _place_in(repo, name, village, parent=None, display=False):
    return repo.create_place({"village": village, "parent_id": parent, "name": name, "kind": None,
                              "is_display": display, "notes": None})


def test_pieces_places_and_summary_are_per_village(repo):
    garage = _place(repo, "Garage")
    holy_garage = _place_in(repo, "Garage", "holy-land")
    holy_tub = _place_in(repo, "1", "dickens", parent=holy_garage)  # a child takes its parent's village
    assert repo.get_place(holy_tub)["village"] == "holy-land"
    from app.places import PlaceTree

    tree = PlaceTree(repo.list_places())
    assert tree.roots("holy-land") == [holy_garage] and tree.roots("dickens") == [garage]
    assert "same village" in repo.update_place(holy_tub, {"parent_id": garage, "name": "1", "kind": None,
                                                          "is_display": False, "notes": None})

    church = repo.create_item({**item(name="Church", price_paid="10"), "storage_place_id": garage})
    nativity = repo.create_item({**item(name="Nativity", village="holy-land", price_paid="55"),
                                 "storage_place_id": holy_tub})
    figure = repo.create_item(item(name="Carpenter & Son", category="figure", village="holy-land"))
    repo.add_extra(nativity, parse_extra_form({"description": "Star", "quantity": "2"})[0])

    def names(**args):
        return sorted(r["name"] for r in repo.list_items({**filters(**args), "village": args.get("village")})[0])

    assert names(village="holy-land") == ["Carpenter & Son", "Nativity"]
    assert names(village="dickens") == ["Church"]
    assert names() == ["Carpenter & Son", "Church", "Nativity"]
    assert repo.count_unplaced("holy-land") == 1 and repo.count_unplaced("dickens") == 0
    assert repo.count_unplaced() == 1

    holy = repo.collection_summary("holy-land")["totals"]
    assert (holy["item_count"], holy["total_paid"], holy["extras_count"]) == (2, Decimal("55.00"), 2)
    dickens = repo.collection_summary("dickens")["totals"]
    assert (dickens["item_count"], dickens["extras_count"]) == (1, 0)
    assert repo.collection_summary()["totals"]["item_count"] == 3

    # Links only within a village.
    assert [r["id"] for r in repo.link_candidates(repo.get_item(nativity))] == [figure]
    assert not repo.add_link(church, figure)
    assert repo.add_link(nativity, figure)

    # Old phone values make places in the piece's own village.
    data = {**item(name="Old phone", village="holy-land"), "storage_location": "Garage", "storage_tub": "1",
            "_legacy_storage": True}
    old_id = repo.create_item(data)
    assert repo.get_item(old_id)["storage_place_id"] == holy_tub

    snap = repo.snapshot()
    assert {p["id"]: p["village"] for p in snap["places"]}[holy_tub] == "holy-land"
    assert {i["id"]: i["village"] for i in snap["items"]}[nativity] == "holy-land"
    assert {p["id"]: p["village"] for p in repo.export_data()["places"]}[holy_garage] == "holy-land"


def test_catalog_villages_and_filters(db_config, repo):
    conn = connect(db_config)
    try:
        catalog.sync(conn, catalog.load_directory(CATALOG_DIR))
    finally:
        conn.close()
    bethlehem = repo.search_catalog("56.59757")[0]
    assert bethlehem["village"] == "holy-land" and bethlehem["list_name"] == "holy-land-lighted"
    lamps = repo.search_catalog("56.59960")[0]
    assert lamps["village"] is None
    assert repo.search_catalog("56.65005")[0]["village"] == "dickens"

    assert repo.search_catalog("56.59757", village="dickens") == []
    assert repo.search_catalog("56.65005", village="holy-land") == []
    assert repo.search_catalog("56.59960", village="holy-land")[0]["id"] == lamps["id"]
    assert repo.search_catalog("56.59960", village="dickens")[0]["id"] == lamps["id"]

    rows, total = repo.browse_catalog("bethlehem", village="holy-land")
    assert bethlehem["id"] in {r["id"] for r in rows} and total >= 1
    assert all(r["village"] in (None, "dickens") for r in repo.browse_catalog("bethlehem", village="dickens")[0])
    _, everything = repo.browse_catalog(owned_only=False, missing_photos=True, limit=1)
    _, holy_total = repo.browse_catalog(missing_photos=True, limit=1, village="holy-land")
    _, dickens_total = repo.browse_catalog(missing_photos=True, limit=1, village="dickens")
    assert holy_total == 44 + 1189 and dickens_total == 760 + 1189 and everything == 1993

    # Owned counts and "only pieces I own" count the village's own pieces.
    repo.create_item({**item(name="Lamps", village="holy-land"), "catalog_id": lamps["id"]})
    repo.create_item({**item(name="Lamps too"), "catalog_id": lamps["id"]})
    owned, owned_total = repo.browse_catalog(owned_only=True, village="holy-land")
    assert owned_total == 1 and owned[0]["owned_count"] == 1
    assert repo.browse_catalog(owned_only=True)[0][0]["owned_count"] == 2


# -- currencies and settings --------------------------------------------------------


def test_settings_round_trip(repo):
    assert repo.get_settings().get("currency") in (None, "USD")  # seeded USD; other tests truncate it
    repo.save_settings({"currency": "CAD", "usd_cad_rate": "1.38", "usd_cad_rate_set_on": "2026-11-27"})
    assert repo.get_settings() == {"currency": "CAD", "usd_cad_rate": "1.38", "usd_cad_rate_set_on": "2026-11-27"}
    repo.save_settings({"currency": "USD", "usd_cad_rate": None, "usd_cad_rate_set_on": None})
    assert repo.get_settings() == {"currency": "USD"}


def test_currency_column_summary_and_value_sort(repo):
    usd = repo.create_item(item(name="USD piece", price_paid="40", estimated_value="100"))
    cad = repo.create_item(item(name="CAD piece", price_paid="55.20", estimated_value="120", currency="CAD"))
    assert repo.get_item(usd)["currency"] == "USD" and repo.get_item(cad)["currency"] == "CAD"
    money_rows = {r["currency"]: r for r in repo.collection_summary()["money"]}
    assert money_rows["USD"]["total_paid"] == Decimal("40.00")
    assert money_rows["CAD"]["total_value"] == Decimal("120.00")

    def order(**extra):
        return [r["name"] for r in repo.list_items({**filters(sort="estimated_value"), **extra})[0]]

    assert order() == ["CAD piece", "USD piece"]  # raw numbers: 120 before 100
    # at 1.38, CA$120 is about US$87, so the US$100 piece is worth more
    assert order(usd_cad_rate=Decimal("1.38")) == ["USD piece", "CAD piece"]
    assert repo.snapshot()["items"][0]["currency"] in ("USD", "CAD")
