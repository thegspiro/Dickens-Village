"""Form parsing and validation. Every value written to the database passes through here."""

from __future__ import annotations

import datetime as dt
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Mapping

CATEGORIES: dict[str, str] = {
    "building": "Building",
    "figure": "Figure / Person",
    "accessory": "Accessory / Decoration",
}

CONDITIONS: dict[str, str] = {
    "mint": "Mint",
    "excellent": "Excellent",
    "good": "Good",
    "fair": "Fair",
    "poor": "Poor",
    "damaged": "Damaged",
}

MIN_YEAR = 1900
MAX_MONEY = Decimal("99999999.99")  # DECIMAL(10, 2)

# column -> max length for free-text item fields
ITEM_TEXT_FIELDS: dict[str, int] = {
    "name": 200,
    "item_number": 32,
    "series": 120,
    "purchased_from": 200,
    "storage_location": 120,
    "storage_tub": 60,
    "storage_tray": 60,
}
ITEM_LONG_TEXT_FIELDS: dict[str, int] = {"condition_notes": 5000, "notes": 10000}
ITEM_BOOL_FIELDS = ("has_original_box", "has_sleeve", "on_display")
MEASUREMENT_FIELDS = ("height_in", "width_in", "depth_in", "box_height_in", "box_width_in", "box_depth_in")
MAX_INCHES = Decimal("999.999")  # DECIMAL(6, 3): keeps eighths and sixteenths of an inch

ITEM_COLUMNS = (
    "category",
    *ITEM_TEXT_FIELDS,
    *ITEM_LONG_TEXT_FIELDS,
    "year_introduced",
    "year_retired",
    "purchase_date",
    "price_paid",
    "estimated_value",
    "condition_grade",
    "condition_checked_on",
    *ITEM_BOOL_FIELDS,
    *MEASUREMENT_FIELDS,
    "catalog_id",
    "storage_place_id",
    "display_place_id",
)

# The old free-text storage columns. Since migration 0009 they hold the first
# three levels of the piece's storage place, kept up to date by the repository;
# the website doesn't edit them. Older phone app versions may still send them.
STORAGE_COLUMNS = ("storage_location", "storage_tub", "storage_tray")
PLACE_FIELDS = ("storage_place_id", "display_place_id")


def _text(form: Mapping[str, str], key: str, max_len: int, errors: dict[str, str]) -> str | None:
    raw = form.get(key, "")
    value = " ".join(raw.split()) if max_len <= 200 else raw.strip()
    if not value:
        return None
    if len(value) > max_len:
        errors[key] = f"Must be {max_len} characters or fewer."
        return None
    return value


def _year(form: Mapping[str, str], key: str, errors: dict[str, str]) -> int | None:
    raw = form.get(key, "").strip()
    if not raw:
        return None
    if not raw.isdigit():
        errors[key] = "Enter a four-digit year."
        return None
    year = int(raw)
    max_year = dt.date.today().year + 1
    if not MIN_YEAR <= year <= max_year:
        errors[key] = f"Year must be between {MIN_YEAR} and {max_year}."
        return None
    return year


def _date(form: Mapping[str, str], key: str, errors: dict[str, str]) -> dt.date | None:
    raw = form.get(key, "").strip()
    if not raw:
        return None
    try:
        value = dt.date.fromisoformat(raw)
    except ValueError:
        errors[key] = "Enter a date as YYYY-MM-DD."
        return None
    # One day of slack: the server's clock (often UTC) can be a day behind the
    # person's own date late in the evening, or ahead of it in the morning.
    if value > dt.date.today() + dt.timedelta(days=1):
        errors[key] = "Date cannot be in the future."
        return None
    if value.year < MIN_YEAR:
        errors[key] = f"Date must be after {MIN_YEAR}."
        return None
    return value


def _money(form: Mapping[str, str], key: str, errors: dict[str, str]) -> Decimal | None:
    raw = form.get(key, "").strip().replace(",", "").lstrip("$")
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        errors[key] = "Enter an amount like 45.00."
        return None
    if not value.is_finite() or value < 0:
        errors[key] = "Amount must be zero or more."
        return None
    if value > MAX_MONEY:
        errors[key] = "Amount is too large."
        return None
    if value.as_tuple().exponent < -2:  # type: ignore[operator]
        errors[key] = "Use at most two decimal places."
        return None
    return value.quantize(Decimal("0.01"))


_FRACTION_RE = re.compile(r"^(?:(\d+)(?:\s+|-))?(\d+)\s*/\s*(\d+)$")
_UNICODE_FRACTIONS = {"¼": " 1/4", "½": " 1/2", "¾": " 3/4", "⅛": " 1/8", "⅜": " 3/8", "⅝": " 5/8", "⅞": " 7/8"}


