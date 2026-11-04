"""The Ribbon logo: the approved geometry, and the assets drawn from it."""
import importlib.util
import math
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

from voisr import logo_wave

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SCRIPT = ROOT / "scripts" / "make_icon.py"
# Physical size of each file iconutil unpacks from the .icns.
ICNS_REPS = {"icon_16x16": 16, "icon_16x16@2x": 32, "icon_32x32": 32,
             "icon_32x32@2x": 64, "icon_128x128": 128, "icon_128x128@2x": 256,
             "icon_256x256": 256, "icon_256x256@2x": 512,
             "icon_512x512": 512, "icon_512x512@2x": 1024}


@lru_cache(maxsize=1)
def _make_icon():
    pytest.importorskip("PIL")
    spec = importlib.util.spec_from_file_location("make_icon", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=None)
def _rendered(size):
    return _make_icon().render_icon(size)


def _rgb(hex_colour):
    return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))


def test_the_two_curves_are_the_approved_ones():
    assert logo_wave.LARGE == (2.5, 6.5, math.pi / 2)
    assert logo_wave.SMALL == (1.5, 12.0, -math.pi / 2)


def test_the_tile_values_are_the_approved_ones():
    assert logo_wave.TILE_RADIUS == 0.23
    assert logo_wave.GLYPH_SCALE == 0.78
    assert logo_wave.TILE_ANGLE_DEG == 165
    assert logo_wave.TILE_START == "#2A2926"
    assert logo_wave.TILE_END == "#101010"
    assert logo_wave.WAVE_COLOUR == "#B8BAFF"


def test_the_curve_changes_above_34_px():
    assert logo_wave.style_for(34) is logo_wave.SMALL
    assert logo_wave.style_for(35) is logo_wave.LARGE


@pytest.mark.parametrize("style", [logo_wave.LARGE, logo_wave.SMALL])
def test_the_points_follow_the_formula(style):
    points = logo_wave.wave_points(style, samples=40)
    assert len(points) == 41
    assert points[0] == pytest.approx((10, 50))
    assert points[-1] == pytest.approx((90, 50))
    for i, (x, y) in enumerate(points):
        u = i / 40
        assert x == pytest.approx(10 + 80 * u)
        c, _, phi = style
        expected = 50 + 30 * abs(math.sin(math.pi * u)) ** 1.25 * math.sin(
            2 * math.pi * c * u + phi)
        assert y == pytest.approx(expected)
        assert isinstance(y, float), "a negative base gives a complex number"


@pytest.mark.parametrize("size, curve", [
    (16, logo_wave.SMALL), (32, logo_wave.SMALL), (64, logo_wave.LARGE),
    (128, logo_wave.LARGE), (256, logo_wave.LARGE), (512, logo_wave.LARGE),
    (1024, logo_wave.LARGE)])
def test_each_icon_size_gets_its_curve(size, curve):
    tile = _make_icon().tile_size(size)
    assert tile % 2 == 0, "an odd tile would sit on half pixels"
    assert logo_wave.style_for(tile * logo_wave.GLYPH_SCALE) is curve


def test_the_gradient_matches_how_the_board_rendered_it():
    # Read off the board's 250 px tile, as Chrome drew the CSS gradient. The
    # points avoid the wave.
    gradient = _make_icon()._gradient(250)
    for (x, y), want in {(125, 16): (38, 37, 35), (26, 56): (36, 36, 33),
                         (125, 216): (22, 22, 21), (216, 206): (20, 20, 19)}.items():
        assert gradient.getpixel((x, y)) == pytest.approx(want, abs=1)


def test_the_drawn_small_icons_are_the_small_curve_not_a_scaled_one():
    """Scaling one master is the mistake this guards: the 16 and 32 px icons
    would come out as a smaller copy of the large wave, which closes up."""
    icon = _make_icon()
    side = icon.tile_size(32)
    glyph = side * logo_wave.GLYPH_SCALE
    small = icon._wave_mask(side, glyph, logo_wave.SMALL)
    large = icon._wave_mask(side, glyph, logo_wave.LARGE)
    wave = _rendered(32).crop((2, 2, 30, 30)).convert("RGB")
    target = _rgb(logo_wave.WAVE_COLOUR)

    def misses(mask):
        return sum(1 for y in range(side) for x in range(side)
                   if (mask.getpixel((x, y)) > 250)
                   != (wave.getpixel((x, y)) == target))

    assert misses(small) < misses(large)


