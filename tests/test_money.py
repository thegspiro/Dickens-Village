"""Currencies: the pure conversion rules (app/money.py), the Settings page, and how
amounts entered in US or Canadian dollars are shown."""

import datetime as dt
import uuid
from decimal import Decimal

from app import money
from app.money import MoneySettings
from app.validation import parse_item_form, parse_settings_form
from tests.conftest import csrf

USD = MoneySettings("USD", Decimal("1.38"))
CAD = MoneySettings("CAD", Decimal("1.38"))
CAD_NO_RATE = MoneySettings("CAD", None)


# -- pure rules ------------------------------------------------------------------------

def test_parse_rate():
    assert money.parse_rate("1.38") == Decimal("1.38")
    assert money.parse_rate(" 1.365432 ") == Decimal("1.365432")
    for bad in ("", "abc", "0", "-1", "101", "1.1234567", "NaN", "Infinity", None):
        assert money.parse_rate(bad) is None, bad


def test_settings_from_rows_fall_back_safely():
    s = MoneySettings.from_rows({"currency": "CAD", "usd_cad_rate": "1.38", "usd_cad_rate_set_on": "2026-11-20"})
    assert (s.currency, s.usd_cad_rate, s.rate_set_on) == ("CAD", Decimal("1.38"), dt.date(2026, 11, 20))
    s = MoneySettings.from_rows({"currency": "EUR", "usd_cad_rate": "lots", "usd_cad_rate_set_on": "soon"})
    assert (s.currency, s.usd_cad_rate, s.rate_set_on) == ("USD", None, None)
    assert MoneySettings.from_rows({}) == MoneySettings()


def test_convert_rounds_to_the_cent_and_needs_a_rate():
    assert money.convert("45", "USD", "CAD", Decimal("1.38")) == Decimal("62.10")
    assert money.convert("62.10", "CAD", "USD", Decimal("1.38")) == Decimal("45.00")
    assert money.convert("10", "CAD", "USD", Decimal("1.37")) == Decimal("7.30")
    assert money.convert("10", None, "USD", None) == Decimal("10.00")  # no currency means USD
    assert money.convert("10", "CAD", "USD", None) is None
    assert money.convert(None, "USD", "CAD", Decimal("1.38")) is None


def test_show_and_format():
    assert money.show(Decimal("45"), "USD", USD) == {"text": "$45.00", "original": ""}
    assert money.show(Decimal("1234.5"), "USD", CAD) == {"text": "CA$1,703.61", "original": "US$1,234.50"}
    assert money.show(Decimal("62.10"), "CAD", USD) == {"text": "$45.00", "original": "CA$62.10"}
    assert money.show(Decimal("62.10"), "CAD", CAD) == {"text": "CA$62.10", "original": ""}
    # without a rate the amount is shown as entered, never guessed
    assert money.show(Decimal("45"), "USD", CAD_NO_RATE) == {"text": "US$45.00", "original": ""}
    assert money.show(None, "USD", CAD) == {"text": "", "original": ""}


def test_total_keeps_unconvertible_amounts_apart():
    rows = [{"currency": "USD", "v": Decimal("10")}, {"currency": "CAD", "v": Decimal("13.80")},
            {"currency": "USD", "v": None}]
    assert money.total(rows, "v", USD) == {"amount": Decimal("20.00"), "unconverted": {}}
    assert money.total(rows, "v", CAD) == {"amount": Decimal("27.60"), "unconverted": {}}
    assert money.total(rows, "v", MoneySettings("USD", None)) == {
        "amount": Decimal("10.00"), "unconverted": {"CAD": Decimal("13.80")}}


def test_retail_uses_the_published_price_for_the_currency():
    assert money.retail(Decimal("75"), Decimal("105"), USD) == "$75.00"
    assert money.retail(Decimal("75"), Decimal("105"), CAD) == "CA$105.00"
    assert money.retail(Decimal("150"), None, CAD) == "US$150.00"   # no Canadian price: labelled US
    assert money.retail(None, Decimal("20"), USD) == "CA$20.00"
    assert money.retail(None, None, CAD) == ""


