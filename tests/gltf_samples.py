"""Build small, valid glTF 2.0 files for tests (a coloured triangle)."""

from __future__ import annotations

import base64
import json
import struct


def _triangle() -> tuple[dict, bytes]:
    positions = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    doc = {
        "asset": {"version": "2.0", "generator": "dickens-village tests"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.6, 0.15, 0.15, 1.0]}, "doubleSided": True}],
        "buffers": [{"byteLength": len(positions)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions)}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
                       "min": [0, 0, 0], "max": [1, 1, 0]}],
    }
    return doc, positions


def glb_bytes(doc_override: dict | None = None, version: int = 2) -> bytes:
    doc, binary = _triangle()
    if doc_override:
        doc.update(doc_override)
    json_chunk = json.dumps(doc).encode()
    json_chunk += b" " * (-len(json_chunk) % 4)
    bin_chunk = binary + b"\0" * (-len(binary) % 4)
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    return (b"glTF" + struct.pack("<II", version, total)
            + struct.pack("<II", len(json_chunk), 0x4E4F534A) + json_chunk
            + struct.pack("<II", len(bin_chunk), 0x004E4942) + bin_chunk)


def gltf_bytes(embedded: bool = True) -> bytes:
    doc, binary = _triangle()
    uri = "data:application/octet-stream;base64," + base64.b64encode(binary).decode() if embedded else "mesh.bin"
    doc["buffers"][0]["uri"] = uri
    return json.dumps(doc).encode()
