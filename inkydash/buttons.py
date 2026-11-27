"""GPIO buttons (short/long press) and the rear LED (DESIGN.md §6.5). Pi only; both degrade
to no-ops elsewhere."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Press:
    button: str        # "A".."D"
    held_s: float
    long: bool


class ButtonService:
    """Reads both edges; a press is reported on release with its hold time."""

    def __init__(self, pins: dict[str, int], long_press_s: float, events: queue.Queue):
        self.pins = pins
        self.long_press_s = long_press_s
        self.events = events
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.request = None

    def start(self) -> bool:
        try:
            import gpiod  # type: ignore[import-not-found]
            import gpiodevice  # type: ignore[import-not-found]
            from gpiod.line import Bias, Direction, Edge  # type: ignore[import-not-found]
        except ImportError:
            log.info("gpiod not installed: buttons disabled")
            return False
        try:
            chip = gpiodevice.find_chip_by_platform()
            settings = gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP, edge_detection=Edge.BOTH,
                                          debounce_period=__import__("datetime").timedelta(milliseconds=20))
            self.offsets = {chip.line_offset_from_id(pin): name for name, pin in self.pins.items()}
            self.request = chip.request_lines(consumer="inky-dash-buttons",
                                              config=dict.fromkeys(self.offsets, settings))
        except Exception as e:
            log.warning("buttons unavailable: %s", e)
            return False
        self._thread = threading.Thread(target=self._run, name="buttons", daemon=True)
        self._thread.start()
        log.info("buttons ready on BCM %s", self.pins)
        return True

    def _run(self) -> None:
        from gpiod.edge_event import EdgeEvent  # type: ignore[import-not-found]

        down: dict[str, float] = {}
        while not self._stop.is_set():
            try:
                if not self.request.wait_edge_events(timeout=1.0):
                    continue
                for ev in self.request.read_edge_events():
                    name = self.offsets.get(ev.line_offset)
                    if name is None:
                        continue
                    t = ev.timestamp_ns / 1e9
                    if ev.event_type == EdgeEvent.Type.FALLING_EDGE:   # active-low: pressed
                        down[name] = t
                    elif name in down:
                        held = t - down.pop(name)
                        self.events.put(Press(name, held, held >= self.long_press_s))
            except Exception:
                if self._stop.is_set():
                    break
                log.exception("button thread error")
                time.sleep(1)

    def stop(self) -> None:
        self._stop.set()
        thread = getattr(self, "_thread", None)
        if thread is not None:
            thread.join(timeout=2.0)   # wait_edge_events times out after 1 s
        if self.request is not None:
            try:
                self.request.release()
            except Exception:
                pass


class Led:
    """The shine-through LED on the back of the Spectra 6 board (BCM 13)."""

    def __init__(self, pin: int | None):
        self.req = None
        self.offset = None
        self.enabled = True
        self._blink: threading.Thread | None = None
        self._busy = threading.Event()
        if pin is None:
            return
        try:
            import gpiod  # type: ignore[import-not-found]
            import gpiodevice  # type: ignore[import-not-found]
            from gpiod.line import Bias, Direction  # type: ignore[import-not-found]

            chip = gpiodevice.find_chip_by_platform()
            self.offset = chip.line_offset_from_id(pin)
            self.req = chip.request_lines(consumer="inky-dash-led", config={
                self.offset: gpiod.LineSettings(direction=Direction.OUTPUT, bias=Bias.DISABLED)})
        except Exception as e:
            log.debug("LED unavailable: %s", e)
            self.req = None

    def set(self, on: bool) -> None:
        if self.req is None:
            return
        from gpiod.line import Value  # type: ignore[import-not-found]

        self.req.set_value(self.offset, Value.ACTIVE if on and self.enabled else Value.INACTIVE)

    def busy(self, on: bool) -> None:
        """Pulse while a refresh is in progress."""
        if on:
            self._busy.set()
            if self._blink is None or not self._blink.is_alive():
                self._blink = threading.Thread(target=self._pulse, daemon=True)
                self._blink.start()
        else:
            self._busy.clear()

    def _pulse(self) -> None:
        state = False
        while self._busy.is_set():
            state = not state
            self.set(state)
            time.sleep(0.4)
        self.set(False)

    def flash(self) -> None:
        self.set(True)
        threading.Timer(0.15, self.set, args=(False,)).start()
