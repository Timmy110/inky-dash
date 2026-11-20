from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from inkydash.config import parse_config
from inkydash.models import When
from inkydash.scheduler import DayCalendar, Scheduler, last_trigger, live_window, next_trigger, window_end

from .conftest import config_dict

TZ = ZoneInfo("Europe/Brussels")


def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=TZ)


def sched(**overrides):
    return Scheduler(parse_config(config_dict(**overrides)), TZ)


# Design §5 worked example, on Monday 2026-11-23 (not a holiday)
@pytest.mark.parametrize("hh,mm,screen,rule", [
    (5, 0, "night", "night"),
    (6, 0, "weather", "weather-daytime"),
    (7, 14, "weather", "weather-daytime"),
    (7, 15, "to-work", "weekday-commute"),
    (8, 44, "to-work", "weekday-commute"),
    (8, 45, "weather", "weather-daytime"),
    (18, 0, "weather", "evening"),
    (18, 20, "photos", "evening"),
    (18, 40, "weather", "evening"),
    (22, 10, "weather", "evening"),
    (23, 0, "night", "night"),
])
def test_worked_example(hh, mm, screen, rule):
    d = sched().decide(at(2026, 11, 23, hh, mm))
    assert (d.screen, d.rule) == (screen, rule)


def test_public_holiday_skips_commute():
    # 2026-11-11 (Armistice Day) is a public holiday in Belgium and a Wednesday
    s = sched()
    assert s.cal.kinds(date(2026, 11, 11)) == {"holiday"}
    assert s.decide(at(2026, 11, 11, 7, 30)).screen == "weather"


def test_off_days_range():
    s = sched(off_days=["2026-11-23/2026-11-24"])
    assert s.decide(at(2026, 11, 24, 7, 30)).screen == "weather"
    assert s.decide(at(2026, 11, 25, 7, 30)).screen == "to-work"


def test_weekend_no_commute():
    assert sched().decide(at(2026, 11, 21, 7, 30)).screen == "weather"  # Saturday


def test_refresh_changes_slot_not_screen():
    s = sched()
    a, b = s.decide(at(2026, 11, 23, 7, 16)), s.decide(at(2026, 11, 23, 7, 26))
    assert a.screen == b.screen == "to-work"
    assert (a.refresh_index, b.refresh_index) == (0, 1)
    assert a.slot != b.slot
    assert s.decide(at(2026, 11, 23, 7, 17)).slot == a.slot


def test_quiet_screen_never_refreshes():
    data = config_dict()
    data["schedule"][3]["when"]["refresh"] = "10m"
    s = Scheduler(parse_config(data), TZ)
    assert s.decide(at(2026, 11, 23, 23, 0)).slot == s.decide(at(2026, 11, 24, 2, 0)).slot


def test_default_screen_when_nothing_live():
    data = config_dict(schedule=[{"name": "morning", "when": {"every": "day", "at": "07:00", "until": "09:00"},
                                  "screen": "weather"}])
    s = Scheduler(parse_config(data), TZ)
    assert s.decide(at(2026, 11, 23, 8, 0)).screen == "weather"
    d = s.decide(at(2026, 11, 23, 12, 0))
    assert (d.screen, d.rule, d.slot) == ("photos", None, "default")


def test_until_crosses_midnight():
    w = When(every="day", at="22:00", until="06:00")
    cal = DayCalendar()
    assert live_window(w, at(2026, 11, 23, 23, 30), TZ, cal) is not None
    assert live_window(w, at(2026, 11, 24, 5, 59), TZ, cal) is not None
    assert live_window(w, at(2026, 11, 24, 6, 0), TZ, cal) is None
    trig = last_trigger(w, at(2026, 11, 24, 3, 0), TZ, cal)
    assert trig == at(2026, 11, 23, 22, 0).astimezone(UTC)
    assert window_end(w, trig, TZ) == at(2026, 11, 24, 6, 0).astimezone(UTC)


def test_skip_on_judged_by_trigger_day():
    cal = DayCalendar(lambda d: d == date(2026, 11, 23))
    w = When(every="day", at="22:00", until="06:00", skip_on=["holiday"])
    assert live_window(w, at(2026, 11, 24, 1, 0), TZ, cal) is None  # started on the holiday
    assert live_window(w, at(2026, 11, 25, 1, 0), TZ, cal) is not None


def test_only_on():
    cal = DayCalendar(off_ranges=[(date(2026, 11, 25), date(2026, 11, 25))])
    w = When(every="day", at="07:00", only_on=["off"])
    assert last_trigger(w, at(2026, 11, 25, 8, 0), TZ, cal) == at(2026, 11, 25, 7, 0).astimezone(UTC)
    assert next_trigger(w, at(2026, 11, 25, 8, 0), TZ, cal) is None


