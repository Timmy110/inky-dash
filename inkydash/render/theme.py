"""Palette roles → Spectra 6 colours, canvases and fonts (DESIGN.md §7.3).

Dashboards are drawn in mode "P" with the 6 sequential indices below. The driver passes a
P image with exactly 6 palette colours through without dithering, and remaps the sequential
indices to the panel's raw values itself. Never use the driver constants (inky.BLUE == 5,
inky.GREEN == 6) as indices here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageFont

BLACK, WHITE, YELLOW, RED, BLUE, GREEN = range(6)
TRANSPARENT = 6  # overlays only
NAMES = ("black", "white", "yellow", "red", "blue", "green")

# Pure colours: what the driver's DESATURATED_PALETTE holds, and what P images must use.
PURE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0), (255, 255, 255), (255, 255, 0), (255, 0, 0), (0, 0, 255), (0, 255, 0),
)
# The panel's measured look (driver SATURATED_PALETTE). Used for dithering photos at
# saturation 1.0 and for the on-PC preview.
SATURATED: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0), (161, 164, 165), (208, 190, 71), (156, 72, 75), (61, 59, 94), (58, 91, 70),
)
_OVERLAY_KEY = (255, 0, 255)

PALETTE_BYTES = bytes(c for rgb in PURE for c in rgb)
OVERLAY_PALETTE_BYTES = PALETTE_BYTES + bytes(_OVERLAY_KEY)

FONT_DIR = Path(__file__).with_name("fonts")
FONT_FILES = {
    "ui": "AtkinsonHyperlegible-Regular.ttf",
    "bold": "AtkinsonHyperlegible-Bold.ttf",
    "num": "BarlowCondensed-SemiBold.ttf",
}


def blended_palette(saturation: float) -> list[int]:
    """Same blend as the driver's _palette_blend(): SATURATED*s + PURE*(1-s)."""
    s = max(0.0, min(1.0, float(saturation)))
    out: list[int] = []
    for sat, pure in zip(SATURATED, PURE, strict=True):
        out += [int(a * s + b * (1.0 - s)) for a, b in zip(sat, pure, strict=True)]
    return out


def new_canvas(size: tuple[int, int], fill: int = WHITE) -> Image.Image:
    img = Image.new("P", size, fill)
    img.putpalette(PALETTE_BYTES)
    return img


def new_overlay(size: tuple[int, int]) -> Image.Image:
    img = Image.new("P", size, TRANSPARENT)
    img.putpalette(OVERLAY_PALETTE_BYTES)
    return img


@lru_cache(maxsize=128)
def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONT_DIR / FONT_FILES[kind]
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:  # fonts missing from a broken install: still render something
        return ImageFont.load_default(size)


@dataclass(frozen=True)
class Theme:
    """Roles map meaning to the 6 colours."""

    bg: int = WHITE
    fg: int = BLACK
    accent: int = RED        # alerts, warnings, delays
    highlight: int = YELLOW  # best option, sun: needs black outline or yellow background
    info: int = BLUE         # rain, destination
    ok: int = GREEN          # start marker, "normal"
    muted: int = BLACK       # no grey on this panel: secondary text uses smaller sizes instead
    fonts: dict[str, str] = field(default_factory=lambda: dict(FONT_FILES))

    def font(self, kind: str, size: int) -> ImageFont.FreeTypeFont:
        return font(kind, size)
