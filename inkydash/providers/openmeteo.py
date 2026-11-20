"""Open-Meteo forecast + air quality (free, no key). Normalised into plain dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from inkydash.providers.cache import DiskCache, Http, ProviderError

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

POLLEN_TYPES = ("alder", "birch", "grass", "mugwort", "olive", "ragweed")
# grains/m³ at which a type counts as moderate / high / very high (approximate, EAN-style)
POLLEN_LEVELS = {
    "alder": (10, 100, 1000), "birch": (10, 100, 1000), "olive": (10, 100, 1000),
    "grass": (5, 30, 100), "mugwort": (5, 30, 100), "ragweed": (5, 30, 100),
}
AQI_LEVELS = ((20, "good"), (40, "fair"), (60, "moderate"), (80, "poor"), (100, "very_poor"))


def _ts(v: int | None) -> datetime | None:
    return datetime.fromtimestamp(v, UTC) if v is not None else None


@dataclass
class Current:
    time: datetime
    temp: float | None
    feels_like: float | None
    code: int | None
    is_day: bool
    wind: float | None
    gusts: float | None
    wind_dir: float | None
    humidity: float | None


@dataclass
class Hour:
    time: datetime
    temp: float | None
    precip_prob: float | None
    precip: float | None
    code: int | None
    is_day: bool


@dataclass
class Day:
    date: datetime  # local midnight as UTC instant
    code: int | None
    tmax: float | None
    tmin: float | None
    precip_prob: float | None
    precip: float | None
    sunrise: datetime | None
    sunset: datetime | None
    uv_max: float | None
    wind_max: float | None


@dataclass
class Forecast:
    current: Current
    quarter: list[tuple[datetime, float]]   # 15-minute precipitation (mm per 15 min)
    hours: list[Hour]
    days: list[Day]
    utc_offset_s: int = 0


@dataclass
class AirQuality:
    aqi: float | None
    aqi_max_today: float | None
    uv_now: float | None
    pollen: dict[str, float] = field(default_factory=dict)   # today's max per type

    @staticmethod
    def aqi_level(v: float | None) -> str | None:
        if v is None:
            return None
        for limit, name in AQI_LEVELS:
            if v <= limit:
                return name
        return "extremely_poor"

    @staticmethod
    def pollen_level(kind: str, v: float | None) -> int:
        """0 none/low, 1 moderate, 2 high, 3 very high."""
        if not v:
            return 0
        m, h, vh = POLLEN_LEVELS.get(kind, (10, 100, 1000))
        return 3 if v >= vh else 2 if v >= h else 1 if v >= m else 0


def parse_forecast(j: dict) -> Forecast:
    try:
        c = j["current"]
        current = Current(
            time=_ts(c["time"]),  # type: ignore[arg-type]
            temp=c.get("temperature_2m"), feels_like=c.get("apparent_temperature"),
            code=c.get("weather_code"), is_day=bool(c.get("is_day", 1)),
            wind=c.get("wind_speed_10m"), gusts=c.get("wind_gusts_10m"), wind_dir=c.get("wind_direction_10m"),
            humidity=c.get("relative_humidity_2m"),
        )
        m = j.get("minutely_15") or {}
        quarter = [(_ts(t), p or 0.0) for t, p in zip(m.get("time", []), m.get("precipitation", []), strict=False)]
        h = j["hourly"]
        hours = [
            Hour(_ts(t), h["temperature_2m"][i], h["precipitation_probability"][i], h["precipitation"][i],
                 h["weather_code"][i], bool(h["is_day"][i]))  # type: ignore[arg-type]
            for i, t in enumerate(h["time"])
        ]
        d = j["daily"]
        days = [
            Day(_ts(t), d["weather_code"][i], d["temperature_2m_max"][i], d["temperature_2m_min"][i],  # type: ignore[arg-type]
                d["precipitation_probability_max"][i], d["precipitation_sum"][i],
                _ts(d["sunrise"][i]), _ts(d["sunset"][i]), d["uv_index_max"][i], d["wind_speed_10m_max"][i])
            for i, t in enumerate(d["time"])
        ]
    except (KeyError, IndexError, TypeError) as e:
        raise ProviderError(f"open-meteo: unexpected response ({e})") from e
    return Forecast(current, quarter, hours, days, j.get("utc_offset_seconds", 0))  # type: ignore[arg-type]


def parse_air(j: dict, now: datetime) -> AirQuality:
    try:
        cur = j.get("current") or {}
        h = j["hourly"]
        times = [_ts(t) for t in h["time"]]
        offset = j.get("utc_offset_seconds", 0)
        local_day = datetime.fromtimestamp(now.timestamp() + offset, UTC).date()
        idx = [i for i, t in enumerate(times) if t and datetime.fromtimestamp(t.timestamp() + offset, UTC).date()
               == local_day]

        def day_max(key: str) -> float | None:
            vals = [h[key][i] for i in idx if h.get(key) and h[key][i] is not None]
            return max(vals) if vals else None

        pollen = {}
        for p in POLLEN_TYPES:
            v = day_max(f"{p}_pollen")
            if v is not None:
                pollen[p] = v
        return AirQuality(cur.get("european_aqi"), day_max("european_aqi"), cur.get("uv_index"), pollen)
    except (KeyError, IndexError, TypeError) as e:
        raise ProviderError(f"open-meteo air quality: unexpected response ({e})") from e


class OpenMeteo:
    name = "open-meteo"

    def __init__(self, http: Http, cache: DiskCache):
        self.http = http
        self.cache = cache

    def forecast(self, lat: float, lon: float, ttl_s: float = 900):
        params = {
            "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "timezone": "auto", "timeformat": "unixtime",
            "current": "temperature_2m,apparent_temperature,weather_code,is_day,wind_speed_10m,wind_gusts_10m,"
                       "wind_direction_10m,relative_humidity_2m",
            "minutely_15": "precipitation", "forecast_minutely_15": 16,
            "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code,is_day",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                     "precipitation_sum,sunrise,sunset,uv_index_max,wind_speed_10m_max",
            "forecast_days": 7, "wind_speed_unit": "kmh",
        }
        key = f"openmeteo:forecast:{lat:.4f},{lon:.4f}"
        cached = self.cache.get(key, ttl_s, lambda: self.http.get_json(FORECAST_URL, params=params,
                                                                        provider=self.name, limit=2000),
                                max_stale_s=36 * 3600)
        return cached, parse_forecast(cached.value)

    def air_quality(self, lat: float, lon: float, now: datetime, ttl_s: float = 3600):
        params = {
            "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "timezone": "auto", "timeformat": "unixtime",
            "current": "european_aqi,uv_index",
            "hourly": "european_aqi,uv_index," + ",".join(f"{p}_pollen" for p in POLLEN_TYPES),
            "forecast_days": 2,
        }
        key = f"openmeteo:air:{lat:.4f},{lon:.4f}"
        cached = self.cache.get(key, ttl_s, lambda: self.http.get_json(AIR_URL, params=params,
                                                                        provider=self.name, limit=2000),
                                max_stale_s=36 * 3600)
        return cached, parse_air(cached.value, now)
