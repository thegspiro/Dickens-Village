"""Photo upload form: drop, paste, previews, one-by-one upload with progress, and the plain form without JS."""

from __future__ import annotations

import base64
import re

from playwright.sync_api import expect

from .conftest import BASE_URL, add_piece, login

# Builds File objects in the page from base64 so a test can drop or paste them.
MAKE_FILES = """(files) => {
    const dt = new DataTransfer();
    for (const f of files) {
        const bytes = Uint8Array.from(atob(f.data), c => c.charCodeAt(0));
        dt.items.add(new File([bytes], f.name, {type: f.type}));
    }
    return dt;
}"""


def as_payload(path, name=None, mime="image/jpeg"):
    return {"name": name if name is not None else path.name, "type": mime,
            "data": base64.b64encode(path.read_bytes()).decode()}


def drop(page, selector, files):
    dt = page.evaluate_handle(MAKE_FILES, files)
    for event in ("dragenter", "dragover", "drop"):
        page.dispatch_event(selector, event, {"dataTransfer": dt})


def paste(page, files):
    dt = page.evaluate_handle(MAKE_FILES, files)
    page.evaluate("dt => document.body.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true}))", dt)


def test_drop_paste_remove_then_upload_with_progress(desk, tag, images):
    ctx, page, errors = desk
    item_id = add_piece(page, f"Uploader {tag}")
    page.goto(f"/items/{item_id}")
    form = "#photos form[data-uploader]"
    expect(page.locator(".uploader-hint")).to_contain_text("drop photos here")
    drop(page, form, [as_payload(images["house-red.jpg"]), as_payload(images["house-blue.png"], mime="image/png")])
    paste(page, [as_payload(images["house-red.jpg"], name="", mime="image/jpeg")])  # a pasted image has no name
    rows = page.locator(".upload-item")
    expect(rows).to_have_count(3)
    expect(rows.nth(2)).to_contain_text("pasted-photo-3.jpeg")
    expect(rows.first.locator("img.upload-preview")).to_have_attribute("src", re.compile("^blob:"))
    rows.nth(1).locator("button:has-text('Remove')").click()
    expect(rows).to_have_count(2)
    expect(page.locator(f"{form} button[type=submit]")).to_have_text("Upload 2 photos")

    page.click(f"{form} button[type=submit]")
    expect(page.locator(".flash-success")).to_contain_text("Added 2 photos.")
    expect(page.locator("#photos [data-gallery] img")).to_have_count(2)
    assert "uploaded=" not in page.url
    assert errors == []


def test_one_bad_photo_doesnt_stop_the_rest(desk, tag, images):
    ctx, page, errors = desk
    item_id = add_piece(page, f"Uploader mixed {tag}")
    page.goto(f"/items/{item_id}")
    page.set_input_files("#photo", [str(images["house-red.jpg"]), str(images["not-a-photo.jpg"]),
                                    str(images["house-blue.png"])])
    page.click('#photos button:has-text("Upload 3 photos")')
    bad = page.locator(".upload-item", has_text="not-a-photo.jpg")
    expect(bad).to_contain_text("That file is not a supported image.")
    expect(page.locator(".upload-item.uploaded")).to_have_count(2)
    expect(page.locator(".upload-item.uploaded progress").first).to_have_js_property("value", 1)
    page.click('button:has-text("Show the uploaded photos")')
    expect(page.locator(".flash-success")).to_contain_text("Added 2 photos.")
    expect(page.locator("#photos [data-gallery] img")).to_have_count(2)


def test_catalog_entry_page_uses_the_same_uploader(desk, tag, images):
    ctx, page, errors = desk
    page.goto("/catalog?q=56.65161")
    page.locator("tbody a").first.click()
    before = page.locator("#photos [data-gallery] img").count()
    drop(page, "#photos form[data-uploader]", [as_payload(images["house-red.jpg"])])
    page.click('#photos button:has-text("Upload 1 photo")')
    expect(page.locator(".flash-success")).to_contain_text("Added 1 photo.")
    expect(page.locator("#photos [data-gallery] img")).to_have_count(before + 1)
    assert errors == []


def test_plain_form_still_works_without_javascript(browser, tag, images):
    ctx = browser.new_context(base_url=BASE_URL, java_script_enabled=False)
    try:
        page = ctx.new_page()
        login(page)
        page.goto("/items/new")
        page.select_option("#category", "building")
        page.fill("#name", f"No JS {tag}")
        page.click('button:has-text("Add piece")')
        page.wait_for_url(re.compile(r"/items/\d+$"))
        page.set_input_files("#photo", [str(images["house-red.jpg"]), str(images["house-blue.png"])])
        page.click('#photos button:has-text("Upload")')
        expect(page.locator(".flash-success")).to_contain_text("Added 2 photos.")
        expect(page.locator(".upload-queue")).to_have_count(0)
    finally:
        ctx.close()
