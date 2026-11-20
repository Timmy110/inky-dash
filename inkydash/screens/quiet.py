"""`quiet`: clean the panel, show one frame of another screen, then freeze (DESIGN.md §6.4).

The scheduler gives quiet screens no refreshes; the app runs the clean cycle on the first
render of each slot, before this frame is shown.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from inkydash.render.theme import new_canvas
from inkydash.render.widgets import Canvas
from inkydash.screens.base import Action, Frame, RenderContext, ScreenOptions


class QuietOptions(ScreenOptions):
    type: Literal["quiet"] = "quiet"
    show: str | None = None                                  # a screen name; None = blank white panel
    clean: bool | Literal["before"] = "before"
    clean_cycles: int = Field(1, ge=1, le=5)

    @property
    def cleans(self) -> bool:
        return self.clean is not False


class Quiet:
    name: ClassVar[str] = "quiet"
    Options: ClassVar[type[ScreenOptions]] = QuietOptions
    actions: ClassVar[dict[str, Action]] = {}

    @staticmethod
    def check_config(opts: QuietOptions, config) -> str | None:
        if opts.show is not None:
            if opts.show not in config.screens:
                return f"show: unknown screen {opts.show!r}"
            if config.screens[opts.show].type == "quiet":
                return "show: can't be another quiet screen"
        return None

    def render(self, ctx: RenderContext, opts: QuietOptions) -> Frame:
        if opts.show and ctx.render_other:
            frame = ctx.render_other(opts.show)
            frame.volatile = []  # nothing refreshes during quiet anyway
            return frame
        img = new_canvas(ctx.size)
        Canvas(img, ctx.theme)
        return Frame(img)
