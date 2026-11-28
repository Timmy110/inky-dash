"""Soft rendering: RGB artwork that the display layer dithers, under a crisp palette overlay.

Used by the weather screen. Backgrounds, icons and chart fills are drawn in RGB (supersampled
for smooth edges) and come out as stippled tints on the 6-colour panel; text, lines and card
outlines go on the overlay in pure palette colours, so they stay sharp. The icons here are our
own procedural drawings.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from PIL import Image, ImageDraw

from inkydash.render.theme import BLACK, RED, WHITE, YELLOW
from inkydash.render.widgets import Canvas

RGB = tuple[int, int, int]
Box = tuple[int, int, int, int]
SS = 4  # supersampling factor for sprites


@dataclass(frozen=True)
class Look:
    """Colours of one theme. RGB values are dithered; ints are crisp palette indices."""

    name: str
    bg: RGB
    fg: int            # text and outlines (overlay)
    line: int          # chart temperature line (overlay)
    muted: RGB         # icon strokes
    sun: RGB
    moon: RGB
    cloud: RGB
    cloud_back: RGB
    cloud_storm: RGB
    rain: RGB
    snow: RGB
    fog: RGB
    bolt: RGB
    accent: RGB        # orange in stat icons
    blue: RGB
    green: RGB
    red: RGB
    chart_fill: RGB
    rain_bar: RGB


LIGHT = Look(
    name="light", bg=(255, 255, 255), fg=BLACK, line=RED, muted=(70, 76, 98),
    sun=(247, 160, 52), moon=(240, 188, 36), cloud=(142, 154, 200), cloud_back=(76, 88, 150),
    cloud_storm=(92, 98, 132), rain=(40, 84, 214), snow=(88, 128, 222), fog=(98, 104, 130),
    bolt=(250, 204, 30), accent=(240, 132, 40), blue=(58, 108, 214), green=(76, 168, 88), red=(214, 58, 50),
    chart_fill=(250, 196, 92), rain_bar=(96, 126, 214),
)
DARK = Look(
    name="dark", bg=(26, 30, 62), fg=WHITE, line=YELLOW, muted=(214, 220, 236),
    sun=(250, 170, 60), moon=(252, 210, 48), cloud=(226, 231, 243), cloud_back=(132, 142, 188),
    cloud_storm=(150, 156, 186), rain=(112, 152, 242), snow=(236, 241, 255), fog=(190, 196, 212),
    bolt=(252, 214, 40), accent=(246, 146, 52), blue=(112, 152, 242), green=(104, 196, 112), red=(236, 84, 70),
    chart_fill=(232, 122, 40), rain_bar=(110, 136, 214),
)


def look(name: str) -> Look:
    return DARK if name == "dark" else LIGHT


# ------------------------------------------------------------------ sprites
def sprite(size: int, draw: Callable[[ImageDraw.ImageDraw, float], None]) -> Image.Image:
    """Draw on a transparent 4× canvas (draw(d, s) with s = side in px), return it at `size`."""
    s = max(1, size) * SS
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw(ImageDraw.Draw(im), float(s))
    return im.resize((max(1, size), max(1, size)), Image.Resampling.LANCZOS)


def paste(base: Image.Image, im: Image.Image, xy: tuple[float, float]) -> None:
    base.paste(im, (int(round(xy[0])), int(round(xy[1]))), im)


def _cloud(d: ImageDraw.ImageDraw, s: float, x: float, y: float, w: float, h: float, fill: RGB) -> None:
    """Cloud in the box (x, y, w, h), given as fractions of the sprite side."""
    def X(f: float) -> float:
        return (x + f * w) * s

    def Y(f: float) -> float:
        return (y + f * h) * s

    d.ellipse((X(0.00), Y(0.42), X(0.42), Y(1.00)), fill=fill)
    d.ellipse((X(0.20), Y(0.00), X(0.70), Y(0.86)), fill=fill)
    d.ellipse((X(0.54), Y(0.28), X(1.00), Y(1.00)), fill=fill)
    d.rounded_rectangle((X(0.18), Y(0.55), X(0.80), Y(1.00)), radius=h * s * 0.2, fill=fill)


def _cloud_cut(d: ImageDraw.ImageDraw, s: float, x: float, y: float, w: float, h: float, fill: RGB,
               gap_color: RGB, gap: float = 0.035) -> None:
    """Cloud with a background-coloured gap around it, so it stands apart from what is behind it."""
    _cloud(d, s, x - gap, y - gap, w + 2 * gap, h + 2 * gap, gap_color)
    _cloud(d, s, x, y, w, h, fill)


def _disc(d: ImageDraw.ImageDraw, s: float, cx: float, cy: float, r: float, fill: RGB) -> None:
    d.ellipse(((cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s), fill=fill)


def _moon(d: ImageDraw.ImageDraw, s: float, cx: float, cy: float, r: float, fill: RGB) -> None:
    _disc(d, s, cx, cy, r, fill)
    o = r * 0.62
    d.ellipse(((cx - r + o) * s, (cy - r - o * 0.55) * s, (cx + r + o) * s, (cy + r - o * 0.55) * s),
              fill=(0, 0, 0, 0))


def _kind(code: int | None) -> str:
    from inkydash.render.icons import wmo_kind

    return wmo_kind(code)


def weather_sprite(code: int | None, is_day: bool, size: int, lk: Look) -> Image.Image:
    kind = _kind(code)
    showers = code is not None and (80 <= code <= 82 or code in (85, 86))

    def draw(d: ImageDraw.ImageDraw, s: float) -> None:
        def body(fill: RGB, top: float = 0.10, h: float = 0.50) -> None:
            if showers:
                _cloud_cut(d, s, 0.08, top, 0.84, h, fill, lk.bg)
            else:
                _cloud(d, s, 0.08, top, 0.84, h, fill)

        if kind == "clear":
            if is_day:
                _disc(d, s, 0.5, 0.5, 0.36, lk.sun)
            else:
                _moon(d, s, 0.5, 0.5, 0.34, lk.moon)
            return
        if kind == "unknown":
            body(lk.cloud, 0.22, 0.56)
            return
        if kind == "partly" or showers:
            cx, cy = (0.66, 0.32) if kind == "partly" else (0.70, 0.22)
            if is_day:
                _disc(d, s, cx, cy, 0.22, lk.sun)
            else:
                _moon(d, s, cx, cy, 0.20, lk.moon)
        if kind == "partly":
            _cloud_cut(d, s, 0.06, 0.38, 0.80, 0.48, lk.cloud, lk.bg)
            return
        if kind == "cloudy":
            _cloud(d, s, 0.20, 0.14, 0.72, 0.44, lk.cloud_back)
            _cloud_cut(d, s, 0.04, 0.36, 0.82, 0.50, lk.cloud, lk.bg)
            return
        if kind == "storm":
            body(lk.cloud_storm)
            pts = [(0.56, 0.50), (0.38, 0.74), (0.50, 0.74), (0.42, 0.96), (0.66, 0.66), (0.54, 0.66), (0.62, 0.50)]
            d.polygon([(px * s, py * s) for px, py in pts], fill=lk.bolt)
            return
        body(lk.cloud, 0.22, 0.44) if showers else body(lk.cloud)
        if kind == "fog":
            for i, yy in enumerate((0.70, 0.80, 0.90)):
                x0 = 0.14 if i % 2 == 0 else 0.24
                d.rounded_rectangle((x0 * s, (yy - 0.025) * s, (x0 + 0.62) * s, (yy + 0.025) * s),
                                    radius=0.025 * s, fill=lk.fog)
        elif kind in ("rain", "drizzle"):
            n, length, width = (4, 0.20, 0.050) if kind == "rain" else (3, 0.10, 0.045)
            for i in range(n):
                xx = 0.30 + i * (0.42 / (n - 1))
                d.line([(xx * s, 0.68 * s), ((xx - length * 0.4) * s, (0.68 + length) * s)], fill=lk.rain,
                       width=int(width * s))
        elif kind == "snow":
            for xx, yy in ((0.30, 0.72), (0.50, 0.80), (0.70, 0.72), (0.40, 0.92), (0.60, 0.92)):
                _disc(d, s, xx, yy, 0.045, lk.snow)

    return sprite(size, draw)


def stat_sprite(name: str, size: int, lk: Look) -> Image.Image:
    """Small pictograms for the stats grid."""

    def draw(d: ImageDraw.ImageDraw, s: float) -> None:
        w = max(1, int(s * 0.07))
        if name in ("sunrise", "sunset"):
            d.pieslice((0.18 * s, 0.42 * s, 0.82 * s, 1.06 * s), 180, 360, fill=lk.sun)
            d.line([(0.06 * s, 0.76 * s), (0.94 * s, 0.76 * s)], fill=lk.muted, width=w)
            ax = 0.5 * s
            if name == "sunrise":
                d.polygon([(ax, 0.02 * s), (ax - 0.15 * s, 0.20 * s), (ax + 0.15 * s, 0.20 * s)], fill=lk.accent)
                d.rectangle((ax - 0.04 * s, 0.18 * s, ax + 0.04 * s, 0.34 * s), fill=lk.accent)
            else:
                d.polygon([(ax, 0.36 * s), (ax - 0.15 * s, 0.18 * s), (ax + 0.15 * s, 0.18 * s)], fill=lk.accent)
                d.rectangle((ax - 0.04 * s, 0.02 * s, ax + 0.04 * s, 0.20 * s), fill=lk.accent)
        elif name == "wind":
            for yy, x1, r in ((0.32, 0.70, 0.13), (0.52, 0.86, 0.11), (0.72, 0.62, 0.10)):
                d.line([(0.08 * s, yy * s), (x1 * s, yy * s)], fill=lk.blue, width=w)
                d.arc(((x1 - r) * s, (yy - 2 * r) * s, (x1 + r) * s, yy * s), 180, 450, fill=lk.blue, width=w)
        elif name == "humidity":
            d.ellipse((0.22 * s, 0.38 * s, 0.78 * s, 0.94 * s), fill=lk.blue)
            d.polygon([(0.5 * s, 0.04 * s), (0.25 * s, 0.56 * s), (0.75 * s, 0.56 * s)], fill=lk.blue)
            d.arc((0.33 * s, 0.52 * s, 0.62 * s, 0.82 * s), 100, 190, fill=lk.bg, width=max(1, w // 2))
        elif name == "pressure":
            d.ellipse((0.08 * s, 0.08 * s, 0.92 * s, 0.92 * s), outline=lk.muted, width=w)
            for a in range(-210, 31, 40):
                r0, r1 = 0.30, 0.36
                ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
                d.line([((0.5 + ca * r0) * s, (0.5 + sa * r0) * s), ((0.5 + ca * r1) * s, (0.5 + sa * r1) * s)],
                       fill=lk.muted, width=max(1, w // 2))
            d.line([(0.5 * s, 0.5 * s), (0.70 * s, 0.30 * s)], fill=lk.red, width=w)
            _disc(d, s, 0.5, 0.5, 0.07, lk.muted)
        elif name == "uv":
            for i in range(8):
                a = i * math.pi / 4
                d.line([((0.5 + math.cos(a) * 0.30) * s, (0.5 + math.sin(a) * 0.30) * s),
                        ((0.5 + math.cos(a) * 0.46) * s, (0.5 + math.sin(a) * 0.46) * s)], fill=lk.accent, width=w)
            _disc(d, s, 0.5, 0.5, 0.24, lk.sun)
        elif name == "visibility":
            d.arc((0.04 * s, 0.22 * s, 0.96 * s, 1.10 * s), 200, 340, fill=lk.muted, width=w)
            d.arc((0.04 * s, -0.10 * s, 0.96 * s, 0.78 * s), 20, 160, fill=lk.muted, width=w)
            _disc(d, s, 0.5, 0.5, 0.17, lk.blue)
            _disc(d, s, 0.5, 0.5, 0.07, lk.muted)
        elif name == "air_quality":
            box = (0.06 * s, 0.20 * s, 0.94 * s, 1.08 * s)
            for (a0, a1), col in zip(((180, 225), (225, 270), (270, 315), (315, 360)),
                                     (lk.green, lk.sun, lk.accent, lk.red), strict=True):
                d.arc(box, a0, a1, fill=col, width=int(s * 0.14))
            d.line([(0.5 * s, 0.64 * s), (0.30 * s, 0.36 * s)], fill=lk.muted, width=w)
            _disc(d, s, 0.5, 0.64, 0.07, lk.muted)
        elif name == "pollen":
            d.line([(0.5 * s, 0.55 * s), (0.5 * s, 0.98 * s)], fill=lk.green, width=w)
            for i in range(5):
                a = i * 2 * math.pi / 5 - math.pi / 2
                _disc(d, s, 0.5 + math.cos(a) * 0.20, 0.38 + math.sin(a) * 0.20, 0.15, lk.sun)
            _disc(d, s, 0.5, 0.38, 0.11, lk.accent)
        elif name == "rain":
            d.pieslice((0.06 * s, 0.10 * s, 0.94 * s, 0.90 * s), 180, 360, fill=lk.blue)
            d.line([(0.5 * s, 0.50 * s), (0.5 * s, 0.86 * s)], fill=lk.muted, width=w)
            d.arc((0.36 * s, 0.74 * s, 0.50 * s, 0.94 * s), 0, 180, fill=lk.muted, width=w)

    return sprite(size, draw)


# ------------------------------------------------------------------ chart
def _smooth(pts: Sequence[tuple[float, float]], steps: int = 6) -> list[tuple[float, float]]:
    """Catmull–Rom through the points."""
    if len(pts) < 3:
        return list(pts)
    out: list[tuple[float, float]] = []
    p = [pts[0], *pts, pts[-1]]
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        for k in range(steps):
            t = k / steps
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2
                                    + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3) for j in (0, 1)))  # type: ignore[misc]
    out.append(pts[-1])
    return out


def hourly_chart(base: Image.Image, ov: Canvas, box: Box, lk: Look, times: Sequence[datetime],
                 temps: Sequence[float | None], probs: Sequence[float | None],
                 hour_label: Callable[[datetime], str], temp_label: Callable[[float], str],
                 label_every: int = 2) -> None:
    """Temperature area (gradient) over precipitation-probability bars, with axis labels."""
    x0, y0, x1, y1 = box
    n = len(times)
    if n < 2:
        return
    left, right, label_h = 34, 40, 18
    px0, px1, py0, py1 = x0 + left, x1 - right, y0 + 4, y1 - label_h
    col = (px1 - px0) / n
    ph = py1 - py0

    # precipitation probability bars (behind)
    bars = Image.new("RGBA", base.size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(bars)
    for i, p in enumerate(probs):
        if p:
            h = ph * min(p, 100) / 100
            bd.rectangle((px0 + i * col + 1, py1 - h, px0 + (i + 1) * col - 1, py1), fill=(*lk.rain_bar, 255))

    # temperature area
    vals = [t for t in temps if t is not None]
    pts: list[tuple[float, float]] = []
    if vals:
        lo, hi = min(vals), max(vals)
        span = max(hi - lo, 3.0)
        mid = (hi + lo) / 2
        lo, hi = mid - span / 2, mid + span / 2
        top, bottom = py0 + ph * 0.12, py1 - ph * 0.12

        def y_of(v: float) -> float:
            return bottom - (v - lo) / (hi - lo) * (bottom - top)

        raw = [(px0 + (i + 0.5) * col, y_of(t)) for i, t in enumerate(temps) if t is not None]
        raw = [(px0, raw[0][1]), *raw, (px1, raw[-1][1])]
        pts = _smooth(raw)
        mask = Image.new("L", base.size, 0)
        ImageDraw.Draw(mask).polygon([*pts, (px1, py1), (px0, py1)], fill=255)
        ytop = int(min(p[1] for p in pts))
        grad = Image.linear_gradient("L").resize((1, max(1, py1 - ytop))).resize((base.width, max(1, py1 - ytop)))
        fill_layer = Image.new("RGB", base.size, lk.bg)
        solid = Image.new("RGB", grad.size, lk.chart_fill)
        bgpart = Image.new("RGB", grad.size, lk.bg)
        # 255 at the curve's top → fill colour, fading towards the background at the baseline
        fill_layer.paste(Image.composite(bgpart, solid, grad.point(lambda v: int(v * 0.7))), (0, ytop))
        base.paste(fill_layer, (0, 0), mask)
    bars.putalpha(bars.getchannel("A").point(lambda a: int(a * 0.6)))
    base.paste(bars.convert("RGB"), (0, 0), bars.getchannel("A"))

    # crisp parts
    ov.hline(int(py1), int(px0), int(px1), lk.fg, 1)
    if pts:
        ov.line([(int(x), int(y)) for x, y in pts], lk.line, 2)
        ov.text((x0 + left - 6, py0), temp_label(max(vals)), "jost", 13, lk.fg, anchor="ra")
        ov.text((x0 + left - 6, py1), temp_label(min(vals)), "jost", 13, lk.fg, anchor="rs")
    ov.text((px1 + 6, py0), "100%", "jost", 13, lk.fg, anchor="la")
    ov.text((px1 + 6, py1), "0%", "jost", 13, lk.fg, anchor="ls")
    for i, t in enumerate(times):
        if i % label_every == 0:
            ov.text((px0 + (i + 0.5) * col, y1), hour_label(t), "jost", 13, lk.fg, anchor="ms")
