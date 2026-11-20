"""Address → lat/lon, geocoded once and cached forever (Nominatim policy requires caching)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from inkydash.providers.cache import DiskCache, Http, ProviderError

if TYPE_CHECKING:
    from inkydash.config import ProvidersConfig

FOREVER = 10 * 365 * 86400


class Geocoder:
    def __init__(self, http: Http, cache: DiskCache, cfg: ProvidersConfig):
        self.http = http
        self.cache = cache
        self.cfg = cfg

    def lookup(self, address: str) -> tuple[float, float]:
        kind = self.cfg.geocoder
        cached = self.cache.get(f"geocode:{kind}:{address.strip().lower()}", FOREVER,
                                lambda: list(self._fetch(kind, address)))
        lat, lon = cached.value
        return float(lat), float(lon)

    def _fetch(self, kind: str, q: str) -> tuple[float, float]:
        if kind == "tomtom" and self.cfg.tomtom.key:
            j = self.http.get_json(f"https://api.tomtom.com/search/2/geocode/{q}.json",
                                   params={"key": self.cfg.tomtom.key, "limit": 1}, provider="tomtom")
            res = j.get("results") or []
            if res:
                p = res[0]["position"]
                return p["lat"], p["lon"]
        elif kind == "here" and self.cfg.here.key:
            j = self.http.get_json("https://geocode.search.hereapi.com/v1/geocode",
                                   params={"q": q, "apiKey": self.cfg.here.key, "limit": 1}, provider="here")
            items = j.get("items") or []
            if items:
                p = items[0]["position"]
                return p["lat"], p["lng"]
        elif kind == "mapbox" and self.cfg.mapbox.key:
            j = self.http.get_json("https://api.mapbox.com/search/geocode/v6/forward",
                                   params={"q": q, "access_token": self.cfg.mapbox.key, "limit": 1},
                                   provider="mapbox")
            feats = j.get("features") or []
            if feats:
                lon, lat = feats[0]["geometry"]["coordinates"][:2]
                return lat, lon
        else:
            j = self.http.get_json("https://nominatim.openstreetmap.org/search",
                                   params={"q": q, "format": "jsonv2", "limit": 1}, provider="nominatim",
                                   min_interval_s=1.1, limit=50)
            if j:
                return float(j[0]["lat"]), float(j[0]["lon"])
        raise ProviderError(f"geocoder {kind}: no result for {q!r}")
