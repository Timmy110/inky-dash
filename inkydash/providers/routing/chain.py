"""Provider chain per mode: first that answers wins, with a circuit breaker (DESIGN.md §6.3)."""

from __future__ import annotations

import logging
import time
from datetime import datetime

from inkydash.providers.cache import ProviderError
from inkydash.providers.routing.base import LatLon, Route, RoutingProvider

log = logging.getLogger(__name__)

BREAK_AFTER = 3
BREAK_FOR_S = 30 * 60


class RoutingChain:
    def __init__(self, mode: str, providers: list[RoutingProvider]):
        self.mode = mode
        self.providers = providers
        self._fails: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def candidates(self, live_only: bool = False, exclude_live: bool = False) -> list[RoutingProvider]:
        now = time.monotonic()
        out = []
        for p in self.providers:
            if not p.available or self.mode not in p.modes:
                continue
            if live_only and not p.live_traffic:
                continue
            if exclude_live and p.live_traffic:
                continue
            if self._open_until.get(p.name, 0) > now:
                continue
            out.append(p)
        return out

    def routes(self, a: LatLon, b: LatLon, *, depart: datetime | None = None, arrive: datetime | None = None,
               alternatives: int = 0, live_only: bool = False, exclude_live: bool = False) -> list[Route]:
        errors = []
        for p in self.candidates(live_only, exclude_live):
            try:
                routes = p.routes(a, b, self.mode, depart=depart, arrive=arrive, alternatives=alternatives)
                if not routes:
                    raise ProviderError(f"{p.name}: no route")
                self._fails[p.name] = 0
                return routes
            except Exception as e:  # any adapter bug must not stop the chain
                n = self._fails.get(p.name, 0) + 1
                self._fails[p.name] = n
                if n >= BREAK_AFTER:
                    self._open_until[p.name] = time.monotonic() + BREAK_FOR_S
                    log.warning("%s failed %d times: skipping it for 30 min", p.name, n)
                log.warning("routing via %s failed: %s", p.name, e)
                errors.append(f"{p.name}: {e}")
        raise ProviderError("no routing provider answered" + (f" ({'; '.join(errors)})" if errors else
                                                              " (none configured: add an API key)"))
