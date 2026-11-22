"""Website flows in a desktop browser: login, pieces, storage, extras, links,
photos, validation, edit conflicts and security behaviour."""

from __future__ import annotations

import re

from playwright.sync_api import expect

from .conftest import BASE_URL, MAX_UPLOAD_MB, PASSWORD, USERNAME, add_piece, login


def test_login_required_and_wrong_password(make_context):
    ctx, page, errors = make_context()
    for path in ("/items", "/catalog", "/storage", "/summary", "/items/new"):
        page.goto(path)
        expect(page).to_have_url(re.compile(r"/login"))
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD + "-wrong")
    page.click("button[type=submit]")
    expect(page.locator(".flash-error")).to_contain_text("Incorrect username or password")
    # the API answers 401 JSON, not a login page
    status = page.evaluate("fetch('/api/v1/snapshot').then(r => r.status)")
    assert status == 401
    assert [e for e in errors if "status of 401" not in e] == []


def test_login_redirects_back_and_only_to_local_paths(make_context):
    ctx, page, _ = make_context()
    page.goto("/storage")
    expect(page).to_have_url(re.compile(r"/login\?next=/storage"))
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)
    page.click("button[type=submit]")
    expect(page).to_have_url(re.compile(r"/storage$"))
    ctx2, page2, _ = make_context()
    page2.goto("/login?next=//evil.example/x")
    page2.fill("#username", USERNAME)
    page2.fill("#password", PASSWORD)
    page2.click("button[type=submit]")
    expect(page2).to_have_url(re.compile(r"/items$"))


def test_security_headers(desk):
    ctx, page, _ = desk
    response = page.goto("/items")
    headers = response.headers
    csp = headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "frame-ancestors 'none'" in csp
    assert headers["x-content-type-options"] == "nosniff"
    cookie = next(c for c in ctx.cookies() if c["name"] == "dickens_session")
    assert cookie["httpOnly"] and cookie["sameSite"] == "Lax"


def test_piece_create_view_edit_delete(desk, tag):
    ctx, page, errors = desk
    name = f"Old Curiosity Shop {tag}"
    item_id = add_piece(page, name, item_number="56.59056", series="Dickens' Village",
                        year_introduced="1987", year_retired="1999", condition_grade="good",
                        condition_checked_on="2026-11-01", condition_notes="Small chip on the chimney",
                        storage_location=f"Basement {tag}", storage_tub="3", storage_tray="Top",
                        height_in="7 1/4", width_in="6.5", depth_in="5", box_height_in="9",
                        box_width_in="8 1/2", box_depth_in="7¼", price_paid="$1,250.5",
                        purchased_from="Estate sale", estimated_value="80", has_original_box="1")
    expect(page.locator(".flash-success")).to_contain_text(f"Added “{name}”")
    details = page.locator(".details")
    expect(details).to_contain_text(f"Basement {tag} › Tub 3 › Tray Top")
    expect(details).to_contain_text("7.25″ H × 6.5″ W × 5″ D")
    expect(details).to_contain_text("9″ H × 8.5″ W × 7.25″ D")
    expect(details).to_contain_text("$1,250.50")
    expect(details).to_contain_text("Small chip on the chimney")
    expect(details).to_contain_text("Original box")

    page.click('a.button:has-text("Edit")')
    expect(page.locator("#height_in")).to_have_value("7.25")
    expect(page.locator("#price_paid")).to_have_value("1250.50")
    page.fill("#storage_tub", "4")
    page.fill("#price_paid", "")
    page.click('button:has-text("Save changes")')
    expect(page.locator(".flash-success")).to_contain_text("Changes saved")
    expect(details).to_contain_text("Tub 4")
    expect(page.locator(".details")).not_to_contain_text("$1,250.50")

    page.click('a.button:has-text("Delete")')
    expect(page.locator("h1")).to_contain_text("Delete")
    page.click('button:has-text("Delete permanently")')
    expect(page.locator(".flash-success")).to_contain_text("Deleted")
    assert errors == []
    assert page.goto(f"/items/{item_id}").status == 404


