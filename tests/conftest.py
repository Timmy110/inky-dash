"""Shared fixtures: configs, recorded-style provider responses and mocked HTTP."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from inkydash.config import parse_config
from inkydash.providers.routing.polyline import encode_google

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 16, 6, 20, tzinfo=UTC)  # Monday 07:20 in Brussels

BASE_CONFIG = {
    "timezone": "Europe/Brussels",
    "holidays": {"country": "BE"},
    "locations": {
        "home": {"lat": 50.85, "lon": 4.35, "label": "Home"},
        "work": {"lat": 51.054, "lon": 3.722, "label": "Office"},
    },
    "providers": {"tomtom": {"key": "test-key"}, "tiles": {"style": "none"}},
    "screens": {
        "weather": {"type": "weather", "location": "home"},
        "to-work": {"type": "navigation", "from": "home", "to": "work", "arrive_by": "09:00"},
        "photos": {"type": "gallery", "source": "PHOTOS"},
        "status": {"type": "status"},
        "night": {"type": "quiet", "show": "photos"},
    },
    "schedule": [
        {"name": "weather-daytime", "when": {"every": "day", "at": "06:00", "until": "22:00", "refresh": "30m"},
         "screen": "weather"},
        {"name": "weekday-commute", "when": {"every": "week", "days": ["mon", "tue", "wed", "thu", "fri"],
                                             "at": "07:15", "until": "08:45", "refresh": "10m",
                                             "skip_on": ["holiday", "off"]}, "screen": "to-work"},
        {"name": "evening", "when": {"every": "day", "at": "18:00"}, "screens": ["weather", "photos"],
         "rotate_every": "20m"},
        {"name": "night", "when": {"every": "day", "at": "23:00"}, "screen": "night"},
    ],
    "default_screen": "photos",
}


def config_dict(photos: Path | str = "photos", **overrides) -> dict:
    d = json.loads(json.dumps(BASE_CONFIG).replace("PHOTOS", str(photos).replace("\\", "/")))
    d.update(overrides)
    return d


@pytest.fixture
def config_data(tmp_path):
    return config_dict(tmp_path / "photos")


@pytest.fixture
def config(config_data):
    return parse_config(config_data)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYDASH_STATE", str(tmp_path / "state"))
    for var in ("TOMTOM_API_KEY", "INKYDASH_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "state"


@pytest.fixture
def photos(tmp_path):
    from PIL import Image

    folder = tmp_path / "photos"
    folder.mkdir()
    colors = [(200, 30, 30), (30, 200, 30), (30, 30, 200), (220, 220, 40), (240, 240, 240)]
    for i, col in enumerate(colors):
        size = (1200, 800) if i % 2 == 0 else (600, 900)
        Image.new("RGB", size, col).save(folder / f"img{i}.jpg")
    return folder


# ---------------------------------------------------------------- provider responses
def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def forecast_json(now: datetime = NOW, rain: bool = True) -> dict:
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    hours = [start + timedelta(hours=i) for i in range(48)]
    q0 = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    quarters = [q0 + timedelta(minutes=15 * i) for i in range(16)]
    day0 = datetime(now.year, now.month, now.day, tzinfo=UTC) - timedelta(hours=1)
    days = [day0 + timedelta(days=i) for i in range(7)]
    return {
        "utc_offset_seconds": 3600,
        "current": {"time": _ts(now), "temperature_2m": 7.4, "apparent_temperature": 4.1, "weather_code": 61,
                    "is_day": 1, "wind_speed_10m": 18.0, "wind_gusts_10m": 45.0, "wind_direction_10m": 220,
                    "relative_humidity_2m": 88},
        "minutely_15": {"time": [_ts(t) for t in quarters],
                        "precipitation": [0, 0, 0.3, 0.6, 0.4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] if rain else [0] * 16},
        "hourly": {
            "time": [_ts(t) for t in hours],
            "temperature_2m": [6 + (i % 12) * 0.5 for i in range(48)],
            "precipitation_probability": [(i * 17) % 100 for i in range(48)],
            "precipitation": [0.2 if i % 5 == 0 else 0 for i in range(48)],
            "weather_code": [[0, 2, 3, 45, 61, 71, 95, 53][i % 8] for i in range(48)],
            "is_day": [1 if 7 <= (i + 6) % 24 <= 17 else 0 for i in range(48)],
        },
        "daily": {
            "time": [_ts(d) for d in days],
            "weather_code": [61, 3, 0, 2, 71, 95, 45],
            "temperature_2m_max": [9.5, 8, 11, 12, 3, 7, 6],
            "temperature_2m_min": [3, 2, 4, 6, -2, 1, 0],
            "precipitation_probability_max": [80, 20, 5, 10, 60, 90, 30],
            "precipitation_sum": [4.2, 0.3, 0, 0, 2, 12, 0.5],
            "sunrise": [_ts(d + timedelta(hours=8, minutes=5)) for d in days],
            "sunset": [_ts(d + timedelta(hours=16, minutes=50)) for d in days],
            "uv_index_max": [6.5, 2, 1, 1, 1, 1, 1],
            "wind_speed_10m_max": [30, 20, 10, 12, 15, 40, 8],
        },
    }


def air_json(now: datetime = NOW) -> dict:
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=6)
    hours = [start + timedelta(hours=i) for i in range(48)]
    return {
        "utc_offset_seconds": 3600,
        "current": {"european_aqi": 52, "uv_index": 1.5},
        "hourly": {
            "time": [_ts(t) for t in hours],
            "european_aqi": [30 + i % 30 for i in range(48)],
            "uv_index": [0.5] * 48,
            **{f"{p}_pollen": [0.0] * 48 for p in ("alder", "birch", "mugwort", "olive", "ragweed")},
            "grass_pollen": [12.0] * 48,
        },
    }


def meteoalarm_xml(now: datetime = NOW) -> str:
    onset = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    expires = (now + timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    old = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2">
  <entry><title>Orange wind</title><cap:areaDesc>Brussels</cap:areaDesc>
    <cap:awareness_level>3; orange; Severe</cap:awareness_level><cap:awareness_type>1; Wind</cap:awareness_type>
    <cap:onset>{onset}</cap:onset><cap:expires>{expires}</cap:expires></entry>
  <entry><title>Yellow rain</title><cap:areaDesc>Antwerp</cap:areaDesc>
    <cap:awareness_level>2; yellow; Moderate</cap:awareness_level><cap:awareness_type>10; Rain</cap:awareness_type>
    <cap:onset>{onset}</cap:onset><cap:expires>{expires}</cap:expires></entry>
  <entry><title>Expired</title><cap:areaDesc>Brussels</cap:areaDesc>
    <cap:awareness_level>4; red; Extreme</cap:awareness_level><cap:awareness_type>2; Snow-ice</cap:awareness_type>
    <cap:onset>{old}</cap:onset><cap:expires>{old}</cap:expires></entry>
  <entry><title>Green</title><cap:areaDesc>Brussels</cap:areaDesc>
    <cap:awareness_level>1; green; Minor</cap:awareness_level><cap:awareness_type>1; Wind</cap:awareness_type>
  </entry>
</feed>"""


