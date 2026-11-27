"""Villages: switching between Dickens' Village and the Holy Land Village on the
website and in the phone app."""

from __future__ import annotations

import re

from playwright.sync_api import expect

from .conftest import add_piece, add_place, api_snapshot


def switch(page, name):
    page.locator(".village-switch button", has_text=name).click()
    expect(page.locator(".village-switch button.current")).to_have_text(name)


def test_website_keeps_the_villages_apart(desk, tag):
    ctx, page, errors = desk
    dickens_tub = add_place(page, f"Tub {tag}")
    church = add_piece(page, f"Dickens church {tag}", storage_place_id=dickens_tub)

    page.goto("/storage")
    switch(page, "Holy Land Village")
    expect(page).to_have_url(re.compile(r"/storage$"))  # stays in the same section
    expect(page.locator("h1")).to_contain_text("Holy Land Village")
    expect(page.locator(".place-tree", has_text=f"Tub {tag}")).to_have_count(0)
    holy_tub = add_place(page, f"Tub {tag}")  # the same name is fine in the other village

    # The lookup offers the Holy Land lists and the general accessories, not Dickens' Village.
    page.goto("/items/new")
    expect(page.locator("#village")).to_have_value("holy-land")
    page.fill("#catalog-lookup", "65005")
    expect(page.locator("#catalog-status")).to_contain_text("No catalog entries match", timeout=10_000)
    page.fill("#catalog-lookup", "59757")
    option = page.locator("#catalog-results [role=option]", has_text="Little Town Of Bethlehem")
    expect(option).to_contain_text("Holy Land Lighted list")
    option.click()
    expect(page.locator("#series")).to_have_value("Little Town Of Bethlehem")
    page.fill("#name", f"Little Town Of Bethlehem {tag}")
    page.select_option("#storage_place_id", str(holy_tub))
    assert page.locator(f"#storage_place_id option[value='{dickens_tub}']").count() == 0
    page.click('button:has-text("Add piece")')
    page.wait_for_url(re.compile(r"/items/\d+$"))
    holy = int(page.url.rsplit("/", 1)[1])
    expect(page.locator(".details")).to_contain_text("Holy Land Village")

    page.goto(f"/items?q={tag}")
    expect(page.locator("tbody tr")).to_have_count(1)
    expect(page.locator("tbody")).to_contain_text("Little Town Of Bethlehem")

    switch(page, "Dickens' Village")
    page.goto(f"/items?q={tag}")
    expect(page.locator("tbody tr")).to_have_count(1)
    expect(page.locator("tbody")).to_contain_text("Dickens church")

    items = {i["id"]: i for i in api_snapshot(page)["items"]}
    assert items[holy]["village"] == "holy-land" and items[church]["village"] == "dickens"
    assert items[holy]["storage_place_id"] == holy_tub
    assert errors == []


def test_phone_app_switches_villages(phone, make_context, tag):
    ctx, page, errors = phone
    _, desk, _ = make_context()
    from .conftest import login

    login(desk)
    desk.goto("/items")
    switch(desk, "Holy Land Village")
    shelf = add_place(desk, f"Shelf {tag}")
    add_piece(desk, f"Nativity {tag}", storage_place_id=shelf)
    switch(desk, "Dickens' Village")
    add_piece(desk, f"Abbey {tag}")

    page.goto("/app")
    expect(page.locator("#sync-pill")).to_contain_text("All synced", timeout=30_000)
    expect(page.locator('select[aria-label="Village"]')).to_have_value("dickens")
    expect(page.locator(".offline-card", has_text=f"Abbey {tag}")).to_be_visible()
    expect(page.locator(".offline-card", has_text=f"Nativity {tag}")).to_have_count(0)

    page.select_option('select[aria-label="Village"]', "holy-land")
    expect(page.locator(".offline-card", has_text=f"Nativity {tag}")).to_be_visible()
    expect(page.locator(".offline-card", has_text=f"Abbey {tag}")).to_have_count(0)

    # A new piece goes in the chosen village, with that village's places and lists.
    page.click('a:has-text("Add piece")')
    page.fill('input[aria-label="Look up in catalog"]', "65005")
    expect(page.locator(".combo-results li")).to_have_count(0)
    page.fill('input[aria-label="Look up in catalog"]', "59759")
    page.locator(".combo-results li", has_text="Heralding Angels").click()
    page.fill("#f-name", f"Heralding Angels {tag}")
    page.select_option("#f-storage_place_id", str(shelf))
    page.click('button:has-text("Add piece")')
    expect(page.locator(".details")).to_contain_text("Holy Land Village")
    page.goto("/app#/")
    expect(page.locator("#sync-pill")).to_contain_text("All synced", timeout=30_000)

    items = {i["name"]: i for i in api_snapshot(desk)["items"]}
    angels = items[f"Heralding Angels {tag}"]
    assert angels["village"] == "holy-land" and angels["storage_place_id"] == shelf
    assert angels["catalog_id"] is not None

    page.reload()  # the chosen village is kept on the phone
    expect(page.locator('select[aria-label="Village"]')).to_have_value("holy-land")
    expect(page.locator(".offline-card", has_text=f"Heralding Angels {tag}")).to_be_visible()
    assert errors == []
