"""Phone: installable app, phone layout, offline editing, sync, conflicts, logout."""

from __future__ import annotations

import re

from playwright.sync_api import expect

from .conftest import add_piece, api_snapshot, login


def open_app_and_sync(page):
    page.goto("/app")
    expect(page.locator("#sync-pill")).to_contain_text("All synced", timeout=30_000)


def go_offline(ctx, page):
    page.goto("/items")
    page.evaluate("navigator.serviceWorker.ready.then(() => true)")
    ctx.set_offline(True)


def back_online(ctx, page):
    ctx.set_offline(False)
    page.evaluate("window.dispatchEvent(new Event('online'))")


def test_installable_pwa_and_phone_layout(phone, tag, images):
    ctx, page, errors = phone
    add_piece(page, f"Phone layout {tag}", storage_tub="2")
    manifest = page.request.get("/manifest.webmanifest")
    assert manifest.ok and manifest.headers["content-type"].startswith("application/manifest+json")
    page.goto(f"/items?q={tag}")
    assert page.evaluate("navigator.serviceWorker.ready.then(r => r.scope)").endswith("/")
    cdp = ctx.new_cdp_session(page)
    installability = cdp.send("Page.getInstallabilityErrors")["installabilityErrors"]
    assert [e["errorId"] for e in installability if e["errorId"] != "in-incognito"] == []
    assert cdp.send("Page.getAppManifest").get("errors") == []

    # menu collapses behind a button; list rows become cards with labels
    expect(page.locator("#site-nav")).to_be_hidden()
    page.click("#nav-toggle")
    expect(page.locator("#site-nav")).to_be_visible()
    card = page.locator("tbody tr").first
    expect(card.locator("td[data-label=Storage]")).to_contain_text("Tub 2")
    assert card.bounding_box()["width"] <= 390
    # no horizontal scrolling on the main pages
    for path in ("/items", "/storage", "/catalog?q=65005", "/summary", "/items/new"):
        page.goto(path)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), path

    # camera button and photo picker both upload
    page.goto(f"/items?q={tag}")
    page.locator("tbody a").first.click()
    expect(page.locator('label:has-text("Take a photo")')).to_be_visible()
    page.set_input_files("#photo-camera", str(images["house-red.jpg"]))
    page.set_input_files("#photo", str(images["house-blue.png"]))
    expect(page.locator(".picker-status")).to_contain_text("2 photos ready")
    page.click('#photos button:has-text("Upload")')
    expect(page.locator(".flash-success")).to_contain_text("Added 2 photos.")
    assert errors == []


def test_offline_add_edit_photo_then_sync_with_merge_and_conflict(phone, make_context, tag, images):
    ctx, page, errors = phone
    _, desk, _ = make_context()
    login(desk)
    church = add_piece(desk, f"Ivy Glen {tag}", storage_tub="1", condition_grade="good")
    abbey = add_piece(desk, f"Brick Abbey {tag}", storage_tub="2", condition_grade="good")

    open_app_and_sync(page)
    go_offline(ctx, page)
    page.goto("/items")  # a website page while offline opens the phone app
    expect(page.locator("#sync-pill")).to_contain_text("Offline")
    expect(page.locator(".offline-card", has_text=f"Ivy Glen {tag}")).to_be_visible()

    # add a piece from the catalog, with an extra and a photo
    page.goto("/app#/new")
    page.fill('input[aria-label="Look up in catalog"]', "65005")
    page.locator(".combo-results li", has_text="Fezziwig").click()
    page.fill("#f-name", f"Fezziwig {tag}")
    page.fill("#f-storage_tub", "5")
    page.select_option("#f-condition_grade", "excellent")
    page.fill("#f-height_in", "7 1/4")
    page.click('button:has-text("Add piece")')
    expect(page.locator(".flash-pending")).to_be_visible()
    page.click('a:has-text("Add extra")')
    page.fill("#f-description", "Light cord")
    page.click('button:has-text("Add extra")')
    expect(page.locator(".badge-pending", has_text="Not synced yet")).to_be_visible()
    page.set_input_files('input[aria-label="Choose photos"]', str(images["house-red.jpg"]))
    expect(page.locator("figcaption", has_text="Waiting to upload")).to_be_visible()

    # edit two existing pieces
    for item_id, field, value in ((church, "#f-storage_tub", "7"), (abbey, "#f-notes", "Window cracked")):
        page.goto(f"/app#/piece/s-{item_id}")
        expect(page.locator("h1")).not_to_contain_text("Edit")
        page.click('a.button:has-text("Edit")')
        page.fill(field, value)
        page.click('button:has-text("Save changes")')
        expect(page.locator(".flash-pending")).to_be_visible()
    page.goto("/app#/")
    expect(page.locator("#sync-pill")).to_contain_text("5 waiting")

    # meanwhile, on the computer: same field on one piece, a different field on the other
    desk.goto(f"/items/{church}/edit")
    desk.fill("#storage_tub", "9")
    desk.click('button:has-text("Save changes")')
    desk.goto(f"/items/{abbey}/edit")
    desk.select_option("#condition_grade", "fair")
    desk.click('button:has-text("Save changes")')

    back_online(ctx, page)
    expect(page.locator("#sync-pill")).to_contain_text("1 need attention", timeout=30_000)
    items = {i["name"]: i for i in api_snapshot(desk)["items"]}
    new = items[f"Fezziwig {tag}"]
    assert (new["storage_tub"], new["condition_grade"], new["height_in"]) == ("5", "excellent", "7.25")
    assert new["catalog_id"] is not None
    assert [e["description"] for e in new["extras"]] == ["Light cord"] and len(new["photos"]) == 1
    assert items[f"Brick Abbey {tag}"]["notes"] == "Window cracked"       # merged
    assert items[f"Brick Abbey {tag}"]["condition_grade"] == "fair"       # kept
    assert items[f"Ivy Glen {tag}"]["storage_tub"] == "9"                 # held, not overwritten

    page.goto("/app#/attention")
    expect(page.locator("fieldset.conflict")).to_contain_text("Tub")
    page.check('input[value="mine"]')
    page.click('button:has-text("Save my choices")')
    expect(page.locator("#sync-pill")).not_to_contain_text("attention", timeout=30_000)
    expect(page.locator("#sync-pill")).to_contain_text("All synced", timeout=30_000)
    assert {i["name"]: i for i in api_snapshot(desk)["items"]}[f"Ivy Glen {tag}"]["storage_tub"] == "7"
    assert errors == []


