"""`navigation`: best route with live traffic, alternatives, map and leave-time forecast
(DESIGN.md §6.3)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Literal

from pydantic import ConfigDict, Field

from inkydash.models import Mode, Strict, When
from inkydash.providers.cache import ProviderError
from inkydash.providers.routing.alternatives import downsample, filter_alternatives, match_selection
from inkydash.providers.routing.base import Route
from inkydash.render import icons
from inkydash.render.map import render_map
from inkydash.render.theme import BLACK, GREEN, RED, WHITE, YELLOW, new_canvas
from inkydash.render.widgets import Canvas
from inkydash.screens.base import Action, Frame, RenderContext, ScreenOptions
from inkydash.util import HHMM, Duration, Offset

log = logging.getLogger(__name__)
NO_LIVE_FALLBACK_AFTER_S = 3600


class AlternativesOpts(Strict):
    max: int = Field(3, ge=1, le=6)
    within: Duration = Field(default="10m", validate_default=True)
    within_pct: float = Field(20, ge=0, le=200)


class MapOpts(Strict):
    padding: float = Field(0.1, ge=0, le=0.4)
    show_alternatives: bool = True


class AlertOpts(Strict):
    watch: When
    poll: Duration = Field(default="15m", validate_default=True)
    delay_over: Duration = Field(default="10m", validate_default=True)
    hysteresis: Duration = Field(default="3m", validate_default=True)


class NavigationOptions(ScreenOptions):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["navigation"] = "navigation"
    from_: str = Field(alias="from")
    to: str | list[str]
    modes: list[Mode] = ["car"]
    alternatives: AlternativesOpts | Literal[False] = AlternativesOpts()
    arrive_by: HHMM | None = None
    depart_at: HHMM | None = None
    forecast: list[Offset] = Field(default=["+15m", "+30m"], validate_default=True)
    delay_threshold: Duration = Field(default="3m", validate_default=True)
    map: MapOpts | Literal[False] = MapOpts()
    alert: AlertOpts | None = None
    route_names: dict[str, str] = {}      # rename "via" labels: {"E40": "Highway", "R0 / A12": "Ring"}
    cache_for: Duration = Field(default="4m", validate_default=True)

    @property
    def destinations(self) -> list[str]:
        return self.to if isinstance(self.to, list) else [self.to]


# ------------------------------------------------------------------ data
@dataclass
class RouteResult:
    routes: list[Route]
    provider: str
    live: bool
    fetched_at: datetime
    stale: bool = False
    note: str | None = None
    forecast: list[tuple[timedelta, float]] = field(default_factory=list)


def endpoints(ctx: RenderContext, opts: NavigationOptions) -> tuple[str, str]:
    dests = opts.destinations
    a, b = opts.from_, dests[ctx.view.get("dest_index", 0) % len(dests)]
    if ctx.view.get("reversed"):
        a, b = b, a
    return a, b


def current_mode(ctx: RenderContext, opts: NavigationOptions) -> str:
    return opts.modes[ctx.view.get("mode_index", 0) % len(opts.modes)]


def fetch_routes(ctx: RenderContext, opts: NavigationOptions, a_key: str, b_key: str, mode: str,
                 with_forecast: bool = True) -> RouteResult:
    p = ctx.providers
    a, b = p.latlon(a_key), p.latlon(b_key)
    chain = p.routing(mode)
    n_alt = opts.alternatives.max if opts.alternatives else 0
    want_live = mode == "car"
    key = f"route:{mode}:{a[0]:.5f},{a[1]:.5f}:{b[0]:.5f},{b[1]:.5f}:{n_alt}"

    def fetch(live_only: bool, exclude_live: bool) -> dict[str, Any]:
        rs = chain.routes(a, b, alternatives=n_alt, live_only=live_only, exclude_live=exclude_live)
        return {"provider": rs[0].provider, "routes": [r.to_json() for r in rs]}

    note = None
    try:
        cached = p.cache.get(key, opts.cache_for.total_seconds(), lambda: fetch(want_live, False),
                             max_stale_s=NO_LIVE_FALLBACK_AFTER_S)
    except ProviderError:
        if not want_live:
            raise
        # no live traffic for over an hour: keyless router, clearly labelled
        cached = p.cache.get(key + ":nolive", 1800, lambda: fetch(False, True))
        note = "no_live"
    routes = [Route.from_json(r) for r in cached.value["routes"]]
    if opts.alternatives:
        routes = filter_alternatives(routes, opts.alternatives.max, opts.alternatives.within,
                                     opts.alternatives.within_pct)
    else:
        routes = sorted(routes, key=lambda r: r.duration_s)[:1]
    res = RouteResult(routes, cached.value["provider"], bool(routes and routes[0].live), cached.fetched_at,
                      cached.stale, note)
    if with_forecast and res.live and opts.forecast and not cached.stale:
        for off in opts.forecast:
            depart = ctx.now + off
            fkey = f"route-fc:{mode}:{a[0]:.5f},{a[1]:.5f}:{b[0]:.5f},{b[1]:.5f}:{int(off.total_seconds())}"
            try:
                fc = p.cache.get(fkey, opts.cache_for.total_seconds(),
                                 lambda depart=depart: [r.duration_s for r in
                                                        chain.routes(a, b, depart=depart, live_only=True)])
                res.forecast.append((off, min(fc.value)))
            except ProviderError as e:
                log.info("forecast %s unavailable: %s", off, e)
                break
    return res


def best_delay_s(res: RouteResult) -> float | None:
    """Delay of the fastest route compared with usual (or free-flow)."""
    if not res.routes or not res.live:
        return None
    r = res.routes[0]
    base = r.baseline_s
    return None if base is None else r.duration_s - base


def check_alert_delay(ctx: RenderContext, opts: NavigationOptions) -> float | None:
    """Used by the app's alert poller: one request, no forecast. The result is cached, so a
    takeover render reuses it."""
    a, b = opts.from_, opts.destinations[0]
    res = fetch_routes(ctx, opts, a, b, opts.modes[0], with_forecast=False)
    return best_delay_s(res)


# ------------------------------------------------------------------ actions
def _next_route(ctx: RenderContext, opts: NavigationOptions) -> bool:
    n = ctx.view.get("route_count", 1)
    if n < 2:
        return False
    ctx.view["select_index"] = (ctx.view.get("selected", 0) + 1) % n
    return True


def _prev_route(ctx: RenderContext, opts: NavigationOptions) -> bool:
    n = ctx.view.get("route_count", 1)
    if n < 2:
        return False
    ctx.view["select_index"] = (ctx.view.get("selected", 0) - 1) % n
    return True


def _reset_selection(ctx: RenderContext) -> None:
    for k in ("selected", "selected_geom", "select_index", "route_count"):
        ctx.view.pop(k, None)


def _reverse(ctx: RenderContext, opts: NavigationOptions) -> bool:
    ctx.view["reversed"] = not ctx.view.get("reversed", False)
    _reset_selection(ctx)
    return True


def _next_destination(ctx: RenderContext, opts: NavigationOptions) -> bool:
    if len(opts.destinations) < 2:
        return False
    ctx.view["dest_index"] = ctx.view.get("dest_index", 0) + 1
    _reset_selection(ctx)
    return True


def _cycle_mode(ctx: RenderContext, opts: NavigationOptions) -> bool:
    if len(opts.modes) < 2:
        return False
    ctx.view["mode_index"] = ctx.view.get("mode_index", 0) + 1
    _reset_selection(ctx)
    return True


# ------------------------------------------------------------------ screen
class Navigation:
    name: ClassVar[str] = "navigation"
    Options: ClassVar[type[ScreenOptions]] = NavigationOptions
    actions: ClassVar[dict[str, Action]] = {
        "next_route": Action("next_route", _next_route, "route"),
        "prev_route": Action("prev_route", _prev_route, "route"),
        "reverse_route": Action("reverse_route", _reverse, "⇄"),
        "next_destination": Action("next_destination", _next_destination, "dest"),
        "cycle_mode": Action("cycle_mode", _cycle_mode, "mode"),
    }

    @staticmethod
    def check_config(opts: NavigationOptions, config) -> str | None:
        for k in [opts.from_, *opts.destinations]:
            if k not in config.locations:
                return f"unknown location {k!r}"
        if opts.arrive_by and opts.depart_at:
            return "use arrive_by or depart_at, not both"
        return None

    def _via(self, opts: NavigationOptions, r: Route, i: int, ctx: RenderContext) -> str:
        via = r.via or ""
        for pattern, name in opts.route_names.items():
            if via == pattern or (pattern and pattern in via.split(" / ")):
                return name
        return f"{ctx.t('nav.via')} {via}" if via else f"{ctx.t('nav.route')} {i + 1}"

    def render(self, ctx: RenderContext, opts: NavigationOptions) -> Frame:
        img = new_canvas(ctx.size)
        c = Canvas(img, ctx.theme)
        a_key, b_key = endpoints(ctx, opts)
        mode = current_mode(ctx, opts)
        try:
            res = fetch_routes(ctx, opts, a_key, b_key, mode)
        except Exception as e:
            log.warning("navigation: %s", e)
            c.placeholder(ctx.t("nav.unavailable"), str(e))
            box = c.footer(f"{ctx.t('updated')} {ctx.fmt.time(ctx.now)}")
            return Frame(img, volatile=[box])
        if not res.routes:
            c.placeholder(ctx.t("nav.no_route"))
            return Frame(img)

        # selection: explicit button choice, else stick to the previous geometry, else fastest
        routes = res.routes
        note = None
        if "select_index" in ctx.view:
            sel = min(ctx.view.pop("select_index"), len(routes) - 1)
        else:
            prev = ctx.view.get("selected_geom")
            m = match_selection(routes, prev)
            sel = m if m is not None else 0
            if prev and m is None and ctx.view.get("selected", 0) != 0:
                note = ctx.t("nav.selection_gone")
        ctx.view.update(selected=sel, route_count=len(routes), selected_geom=downsample(routes[sel].geometry))

        W, H = ctx.size
        footer_h = 20
        top = 0
        alert = ctx.view.get("alert")
        if alert:
            top = 40
            c.rect((0, 0, W, top), fill=RED)
            icons.warning_triangle(c, 24, top / 2, 26)
            c.text_fit((46, top / 2), ctx.t("nav.alert", delay=ctx.fmt.minutes(alert["delay_s"], sign=True)), "bold",
                       W - 60, 22, 12, WHITE, anchor="lm")
        landscape = ctx.landscape
        map_box = (0, top, 500, H - footer_h) if landscape else (0, top, W, top + 420)
        panel = (map_box[2] + 12, top + 8, W - 10, H - footer_h - 4) if landscape else \
            (12, map_box[3] + 8, W - 12, H - footer_h - 4)
        attribution = ""
        if opts.map is not False:
            tiles = ctx.providers.tiles
            attribution = tiles.attribution if tiles.enabled else ""
            mw, mh = map_box[2] - map_box[0], map_box[3] - map_box[1]
            m = render_map((mw, mh), routes, sel, tiles, opts.map.padding, opts.map.show_alternatives,
                           attribution)
            img.paste(m, (map_box[0], map_box[1]))
            if landscape:
                c.vline(map_box[2], map_box[1], map_box[3], BLACK, 2)
            else:
                c.hline(map_box[3], 0, W, BLACK, 2)
        else:
            panel = (12, top + 8, W - 12, H - footer_h - 4)

        self._panel(c, ctx, opts, res, sel, panel, mode, a_key, b_key, note)

        provider = res.provider.upper() if res.provider in ("osrm", "ors") else res.provider.capitalize()
        if res.provider == "tomtom":
            provider = "TomTom"
        warn = False
        if res.note == "no_live" or (not res.live and mode == "car"):
            foot = f"{ctx.t('nav.no_live')} · {provider}"
            warn = True
        elif not res.live:
            foot = f"{provider} · {ctx.t('updated')} {ctx.fmt.time(res.fetched_at)}"
        elif res.stale:
            foot = f"{ctx.t('nav.traffic_from')} {ctx.fmt.time(res.fetched_at)} · {provider}"
            warn = True
        else:
            foot = f"{ctx.t('nav.live')} · {provider} · {ctx.t('updated')} {ctx.fmt.time(res.fetched_at)}"
        box = c.footer(foot, size=12, height=footer_h, warn=warn)
        return Frame(img, volatile=[box] if not res.stale else [],
                     meta={"provider": res.provider, "live": res.live, "routes": len(routes)})

    def _panel(self, c: Canvas, ctx: RenderContext, opts: NavigationOptions, res: RouteResult, sel: int,
               box: tuple[int, int, int, int], mode: str, a_key: str, b_key: str, note: str | None) -> None:
        x0, y0, x1, y1 = box
        w = x1 - x0
        r = res.routes[sel]
        locs = ctx.config.locations
        dest = locs[b_key].label or b_key
        origin = locs[a_key].label or a_key
        ow = c.width(origin, "bold", 18)
        if ow + 30 < w // 2:
            c.text((x0, y0), origin, "bold", 18, BLACK)
            _arrow_right(c, x0 + ow + 8, y0 + 11, 14)
            c.text_fit((x0 + ow + 28, y0), dest, "bold", w - ow - 28, 18, 12, BLACK)
        else:
            c.text_fit((x0, y0), f"{origin} – {dest}", "bold", w, 18, 12, BLACK)
        y = y0 + 30
        icons.mode_icon(c, (x0, y + 4, x0 + 44, y + 48), mode)
        dur = ctx.fmt.minutes(r.duration_s)
        size = c.fit_size(dur, "num", w - 56, 64, 30)
        c.text((x0 + 54, y + 26), dur, "num", size, BLACK, anchor="lm")
        y += 58
        # leave-by / ETA
        when = self._when_line(ctx, opts, r)
        if when:
            c.text_fit((x0, y), when, "bold", w, 18, 12, BLACK)
            y += 26
        # compared with usual
        base = r.baseline_s
        if res.live and base is not None:
            delta = r.duration_s - base
            label = ctx.t("nav.vs_usual") if r.duration_typical_s is not None else ctx.t("nav.vs_free_flow")
            if abs(delta) < opts.delay_threshold.total_seconds():
                c.badge((x0, y), ctx.t("nav.normal"), GREEN, WHITE, size=16)
            elif delta > 0:
                c.badge((x0, y), f"{ctx.fmt.minutes(delta, sign=True)} {label}", RED, WHITE, size=16)
            else:
                c.badge((x0, y), f"{ctx.fmt.minutes(delta, sign=True)} {label}", GREEN, WHITE, size=16)
            y += 34
        elif not res.live:
            c.badge((x0, y), ctx.t("nav.no_live_short"), WHITE, BLACK, size=14)
            y += 32
        if note:
            c.text_fit((x0, y), note, "ui", w, 13, 10, BLACK)
            y += 18
        # route list
        if len(res.routes) > 1:
            fastest = res.routes[0].duration_s
            for i, rt in enumerate(res.routes):
                line_h = 26
                if i == sel:
                    c.rect((x0 - 4, y - 2, x1, y + line_h - 4), fill=YELLOW)
                c.text((x0 + 2, y + 10), str(i + 1), "bold", 17, BLACK, anchor="lm")
                diff = "" if i == 0 else f" ({ctx.fmt.minutes(rt.duration_s - fastest, sign=True)})"
                right = ctx.fmt.minutes(rt.duration_s) + diff
                rw = c.width(right, "bold", 15)
                c.text_fit((x0 + 22, y + 10), self._via(opts, rt, i, ctx), "ui", w - rw - 34, 15, 11, BLACK,
                           anchor="lm")
                c.text((x1 - 2, y + 10), right, "bold", 15, BLACK, anchor="rm")
                y += line_h
            y += 4
        else:
            c.text_fit((x0, y), self._via(opts, r, 0, ctx), "ui", w, 16, 11, BLACK)
            y += 24
        # incidents on the selected route
        incidents = sorted([s for s in r.traffic_sections if s.magnitude >= 1 or s.category == "closure"],
                           key=lambda s: -(s.delay_s or 0))[:3]
        for s in incidents:
            if y > y1 - 60:
                break
            text = ctx.t(f"nav.cat.{s.category}")
            km = _section_km(r, s)
            if km:
                text += f" · {ctx.fmt.distance(km * 1000)}"
            if s.delay_s:
                text += f" · {ctx.fmt.minutes(s.delay_s, sign=True)}"
            if s.category == "closure":
                icons.closure(c, x0 + 8, y + 9, 8)
            else:
                icons.warning_triangle(c, x0 + 8, y + 9, 17, RED if s.magnitude >= 3 else YELLOW)
            c.text_fit((x0 + 22, y), text, "ui", w - 22, 15, 11, BLACK)
            y += 22
        # leave-time strip
        if res.forecast:
            entries = [(ctx.t("nav.now"), r.duration_s)] + [
                (f"+{int(off.total_seconds() // 60)} {ctx.t('min')}", d) for off, d in res.forecast]
            best = min(range(len(entries)), key=lambda i: entries[i][1])
            yy = max(y + 4, y1 - 34)
            colw = w / len(entries)
            for i, (lab, d) in enumerate(entries):
                cx = x0 + colw * (i + 0.5)
                if i == best and len(entries) > 1:
                    c.rounded((int(x0 + colw * i + 2), yy - 2, int(x0 + colw * (i + 1) - 2), yy + 32), 5,
                              fill=YELLOW, outline=BLACK)
                c.text((cx, yy + 1), lab, "ui", 13, BLACK, anchor="ma")
                c.text((cx, yy + 30), ctx.fmt.minutes(d), "bold", 15, BLACK, anchor="mb")

    def _when_line(self, ctx: RenderContext, opts: NavigationOptions, r: Route) -> str | None:
        local_now = ctx.fmt.local(ctx.now)
        tz = local_now.tzinfo
        if opts.arrive_by:
            arrive = datetime.combine(local_now.date(), opts.arrive_by, tzinfo=tz)
            leave = arrive - timedelta(seconds=r.duration_s)
            if leave.astimezone(UTC) > ctx.now - timedelta(minutes=30):
                return ctx.t("nav.leave_by", time=ctx.fmt.time(leave))
        if opts.depart_at:
            depart = datetime.combine(local_now.date(), opts.depart_at, tzinfo=tz)
            if depart.astimezone(UTC) > ctx.now:
                return ctx.t("nav.depart_arrive", depart=ctx.fmt.time(depart),
                             arrive=ctx.fmt.time(depart + timedelta(seconds=r.duration_s)))
        return ctx.t("nav.arrive", time=ctx.fmt.time(ctx.now + timedelta(seconds=r.duration_s)))


def _arrow_right(c: Canvas, x: float, cy: float, size: float) -> None:
    """→ (the fonts have no arrow glyph)."""
    c.d.line([(x, cy), (x + size - 4, cy)], fill=BLACK, width=2)
    c.d.polygon([(x + size, cy), (x + size - 6, cy - 5), (x + size - 6, cy + 5)], fill=BLACK)


def _section_km(r: Route, s: Any) -> float | None:
    import math

    pts = r.geometry[max(0, s.start_idx): s.end_idx + 1]
    if len(pts) < 2:
        return None
    d = 0.0
    for (a_lat, a_lon), (b_lat, b_lon) in zip(pts, pts[1:], strict=False):
        dy = (b_lat - a_lat) * 111.32
        dx = (b_lon - a_lon) * 111.32 * math.cos(math.radians(a_lat))
        d += math.hypot(dx, dy)
    return d
