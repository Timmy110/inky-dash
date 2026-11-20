"""Palette-pure route maps: stitched raster tiles thresholded to WHITE/BLACK, with route
and traffic overlays (DESIGN.md §6.3 "Map rendering")."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import Image

from inkydash.render import icons
from inkydash.render.theme import BLACK, BLUE, GREEN, PALETTE_BYTES, RED, WHITE, YELLOW, new_canvas
from inkydash.render.widgets import Canvas

if TYPE_CHECKING:
    from inkydash.providers.routing.base import Route
    from inkydash.providers.tiles import TileSource

TILE = 256
LatLon = tuple[float, float]
MAX_LAT = 85.05112878


def world_px(lat: float, lon: float, z: int) -> tuple[float, float]:
    """Web Mercator: lat/lon → global pixel coordinates at zoom z."""
    lat = max(-MAX_LAT, min(MAX_LAT, lat))
    n = TILE * 2 ** z
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def latlon_of(x: float, y: float, z: int) -> LatLon:
    n = TILE * 2 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


@dataclass(frozen=True)
class View:
    """A map window: tiles of zoom `z`, scaled by `scale` (≤ 1, a fractional zoom) to `size`."""

    z: int
    left: float   # global pixel x (at zoom z) of the map's left edge
    top: float
    size: tuple[int, int]
    scale: float = 1.0

    def px(self, lat: float, lon: float) -> tuple[float, float]:
        x, y = world_px(lat, lon, self.z)
        return (x - self.left) * self.scale, (y - self.top) * self.scale


def _bbox_px(lats: list[float], lons: list[float], z: int) -> tuple[float, float, float, float]:
    x0, y0 = world_px(max(lats), min(lons), z)
    x1, y1 = world_px(min(lats), max(lons), z)
    return x0, y0, x1, y1


def fit_view(points: Sequence[LatLon], size: tuple[int, int], padding: float = 0.1, max_zoom: int = 16) -> View:
    """Fit all points inside `size` minus `padding` (fraction per side). Uses the zoom level
    above the largest one that fits, scaled down, so the route fills the map (fractional zoom)."""
    w, h = size
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    usable_w, usable_h = w * (1 - 2 * padding), h * (1 - 2 * padding)
    z = max_zoom
    while z > 1:
        x0, y0, x1, y1 = _bbox_px(lats, lons, z)
        if x1 - x0 <= usable_w and y1 - y0 <= usable_h:
            break
        z -= 1
    scale = 1.0
    if z < max_zoom:
        x0, y0, x1, y1 = _bbox_px(lats, lons, z + 1)
        s = min(usable_w / max(x1 - x0, 1), usable_h / max(y1 - y0, 1))
        if s >= 0.55:  # below this, labels get too small: stay at z
            z, scale = z + 1, min(1.0, s)
    x0, y0, x1, y1 = _bbox_px(lats, lons, z)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return View(z, cx - w / 2 / scale, cy - h / 2 / scale, size, scale)


def basemap(view: View, tiles: TileSource | None) -> Image.Image:
    """Tiles stitched, cropped, scaled and thresholded into a WHITE/BLACK P image."""
    if tiles is None or not tiles.enabled:
        return new_canvas(view.size)
    sw, sh = round(view.size[0] / view.scale), round(view.size[1] / view.scale)
    gray = Image.new("L", (sw, sh), 255)
    tx0, ty0 = int(view.left // TILE), int(view.top // TILE)
    tx1, ty1 = int((view.left + sw) // TILE), int((view.top + sh) // TILE)
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            t = tiles.tile(view.z, tx, ty)
            if t is not None:
                if t.size != (TILE, TILE):
                    t = t.resize((TILE, TILE))
                gray.paste(t, (int(tx * TILE - view.left), int(ty * TILE - view.top)))
    if gray.size != view.size:
        gray = gray.resize(view.size, Image.Resampling.LANCZOS)
    thr = tiles.threshold
    bw = gray.point(lambda v: BLACK if v < thr else WHITE)
    out = Image.frombytes("P", view.size, bw.tobytes())
    out.putpalette(PALETTE_BYTES)
    return out


def _path(view: View, geom: Sequence[LatLon]) -> list[tuple[float, float]]:
    pts = [view.px(lat, lon) for lat, lon in geom]
    # drop points closer than 1.5 px: far fewer segments to draw on the Pi
    out: list[tuple[float, float]] = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) + abs(p[1] - out[-1][1]) >= 1.5:
            out.append(p)
    if pts and out[-1] != pts[-1]:
        out.append(pts[-1])
    return out


def draw_routes(c: Canvas, view: View, routes: Sequence[Route], selected: int, show_alternatives: bool = True,
                incident_marks: int = 3) -> None:
    # alternatives first (thin, dashed, numbered), selected on top
    if show_alternatives:
        for i, r in enumerate(routes):
            if i == selected:
                continue
            pts = _path(view, r.geometry)
            c.line(pts, WHITE, 6)
            c.dashed(pts, BLACK, 3, dash=10, gap=7)
            if len(pts) > 2:
                mx, my = pts[len(pts) // 2]
                icons.marker(c, mx, my, 10, WHITE, str(i + 1))
                c.text((mx, my), str(i + 1), "bold", 13, BLACK, anchor="mm")
    r = routes[selected]
    pts = _path(view, r.geometry)
    c.line(pts, WHITE, 11)
    c.line(pts, BLACK, 6)
    # traffic on the selected route
    full = [view.px(lat, lon) for lat, lon in r.geometry]
    for sec in r.traffic_sections:
        seg = full[max(0, sec.start_idx): sec.end_idx + 1]
        if len(seg) < 2:
            continue
        if sec.category == "closure" or sec.magnitude >= 3:
            color = RED
        elif sec.magnitude >= 1 or sec.category == "roadwork":
            color = YELLOW
        else:
            continue
        c.line(seg, BLACK, 10)
        c.line(seg, color, 6)
    marks = sorted([s for s in r.traffic_sections if s.magnitude >= 2 or s.category == "closure"],
                   key=lambda s: -(s.delay_s or 0))[:incident_marks]
    for s in marks:
        if 0 <= s.start_idx < len(full):
            x, y = full[s.start_idx]
            if s.category == "closure":
                icons.closure(c, x, y, 9)
            else:
                icons.warning_triangle(c, x, y - 12, 20)
    if pts:
        icons.marker(c, *pts[0], 9, GREEN)
        icons.marker(c, *pts[-1], 10, BLUE)
    if len(routes) > 1 and show_alternatives and len(pts) > 2:
        mx, my = pts[len(pts) // 2]
        icons.marker(c, mx, my, 10, YELLOW, str(selected + 1))
        c.text((mx, my), str(selected + 1), "bold", 13, BLACK, anchor="mm")


def render_map(size: tuple[int, int], routes: Sequence[Route], selected: int, tiles: TileSource | None,
               padding: float = 0.1, show_alternatives: bool = True, attribution: str = "") -> Image.Image:
    pts = [p for i, r in enumerate(routes) if show_alternatives or i == selected for p in r.geometry]
    view = fit_view(pts, size, padding)
    img = basemap(view, tiles)
    c = Canvas(img)
    draw_routes(c, view, routes, selected, show_alternatives)
    if attribution:
        tw = c.width(attribution, "ui", 11)
        c.rect((size[0] - tw - 10, size[1] - 16, size[0], size[1]), fill=WHITE)
        c.text((size[0] - 5, size[1] - 2), attribution, "ui", 11, BLACK, anchor="rb")
    return img
