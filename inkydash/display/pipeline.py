"""Frame → panel image (DESIGN.md §7.3). Identical for every backend, so the PNG preview on
the PC shows the same pixels the panel gets."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from PIL import Image

from inkydash.render.theme import PALETTE_BYTES, SATURATED, TRANSPARENT, blended_palette, new_canvas
from inkydash.screens.base import Frame

PANEL_SIZE = (800, 480)


def logical_size(orientation: str) -> tuple[int, int]:
    return PANEL_SIZE if orientation == "landscape" else (PANEL_SIZE[1], PANEL_SIZE[0])


def dither(img: Image.Image, saturation: float = 0.5) -> Image.Image:
    """RGB photo → 6-colour P image, using the driver's maths: Floyd–Steinberg against the
    palette blended by `saturation`. The result uses the theme's pure palette, so the driver
    then passes it through untouched."""
    pal = Image.new("P", (1, 1))
    pal.putpalette(blended_palette(saturation))
    q = img.convert("RGB").quantize(6, palette=pal, dither=Image.Dither.FLOYDSTEINBERG)
    q.putpalette(PALETTE_BYTES)
    return q


def _check_palette_image(img: Image.Image, what: str) -> None:
    lo, hi = img.getextrema()  # type: ignore[misc]
    if hi > 5:
        raise ValueError(f"{what} uses palette index {hi}; only 0..5 are valid")


def compose(frame: Frame, size: tuple[int, int]) -> Image.Image:
    img = frame.image
    if img.size != size:
        raise ValueError(f"frame is {img.size}, expected {size}")
    if img.mode == "P":
        _check_palette_image(img, "frame")
        out = img.copy()
        out.putpalette(PALETTE_BYTES)
    else:
        out = dither(img, frame.saturation)
    if frame.overlay is not None:
        ov = frame.overlay
        if ov.mode != "P" or ov.size != size:
            raise ValueError("overlay must be a P image of the frame size")
        mask = ov.point(lambda i: 0 if i == TRANSPARENT else 255, "L")
        out.paste(ov, (0, 0), mask)
        _check_palette_image(out, "overlay")
    return out


def content_hash(img: Image.Image, volatile: Sequence[tuple[int, int, int, int]] = ()) -> str:
    if volatile:
        img = img.copy()
        for box in volatile:
            img.paste(0, box)
    h = hashlib.sha256()
    h.update(f"{img.mode}{img.size}".encode())
    h.update(img.tobytes())
    return h.hexdigest()[:16]


def to_panel(img: Image.Image, orientation: str, flip: bool) -> Image.Image:
    """Logical image → 800×480 in panel orientation. Portrait is rotated 90° clockwise
    (buttons on the bottom edge when the cable side is up); flip adds 180°.
    The exact direction for portrait is to be checked on the device."""
    out = img
    if orientation == "portrait":
        out = out.transpose(Image.Transpose.ROTATE_270)
    if flip:
        out = out.transpose(Image.Transpose.ROTATE_180)
    out.putpalette(PALETTE_BYTES)
    return out


def preview(img: Image.Image) -> Image.Image:
    """What the panel roughly looks like: indices coloured with the measured panel colours."""
    p = img.copy()
    p.putpalette(bytes(c for rgb in SATURATED for c in rgb))
    return p.convert("RGB")


def solid(color: int, size: tuple[int, int] = PANEL_SIZE) -> Image.Image:
    return new_canvas(size, color)
