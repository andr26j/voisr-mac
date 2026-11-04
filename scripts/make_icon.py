"""Render the Voisr app icon and logo from the Ribbon geometry in voisr/logo_wave.py.

Every icon size is drawn on its own. Scaling one master down can't work here:
the small sizes use a different curve, not a smaller copy of the large one.

macOS icon grid: from 64 px up, artwork sits in a rounded square about 80% of
the canvas, on transparency. Below that the tile is enlarged a little, because
on the grid a 16 px icon has a 13 px tile and a stroke barely over 1 px.

Usage: make_icon.py <iconset-dir>   writes the iconset, icon_1024.png, logo.svg
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageCms, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voisr import logo_wave as logo  # noqa: E402

ASSETS = ROOT / "assets"
GRID_SHARE = 824 / 1024
SMALL_TILES = {16: 14, 32: 28}
# Base names as iconutil wants them: 1x and @2x of each, in physical pixels.
ICONSET_POINTS = (16, 32, 128, 256, 512)

# The board's tile also carries these, which aren't part of the geometry. A
# near-black tile needs the top highlight to keep an edge on a dark Dock.
SHADOW_MIN_TILE = 40    # the board drops the shadow and lower edge below this
HIGHLIGHT_LARGE = 0.34  # top edge, alpha
LOWLIGHT_LARGE = 0.20   # bottom edge, alpha
HIGHLIGHT_SMALL = 0.40
HIGHLIGHT_PER_TILE = 1 / 250     # 1 px on the board's 250 px tile
HIGHLIGHT_SMALL_PX = 0.5
# The board's shadow (5% offset, 14% blur) would be clipped by the icon's
# margin, so it is pulled in to fade out inside it.
SHADOW_OFFSET = 20 / 824
SHADOW_BLUR = 52 / 824
SHADOW_COLOUR = (10, 8, 5)
SHADOW_ALPHA = 0.35


def _rgb(hex_colour):
    return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))


def tile_size(size):
    """The tile's side in pixels, kept even so its edges land on whole pixels."""
    if size in SMALL_TILES:
        return SMALL_TILES[size]
    return 2 * round(size * GRID_SHARE / 2)


def _gradient(side):
    """The tile's gradient, following the CSS definition of an angled gradient."""
    start, end = _rgb(logo.TILE_START), _rgb(logo.TILE_END)
    angle = math.radians(logo.TILE_ANGLE_DEG)
    dx, dy = math.sin(angle), -math.cos(angle)
    length = side * (abs(dx) + abs(dy))
    img = Image.new("RGB", (side, side))
    px = img.load()
    middle = (side - 1) / 2
    for y in range(side):
        for x in range(side):
            t = ((x - middle) * dx + (y - middle) * dy) / length + 0.5
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(start, end))
    return img