def parse_inches(raw: str) -> Decimal | None:
    """Parse '7.25', '7 1/4', '7-1/4', '7¼' or '3/4' (optionally ending in in, ", inches).

    Returns None for blank input; raises ValueError for anything else invalid.
    """
    text = raw.strip().lower()
    for suffix in ("inches", "inch", "in.", "in", '"', "”", "''"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    for symbol, replacement in _UNICODE_FRACTIONS.items():
        text = text.replace(symbol, replacement)
    text = text.strip()
    if not text:
        return None
    match = _FRACTION_RE.match(text)
    if match:
        whole, numerator, denominator = match.groups()
        if int(denominator) == 0:
            raise ValueError("zero denominator")
        value = Decimal(whole or 0) + Decimal(numerator) / Decimal(denominator)
    else:
        if not re.fullmatch(r"\d*\.?\d+|\d+\.", text):
            raise ValueError("not a number")
        value = Decimal(text)
    return value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def format_inches(value: Decimal | float | str | None) -> str:
    """7.250 -> '7.25', 10.000 -> '10'; '' for None."""
    if value is None or value == "":
        return ""
    text = f"{Decimal(value):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _inches(form: Mapping[str, str], key: str, errors: dict[str, str]) -> Decimal | None:
    raw = form.get(key, "")
    try:
        value = parse_inches(raw)
    except (ValueError, ArithmeticError):
        errors[key] = "Enter inches like 7.25 or 7 1/4."
        return None
    if value is None:
        return None
    if value <= 0 or value > MAX_INCHES:
        errors[key] = "Must be more than 0 and under 1000 inches."
        return None
    return value


def _choice(
    form: Mapping[str, str], key: str, choices: Mapping[str, str], errors: dict[str, str], required: bool
) -> str | None:
    raw = form.get(key, "").strip()
    if not raw:
        if required:
            errors[key] = "Choose an option."
        return None
    if raw not in choices:
        errors[key] = "Choose one of the listed options."
        return None
    return raw


def _bool(form: Mapping[str, str], key: str) -> bool:
    return form.get(key, "") in {"1", "on", "true", "yes"}


def _place_id(form: Mapping[str, str], key: str, errors: dict[str, str]) -> int | None:
    raw = form.get(key, "").strip()
    if not raw:
        return None
    value = parse_positive_int(raw)
    if value is None:
        errors[key] = "Choose a place from the list."
    return value


def parse_item_form(form: Mapping[str, str], legacy_storage: bool = False) -> tuple[dict[str, Any], dict[str, str]]:
    """Return (clean_data, errors). clean_data has exactly the ITEM_COLUMNS keys.

    The storage text columns are read from the form only with legacy_storage
    (values sent by an older phone app); otherwise they are left blank, and
    the repository fills them in from the storage place.
    """
    errors: dict[str, str] = {}
    data: dict[str, Any] = {
        "category": _choice(form, "category", CATEGORIES, errors, required=True),
    }
    for key, max_len in ITEM_TEXT_FIELDS.items():
        if key in STORAGE_COLUMNS and not legacy_storage:
            data[key] = None
            continue
        data[key] = _text(form, key, max_len, errors)
    for key, max_len in ITEM_LONG_TEXT_FIELDS.items():
        data[key] = _text(form, key, max_len, errors)
    if data["name"] is None and "name" not in errors:
        errors["name"] = "Name is required."

    data["year_introduced"] = _year(form, "year_introduced", errors)
    data["year_retired"] = _year(form, "year_retired", errors)
    if (
        data["year_introduced"] is not None
        and data["year_retired"] is not None
        and data["year_retired"] < data["year_introduced"]
    ):
        errors["year_retired"] = "Retired year cannot be before the year introduced."

    data["purchase_date"] = _date(form, "purchase_date", errors)
    data["condition_checked_on"] = _date(form, "condition_checked_on", errors)
    data["price_paid"] = _money(form, "price_paid", errors)
    data["estimated_value"] = _money(form, "estimated_value", errors)
    data["condition_grade"] = _choice(form, "condition_grade", CONDITIONS, errors, required=False)
    for key in ITEM_BOOL_FIELDS:
        data[key] = _bool(form, key)
    for key in MEASUREMENT_FIELDS:
        data[key] = _inches(form, key, errors)

    data["catalog_id"] = None
    raw_catalog = form.get("catalog_id", "").strip()
    if raw_catalog and form.get("catalog_unlink", "") not in {"1", "on"}:
        data["catalog_id"] = parse_positive_int(raw_catalog)
        if data["catalog_id"] is None:
            errors["catalog_id"] = "The catalog link is not valid; pick the piece again."
    for key in PLACE_FIELDS:
        data[key] = _place_id(form, key, errors)
    return data, errors


def parse_extra_form(form: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    errors: dict[str, str] = {}
    description = _text(form, "description", 200, errors)
    if description is None and "description" not in errors:
        errors["description"] = "Describe the extra piece."

    quantity = 1
    raw_qty = form.get("quantity", "").strip()
    if raw_qty:
        if not raw_qty.isdigit() or not 1 <= int(raw_qty) <= 999:
            errors["quantity"] = "Quantity must be a whole number from 1 to 999."
        else:
            quantity = int(raw_qty)

    data = {
        "description": description,
        "quantity": quantity,
        "condition_grade": _choice(form, "condition_grade", CONDITIONS, errors, required=False),
        "notes": _text(form, "notes", 1000, errors),
        "storage_place_id": _place_id(form, "storage_place_id", errors),
    }
    return data, errors


PLACE_TEXT_FIELDS: dict[str, int] = {"name": 120, "kind": 40}


def parse_place_form(form: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    """A storage place: name (required), type, parent, whether it's a display spot, notes."""
    errors: dict[str, str] = {}
    data: dict[str, Any] = {key: _text(form, key, n, errors) for key, n in PLACE_TEXT_FIELDS.items()}
    if data["name"] is None and "name" not in errors:
        errors["name"] = "Name is required."
    data["notes"] = _text(form, "notes", 1000, errors)
    data["is_display"] = _bool(form, "is_display")
    data["parent_id"] = None
    raw_parent = form.get("parent_id", "").strip()
    if raw_parent:
        data["parent_id"] = parse_positive_int(raw_parent)
        if data["parent_id"] is None:
            errors["parent_id"] = "Choose a place from the list."
    return data, errors


def parse_positive_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    raw = raw.strip()
    if not raw.isdigit():
        return None
    value = int(raw)
    if not 1 <= value <= 4_294_967_295:  # INT UNSIGNED
        return None
    return value


MAX_CAPTION = 300


def parse_caption(raw: str | None) -> tuple[str | None, str | None]:
    """A photo caption: one line, spaces collapsed, at most 300 characters.

    Returns (caption or None for blank, error message or None).
    """
    value = " ".join("".join(ch for ch in (raw or "") if ch.isprintable() or ch.isspace()).split())
    if not value:
        return None, None
    if len(value) > MAX_CAPTION:
        return None, f"Captions must be {MAX_CAPTION} characters or fewer."
    return value, None


def parse_id_list(raw: str | None, limit: int = 1000) -> list[int] | None:
    """A comma-separated list of distinct positive ids ("12,9,30"), or None if malformed."""
    if raw is None or not raw.strip():
        return None
    ids = [parse_positive_int(part) for part in raw.split(",")]
    if any(i is None for i in ids) or len(ids) > limit or len(set(ids)) != len(ids):
        return None
    return [i for i in ids if i is not None]


SORTS: dict[str, str] = {
    "name": "Name",
    "category": "Category",
    "year_introduced": "Year introduced",
    "storage": "Storage place",
    "estimated_value": "Estimated value",
    "updated": "Recently updated",
}


def parse_list_filters(args: Mapping[str, str]) -> dict[str, Any]:
    """Filters for the item list. Unknown or invalid values are dropped, never passed to SQL."""
    def clean(key: str, max_len: int) -> str | None:
        value = " ".join(args.get(key, "").split())
        return value[:max_len] if value else None

    filters: dict[str, Any] = {
        "q": clean("q", 100),
        "category": args.get("category") if args.get("category") in CATEGORIES else None,
        "condition_grade": args.get("condition_grade") if args.get("condition_grade") in CONDITIONS else None,
        "storage_location": clean("storage_location", 120),
        "storage_tub": clean("storage_tub", 60),
        "storage_tray": clean("storage_tray", 60),
        # a storage place: pieces stored in it or in any place inside it
        "place": parse_positive_int(args.get("place")),
        "on_display": args.get("on_display") if args.get("on_display") in {"yes", "no"} else None,
        "has_extras": args.get("has_extras") if args.get("has_extras") in {"yes", "no"} else None,
        "unstored": args.get("unstored") == "yes",
        # exact storage match: storage columns not given must be empty (used by the Storage page)
        "exact_storage": args.get("exact_storage") == "yes",
    }
    filters["sort"] = args.get("sort") if args.get("sort") in SORTS else "name"
    page = parse_positive_int(args.get("page"))
    filters["page"] = page if page and page <= 10_000 else 1
    return filters
