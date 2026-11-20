"""Raster map tiles with a long-lived disk cache (OSM tile policy: cache ≥ 7 days)."""

from __future__ import annotations

import io
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from inkydash.providers.cache import Http, ProviderError

if TYPE_CHECKING:
    from inkydash.config import TilesConfig

log = logging.getLogger(__name__)

STYLES = {
    "stadia-toner": "https://tiles.stadiamaps.com/tiles/stamen_toner/{z}/{x}/{y}.png?api_key={key}",
    "stadia-toner-lite": "https://tiles.stadiamaps.com/tiles/stamen_toner_lite/{z}/{x}/{y}.png?api_key={key}",
    "osm": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
}
ATTRIBUTION = {
    "stadia-toner": "© Stadia Maps © Stamen © OpenStreetMap",
    "stadia-toner-lite": "© Stadia Maps © Stamen © OpenStreetMap",
    "osm": "© OpenStreetMap contributors",
}
# Grey level below which a tile pixel becomes BLACK. Toner is already two-tone; Toner Lite
# draws roads in light grey, so it needs a much higher cut-off; OSM is colourful.
THRESHOLDS = {"stadia-toner": 128, "stadia-toner-lite": 215, "osm": 175}
TILE_SIZE = 256
REVALIDATE_S = 7 * 86400


class TileSource:
    def __init__(self, http: Http, cfg: TilesConfig, root: Path):
        self.http = http
        self.cfg = cfg
        self.root = root
        style = cfg.style
        if style == "auto":
            style = "stadia-toner" if cfg.stadia_key else "osm"
        self.style = style

    @property
    def enabled(self) -> bool:
        return self.style != "none" and (not self.style.startswith("stadia") or bool(self.cfg.stadia_key))

    @property
    def attribution(self) -> str:
        return ATTRIBUTION.get(self.style, "")

    @property
    def threshold(self) -> int:
        return self.cfg.threshold or THRESHOLDS.get(self.style, 160)

    def tile(self, z: int, x: int, y: int) -> Image.Image | None:
        """Greyscale tile, or None if unavailable (the map then draws on white)."""
        n = 2 ** z
        x %= n
        if not 0 <= y < n:
            return None
        path = self.root / self.style / str(z) / str(x) / f"{y}.png"
        fresh = path.is_file() and time.time() - path.stat().st_mtime < REVALIDATE_S
        if not fresh:
            url = STYLES[self.style].format(z=z, x=x, y=y, key=self.cfg.stadia_key or "")
            provider = "stadia" if self.style.startswith("stadia") else "osm-tiles"
            try:
                r = self.http.request("GET", url, provider=provider, limit=2000)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(r.content)
            except ProviderError as e:
                if not path.is_file():
                    log.warning("tile %s/%s/%s unavailable: %s", z, x, y, e)
                    return None
                path.touch()  # keep using it; try again in a week
        try:
            return Image.open(io.BytesIO(path.read_bytes())).convert("L")
        except OSError:
            path.unlink(missing_ok=True)
            return None
