"""Pure scheduling logic (DESIGN.md §5): (config, now) → active screen + next wake-up.

All instants are handled in UTC internally. Python compares two aware datetimes that share a
tzinfo by wall-clock time (ignoring DST fold), so local times are only used to iterate days.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from inkydash.config import Config, Rule
    from inkydash.models import When

SPECIFICITY = {"month": 3, "week": 2, "day": 1}
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
HORIZON_DAYS = 400


# ---------------------------------------------------------------- calendar of special days
class DayCalendar:
    """Answers which special kinds ("holiday", "off") a local date is."""

    def __init__(self, holiday_fn: Callable[[date], bool] | None = None,
                 off_ranges: list[tuple[date, date]] | None = None):
        self._holiday = holiday_fn or (lambda d: False)
        self._off = off_ranges or []

    @classmethod
    def from_config(cls, config: Config) -> DayCalendar:
        from inkydash.config import parse_off_day

        holiday_fn = None
        if config.holidays.country:
            import holidays as hol

            table = hol.country_holidays(config.holidays.country, subdiv=config.holidays.subdiv)

            def holiday_fn(d: date) -> bool:
                return d in table

        return cls(holiday_fn, [parse_off_day(x) for x in config.off_days])

    def kinds(self, d: date) -> set[str]:
        out = set()
        if self._holiday(d):
            out.add("holiday")
        if any(a <= d <= b for a, b in self._off):
            out.add("off")
        return out


# ---------------------------------------------------------------- triggers of one `when`
def local_instant(d: date, t: time, tz: tzinfo) -> datetime:
    """Local wall time → UTC. A time inside a DST gap moves forward (02:30 → 03:30);
    an ambiguous time (DST end) takes its first occurrence."""
    return datetime.combine(d, t, tzinfo=tz).astimezone(UTC)


def fires_on(when: When, d: date, cal: DayCalendar) -> bool:
    if when.every == "week":
        if when.days is not None and DAY_NAMES[d.weekday()] not in when.days:
            return False
    elif when.every == "month":
        last = calendar.monthrange(d.year, d.month)[1]
        assert when.day is not None
        if when.day > 0:
            if d.day != min(when.day, last):
                return False
        else:
            target = last + 1 + when.day
            if target < 1 or d.day != target:
                return False
    if when.skip_on or when.only_on:
        kinds = cal.kinds(d)
        if kinds & set(when.skip_on):
            return False
        if when.only_on and not (kinds & set(when.only_on)):
            return False
    return True


def _days(start: date, step: int) -> Iterator[date]:
    d = start
    for _ in range(HORIZON_DAYS):
        yield d
        d += timedelta(days=step)


def last_trigger(when: When, t: datetime, tz: tzinfo, cal: DayCalendar) -> datetime | None:
    """Most recent trigger instant ≤ t."""
    t = t.astimezone(UTC)
    for d in _days(t.astimezone(tz).date() + timedelta(days=1), -1):
        if not fires_on(when, d, cal):
            continue
        for at in reversed(when.times):
            inst = local_instant(d, at, tz)
            if inst <= t:
                return inst
    return None


def next_trigger(when: When, t: datetime, tz: tzinfo, cal: DayCalendar) -> datetime | None:
    """First trigger instant strictly after t."""
    t = t.astimezone(UTC)
    for d in _days(t.astimezone(tz).date() - timedelta(days=1), 1):
        if not fires_on(when, d, cal):
            continue
        for at in when.times:
            inst = local_instant(d, at, tz)
            if inst > t:
                return inst
    return None


def window_end(when: When, trigger: datetime, tz: tzinfo) -> datetime | None:
    """When the window that started at `trigger` ends: the first `until` after the trigger."""
    if when.until is None:
        return None
    local_day = trigger.astimezone(tz).date()
    end = local_instant(local_day, when.until, tz)
    if end <= trigger:
        end = local_instant(local_day + timedelta(days=1), when.until, tz)
    return end


def live_window(when: When, t: datetime, tz: tzinfo, cal: DayCalendar) -> tuple[datetime, datetime | None] | None:
    """(trigger, end) if `when` is live at t, else None."""
    trig = last_trigger(when, t, tz, cal)
    if trig is None:
        return None
    end = window_end(when, trig, tz)
    if end is not None and t.astimezone(UTC) >= end:
        return None
    return trig, end


# ---------------------------------------------------------------- decisions
@dataclass(frozen=True)
class Decision:
    screen: str
    rule: str | None            # None → default_screen
    trigger: datetime | None    # UTC
    step: int = 0               # rotation step
    refresh_index: int = 0
    reason: str = ""

    @property
    def slot(self) -> str:
        """Stable id of what should be on screen: same slot → same picture (gallery)."""
        if self.rule is None:
            return "default"
        assert self.trigger is not None
        return f"{self.rule}@{self.trigger.strftime('%Y%m%dT%H%MZ')}#{self.step}.{self.refresh_index}"

    @property
    def view_key(self) -> tuple[str | None, datetime | None, str]:
        """Changes when the active rule, its trigger or the shown screen changes
        (resets button-driven view state)."""
        return self.rule, self.trigger, self.screen


@dataclass(frozen=True)
class _Live:
    rule: Rule
    index: int
    trigger: datetime
    end: datetime | None


class Scheduler:
    def __init__(self, config: Config, tz: tzinfo, cal: DayCalendar | None = None):
        self.config = config
        self.tz = tz
        self.cal = cal or DayCalendar.from_config(config)
        self.rules = [(i, r) for i, r in enumerate(config.schedule) if r.enabled]
        self._frozen = {name for name, s in config.screens.items() if s.type == "quiet"}

    # memoised per instant: the main loop and timeline ask about the same t repeatedly
    def _live(self, t: datetime) -> list[_Live]:
        return _live_cached(self, t.astimezone(UTC))

    def decide(self, t: datetime) -> Decision:
        t = t.astimezone(UTC)
        live = self._live(t)
        if not live:
            return Decision(self.config.default_screen, None, None, reason="no rule live → default_screen")
        best = max(live, key=lambda x: (x.trigger, SPECIFICITY[x.rule.when.every], -x.index))
        rule, trig = best.rule, best.trigger
        elapsed = t - trig
        step = 0
        step_start = trig
        targets = rule.targets
        if rule.rotate_every is not None:
            step = int(elapsed // rule.rotate_every)
            step_start = trig + step * rule.rotate_every
        screen = targets[step % len(targets)]
        refresh_index = 0
        if rule.when.refresh is not None and screen not in self._frozen:
            refresh_index = int((t - step_start) // rule.when.refresh)
        local = trig.astimezone(self.tz).strftime("%a %H:%M")
        others = len(live) - 1
        reason = f"rule {rule.name!r} (triggered {local}" + (f", {others} other live" if others else "") + ")"
        return Decision(screen, rule.name, trig, step, refresh_index, reason)

    def next_trigger_any(self, t: datetime) -> datetime | None:
        cands = [n for _, r in self.rules if (n := next_trigger(r.when, t, self.tz, self.cal))]
        return min(cands) if cands else None

    def next_wake(self, t: datetime) -> datetime | None:
        t = t.astimezone(UTC)
        cands: list[datetime] = []
        nt = self.next_trigger_any(t)
        if nt:
            cands.append(nt)
        for lv in self._live(t):
            if lv.end is not None and lv.end > t:
                cands.append(lv.end)
        d = self.decide(t)
        if d.rule is not None and d.trigger is not None:
            rule = next(r for _, r in self.rules if r.name == d.rule)
            step_start = d.trigger
            if rule.rotate_every is not None:
                step_start = d.trigger + d.step * rule.rotate_every
                cands.append(d.trigger + (d.step + 1) * rule.rotate_every)
            if rule.when.refresh is not None and d.screen not in self._frozen:
                cands.append(step_start + (d.refresh_index + 1) * rule.when.refresh)
        cands = [c for c in cands if c > t]
        return min(cands) if cands else None

    def timeline(self, start: datetime, end: datetime) -> list[tuple[datetime, Decision]]:
        """Every instant in [start, end) where the decision (incl. slot) changes."""
        out: list[tuple[datetime, Decision]] = []
        t = start.astimezone(UTC)
        end = end.astimezone(UTC)
        prev: Decision | None = None
        while t < end:
            d = self.decide(t)
            if prev is None or d.slot != prev.slot or d.screen != prev.screen:
                out.append((t, d))
            prev = d
            nxt = self.next_wake(t)
            if nxt is None:
                break
            t = nxt
        return out


@lru_cache(maxsize=64)
def _live_cached(s: Scheduler, t: datetime) -> list[_Live]:
    live = []
    for idx, rule in s.rules:
        w = live_window(rule.when, t, s.tz, s.cal)
        if w:
            live.append(_Live(rule, idx, w[0], w[1]))
    return live
