"""Builds render contexts and turns a screen into a composed panel image. Shared by the
service (app.py) and the CLI (`render`, `testcard`)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from PIL import Image

from inkydash.config import Config
from inkydash.display.pipeline import compose, content_hash, logical_size
from inkydash.i18n import Formatter
from inkydash.providers.registry import Providers
from inkydash.registry import screen_type
from inkydash.render.theme import Theme
from inkydash.screens.base import Frame, RenderContext
from inkydash.screens.status import StatusOptions
from inkydash.state import StateStore

log = logging.getLogger(__name__)
STATUS_SCREEN = "_status"


def config_tz(config: Config):
    if config.timezone:
        return ZoneInfo(config.timezone)
    try:
        return datetime.now().astimezone().tzinfo
    except Exception:
        return ZoneInfo("UTC")


def ensure_status_screen(config: Config) -> str:
    """The status screen is always available (buttons, errors), even if not configured."""
    for name, s in config.screens.items():
        if s.type == "status":
            return name
    config.screens[STATUS_SCREEN] = StatusOptions()
    return STATUS_SCREEN


@dataclass
class Rendered:
    screen: str
    frame: Frame
    image: Image.Image       # composed 6-colour P image, logical orientation
    hash: str
    seconds: float


class RenderService:
    def __init__(self, config: Config, providers: Providers, store: StateStore):
        self.config = config
        self.providers = providers
        self.store = store
        self.tz = config_tz(config)
        self.fmt = Formatter(config.locale.language, config.locale.units, config.locale.time_format, self.tz)
        self.theme = Theme()
        self.size = logical_size(config.display.orientation)
        self.status_screen = ensure_status_screen(config)
        self._instances: dict[str, Any] = {}

    def instance(self, type_name: str) -> Any:
        if type_name not in self._instances:
            self._instances[type_name] = screen_type(type_name)()
        return self._instances[type_name]

    def options(self, screen: str) -> Any:
        return self.config.screens[screen]

    def context(self, screen: str, now: datetime, slot: str = "", view: dict[str, Any] | None = None,
                depth: int = 0) -> RenderContext:
        ctx = RenderContext(
            size=self.size, orientation=self.config.display.orientation, now=now, theme=self.theme,
            fmt=self.fmt, providers=self.providers, state=self.store.screen(screen), config=self.config,
            screen_name=screen, slot=slot, view=view if view is not None else {}, store=self.store,
        )
        if depth < 1:
            ctx.render_other = lambda other: self.frame(other, now, slot, None, depth + 1)
        return ctx

    def frame(self, screen: str, now: datetime, slot: str = "", view: dict[str, Any] | None = None,
              depth: int = 0) -> Frame:
        opts = self.options(screen)
        ctx = self.context(screen, now, slot, view, depth)
        return self.instance(opts.type).render(ctx, opts)

    def render(self, screen: str, now: datetime, slot: str = "", view: dict[str, Any] | None = None) -> Rendered:
        t0 = time.monotonic()
        frame = self.frame(screen, now, slot, view)
        img = compose(frame, self.size)
        h = content_hash(img, frame.volatile)
        self.store.save()
        self.providers.flush()
        return Rendered(screen, frame, img, h, time.monotonic() - t0)

    def run_action(self, screen: str, action: str, now: datetime, slot: str, view: dict[str, Any]) -> bool:
        opts = self.options(screen)
        t = screen_type(opts.type)
        a = getattr(t, "actions", {}).get(action)
        if a is None:
            return False
        ctx = self.context(screen, now, slot, view)
        changed = a.run(ctx, opts)
        self.store.save()
        return bool(changed)

    def prefetch(self, screen: str, now: datetime, slot: str) -> None:
        opts = self.options(screen)
        inst = self.instance(opts.type)
        if hasattr(inst, "prefetch"):
            try:
                inst.prefetch(self.context(screen, now, slot), opts)
                self.store.save()
            except Exception:
                log.debug("prefetch failed", exc_info=True)
