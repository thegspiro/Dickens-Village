"""Photo gallery: full-screen viewer, captions, cover and reordering, on the website and the phone app."""

from __future__ import annotations

import re

from playwright.sync_api import expect

from .conftest import add_piece


def upload(page, item_id, *paths):
    page.goto(f"/items/{item_id}")
    page.set_input_files("#photo", [str(p) for p in paths])
    page.click('#photos button:has-text("Upload")')
    expect(page.locator(".flash-success")).to_contain_text(f"Added {len(paths)} photo")


def photo_ids(page):
    return [int(x) for x in page.locator("#photos [data-gallery] .photo").evaluate_all(
        "els => els.map(e => e.dataset.photoId)")]


def test_viewer_steps_through_photos_with_keys_and_buttons(desk, tag, images):
    ctx, page, errors = desk
    item_id = add_piece(page, f"Viewer {tag}")
    upload(page, item_id, images["house-red.jpg"], images["house-blue.png"])
    first, second = photo_ids(page)
    page.locator(f'#photos .photo[data-photo-id="{second}"] summary').click()
    page.fill(f"#caption-item-{second}", "Chip on the left eave")
    page.click(f'#photos .photo[data-photo-id="{second}"] button:has-text("Save caption")')
    expect(page.locator(".flash-success")).to_contain_text("Caption saved.")
    expect(page.locator("#photos figcaption")).to_have_text("Chip on the left eave")

    page_url = page.url
    page.locator("#photos .photo-link").first.click()
    viewer = page.locator("dialog.viewer")
    expect(viewer).to_be_visible()
    expect(viewer.locator(".viewer-counter")).to_have_text("1 of 2")
    expect(viewer.locator(".viewer-image")).to_have_attribute("src", re.compile(f"/photos/{first}$"))
    page.keyboard.press("ArrowRight")
    expect(viewer.locator(".viewer-counter")).to_have_text("2 of 2")
    expect(viewer.locator(".viewer-caption")).to_have_text("Chip on the left eave")
    expect(viewer.locator(".viewer-download")).to_have_attribute("href", re.compile(f"/photos/{second}$"))
    viewer.locator(".viewer-next").click()  # wraps around
    expect(viewer.locator(".viewer-counter")).to_have_text("1 of 2")
    viewer.locator(".viewer-image").click()
    expect(viewer).to_have_class("viewer zoomed")
    page.keyboard.press("Escape")
    expect(viewer).to_be_hidden()
    assert page.url == page_url  # the viewer opened over the page instead of leaving it
    assert errors == []  # includes Content-Security-Policy violations


def test_reorder_with_buttons_and_drag_and_cover_in_list(desk, tag, images):
    ctx, page, errors = desk
    item_id = add_piece(page, f"Arranged {tag}")
    upload(page, item_id, images["house-red.jpg"], images["house-blue.png"], images["house-red.jpg"])
    a, b, c = photo_ids(page)
    expect(page.locator(f'#photos .photo[data-photo-id="{a}"] .cover-badge')).to_be_visible()

    page.locator(f'#photos .photo[data-photo-id="{c}"] summary').click()
    page.locator(f'#photos .photo[data-photo-id="{c}"] button:has-text("Make cover")').click()
    expect(page.locator(".flash-success")).to_contain_text("Photo order saved.")
    assert photo_ids(page) == [c, a, b]

    # drag the last photo in front of the first
    with page.expect_navigation():  # the page reloads itself once the new order is saved
        page.locator(f'#photos .photo[data-photo-id="{b}"]').drag_to(
            page.locator(f'#photos .photo[data-photo-id="{c}"]'), target_position={"x": 5, "y": 20})
    assert photo_ids(page) == [b, c, a]

    page.goto(f"/items?q={tag}")
    expect(page.locator("img.cover-thumb")).to_have_attribute("src", f"/photos/{b}/thumb")
    assert errors == []


