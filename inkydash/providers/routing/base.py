"""The normalised Route model every routing adapter returns (DESIGN.md §6.3)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, ClassVar

from inkydash.providers.cache import DiskCache, Http

LatLon = tuple[float, float]


@dataclass
class TrafficSection:
    start_idx: int
    end_idx: int
    category: str          # "jam" | "roadwork" | "closure" | "slow"
    magnitude: int         # 0..4 (TomTom scale)
    delay_s: float | None = None
    description: str | None = None


@dataclass
class Route:
    duration_s: float                       # live (or predicted for a future departure)
    distance_m: float
    geometry: list[LatLon]
    duration_typical_s: float | None = None
    duration_free_flow_s: float | None = None
    traffic_delay_s: float | None = None
    traffic_sections: list[TrafficSection] = field(default_factory=list)
    via: str | None = None
    live: bool = False
    provider: str = ""
    attribution: str = ""

    @property
    def duration(self) -> timedelta:
        return timedelta(seconds=self.duration_s)

    @property
    def baseline_s(self) -> float | None:
        """'Usual' if the provider has it, else free-flow."""
        return self.duration_typical_s if self.duration_typical_s is not None else self.duration_free_flow_s

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Route:
        d = dict(d)
        d["geometry"] = [tuple(p) for p in d["geometry"]]
        d["traffic_sections"] = [TrafficSection(**s) for s in d.get("traffic_sections", [])]
        return cls(**d)


class RoutingProvider:
    """Base for adapters. Subclasses implement `_routes`."""

    name: ClassVar[str] = ""
    modes: ClassVar[set[str]] = set()
    live_traffic: ClassVar[bool] = False
    typical_duration: ClassVar[bool] = False
    has_traffic_sections: ClassVar[bool] = False
    max_alternatives: ClassVar[int] = 0
    needs_key: ClassVar[bool] = True
    attribution: ClassVar[str] = ""

    def __init__(self, http: Http, cache: DiskCache, key: str | None = None, base_url: str | None = None,
                 max_daily_calls: int = 500):
        self.http = http
        self.cache = cache
        self.key = key
        self.base_url = base_url
        self.limit = max_daily_calls

    @property
    def available(self) -> bool:
        return bool(self.key) or not self.needs_key

    def routes(self, a: LatLon, b: LatLon, mode: str, *, depart: datetime | None = None,
               arrive: datetime | None = None, alternatives: int = 0) -> list[Route]:
        routes = self._routes(a, b, mode, depart=depart, arrive=arrive,
                              alternatives=min(alternatives, self.max_alternatives))
        for r in routes:
            r.provider = self.name
            r.attribution = r.attribution or self.attribution
        return routes

    def _routes(self, a: LatLon, b: LatLon, mode: str, *, depart: datetime | None,
                arrive: datetime | None, alternatives: int) -> list[Route]:
        raise NotImplementedError


def via_label(segments: list[tuple[str, float]], max_parts: int = 2) -> str | None:
    """Longest named or numbered roads: [('E40', 23000), ('R0', 4000)] → 'E40 / R0'."""
    totals: dict[str, float] = defaultdict(float)
    for name, length in segments:
        name = (name or "").strip()
        if name:
            totals[name] += length
    if not totals:
        return None
    best = sorted(totals.items(), key=lambda kv: -kv[1])
    top = best[0][1]
    parts = [n for n, length in best[:max_parts] if length >= top * 0.3]
    return " / ".join(parts)


def pick_road(names: list[str], numbers: list[str]) -> str:
    """Prefer a motorway/road number (E40, A12, R0, N9) over a street name."""
    for n in numbers:
        if n:
            return n.split(";")[0].split(",")[0].strip()
    for n in names:
        if n:
            return n
    return ""
