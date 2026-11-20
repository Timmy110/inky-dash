"""The M0 hardware test card: judge the real panel before any layout work.

It answers: are palette images shown undithered, which small font sizes stay legible without
anti-aliasing, how does yellow read, how do thin lines look, what each tile threshold keeps,
how dithering looks, and (after many refreshes) whether ghosting builds up.
"""

from __future__ import annotations

from PIL import Image

from inkydash.display.pipeline import dither
from inkydash.render.theme import BLACK, BLUE, GREEN, NAMES, RED, WHITE, YELLOW, new_canvas
from inkydash.render.widgets import Canvas


def _tile_samples(c: Canvas, x: int, y: int, tiles_by_style: dict[str, Image.Image | None],
                  thresholds: dict[str, int], size: int = 120) -> None:
    for i, (style, tile) in enumerate(tiles_by_style.items()):
        xx = x + i * (size + 10)
        c.text((xx, y), style, "ui", 11, BLACK)
        if tile is None:
            c.rect((xx, y + 16, xx + size, y + 16 + size), outline=BLACK)
            c.text((xx + size / 2, y + 16 + size / 2), "no tile", "ui", 11, BLACK, anchor="mm")
            continue
        g = tile.convert("L").resize((size, size))
        thr = thresholds[style]
        bw = g.point(lambda v, thr=thr: BLACK if v < thr else WHITE)
        p = Image.frombytes("P", bw.size, bw.tobytes())
        p.putpalette(c.img.getpalette())  # type: ignore[arg-type]
        c.img.paste(p, (xx, y + 16))
        c.rect((xx, y + 16, xx + size, y + 16 + size), outline=BLACK)


def render(size: tuple[int, int] = (800, 480), tiles_by_style: dict[str, Image.Image | None] | None = None,
           thresholds: dict[str, int] | None = None) -> Image.Image:
    img = new_canvas(size)
    c = Canvas(img)
    W, H = size

    # 1. palette swatches
    sw = W // 6
    for i, name in enumerate(NAMES):
        c.rect((i * sw, 0, (i + 1) * sw - 1, 40), fill=i, outline=BLACK)
        fg = WHITE if i in (BLACK, RED, BLUE, GREEN) else BLACK
        c.text((i * sw + sw / 2, 20), f"{i} {name}", "bold", 15, fg, anchor="mm")

    # 2. font ladders (no anti-aliasing)
    y = 50
    for kind, sizes in (("ui", (10, 11, 12, 13, 14, 16, 18)), ("bold", (10, 11, 12, 13, 14, 16, 18))):
        x = 8
        for s in sizes:
            b = c.text((x, y), f"{s}px Wind 12°", kind, s, BLACK)
            x = b[2] + 12
        y += 24
    x = 8
    for s in (20, 28, 40, 56, 72):
        b = c.text((x, y), "07:45 42′", "num", s, BLACK)
        x = b[2] + 14
    y += 76

    # 3. yellow legibility
    c.text((8, y), "Yellow text", "bold", 18, YELLOW)
    c.text((140, y), "Yellow + outline", "bold", 18, YELLOW, stroke=1, stroke_fill=BLACK)
    c.badge((330, y - 4), "Black on yellow", YELLOW, BLACK, size=16)
    c.badge((500, y - 4), "White on red", RED, WHITE, size=16)
    c.badge((650, y - 4), "White on blue", BLUE, WHITE, size=16)
    y += 34

    # 4. line widths and dashes
    for i, color in enumerate((BLACK, RED, BLUE, GREEN, YELLOW)):
        for wdt in (1, 2, 3, 4):
            x0 = 8 + (wdt - 1) * 70
            c.line([(x0, y + i * 10), (x0 + 60, y + i * 10)], color, wdt)
    c.dashed([(300, y), (520, y + 40)], BLACK, 2)
    c.dashed([(300, y + 40), (520, y)], BLACK, 3, dash=10, gap=7)
    y += 56

    # 5. threshold ramps: what each tile style's cut-off keeps from a grey ramp
    thresholds = thresholds or {"stadia-toner": 128, "stadia-toner-lite": 215, "osm": 175}
    ramp_w = 240
    for i, (style, thr) in enumerate(thresholds.items()):
        yy = y + i * 22
        c.text((8, yy + 2), f"{style} <{thr}", "ui", 12, BLACK)
        for x in range(ramp_w):
            v = int(x / (ramp_w - 1) * 255)
            c.d.line([(150 + x, yy), (150 + x, yy + 16)], fill=BLACK if v < thr else WHITE)
        c.rect((150, yy, 150 + ramp_w, yy + 16), outline=BLACK)

    # 6. dithered gradient (how photos look) and fine detail (ghosting check)
    grad = Image.new("RGB", (220, 80))
    for x in range(220):
        for yy in range(80):
            grad.putpixel((x, yy), (int(255 * x / 219), int(255 * yy / 79), 255 - int(255 * x / 219)))
    gx, gy = W - 230, y
    img.paste(dither(grad, 0.5), (gx, gy))
    c.text((gx, gy - 14), "dithered @0.5", "ui", 11, BLACK)
    for i in range(0, 60, 2):
        c.d.line([(gx - 80 + i, gy), (gx - 80 + i, gy + 60)], fill=BLACK)
    y += 72

    if tiles_by_style:
        _tile_samples(c, 8, y, tiles_by_style, thresholds)
    c.text((W - 6, H - 4), "inky-dash testcard", "ui", 11, BLACK, anchor="rb")
    return img
