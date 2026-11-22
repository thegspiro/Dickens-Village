"""3D scan uploads (glTF 2.0).

Accepted:
  - .glb  binary glTF 2.0 (what Polycam, Scaniverse, Luma and most apps export)
  - .gltf JSON glTF 2.0, only when self-contained (every buffer and image
          embedded as a data: URI), because the file is served on its own

Every upload is checked structurally before it is stored: header magic,
version 2, declared length equal to the real size, a valid JSON chunk with
asset.version 2.x, and no references to external files. The file is then
stored unchanged under a random name. Nothing is executed or rendered on the
server.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import struct
import tempfile
from pathlib import Path
from typing import IO, Any

log = logging.getLogger("dickens.scans")

FILENAME_RE = re.compile(r"^[0-9a-f]{32}\.(glb|gltf)$")
MIME_TYPES = {"glb": "model/gltf-binary", "gltf": "model/gltf+json"}

_GLB_MAGIC = b"glTF"
_CHUNK_JSON = 0x4E4F534A
_CHUNK_BIN = 0x004E4942
_MAX_JSON_BYTES = 64 * 1024 * 1024


class ScanError(ValueError):
    """The upload was rejected; the message is safe to show to the user."""


def _size(stream: IO[bytes]) -> int:
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    return size


def _check_gltf_json(doc: Any) -> None:
    if not isinstance(doc, dict):
        raise ScanError("That file is not a glTF model.")
    asset = doc.get("asset")
    version = str(asset.get("version", "")) if isinstance(asset, dict) else ""
    if not version.startswith("2."):
        raise ScanError("Only glTF 2.0 models are supported (export as GLB / glTF 2.0).")
    for key in ("buffers", "images"):
        entries = doc.get(key, [])
        if not isinstance(entries, list):
            raise ScanError("That glTF model is damaged.")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ScanError("That glTF model is damaged.")
            uri = entry.get("uri")
            if uri is not None and not (isinstance(uri, str) and uri.startswith("data:")):
                raise ScanError(
                    "That model refers to separate files (textures or .bin). "
                    "Export it as a single .glb file instead."
                )


def _check_glb(stream: IO[bytes], size: int) -> None:
    header = stream.read(12)
    if len(header) < 12 or header[:4] != _GLB_MAGIC:
        raise ScanError("That file is not a GLB model.")
    version, length = struct.unpack("<II", header[4:12])
    if version != 2:
        raise ScanError("Only glTF 2.0 models are supported (export as GLB / glTF 2.0).")
    if length != size:
        raise ScanError("That GLB file is incomplete or damaged.")
    chunk_header = stream.read(8)
    if len(chunk_header) < 8:
        raise ScanError("That GLB file is damaged.")
    json_length, json_type = struct.unpack("<II", chunk_header)
    if json_type != _CHUNK_JSON or json_length == 0 or json_length > _MAX_JSON_BYTES or 20 + json_length > size:
        raise ScanError("That GLB file is damaged.")
    try:
        doc = json.loads(stream.read(json_length).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ScanError("That GLB file is damaged.") from None
    _check_gltf_json(doc)
    offset = 20 + json_length
    if offset < size:  # optional BIN chunk
        stream.seek(offset)
        bin_header = stream.read(8)
        if len(bin_header) < 8:
            raise ScanError("That GLB file is damaged.")
        bin_length, bin_type = struct.unpack("<II", bin_header)
        if bin_type != _CHUNK_BIN or offset + 8 + bin_length > size:
            raise ScanError("That GLB file is damaged.")


def _check_gltf(stream: IO[bytes], size: int) -> None:
    if size > _MAX_JSON_BYTES:
        raise ScanError("A .gltf file this large isn't supported; export it as .glb instead.")
    try:
        doc = json.loads(stream.read().decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ScanError("That file is not a glTF model.") from None
    _check_gltf_json(doc)


def detect_kind(stream: IO[bytes], original_name: str | None) -> str:
    start = stream.read(4)
    stream.seek(0)
    if start == _GLB_MAGIC:
        return "glb"
    if (original_name or "").lower().endswith(".gltf"):
        return "gltf"
    raise ScanError("Upload a 3D scan as a .glb file (or a self-contained .gltf).")


def save_scan(stream: IO[bytes], original_name: str | None, upload_dir: str, max_bytes: int) -> tuple[str, int]:
    """Validate and store a scan. Returns (stored filename, size in bytes)."""
    size = _size(stream)
    if size == 0:
        raise ScanError("That file is empty.")
    if size > max_bytes:
        raise ScanError(f"That scan is larger than {max_bytes // (1024 * 1024)} MB.")
    kind = detect_kind(stream, original_name)
    if kind == "glb":
        _check_glb(stream, size)
    else:
        _check_gltf(stream, size)
    stream.seek(0)

    directory = Path(upload_dir)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{secrets.token_hex(16)}.{kind}"
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".scan-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(stream, out, length=1024 * 1024)
        os.replace(tmp_path, directory / filename)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
    log.info("Stored scan %s (%d bytes)", filename, size)
    return filename, size


def delete_scan_file(upload_dir: str, filename: str) -> None:
    if not FILENAME_RE.match(filename):
        log.error("Refusing to delete unexpected scan filename %r", filename)
        return
    try:
        (Path(upload_dir) / filename).unlink()
        log.info("Deleted scan file %s", filename)
    except FileNotFoundError:
        log.warning("Scan file already missing: %s", filename)
    except OSError:
        log.exception("Could not delete scan file %s", filename)


def mime_type(filename: str) -> str:
    return MIME_TYPES[filename.rsplit(".", 1)[1]]


def download_name(original_name: str | None, filename: str) -> str:
    """A safe filename for downloads: the uploaded name if it is plain, else a generic one."""
    ext = filename.rsplit(".", 1)[1]
    if original_name:
        stem = re.sub(r"[^A-Za-z0-9 ._-]+", "", original_name.rsplit(".", 1)[0]).strip(" .")[:80]
        if stem:
            return f"{stem}.{ext}"
    return f"scan.{ext}"


def human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{max(size // 1024, 1)} KB"
