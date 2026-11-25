"""Reference catalog: item-number lookup, duplicates, catalog pages and photos."""

from __future__ import annotations

import re

from playwright.sync_api import expect

from .conftest import add_piece


def pick(page, query, name):
    page.fill("#catalog-lookup", query)
    option = page.locator("#catalog-results [role=option]", has_text=name).first
    expect(option).to_be_visible()
    option.click()


def test_lookup_by_number_variants_fills_form(desk, tag):
    ctx, page, errors = desk
    page.goto("/items/new")
    for query in ("56.65005", "65005", "5665005"):
        page.fill("#catalog-lookup", query)
        expect(page.locator("#catalog-results [role=option]", has_text="Fezziwig's Warehouse")).to_be_visible()
    page.locator("#catalog-results [role=option]", has_text="Fezziwig's Warehouse").click()
    expect(page.locator("#name")).to_have_value("Fezziwig's Warehouse")
    expect(page.locator("#item_number")).to_have_value("56.65005")
    expect(page.locator("#year_introduced")).to_have_value("1986")
    expect(page.locator("#year_retired")).to_have_value("1995")
    expect(page.locator("#category")).to_have_value("building")
    expect(page.locator("#catalog-linked-text")).to_contain_text("part of “Christmas Carol Cottage”")
    page.fill("#name", f"Fezziwig's Warehouse {tag}")
    page.click('button:has-text("Add piece")')
    expect(page.locator(".details")).to_contain_text("Catalog listing")
    expect(page.locator(".details")).to_contain_text("$25.00 US")
    assert errors == []


def test_duplicate_numbers_show_each_name(desk):
    ctx, page, _ = desk
    page.goto("/items/new")
    page.fill("#catalog-lookup", "58576")
    options = page.locator("#catalog-results [role=option]")
    expect(options).to_have_count(2)
    texts = options.all_inner_texts()
    assert any("Christmas Morning Parade" in t for t in texts) and any("London Gas Worker" in t for t in texts)


def test_keyboard_pick_and_enter_never_submits(desk):
    ctx, page, _ = desk
    page.goto("/items/new")
    page.fill("#catalog-lookup", "prize turkey")
    expect(page.locator("#catalog-results [role=option]").first).to_be_visible()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"/items/new$"))
    expect(page.locator("#name")).to_have_value("The Big Prize Turkey")
    expect(page.locator("#series")).to_have_value("A Christmas Carol")
    expect(page.locator("#category")).to_have_value("accessory")


def test_unlink_catalog_on_edit(desk, tag):
    ctx, page, _ = desk
    page.goto("/items/new")
    pick(page, "6015650", "Lord Lilliput")
    page.fill("#name", f"Lilliput {tag}")
    page.click('button:has-text("Add piece")')
    page.click('a.button:has-text("Edit")')
    expect(page.locator("#catalog-linked")).to_contain_text("Lord Lilliput")
    page.check("#catalog_unlink")
    page.click('button:has-text("Save changes")')
    expect(page.locator(".details")).not_to_contain_text("Catalog listing")


def test_catalog_pages_photos_and_add_to_collection(desk, tag, images):
    ctx, page, errors = desk
    page.click("nav >> text=Catalog")
    page.fill("#q", "56.65080")
    page.click('button:has-text("Search")')
    page.click("text=Blythe Pond Mill House")
    expect(page.locator("h1")).to_contain_text("Blythe Pond Mill House")
    expect(page.locator(".details")).to_contain_text("$37.00 US")
    page.set_input_files("#photo", [str(images["house-red.jpg"])])
    page.click('#photos button:has-text("Upload")')
    expect(page.locator(".flash-success")).to_contain_text("Added 1 photo.")
    catalog_url = page.url.split("#")[0]

    page.click("text=Add to my collection")
    expect(page.locator("#name")).to_have_value("Blythe Pond Mill House")
    page.fill("#name", f"Blythe Pond {tag}")
    page.click('button:has-text("Add piece")')
    expect(page.locator("#photos")).to_contain_text("Catalog reference photos")
    expect(page.locator('#photos img[src*="/catalog-photos/"]').first).to_be_visible()

    page.goto(catalog_url)
    expect(page.locator("section", has_text="In your collection")).to_contain_text(f"Blythe Pond {tag}")
    page.goto("/items/new")
    page.fill("#catalog-lookup", "56.65080")
    expect(page.locator("#catalog-results img.combo-thumb").first).to_be_visible()
    assert errors == []


def test_find_owned_entries_that_need_photos(desk, tag, images):
    ctx, page, errors = desk
    page.goto("/items/new")
    page.fill('#catalog-lookup', "56.65161")
    page.locator("#catalog-results li").first.click()
    page.fill("#name", f"Needs photo {tag}")
    page.click('button:has-text("Add piece")')
    page.wait_for_url(re.compile(r"/items/\d+$"))

    page.goto("/catalog")
    page.check('input[name="missing"]')
    page.check('input[name="owned"]')
    page.click('form.card button[type="submit"]')
    expect(page).to_have_url(re.compile(r"missing=1.*owned=1"))
    row = page.locator("tbody tr", has_text="56.65161")
    expect(row).to_contain_text("You own")
    row.locator("a.thumb-missing").click()
    expect(page).to_have_url(re.compile(r"/catalog/\d+#photos$"))
    page.set_input_files("#photo", str(images["house-red.jpg"]))
    page.click('#photos button:has-text("Upload")')
    expect(page.locator(".flash-success")).to_contain_text("Added 1 photo.")

    page.goto("/catalog?missing=1&owned=1")
    expect(page.locator("tbody tr", has_text="56.65161")).to_have_count(0)
    assert errors == []


def test_use_a_piece_photo_as_catalog_photo(desk, tag, tmp_path):
    from PIL import Image

    ctx, page, errors = desk
    # A photo no other test uploads: the entry may already hold the shared sample images,
    # and the copy is refused (correctly) when the entry already has the same photo.
    unique = tmp_path / "unique.jpg"
    Image.new("RGB", (640, 480), tuple(int(tag[-6:][i:i + 2], 16) for i in (0, 2, 4))).save(unique)
    page.goto("/items/new")
    page.fill("#catalog-lookup", "56.65161")
    page.locator("#catalog-results li").first.click()
    page.fill("#name", f"Copied photo {tag}")
    page.click('button:has-text("Add piece")')
    page.wait_for_url(re.compile(r"/items/\d+$"))
    page.set_input_files("#photo", str(unique))
    page.click('#photos button:has-text("Upload")')
    expect(page.locator(".flash-success")).to_contain_text("Added 1 photo.")
    figure = page.locator("#photos [data-gallery] .photo").first
    figure.locator("summary").click()
    figure.locator("input[name=caption]").fill(f"Front {tag}")
    figure.locator('button:has-text("Save caption")').click()
    page.locator("#photos [data-gallery] .photo").first.locator("summary").click()
    page.locator('button:has-text("Use as catalog photo")').click()
    expect(page.locator(".flash-success")).to_contain_text("Added to the catalog entry's reference photos.")
    # the piece page now shows it among the catalog reference photos too (the second gallery)
    expect(page.locator("#photos [data-gallery]").nth(1)).to_contain_text(f"Front {tag}")
    page.locator('a:has-text("Manage catalog photos")').click()
    expect(page.locator("#photos figcaption", has_text=f"Front {tag}")).to_be_visible()
    assert errors == []
