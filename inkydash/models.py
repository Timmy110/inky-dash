"""Config building blocks shared by config.py and screen modules (kept separate so screens
can use them without importing inkydash.config, which imports the screens)."""

from __future__ import annotations

from datetime import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from inkydash.util import HHMM, Duration

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DayKind = Literal["holiday", "off"]
Mode = Literal["car", "bike", "walk", "transit"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class When(Strict):
    every: Literal["day", "week", "month"]
    at: HHMM | list[HHMM]
    days: list[Weekday] | None = None
    day: int | None = None
    until: HHMM | None = None
    refresh: Duration | None = None
    skip_on: list[DayKind] = []
    only_on: list[DayKind] = []

    @property
    def times(self) -> list[time]:
        ts = self.at if isinstance(self.at, list) else [self.at]
        return sorted(set(ts))

    @model_validator(mode="after")
    def _check(self) -> When:
        if self.every != "week" and self.days is not None:
            raise ValueError("'days' only applies to every: week")
        if self.every == "month":
            if self.day is None:
                raise ValueError("every: month needs 'day' (1..31, or -1 for the last day)")
            if not (1 <= self.day <= 31 or -31 <= self.day <= -1):
                raise ValueError("'day' must be 1..31 or -1..-31")
        elif self.day is not None:
            raise ValueError("'day' only applies to every: month")
        if isinstance(self.at, list) and not self.at:
            raise ValueError("'at' needs at least one time")
        both = set(self.skip_on) & set(self.only_on)
        if both:
            raise ValueError(f"{sorted(both)} is in both skip_on and only_on")
        if self.refresh is not None and self.refresh.total_seconds() < 60:
            raise ValueError("'refresh' must be at least 1m")
        return self
