import pytest

from app.config import ConfigError, load_config
from tests.conftest import make_env


def test_loads_valid_env(tmp_path):
    cfg = load_config(make_env(tmp_path))
    assert cfg.database.port == 3306
    assert cfg.cookie_secure is False
    assert cfg.max_upload_mb == 2


@pytest.mark.parametrize("key", ["SECRET_KEY", "ADMIN_USERNAME", "ADMIN_PASSWORD_HASH", "UPLOAD_DIR", "DB_HOST", "DB_PASSWORD"])
def test_missing_required(tmp_path, key):
    env = make_env(tmp_path)
    env[key] = ""
    with pytest.raises(ConfigError, match=key):
        load_config(env)


def test_rejects_short_secret_and_plain_password(tmp_path):
    env = make_env(tmp_path)
    env["SECRET_KEY"] = "short"
    with pytest.raises(ConfigError):
        load_config(env)
    env = make_env(tmp_path)
    env["ADMIN_PASSWORD_HASH"] = "hunter2"
    with pytest.raises(ConfigError, match="hashpw"):
        load_config(env)


def test_cookie_secure_defaults_true_and_bad_values_rejected(tmp_path):
    env = make_env(tmp_path)
    del env["COOKIE_SECURE"]
    assert load_config(env).cookie_secure is True
    env["COOKIE_SECURE"] = "maybe"
    with pytest.raises(ConfigError):
        load_config(env)
    env["COOKIE_SECURE"] = "true"
    env["DB_PORT"] = "70000"
    with pytest.raises(ConfigError):
        load_config(env)
