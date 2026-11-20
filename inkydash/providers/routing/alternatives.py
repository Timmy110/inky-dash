"""Close-in-time alternatives, near-duplicate removal and sticky selection (DESIGN.md §6.3)."""

from __future__ import annotations

import math
from datetime import timedelta

from inkydash.providers.routing.base import LatLon, Route

GRID_DEG = 0.0006  # ~50–65 m cells


def _densify(geom: list[LatLon], step_m: float = 40.0) -> list[LatLon]:
    out: list[LatLon] = []
    for (a_lat, a_lon), (b_lat, b_lon) in zip(geom, geom[1:], strict=False):
        dy = (b_lat - a_lat) * 111_320
        dx = (b_lon - a_lon) * 111_320 * math.cos(math.radians(a_lat))
        n = max(1, int(math.hypot(dx, dy) / step_m))
        for i in range(n):
            t = i / n
            out.append((a_lat + (b_lat - a_lat) * t, a_lon + (b_lon - a_lon) * t))
    if geom:
        out.append(geom[-1])
    return out


def signature(geom: list[LatLon]) -> set[tuple[int, int]]:
    return {(round(lat / GRID_DEG), round(lon / GRID_DEG)) for lat, lon in _densify(geom)}


def _expand(sig: set[tuple[int, int]]) -> set[tuple[int, int]]:
    return {(a + da, b + db) for a, b in sig for da in (-1, 0, 1) for db in (-1, 0, 1)}


def similarity(a: list[LatLon] | set, b: list[LatLon] | set) -> float:
    """Share of the shorter route that lies along the other (0..1)."""
    sa = a if isinstance(a, set) else signature(a)
    sb = b if isinstance(b, set) else signature(b)
    if not sa or not sb:
        return 0.0
    small, big = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    big_x = _expand(big)
    return sum(1 for c in small if c in big_x) / len(small)


def filter_alternatives(routes: list[Route], max_routes: int = 3, within: timedelta = timedelta(minutes=10),
                        within_pct: float = 20.0, dedupe: float = 0.85) -> list[Route]:
    if not routes:
        return []
    routes = sorted(routes, key=lambda r: r.duration_s)
    fastest = routes[0].duration_s
    limit = fastest + max(within.total_seconds(), fastest * within_pct / 100)
    kept: list[tuple[Route, set]] = []
    for r in routes:
        if r.duration_s > limit:
            continue
        sig = signature(r.geometry)
        if any(similarity(sig, s) > dedupe for _, s in kept):
            continue
        kept.append((r, sig))
        if len(kept) >= max_routes:
            break
    return [r for r, _ in kept]


def downsample(geom: list[LatLon], n: int = 60) -> list[LatLon]:
    if len(geom) <= n:
        return [(round(a, 5), round(b, 5)) for a, b in geom]
    step = (len(geom) - 1) / (n - 1)
    return [(round(geom[round(i * step)][0], 5), round(geom[round(i * step)][1], 5)) for i in range(n)]


def match_selection(routes: list[Route], previous: list[LatLon] | None, min_sim: float = 0.6) -> int | None:
    """Index of the route most like the previously selected geometry, or None."""
    if not previous or not routes:
        return None
    prev_sig = signature(previous)
    scores = [similarity(prev_sig, signature(r.geometry)) for r in routes]
    best = max(range(len(routes)), key=lambda i: scores[i])
    return best if scores[best] >= min_sim else None