def _line(a, b, n=40):
    return [(a[0] + (b[0] - a[0]) * i / (n - 1), a[1] + (b[1] - a[1]) * i / (n - 1)) for i in range(n)]


HOME, WORK = (50.85, 4.35), (51.054, 3.722)
ROUTE_E40 = _line(HOME, (50.95, 4.0)) + _line((50.95, 4.0), WORK)[1:]
ROUTE_R0 = _line(HOME, (50.80, 3.95)) + _line((50.80, 3.95), WORK)[1:]
ROUTE_FAR = _line(HOME, (51.20, 4.40)) + _line((51.20, 4.40), WORK)[1:]


def tomtom_json(live: float = 2700, typical: float = 2100) -> dict:
    def route(geom, secs, typ, sections=()):
        return {
            "summary": {"lengthInMeters": 56000, "travelTimeInSeconds": secs, "trafficDelayInSeconds": secs - typ,
                        "noTrafficTravelTimeInSeconds": typ - 300, "historicTrafficTravelTimeInSeconds": typ},
            "legs": [{"points": [{"latitude": a, "longitude": b} for a, b in geom]}],
            "sections": list(sections),
            "guidance": {"instructions": [
                {"routeOffsetInMeters": 0, "street": "Wetstraat", "roadNumbers": []},
                {"routeOffsetInMeters": 3000, "street": "", "roadNumbers": ["E40"]},
                {"routeOffsetInMeters": 50000, "street": "Kortrijksesteenweg", "roadNumbers": ["N43"]},
                {"routeOffsetInMeters": 56000, "street": "", "roadNumbers": []},
            ]},
        }
    jam = {"sectionType": "TRAFFIC", "startPointIndex": 10, "endPointIndex": 25, "simpleCategory": "JAM",
           "magnitudeOfDelay": 3, "delayInSeconds": 540}
    work = {"sectionType": "TRAFFIC", "startPointIndex": 50, "endPointIndex": 55, "simpleCategory": "ROAD_WORK",
            "magnitudeOfDelay": 1, "delayInSeconds": 60}
    return {"routes": [route(ROUTE_E40, live, typical, [jam, work]), route(ROUTE_R0, live + 240, typical + 200),
                       route(ROUTE_FAR, live + 1500, typical + 1400)]}


def osrm_json() -> dict:
    return {"code": "Ok", "routes": [{
        "duration": 2400, "distance": 56000, "geometry": encode_google(ROUTE_E40, 6),
        "legs": [{"steps": [{"name": "Wetstraat", "ref": "", "distance": 3000},
                            {"name": "", "ref": "E 40", "distance": 47000},
                            {"name": "Kortrijksesteenweg", "ref": "N43", "distance": 6000}]}]}]}


@pytest.fixture
def mock_http():
    with respx.mock(assert_all_called=False) as m:
        m.get(url__startswith="https://api.open-meteo.com/v1/forecast").mock(
            return_value=httpx.Response(200, json=forecast_json()))
        m.get(url__startswith="https://air-quality-api.open-meteo.com").mock(
            return_value=httpx.Response(200, json=air_json()))
        m.get(url__startswith="https://feeds.meteoalarm.org").mock(
            return_value=httpx.Response(200, text=meteoalarm_xml()))
        m.get(url__startswith="https://api.tomtom.com/routing").mock(
            return_value=httpx.Response(200, json=tomtom_json()))
        m.get(url__startswith="https://routing.openstreetmap.de").mock(
            return_value=httpx.Response(200, json=osrm_json()))
        yield m
