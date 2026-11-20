"""Every screen renders in both orientations and in two languages, palette-pure, with
recorded-style provider responses (no network)."""

import pytest

from inkydash.config import parse_config
from inkydash.providers.registry import Providers
from inkydash.render_service import RenderService
from inkydash.state import StateStore

from .conftest import NOW, config_dict


def service(tmp_path, photos, orientation="landscape", language="en", **overrides) -> RenderService:
    data = config_dict(photos, **overrides)
    data["display"] = {"orientation": orientation}
    data["locale"] = {"language": language}
    config = parse_config(data)
    store = StateStore(tmp_path / "state")
    return RenderService(config, Providers(config, store.root), store)


@pytest.mark.parametrize("orientation", ["landscape", "portrait"])
@pytest.mark.parametrize("language", ["en", "nl"])
@pytest.mark.parametrize("screen", ["weather", "to-work", "photos", "status", "night"])
def test_screens_render(tmp_path, photos, mock_http, screen, orientation, language):
    rs = service(tmp_path, photos, orientation, language)
    r = rs.render(screen, NOW, "slot-1")
    w, h = (800, 480) if orientation == "landscape" else (480, 800)
    assert r.image.size == (w, h)
    assert r.image.mode == "P"
    lo, hi = r.image.getextrema()
    assert hi <= 5
    assert r.seconds < 10


@pytest.mark.parametrize("view", ["overview", "hourly", "daily"])
def test_weather_views(tmp_path, photos, mock_http, view):
    rs = service(tmp_path, photos)
    r = rs.render("weather", NOW, "s", {"view": view})
    assert r.frame.meta["view"] == view


def test_weather_flex_blocks_by_priority(tmp_path, photos, mock_http):
    from inkydash.screens.weather import Data, Weather

    rs = service(tmp_path, photos)
    opts = rs.options("weather")
    ctx = rs.context("weather", NOW)
    data = Weather()._load(ctx, opts, "home")
    assert isinstance(data, Data) and data.fc and data.air
    # rain in the next 2 h, UV 6.5 ≥ 6, grass pollen moderate, AQI 59 > 40, sun always
    assert Weather()._relevant_blocks(ctx, opts, data) == ["rain_nowcast", "uv", "pollen", "air_quality", "sun"]
    assert [w.event for w in data.warnings] == ["Wind", "Rain"]


def test_weather_shows_stale_data_when_offline(tmp_path, photos, mock_http, monkeypatch):
    import httpx

    from inkydash.screens.weather import Weather

    rs = service(tmp_path, photos)
    rs.render("weather", NOW, "s")
    mock_http.routes.clear()
    mock_http.route().mock(side_effect=httpx.ConnectError("offline"))
    monkeypatch.setattr("inkydash.providers.cache.time.sleep", lambda s: None)
    for key in list(rs.providers.cache._mem):
        t, v = rs.providers.cache._mem[key]
        rs.providers.cache._mem[key] = (t - 7200, v)  # older than the TTL
    data = Weather()._load(rs.context("weather", NOW), rs.options("weather"), "home")
    assert data.fc is not None and data.stale_since is not None
    assert rs.render("weather", NOW, "s2").image.mode == "P"


def test_navigation_live_alternatives_and_selection(tmp_path, photos, mock_http):
    from inkydash.screens.navigation import _next_route

    rs = service(tmp_path, photos)
    view: dict = {}
    r = rs.render("to-work", NOW, "s", view)
    assert r.frame.meta == {"provider": "tomtom", "live": True, "routes": 2}  # 3rd route too slow
    assert view["selected"] == 0
    ctx = rs.context("to-work", NOW, "s", view)
    assert _next_route(ctx, rs.options("to-work"))
    rs.render("to-work", NOW, "s", view)
    assert view["selected"] == 1
    # a refresh keeps the selection by geometry
    rs.render("to-work", NOW, "s2", view)
    assert view["selected"] == 1


def test_navigation_falls_back_to_osrm_without_keys(tmp_path, photos, mock_http):
    data_overrides = {"providers": {"tiles": {"style": "none"}}}
    rs = service(tmp_path, photos, **data_overrides)
    r = rs.render("to-work", NOW, "s")
    assert r.frame.meta["provider"] == "osrm" and r.frame.meta["live"] is False


def test_alert_delay(tmp_path, photos, mock_http):
    from inkydash.screens.navigation import check_alert_delay

    rs = service(tmp_path, photos)
    assert check_alert_delay(rs.context("to-work", NOW), rs.options("to-work")) == 600


def test_quiet_renders_the_wrapped_screen(tmp_path, photos, mock_http):
    rs = service(tmp_path, photos)
    a = rs.render("night", NOW, "slot-n")
    b = rs.render("photos", NOW, "slot-n")
    assert a.hash == b.hash