def test_phone_gallery_is_uncropped_and_opens_the_viewer(phone, tag, images):
    ctx, page, errors = phone
    item_id = add_piece(page, f"Phone gallery {tag}")
    upload(page, item_id, images["house-red.jpg"], images["house-blue.png"])
    first, second = photo_ids(page)
    page.locator(f'#photos .photo[data-photo-id="{first}"] summary').click()
    page.fill(f"#caption-item-{first}", "Front")
    page.click(f'#photos .photo[data-photo-id="{first}"] button:has-text("Save caption")')
    fit = page.locator("#photos img").first.evaluate("el => getComputedStyle(el).objectFit")
    assert fit == "contain"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")

    page.goto("/app")
    expect(page.locator("#sync-pill")).to_contain_text("All synced", timeout=30_000)
    page.goto(f"/app#/piece/s-{item_id}")
    expect(page.locator(".photos figcaption")).to_have_text("Front")
    page.locator(".photos .photo-link").first.click()
    viewer = page.locator("dialog.viewer")
    expect(viewer).to_be_visible()
    expect(viewer.locator(".viewer-counter")).to_have_text("1 of 2")
    expect(viewer.locator(".viewer-caption")).to_have_text("Front")
    box = viewer.locator(".viewer-stage").bounding_box()
    y = box["y"] + box["height"] / 2
    page.mouse.move(box["x"] + box["width"] - 40, y)  # swipe left (touch-like pointer)
    page.evaluate("""([x1, x2, y]) => {
        const stage = document.querySelector('.viewer-stage');
        const ev = (type, x) => new PointerEvent(type, {clientX: x, clientY: y, pointerType: 'touch', bubbles: true});
        stage.dispatchEvent(ev('pointerdown', x1));
        stage.dispatchEvent(ev('pointerup', x2));
    }""", [box["x"] + box["width"] - 40, box["x"] + 40, y])
    expect(viewer.locator(".viewer-counter")).to_have_text("2 of 2")
    viewer.locator(".viewer-close").click()
    expect(viewer).to_be_hidden()
    assert errors == []


def _touch(cdp, kind, x=None, y=None):
    points = [] if kind == "touchEnd" else [{"x": x, "y": y, "id": 1}]
    cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})


def _centre(page, photo_id):
    box = page.locator(f'#photos .photo[data-photo-id="{photo_id}"] img').bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def test_touch_press_hold_and_drag_reorders(phone, tag, images):
    ctx, page, errors = phone
    item_id = add_piece(page, f"Touch order {tag}")
    upload(page, item_id, images["house-red.jpg"], images["house-blue.png"], images["house-red.jpg"])
    a, b, c = photo_ids(page)
    page.locator("#photos .photos").scroll_into_view_if_needed()
    cdp = ctx.new_cdp_session(page)

    # a quick swipe across the photos scrolls; it doesn't move anything
    x, y = _centre(page, c)
    _touch(cdp, "touchStart", x, y)
    for step in range(1, 6):
        _touch(cdp, "touchMove", x - step * 30, y - step * 10)
    _touch(cdp, "touchEnd")
    page.wait_for_timeout(300)
    assert photo_ids(page) == [a, b, c]
    expect(page.locator("dialog.viewer")).to_be_hidden()

    # press and hold the last photo, then drag it onto the left half of the first
    x, y = _centre(page, c)
    tx, ty = _centre(page, a)
    _touch(cdp, "touchStart", x, y)
    page.wait_for_timeout(600)
    expect(page.locator(f'#photos .photo[data-photo-id="{c}"]')).to_have_class(re.compile("lifted"))
    for step in range(1, 11):
        _touch(cdp, "touchMove", x + (tx - 40 - x) * step / 10, y + (ty - y) * step / 10)
        page.wait_for_timeout(20)
    with page.expect_navigation():
        _touch(cdp, "touchEnd")
    expect(page.locator("dialog.viewer")).to_be_hidden()  # the drag didn't count as a tap
    assert photo_ids(page) == [c, a, b]
    expect(page.locator(f'#photos .photo[data-photo-id="{c}"] .cover-badge')).to_be_visible()

    # a quick tap still opens the viewer
    x, y = _centre(page, a)
    _touch(cdp, "touchStart", x, y)
    _touch(cdp, "touchEnd")
    expect(page.locator("dialog.viewer")).to_be_visible()
    assert errors == []


def test_touch_hold_without_moving_changes_nothing(phone, tag, images):
    ctx, page, errors = phone
    item_id = add_piece(page, f"Touch hold {tag}")
    upload(page, item_id, images["house-red.jpg"], images["house-blue.png"])
    before = photo_ids(page)
    cdp = ctx.new_cdp_session(page)
    x, y = _centre(page, before[0])
    _touch(cdp, "touchStart", x, y)
    page.wait_for_timeout(700)
    _touch(cdp, "touchEnd")
    page.wait_for_timeout(500)
    assert photo_ids(page) == before
    expect(page.locator("dialog.viewer")).to_be_hidden()
    expect(page.locator(".gallery-status")).to_have_text("")
    assert errors == []