def test_forms():
    data, errors = parse_item_form({"category": "building", "name": "Inn", "price_paid": "45"})
    assert errors == {} and data["currency"] == "USD"
    data, _ = parse_item_form({"category": "building", "name": "Inn"}, default_currency="CAD")
    assert data["currency"] == "CAD"
    _, errors = parse_item_form({"category": "building", "name": "Inn", "currency": "EUR"})
    assert "currency" in errors
    data, errors = parse_settings_form({"currency": "CAD", "usd_cad_rate": "1.38"})
    assert errors == {} and data == {"currency": "CAD", "usd_cad_rate": Decimal("1.38")}
    data, errors = parse_settings_form({"currency": "CAD", "usd_cad_rate": ""})
    assert errors == {} and data["usd_cad_rate"] is None
    _, errors = parse_settings_form({"currency": "GBP", "usd_cad_rate": "0"})
    assert set(errors) == {"currency", "usd_cad_rate"}


# -- Settings page -------------------------------------------------------------------------

def save_settings(client, **fields):
    return client.post("/settings", data={"csrf_token": csrf(client), **fields})


def test_settings_page_saves_currency_and_rate(logged_in, fake_repo):
    html = logged_in.get("/settings").get_data(as_text=True)
    assert 'value="USD" checked' in html and 'href="/settings"' in html
    resp = save_settings(logged_in, currency="CAD", usd_cad_rate="1.38")
    assert resp.status_code == 302
    assert fake_repo.settings["currency"] == "CAD" and fake_repo.settings["usd_cad_rate"] == "1.38"
    set_on = fake_repo.settings["usd_cad_rate_set_on"]
    assert dt.date.fromisoformat(set_on) == dt.date.today()
    html = logged_in.get("/settings").get_data(as_text=True)
    assert 'value="CAD" checked' in html and 'value="1.38"' in html and "Current rate 1.38" in html

    # the same rate again keeps its date; a blank rate clears it
    fake_repo.settings["usd_cad_rate_set_on"] = "2026-01-02"
    save_settings(logged_in, currency="USD", usd_cad_rate="1.380")
    assert fake_repo.settings["usd_cad_rate_set_on"] == "2026-01-02" and fake_repo.settings["currency"] == "USD"
    save_settings(logged_in, currency="USD", usd_cad_rate="")
    assert "usd_cad_rate" not in fake_repo.settings and "usd_cad_rate_set_on" not in fake_repo.settings


def test_settings_page_rejects_bad_values(logged_in, fake_repo):
    resp = save_settings(logged_in, currency="EUR", usd_cad_rate="-2")
    assert resp.status_code == 400 and fake_repo.settings == {"currency": "USD"}
    assert logged_in.post("/settings", data={"currency": "CAD"}).status_code == 400  # no CSRF token


def test_settings_need_login(client):
    assert client.get("/settings").status_code == 302


# -- pieces ------------------------------------------------------------------------------

def add_piece(client, **fields):
    data = {"category": "building", "name": "Innkeeper's Caravansary", "csrf_token": csrf(client)}
    data.update(fields)
    return client.post("/items/new", data=data)


def only_item(fake_repo):
    (item,) = fake_repo.items.values()
    return item


def test_new_pieces_default_to_the_site_currency(logged_in, fake_repo):
    save_settings(logged_in, currency="CAD", usd_cad_rate="1.38")
    assert '<option value="CAD" selected>' in logged_in.get("/items/new").get_data(as_text=True)
    add_piece(logged_in, price_paid="70")
    assert only_item(fake_repo)["currency"] == "CAD"


