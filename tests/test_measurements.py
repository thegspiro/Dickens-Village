from decimal import Decimal

import pytest

from app.validation import format_inches, parse_inches, parse_item_form
from tests.conftest import csrf

MEASURE = ("height_in", "width_in", "depth_in", "box_height_in", "box_width_in", "box_depth_in")


@pytest.mark.parametrize("raw, expected", [
    ("7.25", "7.250"), ("7 1/4", "7.250"), ("7-1/4", "7.250"), ("7¼", "7.250"), ("3/4", "0.750"),
    ("7 1/8", "7.125"), ("1/16", "0.063"), ('10"', "10.000"), ("8 in", "8.000"), ("12 inches", "12.000"),
    (".5", "0.500"), ("6.", "6.000"),
])
def test_parse_inches(raw, expected):
    assert parse_inches(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["abc", "1/0", "-3", "1e3", "7..5", "7 1/", "two"])
def test_parse_inches_rejects(raw):
    with pytest.raises(ValueError):
        parse_inches(raw)


def test_blank_is_none():
    assert parse_inches("") is None and parse_inches("  ") is None


def test_format_inches():
    assert [format_inches(v) for v in (Decimal("7.250"), Decimal("10.000"), Decimal("0.125"), None)] == [
        "7.25", "10", "0.125", ""]


def test_item_form_measurements():
    data, errors = parse_item_form({"category": "building", "name": "Mill", "height_in": "8 1/2",
                                    "box_depth_in": "11.75"})
    assert errors == {}
    assert data["height_in"] == Decimal("8.500") and data["box_depth_in"] == Decimal("11.750")
    assert data["width_in"] is None
    _, errors = parse_item_form({"category": "building", "name": "Mill", "height_in": "0", "width_in": "1000",
                                 "depth_in": "tall"})
    assert set(errors) == {"height_in", "width_in", "depth_in"}


def test_measurements_save_show_and_prefill(logged_in, fake_repo):
    resp = logged_in.post("/items/new", data={
        "category": "building", "name": "Blythe Pond Mill House", "height_in": "7 1/8", "width_in": "6.5",
        "depth_in": "5", "box_height_in": "9", "box_width_in": "8", "box_depth_in": "7.25",
        "csrf_token": csrf(logged_in)})
    assert resp.status_code == 302
    item_id = next(iter(fake_repo.items))
    body = logged_in.get(f"/items/{item_id}").get_data(as_text=True)
    assert "7.125″ H × 6.5″ W × 5″ D" in body and "9″ H × 8″ W × 7.25″ D" in body
    edit = logged_in.get(f"/items/{item_id}/edit").get_data(as_text=True)
    assert 'value="7.125"' in edit and 'value="6.5"' in edit


def test_no_size_rows_when_unmeasured(logged_in, fake_repo):
    logged_in.post("/items/new", data={"category": "building", "name": "Plain", "csrf_token": csrf(logged_in)})
    item_id = next(iter(fake_repo.items))
    body = logged_in.get(f"/items/{item_id}").get_data(as_text=True)
    assert "Box size" not in body and "<dt>Size</dt>" not in body


def test_bad_measurement_rerenders(logged_in, fake_repo):
    resp = logged_in.post("/items/new", data={"category": "building", "name": "X", "box_width_in": "wide",
                                              "csrf_token": csrf(logged_in)})
    assert resp.status_code == 400 and "Enter inches like 7.25" in resp.get_data(as_text=True)
    assert fake_repo.items == {}
