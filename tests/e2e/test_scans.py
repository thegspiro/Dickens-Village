"""3D scans in the browser: upload, view in 3D (when the viewer is installed), download, delete."""

from __future__ import annotations

from playwright.sync_api import expect

from tests.gltf_samples import glb_bytes

from .conftest import add_piece


def test_scan_upload_view_download_delete(desk, tag, tmp_path):
    ctx, page, errors = desk
    scan = tmp_path / "Mill House.glb"
    scan.write_bytes(glb_bytes())
    bogus = tmp_path / "notes.glb"
    bogus.write_bytes(b"this is not a model")
    item_id = add_piece(page, f"Scanned {tag}")

    page.set_input_files("#scan", [str(scan), str(bogus)])
    page.click('button:has-text("Upload scan")')
    expect(page.locator(".flash-success")).to_contain_text("Added 1 3D scan.")
    expect(page.locator(".flash-error")).to_contain_text("notes.glb: Upload a 3D scan as a .glb file")
    figure = page.locator("#scans figure.scan")
    expect(figure).to_have_count(1)
    expect(figure).to_contain_text("Mill House.glb")

    viewer_installed = page.request.get("/static/vendor/model-viewer/model-viewer.min.js").ok
    if viewer_installed:
        viewer = figure.locator("model-viewer")
        # the model really loads and renders under the page's security policy
        expect(viewer).to_have_attribute("data-state", "loaded", timeout=30_000)
        assert page.evaluate("document.querySelector('model-viewer').loaded") is True
        box = viewer.bounding_box()
        assert box["width"] > 200 and box["height"] > 200
        page.screenshot(path=str(tmp_path / "scan.png"))
    else:
        expect(figure).to_contain_text("viewer isn't installed")

    with page.expect_download() as download_info:
        figure.locator("a:has-text('Download')").click()
    download = download_info.value
    assert download.suggested_filename == "Mill House.glb"
    assert open(download.path(), "rb").read() == scan.read_bytes()

    page.on("dialog", lambda d: d.accept())
    figure.locator('button:has-text("Remove")').click()
    expect(page.locator(".flash-success")).to_contain_text("3D scan removed.")
    expect(page.locator("#scans figure.scan")).to_have_count(0)
    assert [e for e in errors if "Content Security Policy" in e or "Refused" in e] == []
    assert errors == []
    assert item_id
