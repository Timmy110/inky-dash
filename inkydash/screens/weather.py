"""`weather`: Open-Meteo forecast, air quality/pollen/UV and MeteoAlarm warnings
(DESIGN.md §6.1). Fixed core layout plus priority flex slots."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Literal

from PIL import Image
from pydantic import Field, field_validator

from inkydash.providers.openmeteo import POLLEN_TYPES, AirQuality, Forecast
from inkydash.render import icons, soft
from inkydash.render.charts import hourly_strip
from inkydash.render.theme import BLACK, BLUE, RED, WHITE, YELLOW, new_canvas, new_overlay
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
    theme: Literal["auto", "light", "dark"] = Field(
        "auto", description="overview colours; auto = dark between sunset and sunrise")
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
        if view == "overview":
            return self._overview(ctx, opts, data, label, loc_key)
        top = 0
        if data.warnings:
            top = self._warning_banner(c, ctx, data.warnings)
        body = (0, top, c.w, c.h - 22)
        if view == "hourly":
            self._view_hourly(c, ctx, data, body, label)
        else:
            self._view_daily(c, ctx, data, body, label)

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

    # ------------------------------------------------------------------ overview
    def _overview(self, ctx: RenderContext, opts: WeatherOptions, data: Data, label: str, loc_key: str) -> Frame:
        fc = data.fc
        assert fc is not None
        theme = opts.theme if opts.theme != "auto" else ("light" if fc.current.is_day else "dark")
        lk = soft.look(theme)
        base = Image.new("RGB", ctx.size, lk.bg)
        ov_img = new_overlay(ctx.size)
        ov = Canvas(ov_img, ctx.theme)
        w, h = ctx.size
        land = ctx.landscape

        # header: place and date (or the most severe warning)
        title_y, date_y = (8, 46) if land else (18, 58)
        ov.text_fit((w / 2, title_y), label, "jost-semibold", w - 140, 32, 18, lk.fg, anchor="ma")
        if data.warnings:
            self._warning_pill(ov, ctx, data.warnings, w / 2, date_y + 4, w - 40)
        else:
            ov.text((w / 2, date_y), ctx.fmt.date_long(ctx.fmt.local(ctx.now).date()), "jost", 20, lk.fg,
                    anchor="ma")
        if data.stale_since:
            stale = ctx.fmt.time(data.stale_since)
            sw = ov.width(stale, "jost", 13)
            ov.warn_mark(w - 10 - sw - 18, 16, 14)
            ov.text((w - 8, 16), stale, "jost", 13, lk.fg, anchor="rm")

        hours = self._hours(fc, ctx.now, 24)
        series = ([x.time for x in hours], [x.temp for x in hours], [x.precip_prob for x in hours])
        if land:
            self._current(base, ov, ctx, lk, fc, (24, 78, 420, 236), 150)
            self._stats(base, ov, ctx, opts, data, lk, (430, 76, w - 8, 238), cols=2)
            soft.hourly_chart(base, ov, (8, 246, w - 8, 352), lk, *series, ctx.fmt.hour, ctx.fmt.temp,
                              label_every=2)
            self._day_cards(base, ov, ctx, lk, fc, (8, 362, w - 8, h - 6), 7)
        else:
            self._current(base, ov, ctx, lk, fc, (16, 96, w - 16, 276), 170)
            self._stats(base, ov, ctx, opts, data, lk, (8, 292, w - 8, 440), cols=4)
            soft.hourly_chart(base, ov, (8, 454, w - 8, 636), lk, *series, ctx.fmt.hour, ctx.fmt.temp,
                              label_every=3)
            self._day_cards(base, ov, ctx, lk, fc, (8, 650, w - 8, h - 8), 5)
        return Frame(base, overlay=ov_img, saturation=0.5,
                     meta={"location": loc_key, "view": "overview", "theme": theme})

    def _warning_pill(self, c: Canvas, ctx: RenderContext, warnings: list[Any], cx: float, y: int,
                      max_w: int) -> None:
        wn = warnings[0]
        fill = RED if wn.level >= 3 else YELLOW
        msg = ctx.t("warning." + wn.color) + ": " + wn.event
        if wn.expires:
            msg += " " + ctx.t("until") + " " + ctx.fmt.time(wn.expires)
        if len(warnings) > 1:
            msg += "  (+" + str(len(warnings) - 1) + ")"
        size = c.fit_size(msg, "jost-medium", max_w - 24, 16, 11)
        msg = c.truncate(msg, "jost-medium", size, max_w - 24)
        tw = c.width(msg, "jost-medium", size)
        box = (int(cx - tw / 2 - 12), y - 2, int(cx + tw / 2 + 12), y + size + 8)
        c.rounded(box, r=(box[3] - box[1]) // 2, fill=fill, outline=BLACK)
        c.text((cx, (box[1] + box[3]) / 2), msg, "jost-medium", size, WHITE if fill == RED else BLACK, anchor="mm")

    def _current(self, base: Image.Image, ov: Canvas, ctx: RenderContext, lk: soft.Look, fc: Forecast,
                 box: tuple[int, int, int, int], icon: int) -> None:
        x0, y0, x1, y1 = box
        cur = fc.current
        iy = y0 + (y1 - y0 - icon) // 2
        soft.paste(base, soft.weather_sprite(cur.code, cur.is_day, icon, lk), (x0, iy))
        # big number + small unit, centred in the space right of the icon
        num = ctx.fmt.temp(cur.temp).rstrip("°")
        unit = "°F" if ctx.fmt.units == "imperial" else "°C"
        tx0 = x0 + icon + 12
        size = ov.fit_size(num, "jost-medium", x1 - tx0 - 60, 104, 48)
        usize = size // 3
        nb = ov.d.textbbox((0, 0), num, font=ov.font("jost-medium", size), anchor="ls")
        ub = ov.d.textbbox((0, 0), unit, font=ov.font("jost", usize), anchor="la")
        total = (nb[2] - nb[0]) + 6 + (ub[2] - ub[0])
        cx = (tx0 + x1) / 2
        baseline = iy + icon * 0.62
        left = cx - total / 2
        ov.text((left - nb[0], baseline), num, "jost-medium", size, lk.fg, anchor="ls")
        ov.text((left + (nb[2] - nb[0]) + 6, baseline + nb[1]), unit, "jost", usize, lk.fg, anchor="la")
        feels = ctx.t("feels_like") + " " + ctx.fmt.temp(cur.feels_like)
        ov.text_fit((cx, baseline + 14), feels, "jost", int(x1 - tx0), 21, 13, lk.fg, anchor="ma")

    def _stat_items(self, ctx: RenderContext, opts: WeatherOptions, data: Data) -> list[tuple[str, str, str, str]]:
        """(icon, label, value, unit) for the grid: up to 8 cells; situational ones replace calmer ones."""
        fc, air = data.fc, data.air
        assert fc is not None
        cur, d0 = fc.current, (fc.days[0] if fc.days else None)
        fmt = ctx.fmt

        def split(s: str) -> tuple[str, str]:
            v, _, u = s.partition(" ")
            return v, u

        def tsplit(t: datetime | None) -> tuple[str, str]:
            if t is None:
                return "–", ""
            s = fmt.time(t)
            return (s[:-2], s[-2:].upper()) if s.endswith(("am", "pm")) else (s, "")

        items: dict[str, tuple[str, str, str, str]] = {}
        if opts.block_enabled("sun") and d0:
            items["sunrise"] = ("sunrise", ctx.t("weather.sunrise"), *tsplit(d0.sunrise))
            items["sunset"] = ("sunset", ctx.t("weather.sunset"), *tsplit(d0.sunset))
        items["wind"] = ("wind", ctx.t("wind"), *split(fmt.speed(cur.wind)))
        items["humidity"] = ("humidity", ctx.t("weather.humidity"),
                             "–" if cur.humidity is None else str(round(cur.humidity)), "%")
        if cur.pressure is None:
            pv, pu = "–", ""
        elif fmt.units == "imperial":
            pv, pu = f"{cur.pressure * 0.02953:.2f}", "inHg"
        else:
            pv, pu = str(round(cur.pressure)), "hPa"
        items["pressure"] = ("pressure", ctx.t("weather.pressure"), pv, pu)
        if opts.block_enabled("uv"):
            uv_max = d0.uv_max if d0 else None
            if uv_max is not None and uv_max >= opts.uv_threshold:
                items["uv"] = ("uv", ctx.t("weather.uv_max"), str(round(uv_max)), "")
            else:
                now_uv = air.uv_now if air else None
                items["uv"] = ("uv", ctx.t("weather.uv"), "–" if now_uv is None else str(round(now_uv)), "")
        if cur.visibility is None:
            vv, vu = "–", ""
        else:
            km = cur.visibility / 1000
            dist = km / 1.609344 if fmt.units == "imperial" else km
            vv, vu = (">10" if dist >= 10 else f"{dist:.1f}"), ("mi" if fmt.units == "imperial" else "km")
        items["visibility"] = ("visibility", ctx.t("weather.visibility"), vv, vu)
        if opts.block_enabled("air_quality") and air and air.aqi is not None:
            lvl = AirQuality.aqi_level(air.aqi) or "fair"
            items["air_quality"] = ("air_quality", ctx.t("air_quality"), str(round(air.aqi)), ctx.t("aqi." + lvl))

        relevant = self._relevant_blocks(ctx, opts, data)
        if "rain_nowcast" in relevant:
            q = [(t, p) for t, p in fc.quarter if t + timedelta(minutes=15) >= ctx.now][:8]
            wet = [t for t, p in q if p >= 0.1]
            start = wet[0]
            end = next((t for t, p in q if t > start and p < 0.1), None)
            if start > ctx.now:
                items["pressure"] = ("rain", ctx.t("weather.rain_at"), *tsplit(start))
            elif end:
                items["pressure"] = ("rain", ctx.t("weather.dry_at"), *tsplit(end))
            else:
                items["pressure"] = ("rain", ctx.t("rain"), ctx.t("weather.now"), "")
        if "pollen" in relevant and air:
            k = max(opts.pollen_types, key=lambda k: AirQuality.pollen_level(k, air.pollen.get(k)))
            lvl = AirQuality.pollen_level(k, air.pollen.get(k))
            items["visibility"] = ("pollen", ctx.t("pollen"), ctx.t("pollen." + k).capitalize(), ctx.t(f"level.{lvl}"))
        order = ("sunrise", "sunset", "wind", "humidity", "pressure", "uv", "visibility", "air_quality")
        return [items[k] for k in order if k in items]

    def _stats(self, base: Image.Image, ov: Canvas, ctx: RenderContext, opts: WeatherOptions, data: Data,
               lk: soft.Look, box: tuple[int, int, int, int], cols: int) -> None:
        x0, y0, x1, y1 = box
        items = self._stat_items(ctx, opts, data)
        rows = max(1, -(-len(items) // cols))
        cw, rh = (x1 - x0) / cols, (y1 - y0) / rows
        isz = int(min(rh - 10, 30 if cols > 2 else 32))
        uv_warn = ctx.t("weather.uv_max")
        for i, (icon, label, value, unit) in enumerate(items):
            cx0, cy0 = x0 + (i % cols) * cw, y0 + (i // cols) * rh
            mid = cy0 + rh / 2
            soft.paste(base, soft.stat_sprite(icon, isz, lk), (cx0 + 4, mid - isz / 2))
            tx0, tx1 = cx0 + isz + 8, cx0 + cw - 2
            tcx = (tx0 + tx1) / 2
            ov.text_fit((tcx, mid - 3), label, "jost", int(tx1 - tx0), 13, 10, lk.fg, anchor="ms")
            vsize, usize = 21, 12
            while vsize > 12:
                vw = ov.width(value, "jost-semibold", vsize)
                uw = ov.width(unit, "jost", usize) + 3 if unit else 0
                if vw + uw <= tx1 - tx0:
                    break
                vsize -= 1
                if vsize % 2:
                    usize = max(9, usize - 1)
            vw = ov.width(value, "jost-semibold", vsize)
            uw = ov.width(unit, "jost", usize) + 3 if unit else 0
            left = tcx - (vw + uw) / 2
            vy = mid + 3 + vsize * 0.78
            fill = RED if label == uv_warn and lk.fg == BLACK else (YELLOW if label == uv_warn else lk.fg)
            ov.text((left, vy), value, "jost-semibold", vsize, fill, anchor="ls")
            if unit:
                ov.text((left + vw + 3, vy), unit, "jost", usize, lk.fg, anchor="ls")

    def _day_cards(self, base: Image.Image, ov: Canvas, ctx: RenderContext, lk: soft.Look, fc: Forecast,
                   box: tuple[int, int, int, int], n: int) -> None:
        x0, y0, x1, y1 = box
        days = fc.days[1:n + 1]
        if not days:
            return
        gap = 8
        cw = (x1 - x0 - gap * (n - 1)) / n
        ch = y1 - y0
        isz = int(min(cw - 18, ch - 62))
        for i, d in enumerate(days):
            cx0 = x0 + i * (cw + gap)
            ov.rounded((int(cx0), y0, int(cx0 + cw), y1), r=9, outline=lk.fg, width=1)
            mid = cx0 + cw / 2
            local = ctx.fmt.local(d.date + timedelta(hours=12)).date()
            ov.text((mid, y0 + 6), ctx.fmt.weekday_short(local), "jost-semibold", 18, lk.fg, anchor="ma")
            iy = y0 + 30 + (ch - 58 - isz) / 2
            soft.paste(base, soft.weather_sprite(d.code, True, isz, lk), (mid - isz / 2, iy))
            temps = ctx.fmt.temp(d.tmax) + " / " + ctx.fmt.temp(d.tmin)
            ov.text_fit((mid, y1 - 8), temps, "jost", int(cw - 8), 16, 11, lk.fg, anchor="ms")

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
