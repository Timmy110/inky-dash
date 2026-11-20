"""UI strings (JSON tables) plus locale-aware formatting of dates, times and units."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, tzinfo
from functools import lru_cache
from pathlib import Path
from typing import Any

_DIR = Path(__file__).parent


@lru_cache(maxsize=16)
def strings(lang: str) -> dict[str, str]:
    base = json.loads((_DIR / "en.json").read_text(encoding="utf-8"))
    if lang != "en":
        p = _DIR / f"{lang}.json"
        if p.is_file():
            base.update(json.loads(p.read_text(encoding="utf-8")))
    return base


def _cap(s: str) -> str:
    """Upper-case the first letter only (str.capitalize() would lower-case "November")."""
    return s[:1].upper() + s[1:]


def available_languages() -> list[str]:
    return sorted(p.stem for p in _DIR.glob("*.json"))


class Formatter:
    def __init__(self, language: str = "en", units: str = "metric", time_format: str = "24h",
                 tz: tzinfo | None = None):
        self.language = language
        self.units = units
        self.time_format = time_format
        self.tz = tz
        self._strings = strings(language)

    # -- strings
    def t(self, key: str, **kw: Any) -> str:
        s = self._strings.get(key, key)
        if kw:
            try:
                return s.format(**kw)
            except (KeyError, IndexError, ValueError):
                return s
        return s

    # -- dates and times
    def local(self, dt: datetime) -> datetime:
        return dt.astimezone(self.tz) if self.tz else dt

    def time(self, dt: datetime) -> str:
        dt = self.local(dt)
        if self.time_format == "12h":
            h = dt.hour % 12 or 12
            return f"{h}:{dt.minute:02d}{'am' if dt.hour < 12 else 'pm'}"
        return f"{dt.hour:02d}:{dt.minute:02d}"

    def hour(self, dt: datetime) -> str:
        dt = self.local(dt)
        if self.time_format == "12h":
            h = dt.hour % 12 or 12
            return f"{h}{'a' if dt.hour < 12 else 'p'}"
        return f"{dt.hour:02d}"

    def _babel(self, d: date | datetime, pattern: str) -> str:
        try:
            from babel.dates import format_date

            return format_date(d, pattern, locale=self.language)
        except Exception:
            return d.strftime({"EEE": "%a", "EEEE": "%A", "d MMM": "%d %b",
                               "EEEE d MMMM": "%A %d %B", "EEE d": "%a %d"}.get(pattern, "%Y-%m-%d"))

    def weekday_short(self, d: date) -> str:
        return _cap(self._babel(d, "EEE").rstrip("."))

    def weekday_long(self, d: date) -> str:
        return _cap(self._babel(d, "EEEE"))

    def date_long(self, d: date) -> str:
        return _cap(self._babel(d, "EEEE d MMMM"))

    def date_short(self, d: date) -> str:
        return self._babel(d, "d MMM")

    # -- units (inputs are always metric)
    def temp(self, celsius: float | None, unit: bool = False) -> str:
        if celsius is None:
            return "–"
        v = celsius * 9 / 5 + 32 if self.units == "imperial" else celsius
        s = f"{round(v):d}°"
        if unit:
            s += "F" if self.units == "imperial" else "C"
        return s.replace("-", "−")

    def speed(self, kmh: float | None) -> str:
        if kmh is None:
            return "–"
        if self.units == "imperial":
            return f"{round(kmh / 1.609344)} mph"
        return f"{round(kmh)} km/h"

    def distance(self, meters: float) -> str:
        if self.units == "imperial":
            miles = meters / 1609.344
            return f"{miles:.1f} mi" if miles < 10 else f"{round(miles)} mi"
        km = meters / 1000
        return f"{km:.1f} km" if km < 10 else f"{round(km)} km"

    def precip(self, mm: float) -> str:
        if self.units == "imperial":
            return f"{mm / 25.4:.2f} in"
        return f"{mm:.1f} mm" if mm < 10 else f"{round(mm)} mm"

    def minutes(self, td: timedelta | float, sign: bool = False) -> str:
        """Durations: "42 min", "1 h 05"; with sign "+12 min" / "−4 min"."""
        secs = td.total_seconds() if isinstance(td, timedelta) else float(td)
        m = round(secs / 60)
        prefix = ""
        if sign:
            prefix = "+" if m > 0 else ("−" if m < 0 else "±")
            m = abs(m)
        if m >= 60:
            return f"{prefix}{m // 60} h {m % 60:02d}"
        return f"{prefix}{m} {self.t('min')}"
