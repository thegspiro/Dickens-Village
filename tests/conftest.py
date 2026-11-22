"""Shared fixtures. Unit tests use an in-memory FakeRepository that implements
the same interface as MySQLRepository; the real SQL is covered by
tests/integration against a MySQL container."""

from __future__ import annotations

import datetime as dt
import io
import itertools
import re
from typing import Any

import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.config import load_config
from app.validation import STORAGE_COLUMNS

ADMIN_USER = "gabriel"
ADMIN_PASSWORD = "correct horse battery staple"
_HASH = generate_password_hash(ADMIN_PASSWORD)


class FakeRepository:
    def __init__(self) -> None:
        self.items: dict[int, dict[str, Any]] = {}
        self.extras: dict[int, dict[str, Any]] = {}
        self.links: set[tuple[int, int]] = set()
        self.photos: dict[int, dict[str, Any]] = {}
        self.catalog: dict[int, dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self.closed = 0

    def close(self) -> None:
        self.closed += 1

    def ping(self) -> bool:
        return True

    def _with_counts(self, item: dict[str, Any]) -> dict[str, Any]:
        row = dict(item)
        row["extras_count"] = sum(e["quantity"] for e in self.extras.values() if e["item_id"] == item["id"])
        return row

    def list_items(self, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        rows = [self._with_counts(i) for i in self.items.values()]
        if filters.get("q"):
            q = filters["q"].lower()
            rows = [r for r in rows if any(q in (r.get(k) or "").lower() for k in ("name", "item_number", "series"))]
        for col in ("category", "condition_grade", *STORAGE_COLUMNS):
            if filters.get(col):
                rows = [r for r in rows if r.get(col) == filters[col]]
        if filters.get("has_extras") == "yes":
            rows = [r for r in rows if r["extras_count"]]
        elif filters.get("has_extras") == "no":
            rows = [r for r in rows if not r["extras_count"]]
        if filters.get("on_display") in ("yes", "no"):
            rows = [r for r in rows if bool(r["on_display"]) == (filters["on_display"] == "yes")]
        if filters.get("exact_storage"):
            for col in STORAGE_COLUMNS:
                if not filters.get(col):
                    rows = [r for r in rows if not r.get(col)]
        if filters.get("unstored"):
            rows = [r for r in rows if not any(r.get(c) for c in STORAGE_COLUMNS)]
        rows.sort(key=lambda r: (r["name"], r["id"]))
        return rows, len(rows)

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        item = self.items.get(item_id)
        return self._with_counts(item) if item else None

    def create_item(self, data: dict[str, Any]) -> int:
        new_id = next(self._ids)
        now = dt.datetime(2026, 11, 19, 12, 0)
        self.items[new_id] = {**data, "id": new_id, "created_at": now, "updated_at": now}
        return new_id

    def update_item(self, item_id: int, data: dict[str, Any]) -> bool:
        if item_id not in self.items:
            return False
        self.items[item_id].update(data)
        return True

    def delete_item(self, item_id: int) -> list[str] | None:
        if item_id not in self.items:
            return None
        del self.items[item_id]
        self.extras = {k: v for k, v in self.extras.items() if v["item_id"] != item_id}
        self.links = {l for l in self.links if item_id not in l}
        names = [p["filename"] for p in self.photos.values() if p["item_id"] == item_id]
        self.photos = {k: v for k, v in self.photos.items() if v["item_id"] != item_id}
        return names

    def distinct_values(self, column: str) -> list[str]:
        return sorted({i[column] for i in self.items.values() if i.get(column)})

    def list_extras(self, item_id: int) -> list[dict[str, Any]]:
        return [e for e in self.extras.values() if e["item_id"] == item_id]

    def get_extra(self, item_id: int, extra_id: int) -> dict[str, Any] | None:
        e = self.extras.get(extra_id)
        return e if e and e["item_id"] == item_id else None

    def add_extra(self, item_id: int, data: dict[str, Any]) -> int | None:
        if item_id not in self.items:
            return None
        new_id = next(self._ids)
        self.extras[new_id] = {**data, "id": new_id, "item_id": item_id}
        return new_id

    def update_extra(self, item_id: int, extra_id: int, data: dict[str, Any]) -> bool:
        if not self.get_extra(item_id, extra_id):
            return False
        self.extras[extra_id].update(data)
        return True

    def delete_extra(self, item_id: int, extra_id: int) -> bool:
        if not self.get_extra(item_id, extra_id):
            return False
        del self.extras[extra_id]
        return True

    def list_links(self, item_id: int) -> list[dict[str, Any]]:
        ids = [b if a == item_id else a for a, b in self.links if item_id in (a, b)]
        return [self.items[i] for i in ids]

    def link_candidates(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        linked = {i["id"] for i in self.list_links(item["id"])}
        want_building = item["category"] != "building"
        return [
            i for i in self.items.values()
            if (i["category"] == "building") == want_building and i["id"] not in linked
        ]

    def add_link(self, id_a: int, id_b: int) -> bool:
        if id_a == id_b or id_a not in self.items or id_b not in self.items:
            return False
        cats = {i: self.items[i]["category"] for i in (id_a, id_b)}
        buildings = [i for i, c in cats.items() if c == "building"]
        if len(buildings) != 1:
            return False
        b = buildings[0]
        self.links.add((b, id_b if b == id_a else id_a))
        return True

    def remove_link(self, id_a: int, id_b: int) -> bool:
        before = len(self.links)
        self.links -= {(id_a, id_b), (id_b, id_a)}
        return len(self.links) < before

    def list_photos(self, item_id: int) -> list[dict[str, Any]]:
        return [p for p in self.photos.values() if p["item_id"] == item_id]

    def get_photo(self, photo_id: int) -> dict[str, Any] | None:
        return self.photos.get(photo_id)

    def add_photo(self, item_id: int, filename: str, original_name: str | None) -> int | None:
        if item_id not in self.items:
            return None
        new_id = next(self._ids)
        self.photos[new_id] = {"id": new_id, "item_id": item_id, "filename": filename, "original_name": original_name}
        return new_id

    def delete_photo(self, photo_id: int) -> dict[str, Any] | None:
        return self.photos.pop(photo_id, None)

    def add_catalog(self, **fields: Any) -> int:
        new_id = next(self._ids)
        row = {
            "id": new_id, "list_name": "lighted", "item_number": None, "name": "", "details": None,
            "series": None, "year_issued": None, "year_retired": None, "is_current": 0,
            "srp_usd": None, "srp_cad": None, "default_category": None,
            "parent_name": None, "parent_item_number": None,
        }
        row.update(fields)
        self.catalog[new_id] = row
        return new_id

    def search_catalog(self, query: str, limit: int = 15) -> list[dict[str, Any]]:
        q = query.strip().lower()
        if len(q) < 2:
            return []
        digits = "".join(ch for ch in q if ch.isdigit())
        hits = []
        for row in self.catalog.values():
            number = (row["item_number"] or "").lower()
            if q in number or q in row["name"].lower() or (len(digits) >= 2 and digits in number.replace(".", "")):
                rank = 0 if number == q else 1 if number.startswith(q) else 2 if q in number else 3
                hits.append((rank, number, row["id"], row))
        return [h[3] for h in sorted(hits, key=lambda h: h[:3])][:limit]

    def get_catalog_item(self, catalog_id: int) -> dict[str, Any] | None:
        return self.catalog.get(catalog_id)

    def storage_summary(self) -> list[dict[str, Any]]:
        counts: dict[tuple, int] = {}
        for i in self.items.values():
            key = tuple(i.get(c) for c in STORAGE_COLUMNS)
            counts[key] = counts.get(key, 0) + 1
        return [dict(zip(STORAGE_COLUMNS, k), item_count=n) for k, n in counts.items()]

    def collection_summary(self) -> dict[str, Any]:
        return {
            "by_category": [],
            "by_condition": [],
            "totals": {"item_count": len(self.items), "total_paid": 0, "total_value": 0,
                       "on_display": 0, "with_box": 0, "extras_count": len(self.extras)},
        }


def make_env(tmp_path: Any) -> dict[str, str]:
    return {
        "SECRET_KEY": "x" * 48,
        "ADMIN_USERNAME": ADMIN_USER,
        "ADMIN_PASSWORD_HASH": _HASH,
        "COOKIE_SECURE": "false",
        "UPLOAD_DIR": str(tmp_path / "uploads"),
        "MAX_UPLOAD_MB": "2",
        "DB_HOST": "unused",
        "DB_NAME": "unused",
        "DB_USER": "unused",
        "DB_PASSWORD": "unused",
    }


@pytest.fixture
def fake_repo() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def app(tmp_path: Any, fake_repo: FakeRepository) -> Any:
    application = create_app(load_config(make_env(tmp_path)), repo_factory=lambda _cfg: fake_repo)
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app: Any) -> Any:
    return app.test_client()


_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def csrf_from(response: Any) -> str:
    match = _CSRF_RE.search(response.get_data(as_text=True))
    assert match, "page has no CSRF token"
    return match.group(1)


@pytest.fixture
def logged_in(client: Any) -> Any:
    token = csrf_from(client.get("/login"))
    resp = client.post("/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD, "csrf_token": token})
    assert resp.status_code == 302
    return client


def csrf(client: Any) -> str:
    return csrf_from(client.get("/items"))


def png_bytes(size: tuple[int, int] = (40, 30), mode: str = "RGBA") -> io.BytesIO:
    from PIL import Image

    buf = io.BytesIO()
    Image.new(mode, size, (200, 30, 30, 255) if mode == "RGBA" else (200, 30, 30)).save(buf, format="PNG")
    buf.seek(0)
    return buf