def test_retrying_a_sync_never_duplicates(phone, tag):
    ctx, page, _ = phone
    open_app_and_sync(page)
    go_offline(ctx, page)
    page.goto("/app#/new")
    page.select_option("#f-category", "figure")
    page.fill("#f-name", f"Carolers {tag}")
    page.click('button:has-text("Add piece")')
    expect(page.locator(".flash-pending")).to_be_visible()
    # the upload reaches the server but the reply is lost
    ctx.set_offline(False)
    page.route("**/api/v1/items", lambda route: (route.fetch(), route.abort()))
    page.evaluate("window.dispatchEvent(new Event('online'))")
    page.wait_for_timeout(2500)
    page.unroute("**/api/v1/items")
    page.click("#sync-pill button")
    expect(page.locator("#sync-pill")).to_contain_text("All synced", timeout=30_000)
    names = [i["name"] for i in api_snapshot(page)["items"]]
    assert names.count(f"Carolers {tag}") == 1


def test_piece_deleted_elsewhere_can_be_discarded(phone, make_context, tag):
    ctx, page, _ = phone
    _, desk, _ = make_context()
    login(desk)
    item_id = add_piece(desk, f"Doomed {tag}")
    open_app_and_sync(page)
    go_offline(ctx, page)
    page.goto(f"/app#/piece/s-{item_id}")
    page.click('a.button:has-text("Edit")')
    page.fill("#f-notes", "edited offline")
    page.click('button:has-text("Save changes")')
    desk.goto(f"/items/{item_id}/delete")
    desk.click('button:has-text("Delete permanently")')
    back_online(ctx, page)
    expect(page.locator("#sync-pill")).to_contain_text("need attention", timeout=30_000)
    page.goto("/app#/attention")
    page.on("dialog", lambda d: d.accept())
    page.click('button:has-text("Discard my changes")')
    expect(page.locator("#sync-pill")).not_to_contain_text("attention", timeout=30_000)
    page.goto("/app#/")
    # the next sync drops the piece the server no longer has
    expect(page.locator(".offline-card", has_text=f"Doomed {tag}")).to_have_count(0, timeout=30_000)


def test_offline_app_escapes_text(phone, tag):
    ctx, page, errors = phone
    item_id = add_piece(page, f'<img src=x onerror=alert(1)> {tag}', notes="<b>bold</b>")
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    open_app_and_sync(page)
    page.goto(f"/app#/piece/s-{item_id}")
    expect(page.locator("h1")).to_contain_text("<img src=x onerror=alert(1)>")
    expect(page.locator(".details")).to_contain_text("<b>bold</b>")
    page.wait_for_timeout(300)
    assert dialogs == [] and errors == []


def test_logout_warns_and_erases_phone_copy(phone, tag):
    ctx, page, _ = phone
    open_app_and_sync(page)
    assert "dickens-offline" in page.evaluate("indexedDB.databases().then(d => d.map(x => x.name))")
    go_offline(ctx, page)
    page.goto("/app#/new")
    page.select_option("#f-category", "figure")
    page.fill("#f-name", f"Unsynced {tag}")
    page.click('button:has-text("Add piece")')
    expect(page.locator(".flash-pending")).to_be_visible()
    ctx.set_offline(False)
    page.route("**/api/v1/**", lambda route: route.abort())  # keep it unsynced
    messages = []
    accept = {"yes": False}

    def on_dialog(d):
        messages.append(d.message)
        d.accept() if accept["yes"] else d.dismiss()

    page.on("dialog", on_dialog)
    page.goto("/items")
    page.click("#nav-toggle")
    page.click('button:has-text("Log out")')
    page.wait_for_timeout(500)
    assert messages and "hasn't synced yet" in messages[0]
    expect(page).to_have_url(re.compile(r"/items"))  # cancelled: still logged in
    accept["yes"] = True
    page.click('button:has-text("Log out")')
    expect(page).to_have_url(re.compile(r"/login"))
    assert page.evaluate("indexedDB.databases().then(d => d.map(x => x.name))") == []
    page.unroute("**/api/v1/**")
    page.goto("/app")
    expect(page.locator("#sync-pill")).to_contain_text("Log in to sync")


def test_other_browsers_do_not_keep_a_copy(desk):
    ctx, page, _ = desk
    page.goto("/items")
    page.reload()
    page.wait_for_timeout(1500)
    assert page.evaluate("indexedDB.databases().then(d => d.map(x => x.name))") == []
