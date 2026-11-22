"""Serialization and field-level merging for the phone app's offline sync.

Pure functions (no Flask, no database) so the merge rules can be tested on
their own. The phone and the server exchange values in one canonical JSON
form (see to_json_value), so "has this field changed?" is a plain equality
test on both sides.

Merge rule for a change sent from the phone. For each field the phone changed
it sends the value it started from (base) and its new value (mine):
  - server value == base  -> nobody else touched it: apply mine
  - server value == mine  -> already the same: nothing to do
  - otherwise             -> conflict: someone else changed it too
If any field conflicts, nothing in that request is applied and the conflicts
are returned for the person to resolve.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Any, Mapping

from .validation import ITEM_BOOL_FIELDS, ITEM_COLUMNS, MEASUREMENT_FIELDS, format_inches

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

EXTRA_COLUMNS = ("description", "quantity", "condition_grade", "notes")
MONEY_FIELDS = ("price_paid", "estimated_value")


def to_json_value(field: str, value: Any) -> Any:
    """Canonical JSON form of a database value."""
    if value is None:
        return None
    if field in ITEM_BOOL_FIELDS:
        return bool(value)
    if field in MEASUREMENT_FIELDS:
        return format_inches(value)
    if field in MONEY_FIELDS:
        return f"{Decimal(value):.2f}"
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return value


def item_to_json(row: Mapping[str, Any]) -> dict[str, Any]:
    out = {field: to_json_value(field, row.get(field)) for field in ITEM_COLUMNS}
    out.update({
        "id": row["id"],
        "version": int(row.get("version") or 1),
        "client_uuid": row.get("client_uuid"),
        "updated_at": to_json_value("updated_at", row.get("updated_at")),
    })
    return out


def extra_to_json(row: Mapping[str, Any]) -> dict[str, Any]:
    out = {field: to_json_value(field, row.get(field)) for field in EXTRA_COLUMNS}
    out.update({
        "id": row["id"],
        "item_id": row["item_id"],
        "version": int(row.get("version") or 1),
        "client_uuid": row.get("client_uuid"),
    })
    return out


def json_to_form(values: Mapping[str, Any]) -> dict[str, str]:
    """Turn JSON field values into the strings the HTML-form validators expect."""
    form: dict[str, str] = {}
    for key, value in values.items():
        if value is None or value is False:
            form[key] = ""
        elif value is True:
            form[key] = "1"
        else:
            form[key] = str(value)
    return form


class SyncError(ValueError):
    """The request is malformed; the message is safe to return to the client."""


def clean_changes(payload: Any, allowed: tuple[str, ...]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the shape of {"base": {...}, "changes": {...}} and return (base, changes)."""
    if not isinstance(payload, dict):
        raise SyncError("Request body must be a JSON object.")
    base, changes = payload.get("base"), payload.get("changes")
    if not isinstance(base, dict) or not isinstance(changes, dict):
        raise SyncError('Send "base" and "changes" objects.')
    if not changes:
        raise SyncError("No changes were sent.")
    unknown = (set(changes) | set(base)) - set(allowed)
    if unknown:
        raise SyncError(f"Unknown fields: {', '.join(sorted(unknown))}")
    missing = set(changes) - set(base)
    if missing:
        raise SyncError(f"Missing base values for: {', '.join(sorted(missing))}")
    for value in [*changes.values(), *base.values()]:
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise SyncError("Field values must be strings, numbers, booleans or null.")
    return base, changes


def _same(a: Any, b: Any) -> bool:
    # "" and null both mean "empty" in a form field.
    return (a if a != "" else None) == (b if b != "" else None)


def merge(current: Mapping[str, Any], base: Mapping[str, Any],
          changes: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Merge the phone's changes into the server's current JSON values.

    Returns (merged_values, conflicts). merged_values is only meaningful when
    conflicts is empty.
    """
    merged = dict(current)
    conflicts: list[dict[str, Any]] = []
    for field, mine in changes.items():
        theirs = current.get(field)
        if _same(theirs, base.get(field)) or _same(theirs, mine):
            merged[field] = mine
        else:
            conflicts.append({"field": field, "base": base.get(field), "mine": mine, "theirs": theirs})
    return merged, conflicts
