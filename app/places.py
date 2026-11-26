"""Storage places as a tree: paths, labels, ordering and checks.

Pure functions over the rows of the storage_places table (no Flask, no
database), so the rules can be tested on their own and shared by the website,
the phone API and the exports.

A place has an optional type (kind). The type is shown before the name
unless the name already starts with it: kind "Drawer" + name "3" reads
"Drawer 3", and kind "Drawer" + name "Drawer 3" also reads "Drawer 3".
Places with a name of their own ("Garage", "Hall closet") usually have no
type.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Iterator

SEPARATOR = " › "
MAX_DEPTH = 12

KIND_SUGGESTIONS = ("Room", "Cabinet", "Shelf", "Drawer", "Spot", "Tub", "Tray", "Box", "Bin", "Table")


def label(place: dict[str, Any]) -> str:
    """How one place reads on its own: "Tub 3", "Garage"."""
    name = str(place.get("name") or "")
    kind = str(place.get("kind") or "").strip()
    if not kind or name.lower().startswith(kind.lower()):
        return name
    return f"{kind} {name}"


def _natural_key(text: str) -> list[Any]:
    """Sort "Tub 2" before "Tub 10"."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


class PlaceTree:
    """All storage places, indexed for path and subtree lookups."""

    def __init__(self, rows: Iterable[dict[str, Any]]) -> None:
        self.by_id: dict[int, dict[str, Any]] = {int(r["id"]): dict(r) for r in rows}
        self.children: dict[int | None, list[int]] = {}
        for place_id, row in self.by_id.items():
            parent = row.get("parent_id")
            parent = int(parent) if parent is not None and int(parent) in self.by_id else None
            self.children.setdefault(parent, []).append(place_id)
        for ids in self.children.values():
            ids.sort(key=lambda i: (_natural_key(label(self.by_id[i])), i))

    def __contains__(self, place_id: object) -> bool:
        return place_id in self.by_id

    def __len__(self) -> int:
        return len(self.by_id)

    def get(self, place_id: int | None) -> dict[str, Any] | None:
        return self.by_id.get(place_id) if place_id is not None else None

    def path(self, place_id: int | None) -> list[dict[str, Any]]:
        """The places from the top level down to this one (empty if unknown)."""
        out: list[dict[str, Any]] = []
        seen: set[int] = set()
        current = place_id
        while current is not None and current in self.by_id and current not in seen:
            seen.add(current)
            row = self.by_id[current]
            out.append(row)
            parent = row.get("parent_id")
            current = int(parent) if parent is not None else None
        out.reverse()
        return out

    def path_text(self, place_id: int | None, start: int = 0) -> str:
        """ "Garage › Cabinet 2 › Drawer 3"; start skips that many top levels. """
        return SEPARATOR.join(label(p) for p in self.path(place_id)[start:])

    def depth(self, place_id: int) -> int:
        """1 for a top-level place."""
        return len(self.path(place_id))

    def legacy(self, place_id: int | None) -> tuple[str | None, str | None, str | None]:
        """The first three levels' names, for the Location / Tub / Tray columns."""
        names = [str(p["name"]) for p in self.path(place_id)[:3]]
        names += [None] * (3 - len(names))  # type: ignore[list-item]
        return names[0], names[1], names[2]

    def subtree_height(self, place_id: int) -> int:
        """Levels from this place down to its deepest descendant (1 with no children)."""
        best = 1
        stack = [(place_id, 1)]
        while stack:
            current, level = stack.pop()
            best = max(best, level)
            stack.extend((child, level + 1) for child in self.children.get(current, []))
        return best

    def descendants(self, place_id: int) -> list[int]:
        """This place and every place inside it, top-down."""
        out: list[int] = []
        stack = [place_id]
        while stack:
            current = stack.pop()
            if current in out:
                continue
            out.append(current)
            stack.extend(reversed(self.children.get(current, [])))
        return out

    def walk(self, place_id: int | None = None, depth: int = 0) -> Iterator[tuple[dict[str, Any], int]]:
        """Every place under place_id (the whole tree for None) with its depth, in display order."""
        for child in self.children.get(place_id, []):
            yield self.by_id[child], depth
            yield from self.walk(child, depth + 1)

    def options(self, display: bool | None = None) -> list[tuple[int, str]]:
        """(id, full path) for a picker, in tree order. display=True or False keeps only
        display places or only storage places; None keeps both."""
        out = []
        for row, _depth in self.walk():
            if display is None or bool(row.get("is_display")) == display:
                out.append((int(row["id"]), self.path_text(int(row["id"]))))
        return out

    def move_problem(self, place_id: int, new_parent_id: int | None) -> str | None:
        """Why place_id can't be put inside new_parent_id, or None if it can."""
        if new_parent_id is None:
            return None
        if new_parent_id not in self.by_id:
            return "That place no longer exists."
        if new_parent_id in self.descendants(place_id):
            return "A place can't go inside itself or one of the places inside it."
        if self.depth(new_parent_id) + self.subtree_height(place_id) > MAX_DEPTH:
            return f"Places can be nested at most {MAX_DEPTH} levels deep."
        return None


def legacy_path(location: str | None, tub: str | None, tray: str | None) -> list[tuple[str, str | None]]:
    """(name, kind) for each level of an old Location / Tub / Tray value, skipping blanks.

    The same rule as migration 0009, so a value sent by an older phone app
    lands in the place the migration created for it.
    """
    out: list[tuple[str, str | None]] = []
    if location:
        out.append((location, None))
    if tub:
        out.append((tub, "Tub"))
    if tray:
        out.append((tray, "Tray"))
    return out
