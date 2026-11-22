from pathlib import Path

import pytest

from app import migrate

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def test_discover_real_migrations_have_rollbacks():
    found = migrate.discover(MIGRATIONS)
    assert [m.version for m in found] == ["0001_initial", "0002_catalog"]
    assert all(m.down_path is not None for m in found)


def test_initial_migration_is_idempotent_sql():
    statements = migrate.split_statements((MIGRATIONS / "0001_initial.up.sql").read_text())
    assert len(statements) == 4
    assert all(s.startswith("CREATE TABLE IF NOT EXISTS") for s in statements)
    down = migrate.split_statements((MIGRATIONS / "0001_initial.down.sql").read_text())
    assert down == [
        "DROP TABLE IF EXISTS item_photos",
        "DROP TABLE IF EXISTS item_links",
        "DROP TABLE IF EXISTS item_extras",
        "DROP TABLE IF EXISTS items",
    ]


def test_catalog_migration_statements():
    up = migrate.split_statements((MIGRATIONS / "0002_catalog.up.sql").read_text())
    assert up[0].startswith("CREATE TABLE IF NOT EXISTS catalog_items")
    # each guarded ALTER is SET / PREPARE / EXECUTE / DEALLOCATE
    assert sum(s.startswith("PREPARE") for s in up) == 2
    assert all("ADD COLUMN" not in s or s.startswith("SET @dv_sql") for s in up)
    down = migrate.split_statements((MIGRATIONS / "0002_catalog.down.sql").read_text())
    assert down[-1] == "DROP TABLE IF EXISTS catalog_items"
    assert not any(s.startswith("DROP TABLE IF EXISTS items") for s in down)


def test_split_requires_terminator():
    with pytest.raises(migrate.MigrationError):
        migrate.split_statements("SELECT 1")


def test_orphan_down_rejected(tmp_path):
    (tmp_path / "0002_x.down.sql").write_text("SELECT 1;")
    with pytest.raises(migrate.MigrationError):
        migrate.discover(tmp_path)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.log.append(sql)
        if sql.startswith("SELECT GET_LOCK"):
            self._result = [{"got": 1 if self.conn.lock_ok else 0}]
        elif sql.startswith("SELECT RELEASE_LOCK"):
            self.conn.released = True
            self._result = [{"released": 1}]
        elif sql.startswith("SELECT version"):
            self._result = [{"version": v} for v in sorted(self.conn.applied)]
        elif sql.startswith("INSERT INTO schema_migrations"):
            self.conn.applied.add(params[0])
        elif sql.startswith("DELETE FROM schema_migrations"):
            self.conn.applied.discard(params[0])
        elif "FAIL" in sql:
            raise RuntimeError("boom")

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result


class FakeConn:
    def __init__(self, applied=(), lock_ok=True):
        self.applied = set(applied)
        self.lock_ok = lock_ok
        self.log = []
        self.released = False
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        self.rollbacks += 1


def write(tmp_path, name, sql):
    (tmp_path / name).write_text(sql)


def test_up_applies_pending_in_order_and_is_rerunnable(tmp_path):
    write(tmp_path, "0001_a.up.sql", "CREATE TABLE IF NOT EXISTS a (id INT);")
    write(tmp_path, "0002_b.up.sql", "CREATE TABLE IF NOT EXISTS b (id INT);")
    conn = FakeConn(applied={"0001_a"})
    assert migrate.migrate_up(conn, migrate.discover(tmp_path)) == ["0002_b"]
    assert conn.released
    assert migrate.migrate_up(conn, migrate.discover(tmp_path)) == []


def test_up_refuses_unknown_db_versions(tmp_path):
    write(tmp_path, "0001_a.up.sql", "SELECT 1;")
    conn = FakeConn(applied={"0001_a", "0009_future"})
    with pytest.raises(migrate.MigrationError, match="does not know"):
        migrate.migrate_up(conn, migrate.discover(tmp_path))
    assert conn.released


def test_up_requires_lock(tmp_path):
    write(tmp_path, "0001_a.up.sql", "SELECT 1;")
    with pytest.raises(migrate.MigrationError, match="lock"):
        migrate.migrate_up(FakeConn(lock_ok=False), migrate.discover(tmp_path))


def test_failed_migration_not_recorded(tmp_path):
    write(tmp_path, "0001_a.up.sql", "SELECT FAIL;")
    conn = FakeConn()
    with pytest.raises(RuntimeError):
        migrate.migrate_up(conn, migrate.discover(tmp_path))
    assert conn.applied == set() and conn.rollbacks == 1 and conn.released


def test_down_rolls_back_latest_only(tmp_path):
    for n in ("0001_a", "0002_b"):
        write(tmp_path, f"{n}.up.sql", "SELECT 1;")
        write(tmp_path, f"{n}.down.sql", f"DROP TABLE IF EXISTS {n};")
    conn = FakeConn(applied={"0001_a", "0002_b"})
    assert migrate.migrate_down(conn, migrate.discover(tmp_path)) == "0002_b"
    assert conn.applied == {"0001_a"}
    assert "DROP TABLE IF EXISTS 0002_b" in conn.log


def test_cli_down_requires_yes(capsys):
    assert migrate.main(["down"]) == 2
    assert "--yes" in capsys.readouterr().err
