"""One place that owns the HTTP client, caches and provider instances."""

from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from inkydash.providers.cache import DiskCache, Http, Usage

if TYPE_CHECKING:
    from inkydash.config import Config
    from inkydash.providers.gallery.folder import FolderSource
    from inkydash.providers.routing.chain import RoutingChain


class Providers:
    def __init__(self, config: Config, state_root: Path):
        self.config = config
        self.root = Path(state_root)
        self.cache = DiskCache(self.root / "cache")
        self.usage = Usage(self.root / "usage.json")
        self._chains: dict[str, RoutingChain] = {}
        self._galleries: dict[str, FolderSource] = {}

    @cached_property
    def http(self) -> Http:
        return Http(self.usage)

    @cached_property
    def weather(self):
        from inkydash.providers.openmeteo import OpenMeteo

        return OpenMeteo(self.http, self.cache)

    @cached_property
    def warnings(self):
        from inkydash.providers.meteoalarm import MeteoAlarm

        return MeteoAlarm(self.http, self.cache)

    @cached_property
    def geocoder(self):
        from inkydash.providers.geocode import Geocoder

        return Geocoder(self.http, self.cache, self.config.providers)

    @cached_property
    def tiles(self):
        from inkydash.providers.tiles import TileSource

        return TileSource(self.http, self.config.providers.tiles, self.root / "tiles")

    def latlon(self, location_key: str) -> tuple[float, float]:
        loc = self.config.locations[location_key]
        if loc.lat is not None and loc.lon is not None:
            return loc.lat, loc.lon
        assert loc.address
        return self.geocoder.lookup(loc.address)

    def routing(self, mode: str) -> RoutingChain:
        if mode not in self._chains:
            from inkydash.providers.routing.adapters import ADAPTERS
            from inkydash.providers.routing.chain import RoutingChain

            pc = self.config.providers
            chain = []
            for name in pc.routing.get(mode, ["osrm"]):  # type: ignore[call-overload]
                p = getattr(pc, name)
                chain.append(ADAPTERS[name](self.http, self.cache, key=getattr(p, "key", None),
                                            base_url=p.base_url, max_daily_calls=p.max_daily_calls))
            self._chains[mode] = RoutingChain(mode, chain)
        return self._chains[mode]

    def gallery(self, path: str, rescan_s: float = 3600, name: str | None = None) -> FolderSource:
        key = f"{path}|{name}"
        if key not in self._galleries:
            from inkydash.providers.gallery.folder import FolderSource

            self._galleries[key] = FolderSource(path, self.root, rescan_s, name)
        return self._galleries[key]

    def flush(self) -> None:
        self.usage.flush()

    def close(self) -> None:
        self.flush()
        if "http" in self.__dict__:
            self.http.close()