def test_validation_messages(desk, tag):
    ctx, page, _ = desk
    page.goto("/items/new")
    page.select_option("#category", "building")
    page.fill("#name", f"Invalid {tag}")
    page.fill("#price_paid", "abc")
    page.fill("#year_introduced", "1995")
    page.fill("#year_retired", "1990")
    page.fill("#height_in", "tall")
    page.fill("#purchase_date", "2099-01-01")
    page.click('button:has-text("Add piece")')
    body = page.locator("form.card")
    expect(body).to_contain_text("Enter an amount like 45.00.")
    expect(body).to_contain_text("Retired year cannot be before the year introduced.")
    expect(body).to_contain_text("Enter inches like 7.25 or 7 1/4.")
    expect(body).to_contain_text("Date cannot be in the future.")
    expect(page.locator("#name")).to_have_value(f"Invalid {tag}")  # input kept
    page.goto(f"/items?q={tag}")
    expect(page.locator("h1")).to_contain_text("(0 pieces)")


def test_list_filters_storage_page_and_summary(desk, tag):
    ctx, page, _ = desk
    loc = f"Attic {tag}"
    add_piece(page, f"Loose {tag}", storage_location=loc)
    add_piece(page, f"Packed {tag}", storage_location=loc, storage_tub="7", storage_tray="B",
              condition_grade="fair", on_display="1")
    page.goto(f"/items?q={tag}&storage_tub=7")
    expect(page.locator("tbody tr")).to_have_count(1)
    expect(page.locator("tbody")).to_contain_text(f"Packed {tag}")
    page.goto(f"/items?q={tag}&condition_grade=fair")
    expect(page.locator("tbody tr")).to_have_count(1)
    page.goto(f"/items?q={tag}&on_display=yes")
    expect(page.locator("tbody tr")).to_have_count(1)

    page.goto("/storage")
    row = page.locator("tr", has_text=loc).filter(has_text="—")
    expect(row).to_have_count(1)  # the location with no tub
    row.locator("a").click()
    expect(page.locator("tbody")).to_contain_text(f"Loose {tag}")
    expect(page.locator("tbody")).not_to_contain_text(f"Packed {tag}")  # exact match only

    page.goto("/summary")
    expect(page.locator(".stats")).to_contain_text("Pieces")


def test_search_is_safe_with_wildcards_and_html(desk, tag):
    ctx, page, errors = desk
    nasty = f'<img src=x onerror=alert(1)> 100% {tag}'
    item_id = add_piece(page, nasty, notes="<script>alert('x')</script>")
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.goto(f"/items/{item_id}")
    expect(page.locator("h1")).to_contain_text("<img src=x onerror=alert(1)>")
    page.goto(f"/items?q=100%25 {tag}")
    expect(page.locator("tbody tr")).to_have_count(1)
    page.goto(f"/items?q=%25%25 {tag}")  # literal %% matches nothing
    expect(page.locator("tbody tr")).to_have_count(0)
    page.wait_for_timeout(300)
    assert dialogs == [] and errors == []


def test_extras_add_edit_remove(desk, tag):
    ctx, page, _ = desk
    item_id = add_piece(page, f"Extras {tag}")
    page.fill("#description", "Light cord")
    page.fill("#quantity", "2")
    page.select_option("#extras #condition_grade", "excellent")
    page.click('button:has-text("Add extra")')
    extras = page.locator("#extras")
    expect(extras).to_contain_text("Light cord")
    expect(extras).to_contain_text("Excellent")
    page.goto(f"/items?q={tag}&has_extras=yes")
    expect(page.locator("tbody tr")).to_have_count(1)
    page.goto(f"/items/{item_id}")
    page.click('#extras a:has-text("Edit")')
    page.fill("#quantity", "3")
    page.click('button:has-text("Save")')
    expect(page.locator("#extras")).to_contain_text("3")
    page.click('#extras button:has-text("Remove")')
    expect(page.locator("#extras")).to_contain_text("No extras recorded.")
    # an empty description is stopped by the browser before it is sent
    page.click('button:has-text("Add extra")')
    assert page.eval_on_selector("#description", "e => e.validity.valueMissing")
    expect(page.locator("#extras")).to_contain_text("No extras recorded.")


