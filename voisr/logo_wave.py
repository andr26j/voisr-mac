"""The Ribbon sound wave: the one curve behind every Voisr logo.

The app icon, assets/logo.svg and the menu-bar icon all draw from here, on a
100-unit box:

    y = 50 + 30 * sin(pi*u)^1.25 * sin(2*pi*c*u + phi),  x from 10 to 90

Below a certain size the full wave closes up into a smear, so small sizes use
a different curve rather than a scaled-down copy.

Imports nothing beyond the standard library: the menu-bar code loads this in
the process hosting the push-to-talk event tap, and the icon script loads it
outside the app.
"""
import math
from typing import NamedTuple


class WaveStyle(NamedTuple):
    cycles: float
    stroke: float  # in box units, with round caps
    phase: float


LARGE = WaveStyle(cycles=2.5, stroke=6.5, phase=math.pi / 2)
SMALL = WaveStyle(cycles=1.5, stroke=12.0, phase=-math.pi / 2)

# Measured on the glyph, not the tile, so tiles up to 44 px take the small curve.
SMALL_MAX_PX = 34

X_START, X_END = 10.0, 90.0
CENTRE_Y = 50.0
AMPLITUDE = 30.0
ENVELOPE_POWER = 1.25

TILE_RADIUS = 0.23       # of the tile's side
GLYPH_SCALE = 0.78       # the 100-unit box, as a share of the tile's side
TILE_ANGLE_DEG = 165     # CSS convention: 0 points up, 90 points right
TILE_START = "#2A2926"
TILE_END = "#101010"
WAVE_COLOUR = "#B8BAFF"


def style_for(glyph_px: float) -> WaveStyle:
    # Rounded as the board rounds it. A 44 px tile's glyph is 34.32 px, and
    # taking that literally would give it the large curve.
    return SMALL if round(glyph_px) <= SMALL_MAX_PX else LARGE


def wave_y(u: float, style: WaveStyle) -> float:
    # max(): sin(pi) can land a hair below zero, and a negative base raised to
    # 1.25 gives a complex number rather than an error.
    envelope = max(0.0, math.sin(math.pi * u)) ** ENVELOPE_POWER
    return CENTRE_Y + AMPLITUDE * envelope * math.sin(
        2 * math.pi * style.cycles * u + style.phase)


def wave_points(style: WaveStyle, samples: int = 72) -> list:
    """The curve as (x, y) points in box units, y pointing down."""
    return [(X_START + (X_END - X_START) * (i / samples),
             wave_y(i / samples, style))
            for i in range(samples + 1)]
