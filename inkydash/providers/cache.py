"""Disk cache with TTL and stale fallback, plus HTTP and per-provider call accounting."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Generic, TypeVar

import httpx

from inkydash import USER_AGENT
from inkydash.state import atomic_write

log = logging.getLogger(__name__)
T = TypeVar("T")


class ProviderError(Exception):
    """A provider couldn't deliver (network, HTTP error, bad response, quota)."""


class QuotaExceeded(ProviderError):
    pass


@dataclass
class Cached(Generic[T]):
    value: T
    fetched_at: datetime   # UTC
    stale: bool = False    # True: the fetch failed and this is older data
    error: str | None = None

    def age_s(self, now: datetime | None = None) -> float:
        return ((now or datetime.now(UTC)) - self.fetched_at).total_seconds()


class DiskCache:
    """JSON values keyed by string, stored one file per key under state/cache/."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._mem: dict[str, tuple[float, Any]] = {}

    def _path(self, key: str) -> Path:
        h = hashlib.sha1(key.encode()).hexdigest()[:20]
        safe = "".join(ch if ch.isalnum() else "_" for ch in key.split(":", 1)[0])[:20]
        return self.root / f"{safe}-{h}.json"

    def read(self, key: str) -> tuple[float, Any] | None:
        if key in self._mem:
            return self._mem[key]
        try:
            data = json.loads(self._path(key).read_text(encoding="utf-8"))
            entry = (float(data["t"]), data["v"])
        except (OSError, ValueError, KeyError):
            return None
        self._mem[key] = entry
        return entry

    def write(self, key: str, value: Any, t: float | None = None) -> None:
        t = time.time() if t is None else t
        self._mem[key] = (t, value)
        try:
            atomic_write(self._path(key), json.dumps({"k": key, "t": t, "v": value}))
        except OSError:
            log.warning("cache write failed for %s", key, exc_info=True)

    def get(self, key: str, ttl_s: float, fetch: Callable[[], T], max_stale_s: float | None = None) -> Cached[T]:
        """Fresh value if younger than ttl; otherwise fetch. On failure return the old value
        marked stale (if not older than max_stale_s), else re-raise."""
        hit = self.read(key)
        now = time.time()
        if hit and now - hit[0] < ttl_s:
            return Cached(hit[1], datetime.fromtimestamp(hit[0], UTC))
        try:
            value = fetch()
        except Exception as e:
            if hit and (max_stale_s is None or now - hit[0] < max_stale_s):
                log.warning("%s: fetch failed (%s), using cached data from %s", key, e,
                            datetime.fromtimestamp(hit[0]).strftime("%H:%M"))
                return Cached(hit[1], datetime.fromtimestamp(hit[0], UTC), stale=True, error=str(e))
            raise
        self.write(key, value, now)
        return Cached(value, datetime.fromtimestamp(now, UTC))


class Usage:
    """Calls per provider per day, persisted (for max_daily_calls and `doctor`)."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.data: dict[str, dict[str, int]] = {}
        self.last_ok: dict[str, datetime] = {}
        self.last_error: dict[str, str] = {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.data = raw.get("days", {})
            self.last_ok = {k: datetime.fromisoformat(v) for k, v in raw.get("ok", {}).items()}
            self.last_error = raw.get("errors", {})
        except (OSError, ValueError, AttributeError):
            pass
        self._dirty = False

    def today(self, provider: str) -> int:
        return self.data.get(date.today().isoformat(), {}).get(provider, 0)

    def month(self, provider: str) -> int:
        prefix = date.today().isoformat()[:7]
        return sum(v.get(provider, 0) for d, v in self.data.items() if d.startswith(prefix))

    def count(self, provider: str, limit: int | None = None) -> None:
        with self._lock:
            day = date.today().isoformat()
            used = self.data.get(day, {}).get(provider, 0)
            if limit is not None and used >= limit:
                raise QuotaExceeded(f"{provider}: max_daily_calls ({limit}) reached")
            self.data.setdefault(day, {})[provider] = used + 1
            # keep ~2 months
            for d in sorted(self.data)[:-62]:
                del self.data[d]
            self._dirty = True

    def ok(self, provider: str) -> None:
        self.last_ok[provider] = datetime.now(UTC)
        self.last_error.pop(provider, None)
        self._dirty = True

    def failed(self, provider: str, err: str) -> None:
        self.last_error[provider] = err[:300]
        self._dirty = True

    def flush(self) -> None:
        """Written with the state, not per call (SD card wear)."""
        with self._lock:
            if self._dirty:
                atomic_write(self.path, json.dumps({
                    "days": self.data,
                    "ok": {k: v.isoformat() for k, v in self.last_ok.items()},
                    "errors": self.last_error,
                }))
                self._dirty = False


class Http:
    """Shared httpx client: User-Agent, timeouts, small retry, call counting."""

    def __init__(self, usage: Usage, timeout: float = 15.0, retries: int = 2):
        self.usage = usage
        self.retries = retries
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
        )
        self._polite: dict[str, float] = {}

    def request(self, method: str, url: str, *, provider: str, limit: int | None = None,
                min_interval_s: float = 0.0, **kw: Any) -> httpx.Response:
        if min_interval_s:
            wait = self._polite.get(provider, 0) + min_interval_s - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        self.usage.count(provider, limit)
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                r = self.client.request(method, url, **kw)
                self._polite[provider] = time.monotonic()
                if r.status_code in (429,) or r.status_code >= 500:
                    raise ProviderError(f"{provider}: HTTP {r.status_code}")
                if r.status_code >= 400:
                    # client errors (bad key, bad request) won't get better with a retry
                    msg = f"{provider}: HTTP {r.status_code} {r.text[:200]}"
                    self.usage.failed(provider, msg)
                    raise ProviderError(msg)
                self.usage.ok(provider)
                return r
            except ProviderError as e:
                last = e
                if "HTTP 4" in str(e) and "HTTP 429" not in str(e):
                    raise
            except httpx.HTTPError as e:
                last = ProviderError(f"{provider}: {type(e).__name__}: {e}")
            if attempt < self.retries:
                time.sleep(1.5 * (attempt + 1))
        assert last is not None
        self.usage.failed(provider, str(last))
        raise last

    def get_json(self, url: str, *, provider: str, **kw: Any) -> Any:
        r = self.request("GET", url, provider=provider, **kw)
        try:
            return r.json()
        except ValueError as e:
            raise ProviderError(f"{provider}: response is not JSON") from e

    def close(self) -> None:
        self.client.close()