def test_links_between_building_and_figure(desk, tag):
    ctx, page, _ = desk
    building = add_piece(page, f"Cottage {tag}")
    figure = add_piece(page, f"Tiny Tim {tag}", category="figure")
    page.select_option("#other_id", label=f"Cottage {tag}")
    page.click('#links button:has-text("Link")')
    expect(page.locator("#links")).to_contain_text(f"Cottage {tag}")
    page.goto(f"/items/{building}")
    expect(page.locator("#links")).to_contain_text(f"Tiny Tim {tag}")
    # a building can't be linked to another building
    options = page.locator("#other_id option").all_inner_texts()
    assert not any(f"Cottage {tag}" in o for o in options)
    page.click('#links button:has-text("Unlink")')
    expect(page.locator("#links")).to_contain_text("Nothing linked yet.")
    assert figure


def test_photos_upload_several_view_delete_and_reject_bad_files(desk, tag, images):
    ctx, page, errors = desk
    item_id = add_piece(page, f"Photos {tag}")
    page.set_input_files("#photo", [str(images["house-red.jpg"]), str(images["house-blue.png"])])
    expect(page.locator(".picker-status")).to_contain_text("2 photos ready")
    page.click('#photos button:has-text("Upload")')
    expect(page.locator(".flash-success")).to_contain_text("Added 2 photos.")
    thumbs = page.locator("#photos .photos img")
    expect(thumbs).to_have_count(2)
    # thumbnails really load (are images, served with a login)
    widths = page.eval_on_selector_all("#photos .photos img", "els => els.map(e => e.naturalWidth)")
    assert all(0 < w <= 320 for w in widths)
    full = page.request.get(thumbs.first.evaluate("e => e.parentElement.href"))
    assert full.ok and full.headers["content-type"] == "image/jpeg"

    page.set_input_files("#photo", [str(images["not-a-photo.jpg"]), str(images["too-big.jpg"])])
    page.click('#photos button:has-text("Upload")')
    flashes = page.locator(".flash-error")
    expect(flashes.filter(has_text="not-a-photo.jpg: That file is not a supported image.")).to_have_count(1)
    expect(flashes.filter(has_text=f"too-big.jpg: That photo is larger than {MAX_UPLOAD_MB} MB.")).to_have_count(1)
    expect(page.locator("#photos .photos img")).to_have_count(2)

    page.locator('#photos button:has-text("Remove")').first.click()
    expect(page.locator("#photos .photos img")).to_have_count(1)
    # photos need a login
    photo_url = thumbs.first.get_attribute("src")
    anon = ctx.browser.new_context(base_url=BASE_URL)
    response = anon.request.get(photo_url, max_redirects=0)
    assert response.status == 302
    anon.close()
    assert errors == []


def test_edit_page_warns_when_piece_changed_elsewhere(make_context, tag):
    ctx_a, a, _ = make_context()
    ctx_b, b, _ = make_context()
    login(a)
    login(b)
    item_id = add_piece(a, f"Clash {tag}", storage_tub="1", condition_grade="good")
    a.goto(f"/items/{item_id}/edit")  # opened first...
    b.goto(f"/items/{item_id}/edit")  # ...and changed and saved elsewhere meanwhile
    b.fill("#storage_tub", "9")
    b.click('button:has-text("Save changes")')
    expect(b.locator(".flash-success")).to_be_visible()
    a.select_option("#condition_grade", "fair")
    a.click('button:has-text("Save changes")')
    warning = a.locator(".flash-error")
    expect(warning).to_contain_text("changed somewhere else")
    expect(warning).to_contain_text("Tub: 9")
    a.goto(f"/items/{item_id}")
    expect(a.locator(".details")).to_contain_text("Tub 9")  # not overwritten
    expect(a.locator(".details")).to_contain_text("Good")


def test_csrf_and_logout(desk):
    ctx, page, _ = desk
    status = page.evaluate(
        "fetch('/items/new', {method: 'POST', body: new URLSearchParams({name: 'x', category: 'building'})})"
        ".then(r => r.status)")
    assert status == 400
    page.goto("/items")
    page.click('button:has-text("Log out")')
    expect(page).to_have_url(re.compile(r"/login"))
    page.goto("/items")
    expect(page).to_have_url(re.compile(r"/login"))
