"""Settings: the site currency and exchange rate, on the website and the phone app."""

from __future__ import annotations

from playwright.sync_api import expect

from .conftest import add_piece, api_snapshot


def set_currency(page, code, rate):
    page.goto("/settings")
    page.check(f"input[name=currency][value={code}]")
    page.fill("#usd_cad_rate", rate)
    page.click('button:has-text("Save")')
    expect(page.locator(".flash-success")).to_contain_text("Settings saved.")


def test_currency_setting_converts_for_display(desk, make_context, tag):
    ctx, page, errors = desk
    item_id = add_piece(page, f"Priced {tag}", price_paid="45")
    expect(page.locator(".details")).to_contain_text("$45.00")
    try:
        set_currency(page, "CAD", "1.38")
        page.goto(f"/items/{item_id}")
        expect(page.locator(".details")).to_contain_text("CA$62.10 (entered as US$45.00)")
        page.goto("/items/new")
        expect(page.locator("#currency")).to_have_value("CAD")
        cad_id = add_piece(page, f"Canadian {tag}", price_paid="70")
        expect(page.locator(".details")).to_contain_text("CA$70.00")
        items = {i["id"]: i for i in api_snapshot(page)["items"]}
        assert items[item_id]["currency"] == "USD" and items[item_id]["price_paid"] == "45.00"
        assert items[cad_id]["currency"] == "CAD"

        # The phone app labels amounts and offers the site currency for new pieces.
        _, phone, phone_errors = make_context(phone=True)
        from .conftest import login

        login(phone)
        phone.goto("/app")
        expect(phone.locator("#sync-pill")).to_contain_text("All synced", timeout=30_000)
        phone.goto(f"/app#/piece/s-{cad_id}")
        expect(phone.locator(".details")).to_contain_text("CA$70.00")
        phone.goto("/app#/new")
        expect(phone.locator("#f-currency")).to_have_value("CAD")
        assert phone_errors == []
    finally:
        set_currency(page, "USD", "")  # other tests expect US dollars
    assert errors == []
