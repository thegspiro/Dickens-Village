"""Add catalog photos by item number: match, review, choose, upload."""

from __future__ import annotations

import re
import shutil

from playwright.sync_api import expect


def named(tmp_path, images, *names):
    paths = []
    for name in names:
        path = tmp_path / name
        shutil.copy(images["house-red.jpg"], path)
        paths.append(str(path))
    return paths


def photo_count(page, number):
    page.goto(f"/catalog?q={number}")
    page.locator("tbody a").first.click()
    return page.locator("#photos [data-gallery] .photo").count()


def test_bulk_upload_matches_by_item_number(desk, tmp_path, images):
    ctx, page, errors = desk
    before = photo_count(page, "56.65161")
    page.goto("/catalog")
    page.click('a:has-text("Add photos by item number")')
    expect(page).to_have_url(re.compile(r"/catalog/bulk$"))
    files = named(tmp_path, images, "56.65161.jpg", "65161 (2).jpg", "56-65005.jpg", "IMG_0001.jpg")
    page.set_input_files("#bulk-files", files)
    expect(page.locator("#bulk-status")).to_contain_text("3 of 4 photos matched a catalog entry; choose the entry for 1 below.")
    rows = page.locator("#bulk-body tr")
    expect(rows).to_have_count(4)
    expect(rows.nth(0)).to_contain_text("56.65161")
    expect(rows.nth(1)).to_contain_text("56.65161")
    unmatched = rows.filter(has_text="IMG_0001.jpg")
    expect(unmatched).to_contain_text("doesn't start with an item number")
    expect(unmatched.locator("input[type=checkbox]")).to_be_disabled()
    shared = rows.filter(has_text="56-65005.jpg")
    expect(shared).to_contain_text("entries share this number")
    expect(shared.locator("input[type=checkbox]")).not_to_be_checked()
    expect(page.locator("#bulk-upload")).to_have_text("Upload 2 photos")

    options = shared.locator("select option").all_inner_texts()[1:]
    assert len(options) >= 2 and all(o.startswith("56.65005") for o in options)
    shared.locator("select").select_option(index=1)
    chosen = options[0]
    expect(page.locator("#bulk-upload")).to_have_text("Upload 3 photos")
    rows.nth(1).locator("input[type=checkbox]").uncheck()  # leave one out
    page.click("#bulk-upload")
    expect(page.locator("#bulk-status")).to_contain_text("Uploaded 2 photos to 2 catalog entries.")
    expect(page.locator("#bulk-done li")).to_have_count(2)
    expect(rows.nth(0)).to_contain_text("Uploaded")

    assert photo_count(page, "56.65161") == before + 1  # the unchecked one stayed behind
    page.goto("/catalog?q=56.65005")
    page.locator("tbody a", has_text=chosen.split(" · ", 1)[1].split(" (part of")[0]).first.click()
    expect(page.locator("#photos [data-gallery] .photo")).not_to_have_count(0)
    assert errors == []


def test_bulk_page_fits_a_phone(phone, tmp_path, images):
    ctx, page, errors = phone
    page.goto("/catalog/bulk")
    page.set_input_files("#bulk-files", named(tmp_path, images, "56.65161.jpg", "56-65005.jpg"))
    expect(page.locator("#bulk-body tr")).to_have_count(2)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert errors == []
