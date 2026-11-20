"""The contract between the app and screen types (DESIGN.md §6)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

from PIL import Image
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from inkydash.config import Config
    from inkydash.i18n import Formatter
    from inkydash.providers.registry import Providers
    from inkydash.render.theme import Theme
    from inkydash.state import StateStore

Box = tuple[int, int, int, int]


@dataclass
class Frame:
    """What a screen renders.

    - ``image`` in mode ``P`` with the theme palette is shown as is (never dithered).
    - ``image`` in ``RGB`` is a photo: the display layer dithers it at ``saturation``.
    - ``overlay`` (mode ``P``, index ``TRANSPARENT`` = see-through) is pasted after dithering,
      so captions and legends stay sharp on top of photos.
    - ``volatile`` regions are left out of the content hash (the "updated 07:00" footer).
    """

    image: Image.Image
    overlay: Image.Image | None = None
    saturation: float = 0.5
    volatile: Sequence[Box] = ()
    meta: dict[str, Any] = field(default_factory=dict)


class ScreenOptions(BaseModel):
    """Base for each screen type's options. Subclasses set ``type: Literal["<name>"]``."""

    model_config = ConfigDict(extra="forbid")


@dataclass
class RenderContext:
    size: tuple[int, int]
    orientation: str
    now: datetime
    theme: Theme
    fmt: Formatter
    providers: Providers
    state: dict[str, Any]          # persistent per-screen dict (saved by the app when it changes)
    config: Config
    screen_name: str
    slot: str = ""                 # schedule slot id, used by the gallery to keep picks stable
    view: dict[str, Any] = field(default_factory=dict)   # button-driven view state (route, view, …)
    store: StateStore | None = None
    render_other: Callable[[str], Frame] | None = None   # render another screen by name (quiet)

    @property
    def landscape(self) -> bool:
        return self.orientation == "landscape"

    def t(self, key: str, **kw: Any) -> str:
        return self.fmt.t(key, **kw)


@dataclass(frozen=True)
class Action:
    """A button action a screen type offers. ``run(ctx, opts)`` mutates ctx.view / ctx.state
    and returns True when a re-render is needed."""

    name: str
    run: Callable[[RenderContext, Any], bool]
    label: str = ""


class ScreenType(Protocol):
    name: ClassVar[str]
    Options: ClassVar[type[ScreenOptions]]
    actions: ClassVar[dict[str, Action]]

    def render(self, ctx: RenderContext, opts: Any) -> Frame: ...
