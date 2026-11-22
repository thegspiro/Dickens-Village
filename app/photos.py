"""Photo upload handling.

Uploads are never stored as sent. Each file is decoded with Pillow, which
rejects anything that is not a real JPEG/PNG/WebP image. It is then rotated
upright, resized, and re-encoded as a new JPEG, so metadata such as GPS
location in EXIF is dropped. Stored filenames are random and never derived
from user input.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import tempfile
import warnings
from pathlib import Path
from typing import IO

from PIL import Image, ImageOps, UnidentifiedImageError

log = logging.getLogger("dickens.photos")

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_PIXELS = 40_000_000
MAX_EDGE = 2000
JPEG_QUALITY = 85
THUMB_EDGE = 320
FILENAME_RE = re.compile(r"^[0-9a-f]{32}\.jpg$")


class PhotoError(ValueError):
    """The upload was rejected; the message is safe to show to the user."""


def _open_checked(stream: IO[bytes]) -> Image.Image:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            probe = Image.open(stream)
            if probe.format not in ALLOWED_FORMATS:
                raise PhotoError("Upload a JPEG, PNG or WebP image.")
            if probe.width * probe.height > MAX_PIXELS:
                raise PhotoError("That image is too large (over 40 megapixels).")
            probe.verify()
            stream.seek(0)
            image = Image.open(stream)
            image.load()
            return image
        except PhotoError:
            raise
        except (UnidentifiedImageError, Image.DecompressionBombWarning, Image.DecompressionBombError):
            raise PhotoError("That file is not a supported image.") from None
        except (OSError, SyntaxError, ValueError):
            raise PhotoError("That image file appears to be damaged.") from None


def thumb_name(filename: str) -> str:
    """Name of the small preview stored next to a photo (abc.jpg -> abc.thumb.jpg)."""
    return filename[: -len(".jpg")] + ".thumb.jpg"


def _write_jpeg(image: Image.Image, path: Path) -> None:
    # Write to a temp file in the same directory, then rename, so a crash
    # never leaves a half-written photo under its final name.
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".upload-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as out:
            image.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def save_photo(stream: IO[bytes], upload_dir: str) -> str:
    """Validate and store an uploaded image plus a small thumbnail.

    Returns the stored filename; the thumbnail is thumb_name(filename).
    """
    image = _open_checked(stream)
    thumb = None
    try:
        image = ImageOps.exif_transpose(image)
        if image.mode not in ("RGB", "L"):
            background = Image.new("RGB", image.size, (255, 255, 255))
            rgba = image.convert("RGBA")
            background.paste(rgba, mask=rgba.split()[-1])
            image = background
        image.thumbnail((MAX_EDGE, MAX_EDGE))
        thumb = image.copy()
        thumb.thumbnail((THUMB_EDGE, THUMB_EDGE))

        directory = Path(upload_dir)
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"{secrets.token_hex(16)}.jpg"
        _write_jpeg(thumb, directory / thumb_name(filename))
        try:
            _write_jpeg(image, directory / filename)
        except Exception:
            _unlink_quietly(directory / thumb_name(filename))
            raise
    finally:
        image.close()
        if thumb is not None:
            thumb.close()
    log.info("Stored photo %s", filename)
    return filename


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        log.exception("Could not delete %s", path.name)


def delete_photo_file(upload_dir: str, filename: str) -> None:
    """Delete a stored photo and its thumbnail (photos from before thumbnails have none)."""
    if not FILENAME_RE.match(filename):
        log.error("Refusing to delete unexpected photo filename %r", filename)
        return
    path = Path(upload_dir) / filename
    try:
        path.unlink()
        log.info("Deleted photo file %s", filename)
    except FileNotFoundError:
        log.warning("Photo file already missing: %s", filename)
    except OSError:
        log.exception("Could not delete photo file %s", filename)
    _unlink_quietly(Path(upload_dir) / thumb_name(filename))


def clean_original_name(name: str | None) -> str | None:
    if not name:
        return None
    base = os.path.basename(name.replace("\\", "/")).strip()
    base = "".join(ch for ch in base if ch.isprintable())
    return base[:255] or None
