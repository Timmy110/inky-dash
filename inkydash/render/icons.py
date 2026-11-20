"""Procedural icons in palette colours: weather (WMO codes), warnings, travel modes, markers.

Drawn with primitives instead of an icon font, so they can use several palette colours
(yellow sun with black outline, blue rain) and scale to any size crisply.
"""

from __future__ import annotations

import math

from inkydash.render.theme import BLACK, BLUE, GREEN, RED, WHITE, YELLOW
from inkydash.render.widgets import Canvas

Box = tuple[int, int, int, int]


def wmo_kind(code: int | None) -> str:
    if code is None:
        return "unknown"
    if code == 0:
        return "clear"
    if code in (1, 2):
        return "partly"
    if code == 3:
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 57:
        return "drizzle"
    if 61 <= code <= 67 or 80 <= code <= 82:
        return "rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if code >= 95:
        return "storm"
    return "cloudy"


def _sun(c: Canvas, cx: float, cy: float, r: float, rays: bool = True) -> None:
    lw = max(1, round(r / 8))
    if rays:
        for i in range(8):
            a = i * math.pi / 4
            p0 = (cx + math.cos(a) * r * 1.35, cy + math.sin(a) * r * 1.35)
            p1 = (cx + math.cos(a) * r * 1.8, cy + math.sin(a) * r * 1.8)
            c.d.line([p0, p1], fill=BLACK, width=lw + 1)
    c.d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=YELLOW, outline=BLACK, width=lw)


def _moon(c: Canvas, cx: float, cy: float, r: float) -> None:
    lw = max(1, round(r / 8))
    c.d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=YELLOW, outline=BLACK, width=lw)
    o = r * 0.55
    c.d.ellipse((cx - r + o, cy - r - o * 0.4, cx + r + o, cy + r - o * 0.4), fill=WHITE)


def _cloud(c: Canvas, x: float, y: float, w: float, h: float, fill: int = WHITE) -> None:
    lw = max(1, round(w / 24))
    # three bumps + a flat base, outlined by drawing black shapes slightly larger first
    parts = [
        (x + w * 0.05, y + h * 0.40, x + w * 0.45, y + h * 1.00),
        (x + w * 0.25, y + h * 0.05, x + w * 0.70, y + h * 0.85),
        (x + w * 0.55, y + h * 0.30, x + w * 0.95, y + h * 1.00),
    ]
    base = (x + w * 0.25, y + h * 0.55, x + w * 0.75, y + h * 1.00)
    for e in parts:
        c.d.ellipse((e[0] - lw, e[1] - lw, e[2] + lw, e[3] + lw), fill=BLACK)
    c.d.rectangle((base[0], base[1], base[2], base[3] + lw), fill=BLACK)
    for e in parts:
        c.d.ellipse(e, fill=fill)
    c.d.rectangle(base, fill=fill)


