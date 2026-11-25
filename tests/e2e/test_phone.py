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
    expect(page.locator(".upload-item")).to_have_count(2)  # camera and library photos queue together
    page.click('#photos button:has-text("Upload 2 photos")')
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


def wait_for_server(page, item_id, field, value, timeout_s=30):
    """Poll the API until the piece has `value` in `field`. The sync pill can
    still say "All synced" for a moment after a save, before its sync starts."""
    for _ in range(timeout_s * 5):
        items = {i["id"]: i for i in api_snapshot(page)["items"]}
        if items.get(item_id, {}).get(field) == value:
            return
        page.wait_for_timeout(200)
    raise AssertionError(f"piece {item_id} never had {field}={value!r}: {items.get(item_id)}")


def lose_replies(page, pattern, method):
    """Let matching requests reach the server but drop the reply, like flaky Wi-Fi."""
    def handler(route):
        if route.request.method == method:
            route.fetch()
            route.abort()
        else:
            route.continue_()
    page.route(pattern, handler)


def test_edit_after_a_lost_create_reply_still_syncs(phone, tag):
    ctx, page, errors = phone
    open_app_and_sync(page)
    go_offline(ctx, page)
    page.goto("/app#/new")
    page.select_option("#f-category", "figure")
    page.fill("#f-name", f"Lamplighter {tag}")
    page.fill("#f-storage_tub", "1")
    page.click('button:has-text("Add piece")')
    expect(page.locator(".flash-pending")).to_be_visible()
    key = page.evaluate("decodeURIComponent(location.hash.split('/')[2])")
    ctx.set_offline(False)
    lose_replies(page, "**/api/v1/items", "POST")
    page.evaluate("window.dispatchEvent(new Event('online'))")
    page.wait_for_timeout(2500)  # the create reached the server; the phone never heard back
    page.unroute("**/api/v1/items")
    ctx.set_offline(True)
    page.goto(f"/app#/edit/{key}")
    page.fill("#f-storage_tub", "4")
    page.click('button:has-text("Save changes")')
    back_online(ctx, page)
    expect(page.locator("#sync-pill")).to_contain_text("All synced", timeout=30_000)
    pieces = [i for i in api_snapshot(page)["items"] if i["name"] == f"Lamplighter {tag}"]
    assert len(pieces) == 1 and pieces[0]["storage_tub"] == "4"
    assert [e for e in errors if e.startswith("pageerror")] == []  # dropped replies log network errors


def test_editing_the_same_field_after_a_lost_patch_reply_is_not_a_conflict(phone, tag):
    ctx, page, errors = phone
    item_id = add_piece(page, f"Bookseller {tag}", storage_tub="1")
    open_app_and_sync(page)
    page.goto(f"/app#/edit/s-{item_id}")
    lose_replies(page, f"**/api/v1/items/{item_id}", "PATCH")
    page.fill("#f-storage_tub", "2")
    page.click('button:has-text("Save changes")')
    page.wait_for_timeout(2500)  # saved on the server as tub 2; the reply was lost
    page.unroute(f"**/api/v1/items/{item_id}")
    assert {i["id"]: i for i in api_snapshot(page)["items"]}[item_id]["storage_tub"] == "2"
    page.goto(f"/app#/edit/s-{item_id}")
    page.fill("#f-storage_tub", "3")
    page.click('button:has-text("Save changes")')
    wait_for_server(page, item_id, "storage_tub", "3")
    expect(page.locator("#sync-pill")).to_contain_text("All synced", timeout=30_000)
    expect(page.locator("#sync-pill")).not_to_contain_text("attention")
    assert [e for e in errors if e.startswith("pageerror")] == []  # dropped replies log network errors


def test_background_sync_does_not_wipe_a_half_filled_form(phone, tag):
    ctx, page, errors = phone
    open_app_and_sync(page)
    page.goto("/app#/new")
    page.fill("#f-name", f"Half typed {tag}")
    page.fill("#f-storage_tub", "8")
    page.locator("h1").click()  # focus leaves the fields, as when switching apps
    page.click("#sync-pill button")
    expect(page.locator("#sync-pill")).to_contain_text("All synced", timeout=30_000)
    page.wait_for_timeout(300)
    expect(page.locator("#f-name")).to_have_value(f"Half typed {tag}")
    expect(page.locator("#f-storage_tub")).to_have_value("8")
    assert errors == []


