import numpy
import pytest
from PIL import Image

from inkydash.display.backends import FileBackend
from inkydash.display.pipeline import compose, content_hash, dither, logical_size, preview, to_panel
from inkydash.render.theme import BLACK, BLUE, GREEN, PURE, RED, TRANSPARENT, WHITE, new_canvas, new_overlay
from inkydash.render.widgets import Canvas
from inkydash.screens.base import Frame

# The driver's own constants and logic (inky/inky_e673.py), reproduced to check our images
# pass through it the way we expect.
DRIVER_DESATURATED = [[0, 0, 0], [255, 255, 255], [255, 255, 0], [255, 0, 0], [0, 0, 255], [0, 255, 0],
                      [255, 255, 255]]
DRIVER_REMAP = numpy.array([0, 1, 2, 3, 5, 6])


def driver_set_image(image: Image.Image) -> numpy.ndarray:
    """inky_e673.Inky.set_image() for P images (saturation is unused for them)."""
    dither_mode = Image.Dither.FLOYDSTEINBERG
    palette_image = Image.new("P", (1, 1))
    palette = numpy.array(DRIVER_DESATURATED, dtype=numpy.uint8).flatten().tobytes()
    palette_image.putpalette(palette)
    if not image.palette.colors:
        image.putpalette(palette)
    if len(image.palette.colors) == 6:
        dither_mode = Image.Dither.NONE
    q = image.convert("RGB").quantize(6, palette=palette_image, dither=dither_mode)
    return DRIVER_REMAP[numpy.array(q, dtype=numpy.uint8)]


def dashboard() -> Image.Image:
    img = new_canvas((800, 480))
    c = Canvas(img)
    c.text((10, 10), "Wind 12 km/h · 07:45", "ui", 12, BLACK)
    c.rect((100, 100, 300, 200), fill=BLUE)
    c.line([(0, 300), (800, 320)], GREEN, 3)
    c.badge((400, 50), "+12 min", RED, WHITE)
    return img


def test_palette_frame_passes_driver_undithered():
    img = compose(Frame(dashboard()), (800, 480))
    assert len(img.palette.colors) == 6
    panel = to_panel(img, "landscape", False)
    raw = driver_set_image(panel)
    ours = numpy.array(panel)
    assert (raw == DRIVER_REMAP[ours]).all(), "driver must map our indices 1:1 (no dithering)"


def test_rotated_and_flipped_panels_keep_the_palette():
    img = compose(Frame(new_canvas((480, 800), RED)), (480, 800))
    for flip in (False, True):
        panel = to_panel(img, "portrait", flip)
        assert panel.size == (800, 480)
        assert len(panel.palette.colors) == 6
        assert (driver_set_image(panel) == 3).all()


def test_photo_is_dithered_to_six_colours():
    grad = Image.linear_gradient("L").resize((800, 480)).convert("RGB")
    out = compose(Frame(grad, saturation=0.5), (800, 480))
    assert out.mode == "P"
    lo, hi = out.getextrema()
    assert hi <= 5
    assert len(set(out.tobytes())) > 2


def test_overlay_is_not_dithered():
    photo = Image.new("RGB", (800, 480), (120, 90, 200))
    ov = new_overlay((800, 480))
    Canvas(ov).rect((0, 450, 800, 480), fill=WHITE)
    out = compose(Frame(photo, overlay=ov), (800, 480))
    strip = numpy.array(out)[450:, :]
    assert (strip == WHITE).all()
    assert TRANSPARENT not in set(out.tobytes())


def test_invalid_index_rejected():
    img = new_canvas((800, 480))
    img.putpixel((0, 0), 7)
    with pytest.raises(ValueError):
        compose(Frame(img), (800, 480))


def test_wrong_size_rejected():
    with pytest.raises(ValueError):
        compose(Frame(new_canvas((480, 800))), (800, 480))


def test_volatile_regions_dont_change_hash():
    a, b = dashboard(), dashboard()
    Canvas(a).footer("updated 07:00")
    box = Canvas(b).footer("updated 07:30")
    assert content_hash(a, [box]) == content_hash(b, [box])
    assert content_hash(a) != content_hash(b)
    Canvas(b).rect((10, 10, 20, 20), fill=RED)
    assert content_hash(a, [box]) != content_hash(b, [box])


def test_dither_matches_driver_maths():
    """Our dither must give the same indices the driver would produce for an RGB image."""
    from inkydash.render.theme import blended_palette

    img = Image.effect_noise((200, 120), 80).convert("RGB")
    ours = numpy.array(dither(img, 0.5))
    pal = Image.new("P", (1, 1))
    pal.putpalette(blended_palette(0.5))
    theirs = numpy.array(img.quantize(6, palette=pal, dither=Image.Dither.FLOYDSTEINBERG))
    assert (ours == theirs).all()


def test_preview_uses_panel_colours():
    p = preview(new_canvas((10, 10), WHITE))
    assert p.getpixel((0, 0)) == (161, 164, 165)


def test_logical_sizes():
    assert logical_size("landscape") == (800, 480)
    assert logical_size("portrait") == (480, 800)


def test_file_backend_writes_png_and_preview(tmp_path):
    b = FileBackend(tmp_path)
    img = compose(Frame(dashboard()), (800, 480))
    b.show(img, to_panel(img, "landscape", False))
    assert (tmp_path / "latest.png").is_file() and (tmp_path / "latest-preview.png").is_file()
    with Image.open(tmp_path / "latest.png") as saved:
        assert saved.mode == "P" and saved.getpalette()[:18] == [c for rgb in PURE for c in rgb]
