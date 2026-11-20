"""The service: main loop, precedence, rate limiting, buttons, alerts (DESIGN.md §5, §6.5)."""

from __future__ import annotations

import logging
import os
import queue
import signal
import socket
import subprocess
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from inkydash import actions, sysinfo
from inkydash.buttons import ButtonService, Led, Press
from inkydash.config import Config, ConfigError, load_config, parse_config
from inkydash.display.backends import DisplayBackend, make_backend
from inkydash.display.pipeline import to_panel
from inkydash.providers.registry import Providers
from inkydash.render.theme import BLACK, WHITE, new_canvas
from inkydash.render.widgets import Canvas
from inkydash.render_service import RenderService, config_tz
from inkydash.scheduler import Decision, Scheduler, live_window, next_trigger
from inkydash.screens.base import Frame
from inkydash.screens.status import describe_schedule
from inkydash.state import StateStore, state_dir

log = logging.getLogger(__name__)

BUTTON_MIN_INTERVAL_S = 10
MAX_SLEEP_S = 60            # wake at least this often (watchdog, config check, clock sync)
CONFIG_CHECK_S = 30
FAILURES_BEFORE_STATUS = 3
CLOCK_WAIT_S = 120


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- systemd integration
def sd_notify(msg: str) -> None:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr or not hasattr(socket, "AF_UNIX"):
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(msg.encode())
    except OSError:
        pass


@dataclass
class Override:
    screen: str
    until: datetime | None          # next trigger of any rule
    reason: str = "button"


@dataclass
class AlertState:
    screen: str
    active: bool = False
    delay_s: float = 0.0
    next_poll: datetime | None = None
    window: datetime | None = None   # trigger of the watch window the state belongs to
    dismissed: bool = False


@dataclass
class Shown:
    key: tuple[Any, ...] | None = None
    hash: str | None = None
    at: float = 0.0                  # monotonic time of the last panel update
    screen: str | None = None
    slot: str = ""


@dataclass
class Want:
    screen: str
    slot: str
    reason: str
    source: str                      # "schedule" | "override" | "alert" | "default"
    view: dict[str, Any] = field(default_factory=dict)