def test_unsynced_extra_of_a_piece_deleted_elsewhere_needs_attention(phone, make_context, tag):
    ctx, page, errors = phone
    _, desk, _ = make_context()
    login(desk)
    item_id = add_piece(desk, f"Short-lived {tag}")
    open_app_and_sync(page)
    go_offline(ctx, page)
    page.goto(f"/app#/piece/s-{item_id}/extra/new")
    page.fill("#f-description", "Spare bulb")
    page.click('button:has-text("Add extra")')
    expect(page.locator(".badge-pending", has_text="Not synced yet")).to_be_visible()
    desk.goto(f"/items/{item_id}/delete")
    desk.click('button:has-text("Delete permanently")')
    back_online(ctx, page)
    expect(page.locator("#sync-pill")).to_contain_text("1 need attention", timeout=30_000)
    page.goto("/app#/attention")
    card = page.locator(".card", has_text="Spare bulb")
    expect(card).to_contain_text("a deleted piece")
    expect(card.locator("a:has-text('Edit to fix')")).to_have_count(0)
    page.on("dialog", lambda d: d.accept())
    card.locator('button:has-text("Discard my changes")').click()
    expect(page.locator("#sync-pill")).to_contain_text("All synced", timeout=30_000)
    expect(page.locator("#sync-pill")).not_to_contain_text("waiting")
    assert [e for e in errors if e.startswith("pageerror")] == []  # the server's 404 for the extra is logged


def test_gateway_error_opens_the_phone_app(phone, tag):
    ctx, page, errors = phone
    open_app_and_sync(page)
    page.goto("/items")
    page.evaluate("navigator.serviceWorker.ready.then(() => true)")
    # the reverse proxy answers for the app while it restarts
    ctx.route("**/items*", lambda route: route.fulfill(status=502, content_type="text/html",
                                                      body="<h1>502 Bad Gateway</h1>"))
    page.goto("/items")
    expect(page.locator("body.offline-app")).to_be_visible()
    expect(page.locator("h1")).to_contain_text("My collection")
    ctx.unroute("**/items*")
    # a real server error is still shown, not hidden behind the phone app
    ctx.route("**/items*", lambda route: route.fulfill(status=500, content_type="text/html",
                                                      body="<h1>Server error</h1>"))
    page.goto("/items")
    expect(page.locator("h1")).to_contain_text("Server error")
    ctx.unroute("**/items*")


def test_service_worker_cache_belongs_to_this_deploy(phone):
    ctx, page, errors = phone
    open_app_and_sync(page)
    build = page.request.get("/sw.js").text().split('var CACHE_VERSION = "', 1)[1].split('"', 1)[0]
    assert build and build != "__BUILD__"
    names = page.evaluate("caches.keys()")
    assert f"dickens-static-{build}" in names
    assert [n for n in names if n.startswith("dickens-static-")] == [f"dickens-static-{build}"]
    cached = page.evaluate(f"caches.open('dickens-static-{build}').then(c => c.keys())"
                           ".then(ks => ks.map(k => new URL(k.url).pathname))")
    assert "/static/offline/sync.mjs" in cached and "/app" in cached
    assert not [p for p in cached if p.startswith("/static/vendor/")]
    assert errors == []


def test_thumbnail_of_a_photo_deleted_elsewhere_is_forgotten(phone, make_context, tag, images):
    ctx, page, errors = phone
    _, desk, _ = make_context()
    login(desk)
    item_id = add_piece(desk, f"Pictured {tag}")
    desk.goto(f"/items/{item_id}")
    desk.set_input_files("#photo", str(images["house-red.jpg"]))
    desk.click('#photos button:has-text("Upload")')
    expect(desk.locator(".flash-success")).to_contain_text("Added 1 photo.")
    photo_id = int(desk.locator("#photos img").first.get_attribute("src").split("/")[2])
    open_app_and_sync(page)
    page.goto(f"/app#/piece/s-{item_id}")
    expect(page.locator(f'img[src="/photos/{photo_id}/thumb"]')).to_be_visible()
    thumb = f"/photos/{photo_id}/thumb"

    def cached():
        return page.evaluate("caches.open('dickens-data-v1').then(c => c.keys())"
                             ".then(ks => ks.map(k => new URL(k.url).pathname))")

    def wait_until(check):
        for _ in range(50):
            if check():
                return
            page.wait_for_timeout(200)
        raise AssertionError(f"cached thumbnails: {cached()}")

    wait_until(lambda: thumb in cached())
    desk.locator(f'#photos .photo[data-photo-id="{photo_id}"] summary').click()
    desk.locator(f'#photos form[action="/photos/{photo_id}/delete"] button').click()
    expect(desk.locator(".flash-success")).to_contain_text("Photo removed.")
    page.click("#sync-pill button")
    expect(page.locator("#sync-pill")).to_contain_text("All synced", timeout=30_000)
    wait_until(lambda: thumb not in cached())
    assert errors == []


