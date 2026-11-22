import io

import pytest
from PIL import Image

from app import photos
from tests.conftest import png_bytes


def test_png_with_alpha_becomes_jpeg(tmp_path):
    name = photos.save_photo(png_bytes(), str(tmp_path))
    assert photos.FILENAME_RE.match(name)
    with Image.open(tmp_path / name) as img:
        assert img.format == "JPEG" and img.mode == "RGB"


def test_large_image_is_downscaled(tmp_path):
    name = photos.save_photo(png_bytes((3000, 1500), mode="RGB"), str(tmp_path))
    with Image.open(tmp_path / name) as img:
        assert max(img.size) == photos.MAX_EDGE


def test_exif_is_stripped(tmp_path):
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x010F] = "SecretCameraMaker"
    Image.new("RGB", (20, 20)).save(buf, format="JPEG", exif=exif)
    buf.seek(0)
    name = photos.save_photo(buf, str(tmp_path))
    data = (tmp_path / name).read_bytes()
    assert b"SecretCameraMaker" not in data


def test_rejects_gif_and_garbage(tmp_path):
    buf = io.BytesIO()
    Image.new("RGB", (5, 5)).save(buf, format="GIF")
    buf.seek(0)
    with pytest.raises(photos.PhotoError):
        photos.save_photo(buf, str(tmp_path))
    with pytest.raises(photos.PhotoError):
        photos.save_photo(io.BytesIO(b"not an image"), str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_rejects_truncated_image(tmp_path):
    data = png_bytes((200, 200), mode="RGB").getvalue()
    with pytest.raises(photos.PhotoError):
        photos.save_photo(io.BytesIO(data[: len(data) // 2]), str(tmp_path))
    assert [p for p in tmp_path.iterdir()] == []


def test_delete_refuses_unexpected_names(tmp_path):
    victim = tmp_path / "keep.txt"
    victim.write_text("x")
    photos.delete_photo_file(str(tmp_path), "../keep.txt")
    photos.delete_photo_file(str(tmp_path), "keep.txt")
    assert victim.exists()


def test_clean_original_name():
    assert photos.clean_original_name("C:\\Users\\me\\front.png") == "front.png"
    assert photos.clean_original_name("../../etc/passwd") == "passwd"
    assert photos.clean_original_name("") is None


def test_very_large_jpeg_is_accepted_and_decoded_small(tmp_path, monkeypatch):
    # 9000 x 6000 = 54 MP, like a 48-50 MP phone photo: above the 40 MP full-decode cap
    buf = io.BytesIO()
    Image.new("RGB", (9000, 6000), (90, 120, 160)).save(buf, format="JPEG", quality=60)
    buf.seek(0)
    loaded = {}
    real_open = photos._open_checked

    def spy(stream):
        img = real_open(stream)
        loaded["size"] = img.size
        return img

    monkeypatch.setattr(photos, "_open_checked", spy)
    name = photos.save_photo(buf, str(tmp_path))
    assert max(loaded["size"]) < 9000  # decoded at reduced scale, not full size
    with Image.open(tmp_path / name) as img:
        assert max(img.size) == photos.MAX_EDGE


def test_large_png_still_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(photos, "MAX_PIXELS", 10_000)
    with pytest.raises(photos.PhotoError, match="megapixels"):
        photos.save_photo(png_bytes((200, 200), mode="RGB"), str(tmp_path))


def test_per_file_size_limit(tmp_path):
    with pytest.raises(photos.PhotoError, match="larger than 1 MB"):
        photos.save_photo(io.BytesIO(b"0" * (2 * 1024 * 1024)), str(tmp_path), max_bytes=1024 * 1024)