@pytest.mark.parametrize("day,month_days,expected", [
    (31, (2026, 2), date(2026, 2, 28)),
    (31, (2028, 2), date(2028, 2, 29)),
    (31, (2026, 4), date(2026, 4, 30)),
    (-1, (2026, 2), date(2026, 2, 28)),
    (-2, (2026, 3), date(2026, 3, 30)),
    (15, (2026, 6), date(2026, 6, 15)),
])
def test_month_day_clamping(day, month_days, expected):
    w = When(every="month", day=day, at="09:00")
    y, m = month_days
    t = next_trigger(w, at(y, m, 1, 0, 0), TZ, DayCalendar())
    assert t.astimezone(TZ).date() == expected


def test_dst_spring_forward_gap():
    # 2026-03-29 02:30 doesn't exist in Brussels: the trigger moves to 03:30 CEST
    w = When(every="day", at="02:30")
    t = next_trigger(w, at(2026, 3, 29, 0, 0), TZ, DayCalendar())
    assert t == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)


def test_dst_fall_back_fires_once():
    # 2026-10-25 02:30 happens twice; the rule fires at the first one only
    w = When(every="day", at="02:30")
    cal = DayCalendar()
    t1 = next_trigger(w, at(2026, 10, 25, 0, 0), TZ, cal)
    assert t1 == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)  # 02:30 CEST
    t2 = next_trigger(w, t1, TZ, cal)
    assert t2.astimezone(TZ).date() == date(2026, 10, 26)


def test_refresh_across_dst_is_by_real_time():
    data = config_dict(schedule=[{"name": "all-day", "when": {"every": "day", "at": "00:00", "refresh": "1h"},
                                  "screen": "weather"}])
    s = Scheduler(parse_config(data), TZ)
    # 2026-03-29 is 23 hours long: at local 05:00 (03:00 UTC) four real hours have passed
    assert s.decide(at(2026, 3, 29, 5, 0)).refresh_index == 4


def test_tie_more_specific_wins():
    data = config_dict(schedule=[
        {"name": "daily", "when": {"every": "day", "at": "08:00"}, "screen": "weather"},
        {"name": "monthly", "when": {"every": "month", "day": 1, "at": "08:00"}, "screen": "photos"},
        {"name": "weekly", "when": {"every": "week", "days": ["tue"], "at": "08:00"}, "screen": "status"},
    ])
    s = Scheduler(parse_config(data), TZ)
    assert s.decide(at(2026, 12, 1, 9, 0)).rule == "monthly"   # Tue 1 Dec: all three fire
    assert s.decide(at(2026, 12, 8, 9, 0)).rule == "weekly"
    assert s.decide(at(2026, 12, 9, 9, 0)).rule == "daily"


def test_tie_same_kind_first_listed_wins():
    data = config_dict(schedule=[
        {"name": "a", "when": {"every": "day", "at": "08:00"}, "screen": "weather"},
        {"name": "b", "when": {"every": "day", "at": "08:00"}, "screen": "photos"},
    ])
    assert Scheduler(parse_config(data), TZ).decide(at(2026, 12, 9, 9, 0)).rule == "a"


def test_multiple_at_times():
    data = config_dict(schedule=[{"name": "x", "when": {"every": "day", "at": ["07:00", "12:00"]},
                                  "screen": "weather"}])
    s = Scheduler(parse_config(data), TZ)
    assert s.decide(at(2026, 12, 9, 13, 0)).trigger == at(2026, 12, 9, 12, 0).astimezone(UTC)


def test_next_wake_covers_refresh_until_and_triggers():
    s = sched()
    assert s.next_wake(at(2026, 11, 23, 7, 16)) == at(2026, 11, 23, 7, 25).astimezone(UTC)
    assert s.next_wake(at(2026, 11, 23, 8, 40)) == at(2026, 11, 23, 8, 45).astimezone(UTC)
    assert s.next_wake(at(2026, 11, 23, 18, 5)) == at(2026, 11, 23, 18, 20).astimezone(UTC)
    assert s.next_wake(at(2026, 11, 23, 23, 30)) == at(2026, 11, 24, 6, 0).astimezone(UTC)


def test_timeline_is_ordered_and_covers_a_day():
    s = sched()
    tl = s.timeline(at(2026, 11, 23, 0, 0), at(2026, 11, 24, 0, 0))
    times = [t for t, _ in tl]
    assert times == sorted(times)
    screens = {d.screen for _, d in tl}
    assert screens == {"night", "weather", "to-work", "photos"}


def test_stateless_restart_lands_on_same_decision():
    t = at(2026, 11, 23, 19, 7)
    assert sched().decide(t) == sched().decide(t)


def test_next_trigger_any():
    s = sched()
    assert s.next_trigger_any(at(2026, 11, 23, 7, 20)) == at(2026, 11, 23, 18, 0).astimezone(UTC)


def test_rotation_index_math():
    s = sched()
    d = s.decide(at(2026, 11, 23, 19, 59))
    assert (d.screen, d.step) == ("photos", 5)
    assert s.decide(at(2026, 11, 23, 18, 0) + timedelta(minutes=120)).screen == "weather"