def _supersample(size):
    return max(4, min(16, 8192 // size))


def _wave_mask(side, glyph_px, style):
    """Coverage of the wave on a `side`-square canvas, with its box centred."""
    ss = _supersample(side)
    mask = Image.new("L", (side * ss, side * ss), 0)
    draw = ImageDraw.Draw(mask)
    k = glyph_px * ss / 100
    offset = (side * ss - glyph_px * ss) / 2
    r = style.stroke * k / 2
    # Stamped circles give exact round caps and joins. Dense enough that the
    # gaps between them can't show as scallops at this resolution.
    for x, y in logo.wave_points(style, samples=1500):
        cx, cy = offset + x * k, offset + y * k
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    return mask.resize((side, side), Image.Resampling.BOX)


def _tile_mask_hi(side, ss):
    mask = Image.new("L", (side * ss, side * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, side * ss - 1, side * ss - 1],
        radius=side * ss * logo.TILE_RADIUS, fill=255)
    return mask


def _edge(hi, ss, side, px_thick, direction):
    """The part of the tile within px_thick of its top (-1) or bottom (+1)."""
    shift = max(1, round(px_thick * ss))
    moved = Image.new("L", hi.size, 0)
    moved.paste(hi, (0, -direction * shift))
    edge = ImageChops.subtract(hi, moved)
    return edge.resize((side, side), Image.Resampling.BOX)


def render_icon(size):
    """One icon at `size` physical pixels, as RGBA."""
    side = tile_size(size)
    inset = (size - side) // 2
    ss = _supersample(side)
    hi = _tile_mask_hi(side, ss)
    tile_mask = hi.resize((side, side), Image.Resampling.BOX)

    tile = _gradient(side).convert("RGBA")
    large = side >= SHADOW_MIN_TILE
    thick = side * HIGHLIGHT_PER_TILE if large else HIGHLIGHT_SMALL_PX

    top = _edge(hi, ss, side, thick, -1)
    alpha = HIGHLIGHT_LARGE if large else HIGHLIGHT_SMALL
    white = Image.new("RGBA", (side, side), (255, 255, 255, 255))
    white.putalpha(top.point(lambda v: round(v * alpha)))
    tile.alpha_composite(white)

    if large:
        bottom = _edge(hi, ss, side, thick, 1)
        black = Image.new("RGBA", (side, side), (0, 0, 0, 255))
        black.putalpha(bottom.point(lambda v: round(v * LOWLIGHT_LARGE)))
        tile.alpha_composite(black)

    glyph_px = side * logo.GLYPH_SCALE
    wave = Image.new("RGBA", (side, side), _rgb(logo.WAVE_COLOUR) + (255,))
    wave.putalpha(_wave_mask(side, glyph_px, logo.style_for(glyph_px)))
    tile.alpha_composite(wave)
    tile.putalpha(tile_mask)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if large:
        blur = side * SHADOW_BLUR
        drop = side * SHADOW_OFFSET
        shadow_mask = Image.new("L", (size, size), 0)
        shadow_mask.paste(tile_mask, (inset, inset + round(drop)))
        shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(blur / 2))
        shadow = Image.new("RGBA", (size, size), SHADOW_COLOUR + (255,))
        shadow.putalpha(shadow_mask.point(lambda v: round(v * SHADOW_ALPHA)))
        canvas.alpha_composite(shadow)
    canvas.alpha_composite(tile, (inset, inset))
    return canvas


def logo_svg():
    """The logo as a flat tile filling its viewBox, for use outside the Dock."""
    angle = math.radians(logo.TILE_ANGLE_DEG)
    dx, dy = math.sin(angle), -math.cos(angle)
    half = 100 * (abs(dx) + abs(dy)) / 2
    x1, y1 = 50 - dx * half, 50 - dy * half
    x2, y2 = 50 + dx * half, 50 + dy * half
    margin = 50 * (1 - logo.GLYPH_SCALE)
    points = logo.wave_points(logo.LARGE, samples=160)
    path = "M" + "L".join(f"{x:.2f} {y:.2f}" for x, y in points)
    return f"""<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Voisr logo">
  <defs>
    <linearGradient id="voisr-tile" gradientUnits="userSpaceOnUse" x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}">
      <stop offset="0" stop-color="{logo.TILE_START}"/>
      <stop offset="1" stop-color="{logo.TILE_END}"/>
    </linearGradient>
  </defs>
  <rect width="100" height="100" rx="{100 * logo.TILE_RADIUS:g}" fill="url(#voisr-tile)"/>
  <path transform="translate({margin:g} {margin:g}) scale({logo.GLYPH_SCALE:g})" d="{path}" fill="none" stroke="{logo.WAVE_COLOUR}" stroke-width="{logo.LARGE.stroke:g}" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""


def main(iconset):
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    iconset = Path(iconset)
    icons = {}

    def icon(size):
        if size not in icons:
            icons[size] = render_icon(size)
        return icons[size]

    for points in ICONSET_POINTS:
        name = f"icon_{points}x{points}"
        icon(points).save(iconset / f"{name}.png", icc_profile=srgb)
        icon(points * 2).save(iconset / f"{name}@2x.png", icc_profile=srgb)

    icon(1024).save(ASSETS / "icon_1024.png", icc_profile=srgb)
    (ASSETS / "logo.svg").write_text(logo_svg())
    print(f"wrote {iconset}, assets/icon_1024.png and assets/logo.svg")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: make_icon.py <iconset-dir>")
    main(sys.argv[1])