DATABASES = "indexedDB.databases().then(d => d.map(x => x.name))"


def test_logout_in_one_tab_stops_another_tab_restoring_the_copy(phone, tag):
    ctx, app_tab, errors = phone
    add_piece(app_tab, f"Lingering {tag}")
    open_app_and_sync(app_tab)
    site_tab = ctx.new_page()
    site_tab.goto("/items")
    site_tab.click("#nav-toggle")
    site_tab.click('button:has-text("Log out")')
    expect(site_tab).to_have_url(re.compile(r"/login"))
    assert site_tab.evaluate(DATABASES) == []
    # the phone app tab, still open, syncs on its timer
    app_tab.click("#sync-pill button")
    expect(app_tab.locator("#sync-pill")).to_contain_text(re.compile("Log in to sync|Copy erased"), timeout=30_000)
    assert app_tab.evaluate(DATABASES) == []  # that sync didn't recreate the phone's copy
    app_tab.goto("/app#/")
    app_tab.reload()
    expect(app_tab.locator("#sync-pill")).to_contain_text("Log in to sync", timeout=30_000)
    expect(app_tab.locator(".offline-card", has_text=f"Lingering {tag}")).to_have_count(0)


def test_phone_app_can_log_out(phone, tag):
    ctx, page, errors = phone
    add_piece(page, f"Logged {tag}")
    open_app_and_sync(page)
    page.click('.this-phone button:has-text("Log out")')
    expect(page).to_have_url(re.compile(r"/login"))
    assert page.evaluate(DATABASES) == []
    assert page.request.get("/api/v1/session").status == 401  # the server session ended too
    assert errors == []


def test_phone_app_can_erase_its_copy_and_stay_logged_in(phone, tag):
    ctx, page, errors = phone
    add_piece(page, f"Erasable {tag}")
    open_app_and_sync(page)
    page.on("dialog", lambda d: d.accept())
    page.click('.this-phone button:has-text("Erase")')
    expect(page.locator("h1")).to_contain_text("Copy erased")
    assert page.evaluate(DATABASES) == []
    assert page.request.get("/api/v1/session").status == 200
    page.click('button:has-text("Download a fresh copy")')
    expect(page.locator("#sync-pill")).to_contain_text("All synced", timeout=30_000)
    expect(page.locator(".offline-card", has_text=f"Erasable {tag}")).to_be_visible()
    assert errors == []


def test_logging_out_offline_erases_nothing(phone, tag):
    ctx, page, errors = phone
    open_app_and_sync(page)
    page.goto("/items")
    page.evaluate("navigator.serviceWorker.ready.then(() => true)")
    messages = []
    page.on("dialog", lambda d: (messages.append(d.message), d.accept()))
    ctx.route("**/logout", lambda route: route.abort())  # the server can't be reached
    page.click("#nav-toggle")
    page.click('button:has-text("Log out")')
    page.wait_for_timeout(500)
    ctx.unroute("**/logout")
    assert messages and "can't log out right now" in messages[0]
    assert "dickens-offline" in page.evaluate(DATABASES)
    assert page.request.get("/api/v1/session").status == 200


def test_an_outdated_security_token_is_refreshed_not_treated_as_logged_out(phone, tag):
    ctx, page, errors = phone
    item_id = add_piece(page, f"Token {tag}", storage_tub="1")
    open_app_and_sync(page)
    refused = []

    def stale_once(route):
        if route.request.method == "PATCH" and not refused:
            refused.append(True)
            route.fulfill(status=403, content_type="application/json",
                          body='{"error": "Security token missing or expired. Reload the app."}')
        else:
            route.continue_()

    page.route(f"**/api/v1/items/{item_id}", stale_once)
    page.goto(f"/app#/edit/s-{item_id}")
    page.fill("#f-storage_tub", "6")
    page.click('button:has-text("Save changes")')
    wait_for_server(page, item_id, "storage_tub", "6")
    assert refused
    expect(page.locator("#sync-pill")).to_contain_text("All synced", timeout=30_000)
    expect(page.locator("#sync-pill")).not_to_contain_text("Log in")


