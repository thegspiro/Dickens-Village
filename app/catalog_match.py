"""Match photo filenames to catalog entries by item number, for bulk uploads.

A filename must start with the item number, in any of its usual spellings:
    56.65005.jpg   56-65005.jpg   5665005.jpg   56 65005.jpg   65005.jpg (short for 56.65005)
optionally followed by a photo number or words:
    56.65005-2.jpg   5665005_3.jpg   65005 (2).jpg   56.65005 front.jpg
Names that don't start with a number (IMG_1234.jpg) never match. A number
several entries share (a set and its pieces often do) is returned with all of
them, for the person to choose; nothing is guessed.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Iterable

_SPLIT = re.compile(r"[\s_(),]+")
_INDEX = re.compile(r"-\d{1,2}$")  # "56.65005-2": the photo number, not part of the item number
_SHORT_PREFIX = "56"  # Dickens' Village numbers are 56.xxxxx; "65005" alone means 56.65005


def normalise(number: str | None) -> str:
    """Letters and digits only, lower case: '56.65005' -> '5665005', '6000123FE' -> '6000123fe'."""
    return re.sub(r"[^0-9a-z]", "", (number or "").lower())


def filename_key(filename: str) -> str | None:
    """The normalised item number a filename starts with, or None."""
    stem = PurePosixPath(filename.replace("\\", "/")).name
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]  # the extension (a number's own dot stays: 56.65005.jpg -> 56.65005)
    tokens = [t for t in _SPLIT.split(stem.strip()) if t]
    if not tokens or not tokens[0][0].isdigit():
        return None
    token = tokens[0]
    # "56 65005": a two-digit prefix written apart from the rest
    if len(token) == 2 and token.isdigit() and len(tokens) > 1 and tokens[1].isdigit() and len(tokens[1]) >= 4:
        token += tokens[1]
    token = _INDEX.sub("", token)
    key = normalise(token)
    return key if len(key) >= 4 else None


def index_entries(entries: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Catalog entries by normalised item number, also under the 5-digit short form of 56.xxxxx."""
    index: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        key = normalise(entry.get("item_number"))
        if not key:
            continue
        index.setdefault(key, []).append(entry)
        if key.startswith(_SHORT_PREFIX) and len(key) == 7 and key.isdigit():
            index.setdefault("short:" + key[2:], []).append(entry)
    return index


def match(filename: str, index: dict[str, list[dict[str, Any]]]) -> tuple[str | None, list[dict[str, Any]]]:
    """(key, matching entries) for one filename; entries is empty when nothing matches."""
    key = filename_key(filename)
    if key is None:
        return None, []
    found = index.get(key)
    if not found and len(key) == 5 and key.isdigit():
        found = index.get("short:" + key)
    return key, list(found or [])
