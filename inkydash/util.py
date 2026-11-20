"""Small parsing helpers shared by config, scheduler and screens."""

from __future__ import annotations

import re
from datetime import time, timedelta
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer, WithJsonSchema

_DURATION_RE = re.compile(r"^(\d+)([smhd])$")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_OFFSET_RE = re.compile(r"^\+(\d+)([smhd])$")


def parse_duration(value: Any) -> timedelta:
    """`"10m"` → 10 minutes. Also accepts a timedelta or a number of seconds."""
    if isinstance(value, timedelta):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return timedelta(seconds=value)
    if not isinstance(value, str):
        raise ValueError(f"expected a duration like '10m', got {value!r}")
    m = _DURATION_RE.match(value.strip())
    if not m:
        raise ValueError(f"expected a duration like '30s', '10m', '1h' or '2d', got {value!r}")
    return timedelta(seconds=int(m.group(1)) * _UNITS[m.group(2)])


def parse_offset(value: Any) -> timedelta:
    """`"+15m"` → 15 minutes (departure-time forecast offsets)."""
    if isinstance(value, timedelta):
        return value
    if not isinstance(value, str):
        raise ValueError(f"expected an offset like '+15m', got {value!r}")
    m = _OFFSET_RE.match(value.strip())
    if not m:
        raise ValueError(f"expected an offset like '+15m', got {value!r}")
    return timedelta(seconds=int(m.group(1)) * _UNITS[m.group(2)])


def parse_hhmm(value: Any) -> time:
    if isinstance(value, time):
        return value
    if not isinstance(value, str) or not _HHMM_RE.match(value.strip()):
        raise ValueError(f"expected HH:MM (00:00–23:59), got {value!r}")
    h, m = value.strip().split(":")
    return time(int(h), int(m))


def format_duration(td: timedelta) -> str:
    s = int(td.total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s and s % size == 0:
            return f"{s // size}{unit}"
    return f"{s}s"


Duration = Annotated[
    timedelta,
    BeforeValidator(parse_duration),
    PlainSerializer(format_duration, return_type=str),
    WithJsonSchema({"type": "string", "pattern": r"^\d+[smhd]$", "examples": ["10m", "1h"]}),
]

Offset = Annotated[
    timedelta,
    BeforeValidator(parse_offset),
    PlainSerializer(lambda td: "+" + format_duration(td), return_type=str),
    WithJsonSchema({"type": "string", "pattern": r"^\+\d+[smhd]$", "examples": ["+15m"]}),
]

HHMM = Annotated[
    time,
    BeforeValidator(parse_hhmm),
    PlainSerializer(lambda t: t.strftime("%H:%M"), return_type=str),
    WithJsonSchema({"type": "string", "pattern": r"^([01]\d|2[0-3]):[0-5]\d$", "examples": ["07:15"]}),
]
