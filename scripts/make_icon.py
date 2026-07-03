"""Render the Voisr app icon master PNG (1024px) from the logo geometry.

macOS icon grid: artwork sits in an ~824px rounded square centered on a
transparent 1024px canvas.
"""
from pathlib import Path

from PIL import Image, ImageDraw

CANVAS = 1024
BOX = 824
INSET = (CANVAS - BOX) // 2
RADIUS = int(BOX * 0.2225)
TOP = (240, 118, 75)      # #F0764B
BOTTOM = (222, 85, 48)    # #DE5530

# logo-space (1024) bar geometry: x, y, w, h — scaled into the 824 box
BARS = [(200, 282, 80, 460), (336, 362, 80, 300), (472, 422, 80, 180),
        (608, 362, 80, 300), (744, 282, 80, 460)]


def main():
    img = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))

    # vertical gradient strip, masked by the rounded square
    grad = Image.new("RGBA", (BOX, BOX))
    gd = ImageDraw.Draw(grad)
    for y in range(BOX):
        t = y / (BOX - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)) + (255,)
        gd.line([(0, y), (BOX, y)], fill=color)
    mask = Image.new("L", (BOX, BOX), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, BOX - 1, BOX - 1], RADIUS, fill=255)
    img.paste(grad, (INSET, INSET), mask)

    draw = ImageDraw.Draw(img)
    s = BOX / 1024
    for x, y, w, h in BARS:
        x0, y0 = INSET + x * s, INSET + y * s
        x1, y1 = x0 + w * s, y0 + h * s
        draw.rounded_rectangle([x0, y0, x1, y1], radius=(w * s) / 2, fill=(255, 255, 255, 255))

    out = Path(__file__).resolve().parent.parent / "assets" / "icon_1024.png"
    img.save(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