def test_a_proxy_refusal_is_reported_on_the_change_not_as_a_login_problem(phone, tag):
    ctx, page, errors = phone
    item_id = add_piece(page, f"Proxy {tag}", storage_tub="1")
    open_app_and_sync(page)
    page.route(f"**/api/v1/items/{item_id}", lambda route: route.fulfill(
        status=403, content_type="text/html", body="<h1>Forbidden by firewall</h1>")
        if route.request.method == "PATCH" else route.continue_())
    page.goto(f"/app#/edit/s-{item_id}")
    page.fill("#f-storage_tub", "6")
    page.click('button:has-text("Save changes")')
    expect(page.locator("#sync-pill")).to_contain_text("1 need attention", timeout=30_000)
    expect(page.locator("#sync-pill")).not_to_contain_text("Log in")
    page.goto("/app#/attention")
    expect(page.locator(".card", has_text=f"Proxy {tag}")).to_contain_text("HTTP 403")


STORED_PHOTOS = """() => new Promise((resolve, reject) => {
    const req = indexedDB.open('dickens-offline');
    req.onerror = () => reject(req.error);
    req.onsuccess = () => {
        const get = req.result.transaction('photos').objectStore('photos').getAll();
        get.onsuccess = async () => {
            const out = [];
            for (const p of get.result) {
                const bmp = await createImageBitmap(p.blob);
                out.push({ key: p.key, type: p.blob.type, name: p.name, width: bmp.width, height: bmp.height,
                           error: p.error && p.error.message });
            }
            req.result.close();
            resolve(out);
        };
    };
})"""


def test_phone_shrinks_photos_before_storing_them(phone, tag, tmp_path):
    from PIL import Image

    ctx, page, errors = phone
    item_id = add_piece(page, f"Big photo {tag}")
    big = tmp_path / "camera.png"
    Image.new("RGB", (3000, 4000), (120, 60, 50)).save(big)
    open_app_and_sync(page)
    go_offline(ctx, page)
    page.goto(f"/app#/piece/s-{item_id}")
    page.set_input_files('input[aria-label="Choose photos"]', str(big))
    expect(page.locator("figcaption", has_text="Waiting to upload")).to_be_visible()
    stored = page.evaluate(STORED_PHOTOS)
    assert [(p["width"], p["height"], p["type"], p["name"]) for p in stored] == [(1500, 2000, "image/jpeg", "camera.jpg")]
    back_online(ctx, page)
    expect(page.locator("#sync-pill")).to_contain_text("All synced", timeout=30_000)
    assert len({i["id"]: i for i in api_snapshot(page)["items"]}[item_id]["photos"]) == 1
    assert errors == []


def test_a_failed_photo_upload_can_be_tried_again(phone, tag, images):
    ctx, page, errors = phone
    item_id = add_piece(page, f"Retry photo {tag}")
    open_app_and_sync(page)
    refuse = {"on": True}
    page.route(f"**/api/v1/items/{item_id}/photos", lambda route: route.fulfill(
        status=413, content_type="text/html", body="<h1>413 Request Entity Too Large</h1>")
        if refuse["on"] else route.continue_())
    page.goto(f"/app#/piece/s-{item_id}")
    page.set_input_files('input[aria-label="Choose photos"]', str(images["house-red.jpg"]))
    expect(page.locator("#sync-pill")).to_contain_text("1 need attention", timeout=30_000)
    page.goto("/app#/attention")
    expect(page.locator(".card", has_text="Photo for")).to_contain_text("Too large")
    refuse["on"] = False  # e.g. the proxy's limit was raised
    page.click('button:has-text("Try again")')
    expect(page.locator("#sync-pill")).to_contain_text("All synced", timeout=30_000)
    assert len({i["id"]: i for i in api_snapshot(page)["items"]}[item_id]["photos"]) == 1
    assert page.evaluate(STORED_PHOTOS) == []
