"""Draw the app icons (an original Victorian lamppost in falling snow).

    python scripts/make_icons.py

Writes PNGs to app/static/icons. Pure Pillow; no external artwork is used.
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"
BURGUNDY = (122, 31, 43)
IRON = (38, 32, 30)
GLOW = (255, 214, 120)
SNOW = (250, 247, 240)

SNOWFLAKES = [(0.18, 0.20), (0.80, 0.16), (0.70, 0.36), (0.14, 0.55), (0.86, 0.60), (0.28, 0.80),
              (0.62, 0.12), (0.40, 0.30), (0.90, 0.85), (0.08, 0.35)]


def draw(size: int, safe: float = 1.0, rounded: bool = True) -> Image.Image:
    """safe < 1 shrinks the artwork into the centre (for maskable icons)."""
    scale = 4  # draw large, then downsample for smooth edges
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = int(s * 0.22) if rounded else 0
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=BURGUNDY)

    def p(x: float, y: float) -> tuple[float, float]:
        off = (1 - safe) / 2
        return ((off + x * safe) * s, (off + y * safe) * s)

    for fx, fy in SNOWFLAKES:
        cx, cy = p(fx, fy)
        r = s * 0.018 * safe
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=SNOW)
    # snow bank runs edge to edge even on maskable icons
    _, y0 = p(0.0, 0.84)
    bank = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(bank).ellipse([-s * 0.3, y0, s * 1.3, y0 + s * 0.6], fill=SNOW)
    img.alpha_composite(bank)
    if rounded:  # keep the bank inside the rounded corners
        mask = Image.new("L", (s, s), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=255)
        img.putalpha(mask)
    # soft glow around the lantern
    gx, gy = p(0.5, 0.33)
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for i in range(12, 0, -1):
        gr = s * 0.022 * i * safe
        gd.ellipse([gx - gr, gy - gr, gx + gr, gy + gr], fill=(255, 214, 120, 14))
    img.alpha_composite(glow)
    d = ImageDraw.Draw(img)
    # post
    d.rectangle([*p(0.47, 0.42), *p(0.53, 0.86)], fill=IRON)
    d.rectangle([*p(0.41, 0.80), *p(0.59, 0.86)], fill=IRON)
    # lantern
    d.polygon([p(0.38, 0.24), p(0.62, 0.24), p(0.58, 0.42), p(0.42, 0.42)], fill=IRON)
    d.polygon([p(0.42, 0.27), p(0.58, 0.27), p(0.555, 0.39), p(0.445, 0.39)], fill=GLOW)
    d.polygon([p(0.36, 0.24), p(0.5, 0.15), p(0.64, 0.24)], fill=IRON)
    d.ellipse([*p(0.475, 0.11), *p(0.525, 0.16)], fill=IRON)
    # snow cap on lantern
    d.polygon([p(0.38, 0.215), p(0.5, 0.14), p(0.62, 0.215), p(0.5, 0.18)], fill=SNOW)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    draw(192).save(OUT / "icon-192.png", optimize=True)
    draw(512).save(OUT / "icon-512.png", optimize=True)
    draw(512, safe=0.72, rounded=False).save(OUT / "icon-maskable-512.png", optimize=True)
    draw(180, rounded=False).convert("RGB").save(OUT / "apple-touch-icon.png", optimize=True)
    draw(32).save(OUT / "favicon-32.png", optimize=True)


if __name__ == "__main__":
    main()
