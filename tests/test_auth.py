from app.auth import LoginThrottle
from tests.conftest import ADMIN_PASSWORD, ADMIN_USER, csrf, csrf_from


def test_pages_require_login(client):
    for path in ("/", "/items", "/items/new", "/storage", "/summary", "/items/1", "/photos/1"):
        resp = client.get(path)
        assert resp.status_code == 302, path
        assert "/login" in resp.headers["Location"]


def test_healthz_is_public(client):
    assert client.get("/healthz").status_code == 200


def test_login_requires_csrf(client):
    resp = client.post("/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    assert resp.status_code == 400


def test_wrong_password_rejected(client):
    token = csrf_from(client.get("/login"))
    resp = client.post("/login", data={"username": ADMIN_USER, "password": "nope", "csrf_token": token})
    assert resp.status_code == 401
    assert client.get("/items").status_code == 302


def test_wrong_username_rejected(client):
    token = csrf_from(client.get("/login"))
    resp = client.post("/login", data={"username": "someone", "password": ADMIN_PASSWORD, "csrf_token": token})
    assert resp.status_code == 401


def test_login_and_logout(logged_in):
    assert logged_in.get("/items").status_code == 200
    resp = logged_in.post("/logout", data={"csrf_token": csrf(logged_in)})
    assert resp.status_code == 302
    assert logged_in.get("/items").status_code == 302


def test_logout_requires_csrf(logged_in):
    assert logged_in.post("/logout", data={}).status_code == 400
    assert logged_in.get("/items").status_code == 200


def test_login_redirects_to_safe_next_only(client):
    token = csrf_from(client.get("/login"))
    resp = client.post("/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD,
                                       "csrf_token": token, "next": "/storage"})
    assert resp.headers["Location"].endswith("/storage")
    client.post("/logout", data={"csrf_token": csrf(client)})
    for evil in ("//evil.example/x", "https://evil.example", "/\\evil.example"):
        token = csrf_from(client.get("/login"))
        resp = client.post("/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD,
                                           "csrf_token": token, "next": evil})
        assert resp.headers["Location"].endswith("/items"), evil
        client.post("/logout", data={"csrf_token": csrf(client)})


def test_session_cookie_flags(client):
    resp = client.get("/login")
    cookie = resp.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_throttle_blocks_after_failures(client):
    for _ in range(5):
        token = csrf_from(client.get("/login"))
        client.post("/login", data={"username": ADMIN_USER, "password": "bad", "csrf_token": token})
    token = csrf_from(client.get("/login"))
    resp = client.post("/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD, "csrf_token": token})
    assert resp.status_code == 429


def test_throttle_window_expires():
    t = LoginThrottle(max_failures=2, window_seconds=10)
    t.record_failure("a", now=0)
    t.record_failure("a", now=1)
    assert t.is_blocked("a", now=5)
    assert not t.is_blocked("a", now=12)
    assert not t.is_blocked("b", now=5)


def test_security_headers(client):
    resp = client.get("/login")
    assert "script-src 'none'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Frame-Options"] == "DENY"