class App:
    def __init__(self, config_path: str | None = None, driver: str = "auto", out_dir: str = "out"):
        self.config_path = config_path
        self.driver = driver
        self.out_dir = out_dir
        self.store = StateStore(state_dir())
        self.events: queue.Queue[Press | str] = queue.Queue()
        self.stop = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="panel")
        self.busy: Future | None = None
        self.shown = Shown(hash=self.store.get("last_hash"))
        self.override: Override | None = None
        self.alerts: dict[str, AlertState] = {}
        self.views: dict[str, dict[str, Any]] = {}
        self.view_versions: dict[str, int] = {}
        self.errors: list[str] = []
        self.failures = 0
        self.button_render_at: float | None = None
        self.defer_until: float | None = None
        self.button_forced = False
        self.last_view_key: Any = None
        self.cleaned_slot: str | None = self.store.get("cleaned_slot")
        self.paused = bool(self.store.get("paused", False))
        self.clock_ok = True
        self.config_mtime = 0.0
        self.last_config_check = 0.0
        self.config: Config
        self._load_initial_config()
        self.backend: DisplayBackend = make_backend(driver, self.config.display.refresh_speed,
                                                    self.config.display.border, out_dir)
        self.buttons: ButtonService | None = None
        self.led = Led(None)

    # ------------------------------------------------------------ config
    def _load_initial_config(self) -> None:
        try:
            config, path = load_config(self.config_path)
            self.config_path = str(path)
            self.config_mtime = Path(path).stat().st_mtime
        except (ConfigError, FileNotFoundError) as e:
            self.errors = e.errors if isinstance(e, ConfigError) else [str(e)]
            log.error("config invalid: %s", "; ".join(self.errors))
            config = parse_config({"screens": {"status": {"type": "status"}}, "default_screen": "status"})
        self._apply_config(config)

    def _apply_config(self, config: Config) -> None:
        self.config = config
        if hasattr(self, "providers"):
            self.providers.close()
        self.providers = Providers(config, self.store.root)
        self.renderer = RenderService(config, self.providers, self.store)
        self.scheduler = Scheduler(config, config_tz(config))
        self.alerts = {name: AlertState(name) for name, s in config.screens.items()
                       if s.type == "navigation" and getattr(s, "alert", None)}
        self.views.clear()
        if self.override and self.override.screen not in config.screens:
            self.override = None

    def _check_config(self) -> None:
        if not self.config_path or time.monotonic() - self.last_config_check < CONFIG_CHECK_S:
            return
        self.last_config_check = time.monotonic()
        try:
            mtime = Path(self.config_path).stat().st_mtime
        except OSError:
            return
        if mtime == self.config_mtime:
            return
        self.config_mtime = mtime
        try:
            config, _ = load_config(self.config_path)
        except ConfigError as e:
            self.errors = e.errors
            log.error("new config is invalid, keeping the old one: %s", "; ".join(e.errors))
            self._go_status()
            return
        log.info("config reloaded")
        self.errors = []
        self._apply_config(config)
        self.shown.key = None  # re-render with the new config

    # ------------------------------------------------------------ decisions
    def _status_rows(self, now: datetime) -> list[tuple[str, str, int]]:
        d = self.scheduler.decide(now)
        return describe_schedule(now, d, self.scheduler.next_wake(now), self.renderer.fmt)

    def want(self, now: datetime) -> Want:
        d: Decision = self.scheduler.decide(now)
        # view state resets when the active rule (or its trigger) changes
        if d.view_key != self.last_view_key:
            self.last_view_key = d.view_key
            if self.override is None:
                self.views.clear()
        for a in self.alerts.values():
            if a.active and not a.dismissed:
                view = self.views.setdefault(a.screen, {})
                view["alert"] = {"delay_s": a.delay_s}
                return Want(a.screen, f"alert@{a.window}", f"traffic alert ({a.delay_s / 60:+.0f} min)", "alert",
                            view)
        if self.override:
            if self.override.until and now >= self.override.until:
                log.info("override of %s ended by a schedule trigger", self.override.screen)
                self.override = None
                self.views.clear()
            else:
                ov = self.override
                return Want(ov.screen, d.slot if ov.screen == d.screen else f"override:{ov.screen}",
                            f"manual ({ov.reason})", "override", self.views.setdefault(ov.screen, {}))
        source = "schedule" if d.rule else "default"
        return Want(d.screen, d.slot, d.reason, source, self.views.setdefault(d.screen, {}))

    def _render_key(self, w: Want) -> tuple[Any, ...]:
        return (w.screen, w.slot, self.view_versions.get(w.screen, 0), w.source == "alert")

    # ------------------------------------------------------------ alerts
    def _poll_alerts(self, now: datetime) -> None:
        for name, a in self.alerts.items():
            opts = self.config.screens[name]
            win = live_window(opts.alert.watch, now, self.scheduler.tz, self.scheduler.cal)
            if win is None:
                if a.active:
                    log.info("traffic alert for %s ended: watch window over", name)
                a.active, a.dismissed, a.window, a.next_poll = False, False, None, None
                continue
            if a.window != win[0]:
                a.window, a.active, a.dismissed, a.next_poll = win[0], False, False, None
            if a.next_poll and now < a.next_poll:
                continue
            a.next_poll = now + opts.alert.poll
            try:
                from inkydash.screens.navigation import check_alert_delay

                ctx = self.renderer.context(name, now)
                delay = check_alert_delay(ctx, opts)
            except Exception as e:
                log.warning("alert poll for %s failed: %s", name, e)
                continue
            if delay is None:
                continue
            a.delay_s = delay
            over = opts.alert.delay_over.total_seconds()
            if not a.active and delay > over:
                a.active = True
                log.info("traffic alert for %s: %+.0f min vs usual", name, delay / 60)
            elif a.active and delay < over - opts.alert.hysteresis.total_seconds():
                a.active = False
                log.info("traffic alert for %s ended: %+.0f min", name, delay / 60)

    def _next_alert_poll(self, now: datetime) -> datetime | None:
        """Next due poll inside a watch window, or the start of the next window."""
        cands = []
        for name, a in self.alerts.items():
            watch = self.config.screens[name].alert.watch
            if live_window(watch, now, self.scheduler.tz, self.scheduler.cal) is not None:
                cands.append(a.next_poll or now)
            else:
                nt = next_trigger(watch, now, self.scheduler.tz, self.scheduler.cal)
                if nt:
                    cands.append(nt)
        return min(cands) if cands else None

    # ------------------------------------------------------------ buttons
    def _screen_cycle(self) -> list[str]:
        return [n for n, s in self.config.screens.items() if s.type != "quiet"]

    def _go_status(self) -> None:
        self.override = Override(self.renderer.status_screen, self.scheduler.next_trigger_any(utcnow()), "status")
        self.button_render_at = time.monotonic()

    def handle_press(self, p: Press, now: datetime) -> None:
        self.led.flash()
        current = self.shown.screen or self.want(now).screen
        action = actions.resolve(self.config, current, p.button, p.long)
        if action in actions.DESTRUCTIVE and p.held_s < self.config.buttons.shutdown_hold.total_seconds():
            log.info("%s needs a %.0fs hold (held %.1fs)", action, self.config.buttons.shutdown_hold.total_seconds(),
                     p.held_s)
            return
        log.info("button %s%s → %s", p.button, " (long)" if p.long else "", action)
        self.do_action(action, current, now)

    def do_action(self, action: str, current: str, now: datetime) -> None:
        until = self.scheduler.next_trigger_any(now)
        cycle = self._screen_cycle()
        changed = True
        # dismissing an active alert: A / resume
        alert_on = any(a.active and not a.dismissed for a in self.alerts.values())
        if alert_on and action in ("next_screen", "resume_schedule"):
            for a in self.alerts.values():
                if a.active:
                    a.dismissed = True
                    self.views.get(a.screen, {}).pop("alert", None)
            log.info("traffic alert dismissed")
            if action == "next_screen":
                self.button_render_at = time.monotonic() + self.config.buttons.coalesce.total_seconds()
                return
        if action == "none":
            return
        if action == "next_screen" or action == "prev_screen":
            i = cycle.index(current) if current in cycle else -1
            step = 1 if action == "next_screen" else -1
            self.override = Override(cycle[(i + step) % len(cycle)], until)
        elif action.startswith("goto:"):
            self.override = Override(action[5:], until)
        elif action == "resume_schedule":
            self.override = None
            self.views.clear()
        elif action == "status":
            self.override = Override(self.renderer.status_screen, until, "status")
        elif action == "refresh":
            self.button_forced = True
        elif action == "pause_toggle":
            self.paused = not self.paused
            self.store.set("paused", self.paused)
            self.store.save()
            log.info("updates %s", "paused" if self.paused else "resumed")
            if self.paused:
                self._show_message(self.renderer.fmt.t("paused"), self.renderer.fmt.t("paused_hint"))
                return
            self.shown.key = None
        elif action == "clean":
            self._submit(lambda: self.backend.clean(1))
            self.shown.key = None
        elif action == "led_toggle":
            self.led.enabled = not self.led.enabled
            changed = False
        elif action in ("shutdown", "reboot"):
            self._power(action)
            return
        else:
            # screen-type action on the current screen: pins it (manual override)
            w = self.want(now)
            slot = w.slot if w.screen == current else f"override:{current}"
            view = self.views.setdefault(current, {})
            changed = self.renderer.run_action(current, action, now, slot, view)
            if changed:
                self.view_versions[current] = self.view_versions.get(current, 0) + 1
                if self.override is None or self.override.screen != current:
                    self.override = Override(current, until, action)
        if changed:
            self.button_render_at = time.monotonic() + self.config.buttons.coalesce.total_seconds()

    def _power(self, action: str) -> None:
        fmt = self.renderer.fmt
        title = fmt.t("shutdown.title") if action == "shutdown" else "Rebooting…"
        self._show_message(title, fmt.t("shutdown.hint") if action == "shutdown" else "", wait=True)
        cmd = ["sudo", "-n", "systemctl", "poweroff" if action == "shutdown" else "reboot"]
        log.warning("running %s", " ".join(cmd))
        try:
            subprocess.run(cmd, check=False, timeout=30)
        except (OSError, subprocess.SubprocessError) as e:
            log.error("%s failed: %s", action, e)

    def _show_message(self, title: str, detail: str = "", wait: bool = False) -> None:
        img = new_canvas(self.renderer.size)
        c = Canvas(img)
        c.placeholder(title, detail)
        from inkydash.display.pipeline import compose

        logical = compose(Frame(img), self.renderer.size)
        fut = self._submit(lambda: self._show(logical, "message"))
        if wait and fut:
            fut.result(timeout=120)

    # ------------------------------------------------------------ panel
    def _submit(self, fn) -> Future | None:
        if self.busy and not self.busy.done():
            self.busy.result()  # one panel operation at a time
        self.led.busy(True)
        fut = self.executor.submit(fn)
        fut.add_done_callback(lambda f: (self.led.busy(False), self.events.put("panel-done")))
        self.busy = fut
        return fut

    def _show(self, logical, label: str) -> None:
        cfg = self.config.display
        panel = to_panel(logical, cfg.orientation, cfg.flip)
        t0 = time.monotonic()
        self.backend.show(logical, panel)
        log.info("shown %s (%.1fs)", label, time.monotonic() - t0)

    def _clock_badge(self, img) -> None:
        c = Canvas(img)
        text = self.renderer.fmt.t("clock_not_synced")
        w = c.width(text, "bold", 12) + 30
        c.rect((0, 0, w, 18), fill=BLACK)
        c.warn_mark(4, 9, 14)
        c.text((24, 9), text, "bold", 12, WHITE, anchor="lm")

    # Buttons A–D sit along the left edge of the panel buffer, top to bottom (to verify on the
    # device). Positions as fractions of the panel height.
    BUTTON_Y = {"A": 0.14, "B": 0.38, "C": 0.62, "D": 0.86}

    def _legend(self, img, screen: str) -> None:
        """Small labels next to the physical buttons, placed for orientation + flip."""
        c = Canvas(img)
        w, h = img.size
        orientation, flip = self.config.display.orientation, self.config.display.flip
        for button, frac in self.BUTTON_Y.items():
            text = actions.label(self.config, screen, actions.resolve(self.config, screen, button, False))
            if not text:
                continue
            py = frac * 480  # panel-buffer y on the left edge (x = 0)
            if orientation == "landscape":
                x, y, edge = (w, 479 - py, "right") if flip else (0, py, "left")
            else:  # portrait: buffer left edge = logical bottom edge (top edge when flipped)
                x, y, edge = (479 - py, 0, "top") if flip else (py, h, "bottom")
            tw = c.width(text, "bold", 12) + 10
            if edge in ("left", "right"):
                x0 = 0 if edge == "left" else w - tw
                box = (x0, int(y - 9), x0 + tw, int(y + 9))
            else:
                y0 = 0 if edge == "top" else h - 18
                box = (int(x - tw / 2), y0, int(x + tw / 2), y0 + 18)
            c.rect(box, fill=BLACK)
            c.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), text, "bold", 12, WHITE, anchor="mm")

    def step(self, now: datetime) -> None:
        """One pass: decide, render if the key changed, update the panel if allowed."""
        if self.busy and not self.busy.done():
            return  # the panel is refreshing; we'll be woken when it's done
        if self.paused and not self.button_forced:
            return
        if not self.paused:
            self._poll_alerts(now)
        w = self.want(now)
        if self.renderer.options(w.screen).type == "status":
            w.view["errors"] = self.errors
            w.view["schedule"] = self._status_rows(now)
        key = self._render_key(w)
        button_due = self.button_render_at is not None and time.monotonic() >= self.button_render_at
        if self.button_render_at is not None and not button_due:
            return  # coalescing presses
        if key == self.shown.key and not self.button_forced:
            return
        manual = button_due or self.button_forced
        try:
            r = self.renderer.render(w.screen, now, w.slot, w.view)
            self.failures = 0
        except Exception as e:
            self.failures += 1
            log.exception("render of %s failed (%d in a row)", w.screen, self.failures)
            self.shown.key = key  # don't retry before the next key change
            if self.failures >= FAILURES_BEFORE_STATUS and w.screen != self.renderer.status_screen:
                self.errors = [*self.errors[-4:], f"{w.screen}: {type(e).__name__}: {e}"]
                self._go_status()
            return
        if not self.clock_ok:
            self._clock_badge(r.image)
        if self.config.buttons.legend:
            self._legend(r.image, w.screen)
        min_gap = BUTTON_MIN_INTERVAL_S if manual else self.config.display.min_refresh_interval.total_seconds()
        since = time.monotonic() - self.shown.at
        if r.hash == self.shown.hash and not self.button_forced:
            log.debug("%s unchanged (hash %s): no refresh", w.screen, r.hash)
            self.shown.key, self.shown.screen, self.shown.slot = key, w.screen, w.slot
            self.button_render_at = None
            return
        if self.shown.at and since < min_gap:
            self.defer_until = time.monotonic() + (min_gap - since)
            log.info("deferring %s for %.0fs (rate limit)", w.screen, min_gap - since)
            return
        self.button_render_at = None
        self.button_forced = False
        self.defer_until = None
        quiet_clean = None
        opts = self.renderer.options(w.screen)
        if opts.type == "quiet" and opts.cleans and self.cleaned_slot != w.slot:
            quiet_clean = opts.clean_cycles
            self.cleaned_slot = w.slot
            self.store.set("cleaned_slot", w.slot)
        log.info("showing %s [%s] — %s (rendered in %.1fs)", w.screen, w.slot, w.reason, r.seconds)

        def job(img=r.image, cycles=quiet_clean, label=w.screen):
            if cycles:
                self.backend.clean(cycles)
            self._show(img, label)

        self._submit(job)
        self.shown = Shown(key, r.hash, time.monotonic(), w.screen, w.slot)
        self.store.set("last_hash", r.hash)
        self.store.save()
        self.renderer.prefetch(w.screen, now, w.slot)

    def next_wake_s(self, now: datetime) -> float:
        cands = [MAX_SLEEP_S]
        nxt = self.scheduler.next_wake(now)
        if nxt:
            cands.append((nxt - now).total_seconds())
        if self.override and self.override.until:
            cands.append((self.override.until - now).total_seconds())
        poll = self._next_alert_poll(now)
        if poll:
            cands.append((poll - now).total_seconds())
        mono = time.monotonic()
        for t in (self.defer_until, self.button_render_at):
            if t is not None:
                cands.append(t - mono)
        return max(0.2, min(cands))

    # ------------------------------------------------------------ lifecycle
    def _wait_for_clock(self) -> None:
        synced = sysinfo.clock_synced()
        if synced is None or synced:
            return
        log.info("waiting up to %ds for NTP", CLOCK_WAIT_S)
        deadline = time.monotonic() + CLOCK_WAIT_S
        while time.monotonic() < deadline and not self.stop.is_set():
            if sysinfo.clock_synced():
                log.info("clock synced")
                return
            sd_notify("WATCHDOG=1")
            time.sleep(2)
        log.warning("clock not synced: continuing with the system clock")
        self.clock_ok = False

    def run(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, lambda *_: (self.stop.set(), self.events.put("stop")))
            except ValueError:
                pass
        sd_notify("READY=1")
        self._wait_for_clock()
        if self.config.buttons.enabled:
            self.buttons = ButtonService(dict(self.config.buttons.pins),
                                         self.config.buttons.long_press.total_seconds(), self.events)
            self.buttons.start()
        self.led = Led(self.config.buttons.led_pin)
        if self.errors:
            self._go_status()
        log.info("inky-dash running (%s backend, %s)", self.backend.name, self.config_path)
        try:
            while not self.stop.is_set():
                sd_notify("WATCHDOG=1")
                if not self.clock_ok and sysinfo.clock_synced():
                    log.info("clock synced: re-evaluating the schedule")
                    self.clock_ok = True
                    self.shown.key = None
                self._check_config()
                now = utcnow()
                try:
                    self.step(now)
                except Exception:
                    log.exception("main loop error")
                timeout = self.next_wake_s(utcnow())
                try:
                    ev = self.events.get(timeout=timeout)
                except queue.Empty:
                    continue
                pending = [ev]
                while True:
                    try:
                        pending.append(self.events.get_nowait())
                    except queue.Empty:
                        break
                for ev in pending:
                    if isinstance(ev, Press):
                        try:
                            self.handle_press(ev, utcnow())
                        except Exception:
                            log.exception("button handling failed")
                    elif isinstance(ev, str) and ev.startswith("action:"):
                        self.do_action(ev[7:], self.shown.screen or self.want(utcnow()).screen, utcnow())
        finally:
            log.info("stopping")
            if self.buttons:
                self.buttons.stop()
            if self.busy:
                try:
                    self.busy.result(timeout=60)
                except Exception:
                    pass
            self.executor.shutdown(wait=True)
            self.store.save()
            self.providers.close()
            self.backend.close()


def run(config: str | None, driver: str, out_dir: str) -> int:
    App(config, driver, out_dir).run()
    return 0
