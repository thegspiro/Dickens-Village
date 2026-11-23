"""scripts/backup.sh, run against a stand-in for `docker compose` (no Docker needed).

The stand-in answers `exec -T db sh -c '<mysqldump ...>'` with a small dump and
`exec -T app tar ...` with a tar of a test folder, and can be told to fail.
Every test runs under each POSIX shell available (dash, bash --posix, sh).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backup.sh"

FAKE_COMPOSE = r"""#!/bin/sh
# Stand-in for `docker compose`: records its arguments and plays the containers.
printf '%s\n' "$*" >> "$FAKE_LOG"
[ "$1" = exec ] && [ "$2" = -T ] || { echo "unexpected: $*" >&2; exit 64; }
case "$3" in
  db)
    if [ -n "${FAKE_DB_FAIL:-}" ]; then echo "-- partial"; exit 2; fi
    echo "CREATE TABLE items (id INT);"
    [ -n "${FAKE_DB_TRUNCATED:-}" ] || echo "-- Dump completed on 2026-11-23 10:00:00"
    ;;
  app)
    (cd "$FAKE_UPLOADS" && tar -cf - .)
    if [ -n "${FAKE_TAR_FAIL:-}" ]; then exit 2; fi
    ;;
  *) echo "unknown service $3" >&2; exit 64 ;;
esac
"""

SHELLS = [s for s in (["dash"], ["bash", "--posix"], ["sh"]) if shutil.which(s[0])]
pytestmark = pytest.mark.skipif(not SHELLS, reason="needs a POSIX sh")


@pytest.fixture(params=SHELLS, ids=lambda s: s[0])
def run(request, tmp_path):
    shell = request.param
    fake = tmp_path / "fake-compose.sh"
    fake.write_text(FAKE_COMPOSE)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / ("a" * 32 + ".jpg")).write_bytes(b"\xff\xd8photo")
    (uploads / ("b" * 32 + ".glb")).write_bytes(b"glTFscan")
    log = tmp_path / "compose.log"

    def go(dest: Path, **env: str) -> subprocess.CompletedProcess[str]:
        full = {k: v for k, v in os.environ.items() if k not in ("KEEP", "DOCKER_COMPOSE")}
        full.update({"DOCKER_COMPOSE": f"sh {fake}", "FAKE_UPLOADS": str(uploads), "FAKE_LOG": str(log), **env})
        return subprocess.run([*shell, str(SCRIPT), str(dest)], env=full, capture_output=True, text=True,
                              timeout=60)

    go.log = log  # type: ignore[attr-defined]
    return go


def backups(dest: Path) -> list[Path]:
    return sorted(p for p in dest.iterdir() if p.name.startswith("dickens-backup-") and not p.name.endswith(".partial"))


def test_backup_writes_a_checked_database_dump_and_uploads_archive(run, tmp_path):
    dest = tmp_path / "my backups"  # a space in the path
    result = run(dest)
    assert result.returncode == 0, result.stderr
    (folder,) = backups(dest)
    assert f"Backup written to {folder}" in result.stdout
    assert sorted(p.name for p in folder.iterdir()) == ["SHA256SUMS", "database.sql.gz", "uploads.tar.gz"]
    assert b"Dump completed" in gzip.decompress((folder / "database.sql.gz").read_bytes())
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress((folder / "uploads.tar.gz").read_bytes()))) as archive:
        names = {Path(n).name for n in archive.getnames()}
    assert {"a" * 32 + ".jpg", "b" * 32 + ".glb"} <= names
    sums = dict(reversed(line.split()) for line in (folder / "SHA256SUMS").read_text().splitlines())
    for name, digest in sums.items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest
    # private: only the owner can read the backup
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert stat.S_IMODE((folder / "database.sql.gz").stat().st_mode) == 0o600
    calls = run.log.read_text()
    assert "MYSQL_PWD=\"$MYSQL_PASSWORD\"" in calls and "--single-transaction" in calls  # password never on a command line
    assert "exec -T app tar -C /data/uploads -cf - ." in calls


@pytest.mark.parametrize("failure, message", [
    ({"FAKE_DB_FAIL": "1"}, "mysqldump did not finish"),
    ({"FAKE_DB_TRUNCATED": "1"}, "the database dump is incomplete"),
    ({"FAKE_TAR_FAIL": "1"}, "could not read every file in the upload folder"),
])
def test_a_failed_step_fails_the_backup_and_leaves_no_complete_one(run, tmp_path, failure, message):
    dest = tmp_path / "b"
    result = run(dest, **failure)
    assert result.returncode == 1 and message in result.stderr
    assert backups(dest) == []
    assert len([p for p in dest.iterdir() if p.name.endswith(".partial")]) == 1


def test_keep_removes_only_older_complete_backups(run, tmp_path):
    dest = tmp_path / "b"
    dest.mkdir()
    for old in ("dickens-backup-20240101-000000", "dickens-backup-20250101-000000",
                "dickens-backup-20250601-000000", "dickens-backup-20200101-000000.partial", "photos-i-keep"):
        (dest / old).mkdir()
    result = run(dest, KEEP="02")
    assert result.returncode == 0, result.stderr
    left = sorted(p.name for p in dest.iterdir())
    assert "dickens-backup-20250601-000000" in left
    assert "dickens-backup-20240101-000000" not in left and "dickens-backup-20250101-000000" not in left
    assert "dickens-backup-20200101-000000.partial" in left and "photos-i-keep" in left
    assert len(backups(dest)) == 2


@pytest.mark.parametrize("keep", ["0", "00", "abc", "-1", "1.5"])
def test_bad_keep_values_are_refused_before_anything_runs(run, tmp_path, keep):
    dest = tmp_path / "b"
    result = run(dest, KEEP=keep)
    assert result.returncode == 1 and "KEEP must be" in result.stderr
    assert not dest.exists() and not run.log.exists()