def test_amounts_show_in_the_site_currency_with_what_was_entered(logged_in, fake_repo):
    add_piece(logged_in, price_paid="45", estimated_value="100")
    item = only_item(fake_repo)
    assert item["currency"] == "USD"
    html = logged_in.get(f"/items/{item['id']}").get_data(as_text=True)
    assert "<dd>$45.00</dd>" in html

    save_settings(logged_in, currency="CAD", usd_cad_rate="1.38")
    html = logged_in.get(f"/items/{item['id']}").get_data(as_text=True)
    assert 'CA$62.10 <span class="muted">(entered as US$45.00)</span>' in html
    assert "CA$138.00" in logged_in.get("/items").get_data(as_text=True)
    # the saved amount itself never changes
    assert fake_repo.items[item["id"]]["price_paid"] == Decimal("45.00")

    save_settings(logged_in, currency="CAD", usd_cad_rate="")  # no rate: shown as entered
    assert "<dd>US$45.00</dd>" in logged_in.get(f"/items/{item['id']}").get_data(as_text=True)


def test_editing_without_a_currency_field_keeps_the_currency(logged_in, fake_repo):
    add_piece(logged_in, price_paid="70", currency="CAD")
    item = only_item(fake_repo)
    logged_in.post(f"/items/{item['id']}/edit", data={"category": "building", "name": "Inn", "price_paid": "70",
                                                      "csrf_token": csrf(logged_in)})
    assert fake_repo.items[item["id"]]["currency"] == "CAD"


def test_summary_totals_convert_or_keep_amounts_apart(logged_in, fake_repo):
    add_piece(logged_in, name="A", price_paid="10", estimated_value="20")
    add_piece(logged_in, name="B", price_paid="13.80", currency="CAD")
    html = logged_in.get("/summary").get_data(as_text=True)
    assert '$10.00 <span class="muted">+ $13.80 CAD</span>' in html  # no rate yet
    save_settings(logged_in, currency="USD", usd_cad_rate="1.38")
    html = logged_in.get("/summary").get_data(as_text=True)
    assert '<span class="stat-value">$20.00</span><span class="stat-label">Total paid</span>' in html
    assert "converting at 1 USD = 1.38 CAD" in html
    save_settings(logged_in, currency="CAD", usd_cad_rate="1.38")
    html = logged_in.get("/summary").get_data(as_text=True)
    assert '<span class="stat-value">CA$27.60</span><span class="stat-label">Total paid</span>' in html


def test_catalog_list_shows_retail_in_the_site_currency(logged_in, fake_repo):
    fake_repo.add_catalog(list_name="holy-land-lighted", village="holy-land", item_number="56.59757",
                          name="Little Town Of Bethlehem", srp_usd=Decimal("150"), srp_cad=None)
    fake_repo.add_catalog(list_name="holy-land-lighted", village="holy-land", item_number="56.59795",
                          name="Innkeeper's Caravansary", srp_usd=Decimal("70"), srp_cad=Decimal("112"))
    logged_in.post("/village", data={"village": "holy-land", "csrf_token": csrf(logged_in)})
    save_settings(logged_in, currency="CAD", usd_cad_rate="1.38")
    html = logged_in.get("/catalog?q=56.59").get_data(as_text=True)
    assert "CA$112.00" in html and "US$150.00" in html


# -- phone API -------------------------------------------------------------------------------

def test_api_session_and_create_use_the_site_currency(logged_in, fake_repo):
    save_settings(logged_in, currency="CAD", usd_cad_rate="1.38")
    session = logged_in.get("/api/v1/session").get_json()
    assert session["money"] == {"currency": "CAD", "usd_cad_rate": "1.38"}
    assert session["choices"]["currencies"] == money.CURRENCIES
    headers = {"X-CSRF-Token": session["csrf_token"]}
    resp = logged_in.post("/api/v1/items", headers=headers, json={
        "client_uuid": str(uuid.uuid4()), "fields": {"category": "building", "name": "Old phone", "price_paid": "5"}})
    assert resp.status_code == 201 and resp.get_json()["item"]["currency"] == "CAD"
    item_id = resp.get_json()["item"]["id"]
    # a patch that doesn't touch the currency keeps it, whatever the site currency is now
    save_settings(logged_in, currency="USD", usd_cad_rate="1.38")
    resp = logged_in.patch(f"/api/v1/items/{item_id}", headers=headers,
                           json={"base": {"notes": None}, "changes": {"notes": "boxed"}})
    assert resp.status_code == 200 and resp.get_json()["item"]["currency"] == "CAD"