def weather_icon(c: Canvas, box: Box, code: int | None, is_day: bool = True) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    s = min(w, h)
    x0 += (w - s) / 2
    y0 += (h - s) / 2
    kind = wmo_kind(code)
    lw = max(2, round(s / 20))

    if kind == "clear":
        (_sun if is_day else _moon)(c, x0 + s / 2, y0 + s / 2, s * 0.26)
        return
    if kind == "partly":
        if is_day:
            _sun(c, x0 + s * 0.36, y0 + s * 0.36, s * 0.18)
        else:
            _moon(c, x0 + s * 0.36, y0 + s * 0.36, s * 0.2)
        _cloud(c, x0 + s * 0.22, y0 + s * 0.38, s * 0.74, s * 0.44)
        return
    if kind == "unknown":
        c.text((x0 + s / 2, y0 + s / 2), "?", "bold", int(s * 0.6), BLACK, anchor="mm")
        return

    _cloud(c, x0 + s * 0.08, y0 + s * 0.12, s * 0.84, s * 0.5)
    by = y0 + s * 0.70
    if kind == "fog":
        for i in range(3):
            yy = by + i * s * 0.09
            c.d.line([(x0 + s * 0.15, yy), (x0 + s * 0.85, yy)], fill=BLACK, width=lw)
    elif kind in ("drizzle", "rain"):
        n = 3 if kind == "drizzle" else 4
        length = s * (0.12 if kind == "drizzle" else 0.2)
        for i in range(n):
            xx = x0 + s * (0.25 + i * 0.5 / (n - 1))
            c.d.line([(xx, by), (xx - length * 0.4, by + length)], fill=BLUE, width=lw + 1)
    elif kind == "snow":
        for i in range(3):
            xx = x0 + s * (0.28 + i * 0.22)
            yy = by + s * (0.08 if i % 2 else 0.02)
            r = s * 0.06
            for a in range(3):
                ang = a * math.pi / 3
                c.d.line([(xx - math.cos(ang) * r, yy - math.sin(ang) * r),
                          (xx + math.cos(ang) * r, yy + math.sin(ang) * r)], fill=BLUE, width=lw)
    elif kind == "storm":
        pts = [(x0 + s * 0.55, by - s * 0.05), (x0 + s * 0.40, by + s * 0.13), (x0 + s * 0.52, by + s * 0.13),
               (x0 + s * 0.42, by + s * 0.30), (x0 + s * 0.66, by + s * 0.08), (x0 + s * 0.54, by + s * 0.08),
               (x0 + s * 0.62, by - s * 0.05)]
        c.d.polygon(pts, fill=YELLOW, outline=BLACK)


def warning_triangle(c: Canvas, cx: float, cy: float, size: float, fill: int = YELLOW) -> None:
    h = size * 0.88
    pts = [(cx, cy - h / 2), (cx + size / 2, cy + h / 2), (cx - size / 2, cy + h / 2)]
    c.d.polygon(pts, fill=fill, outline=BLACK, width=max(1, round(size / 14)))
    c.text((cx, cy + h * 0.12), "!", "bold", max(8, int(size * 0.62)), BLACK, anchor="mm")


def arrow(c: Canvas, cx: float, cy: float, size: float, up: bool = True, fill: int = BLACK) -> None:
    """Small solid up/down arrow (the fonts have no ↑ ↓ glyphs)."""
    h = size / 2
    head = [(cx - h, cy), (cx + h, cy), (cx, cy - h)] if up else [(cx - h, cy), (cx + h, cy), (cx, cy + h)]
    shaft = (cx - h / 3, cy, cx + h / 3, cy + h) if up else (cx - h / 3, cy - h, cx + h / 3, cy)
    c.d.rectangle(shaft, fill=fill)
    c.d.polygon(head, fill=fill)


def marker(c: Canvas, x: float, y: float, r: float, fill: int, label: str = "") -> None:
    c.d.ellipse((x - r - 2, y - r - 2, x + r + 2, y + r + 2), fill=WHITE)
    c.d.ellipse((x - r, y - r, x + r, y + r), fill=fill, outline=BLACK, width=2)
    if label:
        c.text((x, y), label, "bold", max(8, int(r * 1.3)), WHITE if fill in (BLACK, BLUE, RED, GREEN) else BLACK,
               anchor="mm")


def closure(c: Canvas, x: float, y: float, r: float) -> None:
    c.d.ellipse((x - r, y - r, x + r, y + r), fill=RED, outline=BLACK, width=1)
    c.d.line([(x - r * 0.5, y - r * 0.5), (x + r * 0.5, y + r * 0.5)], fill=WHITE, width=2)
    c.d.line([(x - r * 0.5, y + r * 0.5), (x + r * 0.5, y - r * 0.5)], fill=WHITE, width=2)


