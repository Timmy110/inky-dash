"""Official weather warnings from the MeteoAlarm Atom feeds (no key)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from inkydash.providers.cache import DiskCache, Http, ProviderError

FEED_URL = "https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-{country}"
NS = {"a": "http://www.w3.org/2005/Atom", "cap": "urn:oasis:names:tc:emergency:cap:1.2"}

COUNTRY_FEEDS = {
    "AT": "austria", "BE": "belgium", "BG": "bulgaria", "CH": "switzerland", "CY": "cyprus", "CZ": "czechia",
    "DE": "germany", "DK": "denmark", "EE": "estonia", "ES": "spain", "FI": "finland", "FR": "france",
    "GB": "united-kingdom", "GR": "greece", "HR": "croatia", "HU": "hungary", "IE": "ireland", "IS": "iceland",
    "IT": "italy", "LT": "lithuania", "LU": "luxembourg", "LV": "latvia", "MT": "malta", "NL": "netherlands",
    "NO": "norway", "PL": "poland", "PT": "portugal", "RO": "romania", "RS": "serbia", "SE": "sweden",
    "SI": "slovenia", "SK": "slovakia",
}
LEVELS = {"yellow": 2, "orange": 3, "red": 4}


@dataclass
class Warning:
    level: int            # 2 yellow, 3 orange, 4 red
    color: str
    event: str            # "Wind", "Thunderstorm", …
    area: str
    onset: datetime | None
    expires: datetime | None


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except ValueError:
        return None


def parse_feed(xml_text: str, now: datetime, region: str | None = None,
               lookahead: timedelta = timedelta(hours=12)) -> list[Warning]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ProviderError(f"meteoalarm: bad XML ({e})") from e
    out: list[Warning] = []
    for e in root.findall("a:entry", NS):
        def g(tag: str, entry: ET.Element = e) -> str:
            el = entry.find(tag, NS)
            return (el.text or "").strip() if el is not None else ""

        area = g("cap:areaDesc")
        if region and region.lower() not in area.lower():
            continue
        # "2; yellow; Moderate"
        parts = [p.strip().lower() for p in g("cap:awareness_level").split(";")]
        color = parts[1] if len(parts) > 1 else ""
        level = LEVELS.get(color, 0)
        if level < 2:
            continue
        onset, expires = _dt(g("cap:onset") or g("cap:effective")), _dt(g("cap:expires"))
        if expires and expires <= now:
            continue
        if onset and onset > now + lookahead:
            continue
        typ = g("cap:awareness_type").split(";")
        event = typ[1].strip() if len(typ) > 1 else (g("cap:event") or g("a:title"))
        out.append(Warning(level, color, event, area, onset, expires))
    # strongest first, then soonest; one per (event, color)
    out.sort(key=lambda w: (-w.level, w.onset or now))
    seen, uniq = set(), []
    for w in out:
        k = (w.event.lower(), w.color)
        if k not in seen:
            seen.add(k)
            uniq.append(w)
    return uniq


class MeteoAlarm:
    name = "meteoalarm"

    def __init__(self, http: Http, cache: DiskCache):
        self.http = http
        self.cache = cache

    def warnings(self, country: str, now: datetime, region: str | None = None, ttl_s: float = 900):
        feed = COUNTRY_FEEDS.get(country.upper(), country.lower())
        url = FEED_URL.format(country=feed)
        cached = self.cache.get(f"meteoalarm:{feed}", ttl_s,
                                lambda: self.http.request("GET", url, provider=self.name, limit=300).text,
                                max_stale_s=6 * 3600)
        return cached, parse_feed(cached.value, now, region)
