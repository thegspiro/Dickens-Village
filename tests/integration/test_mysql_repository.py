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

from app import migrate  # noqa: E402
from app.config import DatabaseConfig  # noqa: E402
from app.db import connect  # noqa: E402
from app.repository import PAGE_SIZE, MySQLRepository  # noqa: E402
from app.validation import parse_extra_form, parse_item_form, parse_list_filters  # noqa: E402

pytestmark = pytest.mark.integration

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
TABLES = ("item_photos", "item_links", "item_extras", "items")


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
        assert migrate.migrate_down(conn, migrations) == "0001_initial"
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'items'")
            assert cur.fetchone() is None
        assert migrate.migrate_up(conn, migrations) == ["0001_initial"]
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
