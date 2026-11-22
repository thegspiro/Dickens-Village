import datetime as dt
from decimal import Decimal

from app.validation import (
    ITEM_COLUMNS,
    parse_extra_form,
    parse_item_form,
    parse_list_filters,
    parse_positive_int,
)


def valid_form(**overrides):
    form = {"category": "building", "name": "Scrooge & Marley Counting House"}
    form.update(overrides)
    return form


def test_minimal_item_is_valid_and_has_every_column():
    data, errors = parse_item_form(valid_form())
    assert errors == {}
    assert set(data) == set(ITEM_COLUMNS)
    assert data["storage_tub"] is None
    assert data["has_original_box"] is False


def test_name_and_category_required():
    _, errors = parse_item_form({})
    assert "name" in errors and "category" in errors


def test_unknown_category_and_condition_rejected():
    _, errors = parse_item_form(valid_form(category="spaceship", condition_grade="shiny"))
    assert "category" in errors and "condition_grade" in errors


def test_storage_fields_are_trimmed_and_whitespace_collapsed():
    data, errors = parse_item_form(
        valid_form(storage_location="  Basement   shelf 2 ", storage_tub=" 3 ", storage_tray="Top")
    )
    assert errors == {}
    assert (data["storage_location"], data["storage_tub"], data["storage_tray"]) == ("Basement shelf 2", "3", "Top")


def test_text_length_limits():
    _, errors = parse_item_form(valid_form(storage_tub="x" * 61))
    assert "storage_tub" in errors


def test_money_parsing():
    data, errors = parse_item_form(valid_form(price_paid="$1,250.5", estimated_value="80"))
    assert errors == {}
    assert data["price_paid"] == Decimal("1250.50")
    assert data["estimated_value"] == Decimal("80.00")


def test_money_rejects_negative_nan_and_three_decimals():
    for bad in ("-1", "NaN", "Infinity", "1.005", "abc", "100000000"):
        _, errors = parse_item_form(valid_form(price_paid=bad))
        assert "price_paid" in errors, bad


def test_years_validated_and_ordered():
    _, errors = parse_item_form(valid_form(year_introduced="1990", year_retired="1985"))
    assert "year_retired" in errors
    _, errors = parse_item_form(valid_form(year_introduced="85"))
    assert "year_introduced" in errors
    _, errors = parse_item_form(valid_form(year_introduced="1850"))
    assert "year_introduced" in errors
    data, errors = parse_item_form(valid_form(year_introduced="1986", year_retired="1995"))
    assert errors == {} and data["year_introduced"] == 1986


def test_dates_validated():
    future = (dt.date.today() + dt.timedelta(days=2)).isoformat()
    _, errors = parse_item_form(valid_form(purchase_date=future, condition_checked_on="2026-13-01"))
    assert "purchase_date" in errors and "condition_checked_on" in errors
    data, errors = parse_item_form(valid_form(condition_checked_on="2026-11-01"))
    assert errors == {} and data["condition_checked_on"] == dt.date(2026, 11, 1)


def test_checkboxes():
    data, _ = parse_item_form(valid_form(has_original_box="1", on_display="on"))
    assert data["has_original_box"] is True and data["on_display"] is True and data["has_sleeve"] is False


def test_extra_form():
    data, errors = parse_extra_form({"description": "Light cord", "quantity": "2", "condition_grade": "good"})
    assert errors == {} and data == {"description": "Light cord", "quantity": 2, "condition_grade": "good", "notes": None}
    data, errors = parse_extra_form({"description": "Bulb"})
    assert errors == {} and data["quantity"] == 1
    _, errors = parse_extra_form({"description": "", "quantity": "0"})
    assert "description" in errors and "quantity" in errors
    _, errors = parse_extra_form({"description": "x", "quantity": "-3"})
    assert "quantity" in errors


def test_list_filters_drop_invalid_values():
    f = parse_list_filters({"category": "'; DROP TABLE items;--", "sort": "id; DELETE", "page": "-4",
                            "has_extras": "maybe", "storage_tub": " 3 "})
    assert f["category"] is None and f["sort"] == "name" and f["page"] == 1
    assert f["has_extras"] is None and f["storage_tub"] == "3"


def test_parse_positive_int():
    assert parse_positive_int("12") == 12
    for bad in (None, "", "0", "-1", "1.5", "abc", "99999999999"):
        assert parse_positive_int(bad) is None


def test_tomorrow_allowed_for_timezone_slack_but_not_later():
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    _, errors = parse_item_form(valid_form(condition_checked_on=tomorrow))
    assert "condition_checked_on" not in errors
    later = (dt.date.today() + dt.timedelta(days=2)).isoformat()
    _, errors = parse_item_form(valid_form(condition_checked_on=later))
    assert "condition_checked_on" in errors
