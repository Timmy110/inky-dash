"""`weather`: Open-Meteo forecast, air quality/pollen/UV and MeteoAlarm warnings
(DESIGN.md §6.1). Fixed core layout plus priority flex slots."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Literal

from pydantic import Field, field_validator

from inkydash.providers.openmeteo import POLLEN_TYPES, AirQuality, Forecast
from inkydash.render import icons
from inkydash.render.charts import hourly_strip, quarter_bars
from inkydash.render.theme import BLACK, BLUE, RED, WHITE, YELLOW, new_canvas
from inkydash.render.widgets import Canvas
from inkydash.screens.base import Action, Frame, RenderContext, ScreenOptions

log = logging.getLogger(__name__)

BLOCKS = ("warnings", "rain_nowcast", "uv", "pollen", "air_quality", "sun")
DEFAULT_PRIORITY = {"rain_nowcast": 1, "uv": 2, "pollen": 3, "air_quality": 4, "sun": 9}
VIEWS = ("overview", "hourly", "daily")


class WeatherOptions(ScreenOptions):
    type: Literal["weather"] = "weather"
    location: str | list[str]
    blocks: dict[str, bool | int] = {}
    uv_threshold: float = 6
    pollen_types: list[str] = ["grass", "birch"]
    warnings_country: str | None = None   # default: holidays.country
    warnings_region: str | None = None    # substring of the MeteoAlarm area name, e.g. "Brussels"
    view: Literal["overview", "hourly", "daily"] = "overview"
    refresh_data: int = Field(15, ge=5, description="minutes to cache the forecast")

    @field_validator("blocks")
    @classmethod
    def _blocks(cls, v: dict[str, bool | int]) -> dict[str, bool | int]:
        bad = set(v) - set(BLOCKS)
        if bad:
            raise ValueError(f"unknown blocks {sorted(bad)} (known: {BLOCKS})")
        return v

    @field_validator("pollen_types")
    @classmethod
    def _pollen(cls, v: list[str]) -> list[str]:
        bad = set(v) - set(POLLEN_TYPES)
        if bad:
            raise ValueError(f"unknown pollen types {sorted(bad)} (known: {POLLEN_TYPES})")
        return v

    @property
    def locations(self) -> list[str]:
        return self.location if isinstance(self.location, list) else [self.location]

    def block_enabled(self, name: str) -> bool:
        return self.blocks.get(name, True) is not False

    def priority(self, name: str) -> int:
        v = self.blocks.get(name, True)
        return v if isinstance(v, int) and not isinstance(v, bool) else DEFAULT_PRIORITY.get(name, 5)


def _cycle_view(ctx: RenderContext, opts: WeatherOptions) -> bool:
    cur = ctx.view.get("view") or opts.view
    ctx.view["view"] = VIEWS[(VIEWS.index(cur) + 1) % len(VIEWS)]
    return True


def _cycle_location(ctx: RenderContext, opts: WeatherOptions) -> bool:
    if len(opts.locations) < 2:
        return False
    ctx.view["location_index"] = ctx.view.get("location_index", 0) + 1
    return True


@dataclass
class Data:
    fc: Forecast | None
    air: AirQuality | None
    warnings: list[Any]
    stale_since: datetime | None
    error: str | None


class Weather:
    name: ClassVar[str] = "weather"
    Options: ClassVar[type[ScreenOptions]] = WeatherOptions
    actions: ClassVar[dict[str, Action]] = {
        "cycle_view": Action("cycle_view", _cycle_view, "view"),
        "cycle_location": Action("cycle_location", _cycle_location, "place"),
    }

    @staticmethod
    def check_config(opts: WeatherOptions, config) -> str | None:
        for loc in opts.locations:
            if loc not in config.locations:
                return f"location: unknown location {loc!r}"
        return None

    # ------------------------------------------------------------------ data
    def _load(self, ctx: RenderContext, opts: WeatherOptions, loc_key: str) -> Data:
        p = ctx.providers
        stale: list[datetime] = []
        fc = air = None
        warnings: list[Any] = []
        error = None
        try:
            lat, lon = p.latlon(loc_key)
        except Exception as e:
            return Data(None, None, [], None, f"{loc_key}: {e}")
        try:
            cached, fc = p.weather.forecast(lat, lon, ttl_s=opts.refresh_data * 60)
            if cached.stale:
                stale.append(cached.fetched_at)
        except Exception as e:
            log.warning("forecast unavailable: %s", e)
            error = str(e)
        wants_air = any(opts.block_enabled(b) for b in ("uv", "pollen", "air_quality"))
        if wants_air:
            try:
                cached, air = p.weather.air_quality(lat, lon, ctx.now)
                if cached.stale:
                    stale.append(cached.fetched_at)
            except Exception as e:
                log.warning("air quality unavailable: %s", e)
        country = opts.warnings_country or ctx.config.holidays.country
        if opts.block_enabled("warnings") and country:
            try:
                cached, warnings = p.warnings.warnings(country, ctx.now, opts.warnings_region)
                if cached.stale:
                    stale.append(cached.fetched_at)
            except Exception as e:
                log.warning("warnings unavailable: %s", e)
        return Data(fc, air, warnings, min(stale) if stale else None, error)

    # ------------------------------------------------------------------ render
    def render(self, ctx: RenderContext, opts: WeatherOptions) -> Frame:
        locs = opts.locations
        loc_key = locs[ctx.view.get("location_index", 0) % len(locs)]
        loc = ctx.config.locations[loc_key]
        label = loc.label or loc_key
        data = self._load(ctx, opts, loc_key)
        img = new_canvas(ctx.size)
        c = Canvas(img, ctx.theme)
        if data.fc is None:
            c.placeholder(ctx.t("weather.unavailable"), data.error or "")
            box = c.footer(f"{label} · {ctx.t('updated')} {ctx.fmt.time(ctx.now)}")
            return Frame(img, volatile=[box])

        view = ctx.view.get("view") or opts.view
        top = 0
        if data.warnings:
            top = self._warning_banner(c, ctx, data.warnings)
        body = (0, top, c.w, c.h - 22)
        if view == "hourly":
            self._view_hourly(c, ctx, data, body, label)
        elif view == "daily":
            self._view_daily(c, ctx, data, body, label)
        elif ctx.landscape:
            self._overview_landscape(c, ctx, opts, data, body, label)
        else:
            self._overview_portrait(c, ctx, opts, data, body, label)

        footer = f"{label} · Open-Meteo · {ctx.t('updated')} {ctx.fmt.time(ctx.now)}"
        if data.stale_since:
            footer = f"{ctx.t('data_from')} {ctx.fmt.time(data.stale_since)} · " + footer
        box = c.footer(footer, align="right", warn=bool(data.stale_since))
        return Frame(img, volatile=[box], meta={"location": loc_key, "view": view})

    # ------------------------------------------------------------------ pieces
    def _warning_banner(self, c: Canvas, ctx: RenderContext, warnings: list[Any]) -> int:
        w = warnings[0]
        fill = RED if w.level >= 3 else YELLOW
        text_fill = WHITE if fill == RED else BLACK
        h = 40
        c.rect((0, 0, c.w, h), fill=fill)
        if fill == YELLOW:
            c.hline(h - 1, fill=BLACK, width=2)
        icons.warning_triangle(c, 24, h / 2, 26, YELLOW if fill == RED else WHITE)
        color_name = ctx.t(f"warning.{w.color}")
        msg = f"{color_name}: {w.event}"
        if w.expires:
            msg += f" {ctx.t('until')} {ctx.fmt.time(w.expires)}"
        if len(warnings) > 1:
            msg += f"  (+{len(warnings) - 1})"
        c.text_fit((46, h / 2), msg, "bold", c.w - 60, 20, 12, text_fill, anchor="lm")
        return h

    def _hours(self, fc: Forecast, now: datetime, n: int):
        start = now.replace(minute=0, second=0, microsecond=0).astimezone(UTC)
        hrs = [h for h in fc.hours if h.time >= start]
        return hrs[:n]

    def _current_block(self, c: Canvas, ctx: RenderContext, fc: Forecast, box: tuple[int, int, int, int],
                       label: str, icon_size: int) -> None:
        x0, y0, x1, y1 = box
        cur = fc.current
        c.text((x0 + 14, y0 + 10), label, "bold", 20, BLACK)
        c.text((x0 + 14, y0 + 36), ctx.fmt.date_long(ctx.fmt.local(ctx.now).date()), "ui", 16, BLACK)
        iy = y0 + 64
        icons.weather_icon(c, (x0 + 8, iy, x0 + 8 + icon_size, iy + icon_size), cur.code, cur.is_day)
        tx = x0 + 16 + icon_size
        temp = ctx.fmt.temp(cur.temp)
        size = c.fit_size(temp, "num", x1 - tx - 8, 112, 40)
        c.text((tx, iy + icon_size * 0.5), temp, "num", size, BLACK, anchor="lm")
        desc = ctx.t(f"wmo.{icons.wmo_kind(cur.code)}")
        dy = iy + icon_size + 6
        c.text_fit((x0 + 14, dy), desc, "bold", x1 - x0 - 28, 22, 14, BLACK)
        c.text_fit((x0 + 14, dy + 28), f"{ctx.t('feels_like')} {ctx.fmt.temp(cur.feels_like)}", "ui",
                   x1 - x0 - 28, 17, 12, BLACK)

    def _today_block(self, c: Canvas, ctx: RenderContext, fc: Forecast, x: int, y: int, w: int) -> int:
        d0 = fc.days[0]
        cur = fc.current
        rows = [
            (ctx.t("today"), f"{ctx.fmt.temp(d0.tmax)} / {ctx.fmt.temp(d0.tmin)}"),
            (ctx.t("rain"), f"{round(d0.precip_prob or 0)}% · {ctx.fmt.precip(d0.precip or 0)}"),
            (ctx.t("wind"), f"{ctx.fmt.speed(cur.wind)}"
             + (f" ({ctx.t('gusts')} {ctx.fmt.speed(cur.gusts)})" if cur.gusts and cur.gusts > (cur.wind or 0) + 15
                else "")),
        ]
        for k, v in rows:
            c.text((x, y), k, "ui", 17, BLACK)
            c.text_fit((x + 90, y), v, "bold", w - 90, 20, 13, BLACK)
            y += 30
        return y

    def _relevant_blocks(self, ctx: RenderContext, opts: WeatherOptions, data: Data) -> list[str]:
        fc, air = data.fc, data.air
        assert fc is not None
        rel = []
        if opts.block_enabled("rain_nowcast"):
            soon = [p for t, p in fc.quarter if ctx.now <= t + timedelta(minutes=15) <= ctx.now + timedelta(hours=2)]
            if any(p >= 0.1 for p in soon):
                rel.append("rain_nowcast")
        if opts.block_enabled("uv"):
            uv = fc.days[0].uv_max if fc.days else None
            if uv is not None and uv >= opts.uv_threshold:
                rel.append("uv")
        if opts.block_enabled("pollen") and air:
            if any(AirQuality.pollen_level(k, air.pollen.get(k)) >= 1 for k in opts.pollen_types):
                rel.append("pollen")
        if opts.block_enabled("air_quality") and air and (air.aqi_max_today or air.aqi or 0) > 40:
            rel.append("air_quality")
        if opts.block_enabled("sun"):
            rel.append("sun")
        return sorted(rel, key=opts.priority)

    def _flex(self, c: Canvas, ctx: RenderContext, opts: WeatherOptions, data: Data, name: str,
              box: tuple[int, int, int, int]) -> None:
        x0, y0, x1, y1 = box
        fc, air = data.fc, data.air
        assert fc is not None
        draw: dict[str, Callable[[], None]] = {}

        def rain() -> None:
            q = [(t, p) for t, p in fc.quarter if t + timedelta(minutes=15) >= ctx.now][:8]
            wet = [t for t, p in q if p >= 0.1]
            if wet:
                start = wet[0]
                end = next((t for t, p in q if t > start and p < 0.1), None)
                if start <= ctx.now:
                    msg = ctx.t("rain.now_until", time=ctx.fmt.time(end)) if end else ctx.t("rain.now")
                else:
                    msg = ctx.t("rain.from", time=ctx.fmt.time(start))
                    if end:
                        msg += " " + ctx.t("rain.until", time=ctx.fmt.time(end))
            else:
                msg = ctx.t("rain.none")
            c.text_fit((x0, y0), msg, "bold", x1 - x0, 18, 12, BLUE)
            quarter_bars(c, (x0, y0 + 26, x1, y1 - 4), [p for _, p in q], scale_mm=1.5)

        def uv() -> None:
            v = fc.days[0].uv_max or 0
            level = "extreme" if v >= 11 else "very_high" if v >= 8 else "high" if v >= 6 else "moderate"
            fill = RED if v >= 8 else YELLOW
            b = c.badge((x0, y0 + 2), f"UV {round(v)}", fill, WHITE if fill == RED else BLACK, size=18)
            c.text_fit((b[2] + 10, (b[1] + b[3]) / 2), ctx.t(f"uv.{level}"), "bold", x1 - b[2] - 10, 18, 12,
                       BLACK, anchor="lm")

        def pollen() -> None:
            assert air
            parts = []
            for k in opts.pollen_types:
                lvl = AirQuality.pollen_level(k, air.pollen.get(k))
                if lvl:
                    parts.append(f"{ctx.t('pollen.' + k)} {ctx.t(f'level.{lvl}')}")
            c.text((x0, y0), ctx.t("pollen"), "ui", 16, BLACK)
            c.text_fit((x0, y0 + 22), ", ".join(parts), "bold", x1 - x0, 19, 12, BLACK)

        def aq() -> None:
            assert air
            v = air.aqi_max_today or air.aqi
            lvl = AirQuality.aqi_level(v) or "fair"
            c.text((x0, y0), ctx.t("air_quality"), "ui", 16, BLACK)
            c.text_fit((x0, y0 + 22), f"{ctx.t('aqi.' + lvl)} (AQI {round(v or 0)})", "bold", x1 - x0, 19, 12,
                       RED if (v or 0) > 60 else BLACK)

        def sun() -> None:
            d0 = fc.days[0]
            if not (d0.sunrise and d0.sunset):
                return
            icons._sun(c, x0 + 14, y0 + 18, 9)
            length = d0.sunset - d0.sunrise
            hrs, mins = divmod(int(length.total_seconds() // 60), 60)
            x, cy = x0 + 40, y0 + 18
            for up, t in ((True, d0.sunrise), (False, d0.sunset)):
                icons.arrow(c, x + 6, cy, 12, up)
                b = c.text((x + 16, cy), ctx.fmt.time(t), "bold", 19, BLACK, anchor="lm")
                x = b[2] + 18
            if x < x1 - 60:
                c.text((x, cy), f"{hrs}h{mins:02d}", "ui", 17, BLACK, anchor="lm")

        draw.update(rain_nowcast=rain, uv=uv, pollen=pollen, air_quality=aq, sun=sun)
        draw[name]()

    def _days_columns(self, c: Canvas, ctx: RenderContext, fc: Forecast, box: tuple[int, int, int, int],
                      n: int = 5) -> None:
        x0, y0, x1, y1 = box
        days = fc.days[1:n + 1]
        if not days:
            return
        col = (x1 - x0) / len(days)
        isz = min(int(col * 0.7), y1 - y0 - 70)
        for i, d in enumerate(days):
            cx = x0 + col * (i + 0.5)
            local = ctx.fmt.local(d.date + timedelta(hours=12)).date()
            c.text((cx, y0 + 2), ctx.fmt.weekday_short(local), "bold", 16, BLACK, anchor="ma")
            icons.weather_icon(c, (int(cx - isz / 2), y0 + 24, int(cx + isz / 2), y0 + 24 + isz), d.code)
            c.text((cx, y1 - 22), ctx.fmt.temp(d.tmax), "bold", 18, BLACK, anchor="mb")
            c.text((cx, y1 - 2), ctx.fmt.temp(d.tmin), "ui", 15, BLACK, anchor="mb")
            if i:
                c.vline(int(x0 + col * i), y0 + 4, y1 - 4, BLACK, 1)

    def _overview_landscape(self, c: Canvas, ctx: RenderContext, opts: WeatherOptions, data: Data,
                            body: tuple[int, int, int, int], label: str) -> None:
        fc = data.fc
        assert fc is not None
        x0, y0, x1, y1 = body
        split_y = y0 + int((y1 - y0) * 0.6)
        self._current_block(c, ctx, fc, (0, y0, 330, split_y), label, 120)
        c.vline(338, y0 + 10, split_y - 10, BLACK, 2)
        y = self._today_block(c, ctx, fc, 354, y0 + 14, c.w - 354 - 14)
        flex = self._relevant_blocks(ctx, opts, data)[:2]
        slot_h = (split_y - y - 6) // 2
        for i, name in enumerate(flex):
            top = y + 6 + i * slot_h
            c.hline(top - 4, 354, c.w - 14, BLACK, 1)
            self._flex(c, ctx, opts, data, name, (354, top + 4, c.w - 14, top + slot_h - 2))
        c.hline(split_y, 0, c.w, BLACK, 2)
        hrs = self._hours(fc, ctx.now, 12)
        hourly_strip(c, (10, split_y + 6, 480, y1 - 2), [h.time for h in hrs], [h.temp for h in hrs],
                     [h.precip_prob for h in hrs], ctx.fmt.hour, ctx.fmt.temp)
        c.vline(492, split_y + 8, y1 - 6, BLACK, 1)
        self._days_columns(c, ctx, fc, (500, split_y + 8, c.w - 6, y1 - 4), 5)

    def _overview_portrait(self, c: Canvas, ctx: RenderContext, opts: WeatherOptions, data: Data,
                           body: tuple[int, int, int, int], label: str) -> None:
        fc = data.fc
        assert fc is not None
        x0, y0, x1, y1 = body
        self._current_block(c, ctx, fc, (0, y0, c.w, y0 + 250), label, 130)
        c.hline(y0 + 256, 10, c.w - 10, BLACK, 2)
        y = self._today_block(c, ctx, fc, 16, y0 + 268, c.w - 32)
        flex = self._relevant_blocks(ctx, opts, data)[:3]
        for name in flex:
            c.hline(y, 16, c.w - 16, BLACK, 1)
            self._flex(c, ctx, opts, data, name, (16, y + 8, c.w - 16, y + 66))
            y += 72
        days_top = y1 - 128
        y_chart = min(y + 6, days_top - 140)
        c.hline(y_chart, 0, c.w, BLACK, 2)
        hrs = self._hours(fc, ctx.now, 12)
        hourly_strip(c, (10, y_chart + 6, c.w - 10, days_top - 6), [h.time for h in hrs], [h.temp for h in hrs],
                     [h.precip_prob for h in hrs], ctx.fmt.hour, ctx.fmt.temp)
        c.hline(days_top, 0, c.w, BLACK, 1)
        self._days_columns(c, ctx, fc, (6, days_top + 6, c.w - 6, y1 - 4), 5)

    def _view_hourly(self, c: Canvas, ctx: RenderContext, data: Data, body: tuple[int, int, int, int],
                     label: str) -> None:
        fc = data.fc
        assert fc is not None
        x0, y0, x1, y1 = body
        c.text((14, y0 + 10), f"{label} · {ctx.t('view.hourly')}", "bold", 22, BLACK)
        n = 24 if ctx.landscape else 12
        hrs = self._hours(fc, ctx.now, n)
        mid = y0 + 44 + (y1 - y0 - 44) // 2 if not ctx.landscape else y1 - 70
        hourly_strip(c, (10, y0 + 44, c.w - 10, mid), [h.time for h in hrs], [h.temp for h in hrs],
                     [h.precip_prob for h in hrs], ctx.fmt.hour, ctx.fmt.temp, label_every=3)
        col = (c.w - 20) / len(hrs) if hrs else 1
        isz = int(min(col * 0.9, 36))
        for i, h in enumerate(hrs):
            if i % 3 == 0:
                cx = 10 + col * (i + 0.5)
                icons.weather_icon(c, (int(cx - isz / 2), mid + 8, int(cx + isz / 2), mid + 8 + isz), h.code,
                                   h.is_day)

    def _view_daily(self, c: Canvas, ctx: RenderContext, data: Data, body: tuple[int, int, int, int],
                    label: str) -> None:
        fc = data.fc
        assert fc is not None
        x0, y0, x1, y1 = body
        c.text((14, y0 + 10), f"{label} · {ctx.t('view.daily')}", "bold", 22, BLACK)
        days = fc.days[:7]
        row_h = (y1 - y0 - 50) / max(1, len(days))
        for i, d in enumerate(days):
            y = y0 + 46 + i * row_h
            local = ctx.fmt.local(d.date + timedelta(hours=12)).date()
            name = ctx.t("today") if i == 0 else ctx.fmt.weekday_long(local)
            isz = int(min(row_h - 6, 48))
            c.text((16, y + row_h / 2), name, "bold", 20, BLACK, anchor="lm")
            ix = 190 if ctx.landscape else 150
            icons.weather_icon(c, (ix, int(y + (row_h - isz) / 2), ix + isz, int(y + (row_h + isz) / 2)), d.code)
            c.text((ix + isz + 20, y + row_h / 2), f"{ctx.fmt.temp(d.tmax)} / {ctx.fmt.temp(d.tmin)}", "bold", 22,
                   BLACK, anchor="lm")
            if ctx.landscape:
                c.text((560, y + row_h / 2), f"{round(d.precip_prob or 0)}%  {ctx.fmt.precip(d.precip or 0)}",
                       "ui", 18, BLUE if (d.precip_prob or 0) >= 50 else BLACK, anchor="lm")
            if i:
                c.hline(int(y), 10, c.w - 10, BLACK, 1)