@pytest.mark.parametrize("size", [16, 32, 64, 256, 1024])
def test_an_icon_is_the_tile_on_transparency(size):
    img = _rendered(size)
    assert img.size == (size, size)
    assert img.mode == "RGBA"
    alpha = img.getchannel("A")
    side = _make_icon().tile_size(size)
    inset = (size - side) // 2
    assert alpha.getpixel((size // 2, size // 2)) == 255
    assert alpha.getpixel((0, 0)) == 0
    # The tile's own corner is cut away by its radius. On the 14 px tile a
    # sliver of the curve still touches that pixel, hence the 1.
    assert alpha.getpixel((inset, inset)) <= 1
    # A shadow reaching the canvas edge would show as a hard cut.
    h = size - 1
    for edge in ((0, 0, size, 1), (0, h, size, size),
                 (0, 0, 1, size), (h, 0, size, size)):
        assert alpha.crop(edge).getextrema()[1] <= 1


@pytest.mark.parametrize("size", [16, 32, 64, 1024])
def test_the_old_orange_is_gone(size):
    """Any opaque pixel here is near-black, periwinkle or a blend of them, so
    red never beats blue by much. The old icon was #F0764B to #DE5530."""
    img = _rendered(size).convert("RGBA")
    colours = img.getcolors(maxcolors=size * size)
    assert colours
    for _count, (r, _g, b, a) in colours:
        if a == 255:
            assert r - b <= 6, f"warm pixel ({r}, {b})"


def test_the_committed_logo_is_what_the_script_draws():
    svg = (ASSETS / "logo.svg").read_text()
    assert svg == _make_icon().logo_svg(), "run scripts/make_icon.sh"
    for value in (logo_wave.TILE_START, logo_wave.TILE_END,
                  logo_wave.WAVE_COLOUR, 'stroke-width="6.5"', 'rx="23"',
                  'stroke-linecap="round"'):
        assert value in svg


def _assert_same_icon(actual, expected, label):
    from PIL import Image, ImageChops, ImageStat
    assert actual.size == expected.size, label

    def on_grey(img):
        # Compared as they'd appear: iconutil doesn't keep the colour hidden
        # under fully transparent pixels, and it doesn't matter.
        grey = Image.new("RGBA", img.size, (128, 128, 128, 255))
        return Image.alpha_composite(grey, img.convert("RGBA")).convert("RGB")

    diff = ImageChops.difference(on_grey(actual), on_grey(expected))
    # Premultiplied storage can shift edge pixels a level or two. A redrawn
    # logo moves far more than this.
    assert max(ImageStat.Stat(diff).mean) < 1.0, f"{label}: run scripts/make_icon.sh"


def test_the_committed_png_is_what_the_script_draws():
    from_script = _rendered(1024)
    pytest.importorskip("PIL")
    from PIL import Image
    with Image.open(ASSETS / "icon_1024.png") as committed:
        _assert_same_icon(committed, from_script, "icon_1024.png")
        assert committed.info.get("icc_profile"), "unlabelled colour can shift"


def test_the_committed_icns_is_what_the_script_draws(tmp_path):
    """This is the file the installer copies into the app, so a change to the
    geometry that forgot to rebuild it would ship the old logo."""
    pytest.importorskip("PIL")
    from PIL import Image
    if shutil.which("iconutil") is None:
        pytest.skip("iconutil is macOS only")
    iconset = tmp_path / "unpacked.iconset"
    subprocess.run(["iconutil", "-c", "iconset", str(ASSETS / "Voisr.icns"),
                    "-o", str(iconset)], check=True)
    for name, size in ICNS_REPS.items():
        with Image.open(iconset / f"{name}.png") as rep:
            _assert_same_icon(rep, _rendered(size), name)
