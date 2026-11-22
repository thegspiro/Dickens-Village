"""3D scans: file validation, upload, serving, download, delete."""

import io
import struct
from pathlib import Path

import pytest

from app import scans
from tests.conftest import csrf
from tests.gltf_samples import glb_bytes, gltf_bytes

MB = 1024 * 1024


# -- validation ---------------------------------------------------------------------

def test_valid_glb_is_stored_unchanged(tmp_path):
    data = glb_bytes()
    name, size = scans.save_scan(io.BytesIO(data), "mill.glb", str(tmp_path), MB)
    assert scans.FILENAME_RE.match(name) and name.endswith(".glb") and size == len(data)
    assert (tmp_path / name).read_bytes() == data


def test_self_contained_gltf_accepted(tmp_path):
    name, _ = scans.save_scan(io.BytesIO(gltf_bytes()), "mill.gltf", str(tmp_path), MB)
    assert name.endswith(".gltf")


@pytest.mark.parametrize("data, name, message", [
    (b"", "x.glb", "empty"),
    (b"PK\x03\x04 not a model", "x.glb", ".glb file"),
    (glb_bytes(version=1), "x.glb", "glTF 2.0"),
    (glb_bytes()[:-4], "x.glb", "incomplete"),
    (glb_bytes({"asset": {"version": "1.0"}}), "x.glb", "glTF 2.0"),
    (glb_bytes({"images": [{"uri": "texture.png"}]}), "x.glb", "separate files"),
    (gltf_bytes(embedded=False), "x.gltf", "separate files"),
    (b"{not json", "x.gltf", "not a glTF"),
    (b"glTF" + struct.pack("<II", 2, 20) + struct.pack("<II", 999, 0x4E4F534A), "x.glb", "damaged"),
])
def test_bad_files_rejected(tmp_path, data, name, message):
    with pytest.raises(scans.ScanError, match=message):
        scans.save_scan(io.BytesIO(data), name, str(tmp_path), MB)
    assert [p for p in tmp_path.iterdir()] == []


def test_size_limit(tmp_path):
    with pytest.raises(scans.ScanError, match="larger than 1 MB"):
        scans.save_scan(io.BytesIO(b"glTF" + b"\0" * (2 * MB)), "big.glb", str(tmp_path), MB)


def test_download_names_are_sanitised():
    assert scans.download_name("Mill House.glb", "a" * 32 + ".glb") == "Mill House.glb"
    assert scans.download_name('../../etc/"evil";.glb', "a" * 32 + ".glb") == "etcevil.glb"
    assert scans.download_name(None, "a" * 32 + ".gltf") == "scan.gltf"


