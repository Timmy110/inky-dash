from datetime import timedelta

import httpx
import pytest
import respx

from inkydash.providers.cache import DiskCache, Http, ProviderError, QuotaExceeded, Usage
from inkydash.providers.meteoalarm import parse_feed
from inkydash.providers.openmeteo import AirQuality, parse_air, parse_forecast
from inkydash.providers.routing import adapters
from inkydash.providers.routing.alternatives import filter_alternatives, match_selection, similarity
from inkydash.providers.routing.base import Route, RoutingProvider, via_label
from inkydash.providers.routing.chain import RoutingChain
from inkydash.providers.routing.polyline import decode_flexible, decode_google, encode_flexible, encode_google

from .conftest import (
    HOME,
    NOW,
    ROUTE_E40,
    ROUTE_FAR,
    ROUTE_R0,
    WORK,
    air_json,
    forecast_json,
    meteoalarm_xml,
    osrm_json,
    tomtom_json,
)

# ------------------------------------------------------------------ polylines


def test_google_polyline_known_value():
    # Example from Google's polyline documentation
    assert decode_google("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]


@pytest.mark.parametrize("precision", [5, 6])
def test_google_polyline_round_trip(precision):
    back = decode_google(encode_google(ROUTE_E40, precision), precision)
    assert all(abs(a[0] - b[0]) < 1e-5 and abs(a[1] - b[1]) < 1e-5 for a, b in zip(back, ROUTE_E40, strict=True))


def test_flexible_polyline_known_value():
    # Example from HERE's flexible-polyline README
    assert decode_flexible("BFoz5xJ67i1B1B7PzIhaxL7Y") == [
        (50.10228, 8.69821), (50.10201, 8.69567), (50.10063, 8.6915), (50.09878, 8.68752)]


def test_flexible_polyline_round_trip():
    back = decode_flexible(encode_flexible(ROUTE_R0))
    assert all(abs(a[0] - b[0]) < 1e-5 for a, b in zip(back, ROUTE_R0, strict=True))


# ------------------------------------------------------------------ adapters
def test_parse_tomtom():
    routes = adapters.parse_tomtom(tomtom_json(live=2700, typical=2100))
    r = routes[0]
    assert (r.duration_s, r.duration_typical_s, r.live) == (2700, 2100, True)
    assert r.via == "E40"
    assert len(r.geometry) == len(ROUTE_E40)
    assert [(s.category, s.magnitude, s.delay_s) for s in r.traffic_sections] == [("jam", 3, 540),
                                                                                    ("roadwork", 1, 60)]


def test_parse_osrm():
    r = adapters.parse_osrm(osrm_json())[0]
    assert (r.duration_s, r.live, r.via) == (2400, False, "E 40")
    assert abs(r.geometry[0][0] - HOME[0]) < 1e-5


def test_parse_osrm_error():
    with pytest.raises(ProviderError):
        adapters.parse_osrm({"code": "NoRoute", "message": "Impossible route"})


def test_parse_here():
    poly = encode_flexible(ROUTE_E40)
    j = {"routes": [{"sections": [{
        "polyline": poly,
        "summary": {"duration": 2600, "baseDuration": 2000, "typicalDuration": 2200, "length": 56000},
        "spans": [
            {"offset": 0, "length": 3000, "names": [{"value": "Wetstraat"}],
             "dynamicSpeedInfo": {"trafficSpeed": 12, "baseSpeed": 13}},
            {"offset": 5, "length": 40000, "routeNumbers": [{"value": "E40"}],
             "dynamicSpeedInfo": {"trafficSpeed": 5, "baseSpeed": 33}},
            {"offset": 60, "length": 13000, "routeNumbers": [{"value": "N43"}],
             "dynamicSpeedInfo": {"trafficSpeed": 15, "baseSpeed": 16}},
        ]}]}]}
    r = adapters.parse_here(j)[0]
    assert (r.duration_s, r.duration_typical_s, r.duration_free_flow_s) == (2600, 2200, 2000)
    assert r.via == "E40 / N43"
    assert len(r.traffic_sections) == 1
    s = r.traffic_sections[0]
    assert (s.start_idx, s.end_idx, s.magnitude) == (5, 60, 3)


def test_parse_mapbox():
    j = {"code": "Ok", "routes": [{
        "duration": 2500, "distance": 56000, "geometry": encode_google(ROUTE_E40, 6),
        "legs": [{"steps": [{"name": "", "ref": "E40", "distance": 50000}],
                  "annotation": {"congestion": ["low"] * 10 + ["heavy"] * 5 + ["severe"] * 3 + ["low"] * 60}}]}]}
    r = adapters.parse_mapbox(j)[0]
    assert r.via == "E40" and r.live
    assert [(s.start_idx, s.end_idx, s.magnitude) for s in r.traffic_sections] == [(10, 15, 2), (15, 18, 3)]


def test_parse_google():
    j = {"routes": [{"duration": "2580s", "staticDuration": "2100s", "distanceMeters": 56000,
                     "polyline": {"encodedPolyline": encode_google(ROUTE_E40)}, "description": "E40",
                     "travelAdvisory": {"speedReadingIntervals": [
                         {"startPolylinePointIndex": 0, "endPolylinePointIndex": 10, "speed": "NORMAL"},
                         {"startPolylinePointIndex": 10, "endPolylinePointIndex": 20, "speed": "TRAFFIC_JAM"}]}}]}
    r = adapters.parse_google(j)[0]
    assert (r.duration_s, r.duration_free_flow_s, r.traffic_delay_s, r.via) == (2580, 2100, 480, "E40")
    assert [(s.start_idx, s.magnitude) for s in r.traffic_sections] == [(10, 3)]


def test_parse_ors():
    j = {"routes": [{"summary": {"duration": 2450, "distance": 56000}, "geometry": encode_google(ROUTE_R0),
                     "segments": [{"steps": [{"name": "R0", "distance": 30000}, {"name": "-", "distance": 5}]}]}]}
    r = adapters.parse_ors(j)[0]
    assert (r.duration_s, r.via, r.live) == (2450, "R0", False)


def test_via_label():
    assert via_label([("E40", 30000), ("R0", 12000), ("Wetstraat", 500)]) == "E40 / R0"
    assert via_label([("E40", 30000), ("R0", 2000)]) == "E40"
    assert via_label([("", 100)]) is None


# ------------------------------------------------------------------ alternatives
def mk(geom, secs) -> Route:
    return Route(duration_s=secs, distance_m=50000, geometry=geom, live=True)


def test_alternatives_close_in_time_and_deduped():
    routes = [mk(ROUTE_E40, 2400), mk(ROUTE_R0, 2700), mk(ROUTE_FAR, 4000), mk(ROUTE_E40[:-1] + [WORK], 2450)]
    kept = filter_alternatives(routes, max_routes=3, within=timedelta(minutes=10), within_pct=20)
    assert [r.duration_s for r in kept] == [2400, 2700]  # far one too slow, near-duplicate dropped


def test_alternatives_pct_beats_fixed_window():
    # 40-minute fastest → window max(10 min, 8 min) = 10 min
    kept = filter_alternatives([mk(ROUTE_E40, 2400), mk(ROUTE_R0, 2400 + 590)], within_pct=20)
    assert len(kept) == 2
    kept = filter_alternatives([mk(ROUTE_E40, 2400), mk(ROUTE_R0, 2400 + 610)], within_pct=20)
    assert len(kept) == 1


def test_selection_sticks_by_geometry():
    routes = [mk(ROUTE_R0, 2400), mk(ROUTE_E40, 2450)]   # order changed since last refresh
    assert match_selection(routes, ROUTE_E40) == 1
    assert match_selection(routes, ROUTE_FAR) is None
    assert similarity(ROUTE_E40, ROUTE_E40) == 1.0


# ------------------------------------------------------------------ chain
class Fake(RoutingProvider):
    name = "fake"
    modes = {"car"}
    live_traffic = True
    needs_key = False

    def __init__(self, name, result=None, live=True):
        super().__init__(None, None)  # type: ignore[arg-type]
        self.name = name
        self.result = result
        self.live_traffic = live
        self.calls = 0

    def _routes(self, a, b, mode, **kw):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_chain_falls_back_and_breaks_circuit():
    bad = Fake("bad", ProviderError("down"))
    good = Fake("good", [mk(ROUTE_E40, 2400)])
    chain = RoutingChain("car", [bad, good])
    for _ in range(4):
        assert chain.routes(HOME, WORK)[0].provider == "good"
    assert bad.calls == 3  # skipped after 3 consecutive failures


def test_chain_skips_providers_without_key():
    keyless = adapters.TomTom(None, None, key=None)  # type: ignore[arg-type]
    assert not keyless.available
    chain = RoutingChain("car", [keyless, Fake("osrm-like", [mk(ROUTE_E40, 1)], live=False)])
    assert chain.routes(HOME, WORK)[0].provider == "osrm-like"


def test_chain_live_only_and_exclude_live():
    live, static = Fake("live", [mk(ROUTE_E40, 1)]), Fake("static", [mk(ROUTE_R0, 2)], live=False)
    chain = RoutingChain("car", [live, static])
    assert chain.routes(HOME, WORK, exclude_live=True)[0].provider == "static"
    live.result = ProviderError("x")
    with pytest.raises(ProviderError):
        chain.routes(HOME, WORK, live_only=True)


# ------------------------------------------------------------------ weather
def test_parse_forecast():
    fc = parse_forecast(forecast_json())
    assert fc.current.temp == 7.4 and fc.current.code == 61
    assert len(fc.hours) == 48 and len(fc.days) == 7 and len(fc.quarter) == 16


def test_parse_air_today_max():
    air = parse_air(air_json(), NOW)
    assert air.pollen["grass"] == 12.0
    assert AirQuality.pollen_level("grass", 12.0) == 1
    assert AirQuality.aqi_level(52) == "moderate"


def test_meteoalarm_filters_and_sorts():
    ws = parse_feed(meteoalarm_xml(), NOW)
    assert [(w.color, w.event) for w in ws] == [("orange", "Wind"), ("yellow", "Rain")]
    assert [w.area for w in parse_feed(meteoalarm_xml(), NOW, region="brussels")] == ["Brussels"]


# ------------------------------------------------------------------ cache and http
def test_cache_stale_fallback(tmp_path):
    cache = DiskCache(tmp_path)
    assert cache.get("k", 0, lambda: 1).value == 1
    got = cache.get("k", 0, lambda: (_ for _ in ()).throw(ProviderError("down")))
    assert got.value == 1 and got.stale
    with pytest.raises(ProviderError):
        DiskCache(tmp_path / "empty").get("k", 0, lambda: (_ for _ in ()).throw(ProviderError("down")))


def test_cache_survives_restart(tmp_path):
    DiskCache(tmp_path).get("k", 3600, lambda: {"a": 1})
    assert DiskCache(tmp_path).get("k", 3600, lambda: pytest.fail("should be cached")).value == {"a": 1}


def test_daily_cap(tmp_path):
    u = Usage(tmp_path / "usage.json")
    u.count("p", 2)
    u.count("p", 2)
    with pytest.raises(QuotaExceeded):
        u.count("p", 2)
    u.flush()
    assert Usage(tmp_path / "usage.json").today("p") == 2


@respx.mock
def test_http_retries_server_errors_not_client_errors(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    http = Http(Usage(tmp_path / "u.json"), retries=2)
    flaky = respx.get("https://x.test/a").mock(side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": 1})])
    assert http.get_json("https://x.test/a", provider="x") == {"ok": 1}
    assert flaky.call_count == 2
    bad = respx.get("https://x.test/b").mock(return_value=httpx.Response(403, text="bad key"))
    with pytest.raises(ProviderError, match="403"):
        http.get_json("https://x.test/b", provider="x")
    assert bad.call_count == 1
    assert "User-Agent" in flaky.calls[0].request.headers
    assert flaky.calls[0].request.headers["User-Agent"].startswith("inky-dash/")
