"""Fixtures for the browser end-to-end tests.

These drive a real Chromium against a running tracker. They are skipped unless
E2E_BASE_URL is set, e.g.:

    E2E_BASE_URL=http://localhost:8000 E2E_USERNAME=admin E2E_PASSWORD=... \\
        python -m pytest tests/e2e

The server must have the reference catalog loaded and MAX_UPLOAD_MB=2
(the upload-limit test relies on it). The tests only add their own,
uniquely named pieces, so they can run against a database with other data,
but use a throwaway database: they do not clean up after themselves.
"""

from __future__ import annotations

import io
import os
import re
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

BASE_URL = os.environ.get("E2E_BASE_URL", "").rstrip("/")
USERNAME = os.environ.get("E2E_USERNAME", "")
PASSWORD = os.environ.get("E2E_PASSWORD", "")
MAX_UPLOAD_MB = int(os.environ.get("E2E_MAX_UPLOAD_MB", "2"))

pytestmark = pytest.mark.e2e


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    if BASE_URL:
        return
    skip = pytest.mark.skip(reason="E2E_BASE_URL not set; skipping browser end-to-end tests")
    for item in items:
        if "tests/e2e" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def browser() -> Iterator[Any]:
    with playwright_api.sync_playwright() as p:
        kwargs: dict[str, Any] = {}
        if os.environ.get("E2E_CHROME"):
            kwargs["executable_path"] = os.environ["E2E_CHROME"]
            kwargs["args"] = ["--no-sandbox"]
        b = p.chromium.launch(**kwargs)
        yield b
        b.close()


class Errors(list):
    """Page errors and console errors collected during a test."""


def _watch(page: Any, errors: Errors) -> None:
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))

    def console(msg: Any) -> None:
        text = msg.text
        expected = ("ERR_INTERNET_DISCONNECTED" in text or "status of 409" in text
                    or "status of 404" in text and "favicon" in text)
        if msg.type == "error" and not expected:
            errors.append(f"console: {text}")

    page.on("console", console)


@pytest.fixture
def make_context(browser: Any) -> Iterator[Any]:
    contexts = []

    def factory(phone: bool = False) -> tuple[Any, Any, Errors]:
        options: dict[str, Any] = {"base_url": BASE_URL}
        if phone:
            options.update(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                           device_scale_factor=2)
        ctx = browser.new_context(**options)
        ctx.set_default_timeout(15_000)
        contexts.append(ctx)
        page = ctx.new_page()
        errors = Errors()
        _watch(page, errors)
        return ctx, page, errors

    yield factory
    for ctx in contexts:
        ctx.close()


def login(page: Any) -> None:
    page.goto("/login")
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r"/items"))


@pytest.fixture
def desk(make_context: Any) -> tuple[Any, Any, Errors]:
    ctx, page, errors = make_context()
    login(page)
    return ctx, page, errors


@pytest.fixture
def phone(make_context: Any) -> tuple[Any, Any, Errors]:
    ctx, page, errors = make_context(phone=True)
    login(page)
    return ctx, page, errors


@pytest.fixture
def tag() -> str:
    """A unique word to put in names so tests never collide."""
    return "e2e" + uuid.uuid4().hex[:8]


def add_piece(page: Any, name: str, category: str = "building", **fields: str) -> int:
    page.goto("/items/new")
    page.select_option("#category", category)
    page.fill("#name", name)
    for field, value in fields.items():
        if field == "condition_grade":
            page.select_option("#condition_grade", value)
        elif field in ("has_original_box", "has_sleeve", "on_display"):
            if value:
                page.check(f"input[name={field}]")
        elif field in ("storage_place_id", "display_place_id"):
            page.select_option(f"#{field}", str(value))
        else:
            page.fill(f"#{field}", value)
    page.click('button:has-text("Add piece")')
    page.wait_for_url(re.compile(r"/items/\d+$"))
    return int(page.url.rsplit("/", 1)[1])


def add_place(page: Any, name: str, kind: str | None = None, parent: int | None = None,
              display: bool = False) -> int:
    """Add a storage place (or display spot) through the website; returns its id."""
    page.goto(f"/places/new?parent={parent}" if parent else "/places/new")
    page.fill("#name", name)
    if kind:
        page.fill("#kind", kind)
    if display:
        page.check("input[name=is_display]")
    page.click('button:has-text("Add place")')
    page.wait_for_url(re.compile(r"/places/\d+$"))
    return int(page.url.rsplit("/", 1)[1])


@pytest.fixture(scope="session")
def images(tmp_path_factory: Any) -> dict[str, Path]:
    from PIL import Image, ImageDraw

    folder = tmp_path_factory.mktemp("images")
    paths = {}
    for name, colour in (("house-red.jpg", (150, 60, 50)), ("house-blue.png", (60, 90, 140))):
        img = Image.new("RGB", (1600, 1200), (235, 230, 220))
        draw = ImageDraw.Draw(img)
        draw.polygon([(400, 600), (800, 250), (1200, 600)], fill=colour)
        draw.rectangle([450, 600, 1150, 1050], fill=(200, 170, 120))
        path = folder / name
        img.save(path)
        paths[name] = path
    # bigger than MAX_UPLOAD_MB but a valid JPEG (noise does not compress)
    big = folder / "too-big.jpg"
    size = 2200
    while True:
        noise = Image.effect_noise((size, size), 128).convert("RGB")
        buf = io.BytesIO()
        noise.save(buf, format="JPEG", quality=98)
        if buf.tell() > MAX_UPLOAD_MB * 1024 * 1024:
            big.write_bytes(buf.getvalue())
            break
        size += 800
    paths["too-big.jpg"] = big
    fake = folder / "not-a-photo.jpg"
    fake.write_bytes(b"<?php system($_GET['x']); ?>")
    paths["not-a-photo.jpg"] = fake
    return paths


def api_snapshot(page: Any) -> dict[str, Any]:
    return page.evaluate("fetch('/api/v1/snapshot', {credentials: 'same-origin', cache: 'no-store'})"
                         ".then(r => r.json())")