def test_human_size():
    assert scans.human_size(500) == "1 KB" and scans.human_size(5 * MB + MB // 2) == "5.5 MB"


# -- routes -------------------------------------------------------------------------

def add_piece(client):
    resp = client.post("/items/new", data={"category": "building", "name": "Mill", "csrf_token": csrf(client)})
    return int(resp.headers["Location"].rsplit("/", 1)[1])


def upload(client, item_id, *files):
    return client.post(f"/items/{item_id}/scans", data={"scan": list(files), "csrf_token": csrf(client)},
                       content_type="multipart/form-data", follow_redirects=True)


def test_upload_view_download_delete(logged_in, fake_repo, app):
    item_id = add_piece(logged_in)
    resp = upload(logged_in, item_id, (io.BytesIO(glb_bytes()), "Mill House.glb"))
    body = resp.get_data(as_text=True)
    assert "Added 1 3D scan." in body and "Mill House.glb" in body
    (scan,) = fake_repo.scans.values()
    stored = Path(app.config["DICKENS"].upload_dir) / scan["filename"]
    assert stored.is_file()

    served = logged_in.get(f"/scans/{scan['id']}")
    assert served.status_code == 200 and served.mimetype == "model/gltf-binary"
    assert served.headers["X-Content-Type-Options"] == "nosniff"
    assert served.data[:4] == b"glTF"
    served.close()
    download = logged_in.get(f"/scans/{scan['id']}/download")
    assert 'attachment; filename="Mill House.glb"' in download.headers["Content-Disposition"]
    download.close()

    resp = logged_in.post(f"/scans/{scan['id']}/delete", data={"csrf_token": csrf(logged_in)})
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/items/{item_id}#scans")
    assert not stored.exists() and fake_repo.scans == {}


def test_viewer_only_when_installed_and_csp_relaxed_only_there(logged_in, fake_repo, app):
    item_id = add_piece(logged_in)
    page = logged_in.get(f"/items/{item_id}")
    assert "wasm-unsafe-eval" not in page.headers["Content-Security-Policy"]
    upload(logged_in, item_id, (io.BytesIO(glb_bytes()), "m.glb"))

    app.config["MODEL_VIEWER_AVAILABLE"] = False
    page = logged_in.get(f"/items/{item_id}")
    body = page.get_data(as_text=True)
    assert "<model-viewer" not in body and "viewer isn't installed" in body
    assert "wasm-unsafe-eval" not in page.headers["Content-Security-Policy"]

    app.config["MODEL_VIEWER_AVAILABLE"] = True
    page = logged_in.get(f"/items/{item_id}")
    body = page.get_data(as_text=True)
    csp = page.headers["Content-Security-Policy"]
    assert "<model-viewer" in body and "vendor/model-viewer/model-viewer.min.js" in body
    assert "script-src 'self' 'wasm-unsafe-eval'" in csp and "worker-src 'self' blob:" in csp
    assert "style-src 'self' 'sha256-" in csp  # only the viewer's own fixed stylesheet
    assert "unsafe-inline" not in csp
    # other pages keep the strict policy
    assert "wasm-unsafe-eval" not in logged_in.get("/items").headers["Content-Security-Policy"]


def test_bad_scan_reported_and_good_one_kept(logged_in, fake_repo):
    item_id = add_piece(logged_in)
    resp = upload(logged_in, item_id, (io.BytesIO(b"not a model"), "notes.glb"), (io.BytesIO(glb_bytes()), "ok.glb"))
    body = resp.get_data(as_text=True)
    assert "notes.glb: Upload a 3D scan as a .glb file" in body and "Added 1 3D scan." in body
    assert len(fake_repo.scans) == 1


def test_scan_routes_need_login_and_csrf(client, logged_in, fake_repo):
    item_id = add_piece(logged_in)
    resp = logged_in.post(f"/items/{item_id}/scans", data={"scan": (io.BytesIO(glb_bytes()), "m.glb")},
                          content_type="multipart/form-data")
    assert resp.status_code == 400 and fake_repo.scans == {}
    logged_in.post("/logout", data={"csrf_token": csrf(logged_in)})
    for path in ("/scans/1", "/scans/1/download"):
        assert logged_in.get(path).status_code == 302
    assert upload_missing(logged_in) == 302


def upload_missing(client):
    return client.post("/items/1/scans", data={}).status_code


def test_deleting_piece_removes_scan_files(logged_in, fake_repo, app):
    item_id = add_piece(logged_in)
    upload(logged_in, item_id, (io.BytesIO(glb_bytes()), "m.glb"))
    (scan,) = fake_repo.scans.values()
    stored = Path(app.config["DICKENS"].upload_dir) / scan["filename"]
    logged_in.post(f"/items/{item_id}/delete", data={"csrf_token": csrf(logged_in)})
    assert not stored.exists()


def test_snapshot_lists_scans(logged_in, fake_repo):
    item_id = add_piece(logged_in)
    upload(logged_in, item_id, (io.BytesIO(glb_bytes()), "m.glb"))
    (item,) = logged_in.get("/api/v1/snapshot").get_json()["items"]
    assert item["scans"] == [{"id": next(iter(fake_repo.scans))}]
