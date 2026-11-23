"""docker/entrypoint.sh: settings are checked before anything starts (POSIX sh)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parent.parent / "docker" / "entrypoint.sh"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")


def run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GUNICORN_")}
    return subprocess.run(["sh", str(ENTRYPOINT), *args], env={**clean, **env},
                          capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("value", ["", "abc", "0", "4x", "-1"])
def test_bad_thread_count_is_refused(value):
    result = run({"GUNICORN_THREADS": value}, "true")
    if value == "":
        assert result.returncode == 0  # empty means "use the default"
    else:
        assert result.returncode == 1
        assert "GUNICORN_THREADS must be a whole number of at least 1" in result.stderr


def test_one_off_commands_run_with_valid_settings():
    result = run({"GUNICORN_THREADS": "8", "GUNICORN_WORKERS": "3"}, "echo", "hello")
    assert result.returncode == 0 and result.stdout.strip() == "hello"


def test_server_command_uses_threaded_workers():
    text = ENTRYPOINT.read_text()
    assert '--worker-class gthread' in text and '--threads "$GUNICORN_THREADS"' in text
