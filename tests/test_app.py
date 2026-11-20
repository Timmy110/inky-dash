"""The service loop: precedence, rate limiting, content hash, buttons."""

import json
from datetime import timedelta

import pytest

from inkydash.app import AlertState, App
from inkydash.buttons import Press

from .conftest import NOW, config_dict


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def app(tmp_path, photos, mock_http, monkeypatch):
    data = config_dict(photos)
    data["screens"]["to-work"]["alert"] = {
        "watch": {"every": "week", "days": ["mon", "tue", "wed", "thu", "fri"], "at": "06:30", "until": "08:45"},
        "poll": "15m", "delay_over": "10m"}
    data["buttons"] = {"enabled": False}
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(data), encoding="utf-8")
    clock = Clock()
    monkeypatch.setattr("inkydash.app.time.monotonic", clock)
    a = App(str(cfg), driver="file", out_dir=str(tmp_path / "out"))
    a.clock = clock  # type: ignore[attr-defined]
    yield a
    a.executor.shutdown(wait=True)


def settle(app):
    if app.busy:
        app.busy.result(timeout=30)


def shown(app) -> int:
    settle(app)
    return app.backend.shown  # type: ignore[attr-defined]


def test_first_step_shows_scheduled_screen(app):
    app.step(NOW)  # Monday 07:20 → commute (alert polled: +10 min is not > 10 min)
    assert shown(app) == 1
    assert app.shown.screen == "to-work"
    app.step(NOW + timedelta(seconds=30))
    assert shown(app) == 1  # same slot: nothing to do


def test_unchanged_content_skips_panel_update(app):
    app.step(NOW)
    settle(app)
    app.clock.t += 600
    app.step(NOW + timedelta(minutes=6))  # 07:26: refresh #1, same routes from the cache
    assert shown(app) == 1
    assert app.shown.slot.endswith("#0.1")


def test_rate_limit_defers_scheduled_changes(app):
    app.step(NOW)
    settle(app)
    app.clock.t += 60  # only 1 min later
    app.views.clear()
    app.override = None
    app.do_action("goto:weather", "to-work", NOW)
    app.button_render_at = None  # pretend it's a scheduled change, not a button
    app.step(NOW + timedelta(minutes=1))
    assert shown(app) == 1
    assert app.defer_until is not None
    app.clock.t += 200
    app.step(NOW + timedelta(minutes=4))
    assert shown(app) == 2


def test_button_override_until_next_trigger(app):
    app.step(NOW)
    settle(app)
    app.do_action("next_screen", "to-work", NOW)
    assert app.override and app.override.screen == "photos"
    assert app.want(NOW).screen == "photos"
    app.clock.t += 15  # past coalescing and the 10 s button minimum
    app.step(NOW)
    assert shown(app) == 2
    # the next trigger of any rule (18:00 evening) ends the override
    later = NOW.replace(hour=17, minute=1)  # 18:01 in Brussels
    w = app.want(later)
    assert app.override is None
    assert (w.screen, w.source) == ("weather", "schedule")


def test_alert_beats_override_and_button_a_dismisses(app):
    app.step(NOW)
    settle(app)
    app.do_action("goto:photos", "to-work", NOW)
    a: AlertState = app.alerts["to-work"]
    a.active, a.delay_s, a.window = True, 840, NOW
    w = app.want(NOW)
    assert (w.screen, w.source) == ("to-work", "alert")
    assert w.view["alert"]["delay_s"] == 840
    app.do_action("next_screen", "to-work", NOW)
    assert a.dismissed
    assert app.want(NOW).screen == "photos"  # back to the override


def test_screen_action_pins_screen(app):
    app.step(NOW)
    settle(app)
    app.do_action("goto:photos", "to-work", NOW)
    app.clock.t += 15
    app.step(NOW)
    settle(app)
    first = app.store.screen("photos")["current"]
    app.handle_press(Press("B", 0.2, False), NOW)  # gallery B = next_image
    assert app.store.screen("photos")["current"] != first
    assert app.override.screen == "photos"


def test_shutdown_needs_long_hold(app, monkeypatch):
    called = []
    monkeypatch.setattr(app, "_power", lambda action: called.append(action))
    app.handle_press(Press("D", 1.5, True), NOW)
    assert called == []
    app.handle_press(Press("D", 5.5, True), NOW)
    assert called == ["shutdown"]


def test_invalid_config_reload_keeps_old_config(app, tmp_path):
    app.step(NOW)
    settle(app)
    cfg = tmp_path / "config.json"
    cfg.write_text('{"screens": {}, "default_screen": "x"}', encoding="utf-8")
    import os

    os.utime(cfg, (1, 1))
    app.clock.t += 60
    app._check_config()
    assert app.errors and "to-work" in app.config.screens
    assert app.override and app.override.screen == app.renderer.status_screen


def test_pause_blocks_scheduled_updates(app):
    app.step(NOW)
    settle(app)
    app.do_action("pause_toggle", "to-work", NOW)
    settle(app)
    n = shown(app)
    app.clock.t += 3600
    app.step(NOW + timedelta(hours=11))
    assert shown(app) == n
    assert app.store.get("paused") is True


@pytest.mark.parametrize("orientation", ["landscape", "portrait"])
@pytest.mark.parametrize("flip", [False, True])
def test_button_legend_placement(app, orientation, flip):
    from inkydash.display.pipeline import logical_size
    from inkydash.render.theme import new_canvas

    app.config.display.orientation = orientation
    app.config.display.flip = flip
    img = new_canvas(logical_size(orientation))
    app._legend(img, "weather")
    assert img.getextrema() == (0, 1)  # black tabs with white text were drawn, nothing else
    assert img.getbbox() is not None