def mode_icon(c: Canvas, box: Box, mode: str) -> None:
    """Simple pictograms: car, bike, walk, transit."""
    x0, y0, x1, y1 = box
    s = min(x1 - x0, y1 - y0)
    lw = max(2, round(s / 14))
    if mode == "car":
        body = (x0 + s * 0.05, y0 + s * 0.45, x0 + s * 0.95, y0 + s * 0.75)
        c.d.rounded_rectangle(body, radius=int(s * 0.08), fill=BLACK)
        c.d.polygon([(x0 + s * 0.22, y0 + s * 0.46), (x0 + s * 0.32, y0 + s * 0.22),
                     (x0 + s * 0.68, y0 + s * 0.22), (x0 + s * 0.78, y0 + s * 0.46)], fill=BLACK)
        c.d.polygon([(x0 + s * 0.30, y0 + s * 0.44), (x0 + s * 0.36, y0 + s * 0.28),
                     (x0 + s * 0.64, y0 + s * 0.28), (x0 + s * 0.70, y0 + s * 0.44)], fill=WHITE)
        for wx in (0.27, 0.73):
            c.d.ellipse((x0 + s * (wx - 0.11), y0 + s * 0.64, x0 + s * (wx + 0.11), y0 + s * 0.86),
                        fill=BLACK, outline=WHITE, width=max(1, lw // 2))
    elif mode == "bike":
        for wx in (0.25, 0.75):
            c.d.ellipse((x0 + s * (wx - 0.2), y0 + s * 0.45, x0 + s * (wx + 0.2), y0 + s * 0.85),
                        outline=BLACK, width=lw)
        c.d.line([(x0 + s * 0.25, y0 + s * 0.65), (x0 + s * 0.45, y0 + s * 0.35), (x0 + s * 0.65, y0 + s * 0.35),
                  (x0 + s * 0.75, y0 + s * 0.65)], fill=BLACK, width=lw)
        c.d.line([(x0 + s * 0.45, y0 + s * 0.35), (x0 + s * 0.52, y0 + s * 0.65), (x0 + s * 0.65, y0 + s * 0.35)],
                 fill=BLACK, width=lw)
    elif mode == "walk":
        c.d.ellipse((x0 + s * 0.42, y0 + s * 0.05, x0 + s * 0.58, y0 + s * 0.21), fill=BLACK)
        c.d.line([(x0 + s * 0.5, y0 + s * 0.25), (x0 + s * 0.47, y0 + s * 0.58)], fill=BLACK, width=lw + 1)
        c.d.line([(x0 + s * 0.47, y0 + s * 0.58), (x0 + s * 0.33, y0 + s * 0.92)], fill=BLACK, width=lw)
        c.d.line([(x0 + s * 0.47, y0 + s * 0.58), (x0 + s * 0.62, y0 + s * 0.92)], fill=BLACK, width=lw)
        c.d.line([(x0 + s * 0.30, y0 + s * 0.45), (x0 + s * 0.5, y0 + s * 0.32), (x0 + s * 0.68, y0 + s * 0.48)],
                 fill=BLACK, width=lw)
    else:  # transit
        c.d.rounded_rectangle((x0 + s * 0.2, y0 + s * 0.1, x0 + s * 0.8, y0 + s * 0.8), radius=int(s * 0.1),
                              fill=BLACK)
        c.d.rectangle((x0 + s * 0.28, y0 + s * 0.2, x0 + s * 0.72, y0 + s * 0.45), fill=WHITE)
        for wx in (0.33, 0.67):
            c.d.ellipse((x0 + s * (wx - 0.05), y0 + s * 0.58, x0 + s * (wx + 0.05), y0 + s * 0.68), fill=WHITE)
        c.d.line([(x0 + s * 0.3, y0 + s * 0.8), (x0 + s * 0.2, y0 + s * 0.95)], fill=BLACK, width=lw)
        c.d.line([(x0 + s * 0.7, y0 + s * 0.8), (x0 + s * 0.8, y0 + s * 0.95)], fill=BLACK, width=lw)
