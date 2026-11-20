"""Palette-pure charts: hourly temperature line + precipitation bars."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from inkydash.render.theme import BLACK, BLUE, WHITE
from inkydash.render.widgets import Canvas

Box = tuple[int, int, int, int]


def hourly_strip(c: Canvas, box: Box, times: Sequence[datetime], temps: Sequence[float | None],
                 rain: Sequence[float | None], hour_label: Callable[[datetime], str],
                 temp_label: Callable[[float], str], label_every: int = 3, rain_max: float = 100.0) -> None:
    """Temperature line on top of precipitation-probability bars (0..rain_max)."""
    x0, y0, x1, y1 = box
    n = len(times)
    if n == 0:
        return
    label_h = 18
    chart_top, chart_bottom = y0 + 20, y1 - label_h
    col = (x1 - x0) / n
    # rain bars
    bar_h_max = (chart_bottom - chart_top) * 0.55
    for i, r in enumerate(rain):
        if r:
            h = max(2, bar_h_max * min(r, rain_max) / rain_max)
            bx0 = x0 + i * col + col * 0.18
            bx1 = x0 + (i + 1) * col - col * 0.18
            c.rect((int(bx0), int(chart_bottom - h), int(bx1), int(chart_bottom)), fill=BLUE)
    c.hline(int(chart_bottom), int(x0), int(x1), BLACK, 1)
    # temperature line
    vals = [t for t in temps if t is not None]
    if vals:
        lo, hi = min(vals), max(vals)
        span = max(hi - lo, 4.0)
        mid = (hi + lo) / 2
        lo, hi = mid - span / 2, mid + span / 2
        line_top, line_bottom = chart_top + 4, chart_bottom - bar_h_max * 0.35

        def y_of(v: float) -> float:
            return line_bottom - (v - lo) / (hi - lo) * (line_bottom - line_top)

        pts = [(x0 + (i + 0.5) * col, y_of(t)) for i, t in enumerate(temps) if t is not None]
        c.line(pts, BLACK, 3)
        for i, t in enumerate(temps):
            if t is None or i % label_every:
                continue
            x, y = x0 + (i + 0.5) * col, y_of(t)
            c.d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=WHITE, outline=BLACK, width=2)
            c.text((x, y - 8), temp_label(t), "bold", 15, BLACK, anchor="mb")
    for i, t in enumerate(times):
        if i % label_every == 0:
            c.text((x0 + (i + 0.5) * col, y1), hour_label(t), "ui", 13, BLACK, anchor="mb")


def quarter_bars(c: Canvas, box: Box, values: Sequence[float], scale_mm: float = 1.0) -> None:
    """15-minute precipitation bars (mm per 15 min), blue on a baseline."""
    x0, y0, x1, y1 = box
    n = max(1, len(values))
    col = (x1 - x0) / n
    for i, v in enumerate(values):
        if v > 0:
            h = max(2, (y1 - y0) * min(v, scale_mm) / scale_mm)
            c.rect((int(x0 + i * col + 1), int(y1 - h), int(x0 + (i + 1) * col - 1), int(y1)), fill=BLUE)
    c.hline(int(y1), int(x0), int(x1), BLACK, 1)
