"""`gallery`: photos from GallerySources (DESIGN.md §6.2).

Picks are tied to the schedule slot: the first render of a slot advances the selection and
stores slot → image; re-rendering the same slot (restart, reload, deferred update) shows the
same image.
"""

from __future__ import annotations

import hashlib
import logging
import random
from pathlib import Path
from typing import Any, ClassVar, Literal

from PIL import Image
from pydantic import Field

from inkydash import imaging
from inkydash.models import Strict
from inkydash.providers.gallery.base import ImageRef
from inkydash.render.theme import BLACK, WHITE, new_canvas, new_overlay
from inkydash.render.widgets import Canvas
from inkydash.screens.base import Action, Frame, RenderContext, ScreenOptions
from inkydash.util import Duration

log = logging.getLogger(__name__)
MAX_SLOTS = 40


class FolderSpec(Strict):
    kind: Literal["folder"] = "folder"
    path: str
    rescan: Duration = Field(default="1h", validate_default=True)
    name: str | None = None


SourceSpec = str | FolderSpec


class GalleryOptions(ScreenOptions):
    type: Literal["gallery"] = "gallery"
    source: SourceSpec | None = None
    sources: list[SourceSpec] | None = None
    order: Literal["shuffle", "sequential", "random", "on_this_day", "favourites"] = "shuffle"
    fit: Literal["auto", "cover", "contain"] = "auto"
    saturation: float = Field(0.6, ge=0, le=1)
    enhance: bool = True
    caption: Literal["none", "filename", "date", "sidecar", "auto"] = "none"
    background: Literal["white", "black"] = "white"

    def specs(self) -> list[FolderSpec]:
        raw = (self.sources or []) + ([self.source] if self.source is not None else [])
        return [FolderSpec(path=s) if isinstance(s, str) else s for s in raw]


# --------------------------------------------------------------------- selection
def _sources(ctx: RenderContext, opts: GalleryOptions):
    return [ctx.providers.gallery(s.path, s.rescan.total_seconds(), s.name) for s in opts.specs()]


def _items(ctx: RenderContext, opts: GalleryOptions) -> dict[str, tuple[Any, ImageRef]]:
    hidden = set(ctx.state.get("hidden", []))
    out: dict[str, tuple[Any, ImageRef]] = {}
    for src in _sources(ctx, opts):
        for ref in src.list_items():
            if ref.key not in hidden:
                out[ref.key] = (src, ref)
    return out


def _local_today(ctx: RenderContext) -> tuple[int, int]:
    d = ctx.fmt.local(ctx.now)
    return d.month, d.day


def _select_next(ctx: RenderContext, opts: GalleryOptions, items: dict[str, tuple[Any, ImageRef]]) -> str | None:
    if not items:
        return None
    st = ctx.state
    keys = sorted(items)
    current = st.get("current")
    order = opts.order
    rng = random.Random()
    if order == "favourites":
        favs = [k for k in st.get("favourites", []) if k in items]
        if favs:
            pool = [k for k in favs if k != current] or favs
            return rng.choice(pool)
        order = "shuffle"
    if order == "on_this_day":
        md = _local_today(ctx)
        today = [k for k in keys if items[k][1].taken_at and (items[k][1].taken_at.month,
                                                              items[k][1].taken_at.day) == md]
        shown = set(st.get("on_this_day_shown", {}).get(f"{md[0]}-{md[1]}", []))
        fresh = [k for k in today if k not in shown]
        if fresh:
            pick = rng.choice(fresh)
            st.setdefault("on_this_day_shown", {})
            st["on_this_day_shown"] = {f"{md[0]}-{md[1]}": sorted(shown | {pick})}
            return pick
        order = "shuffle"
    if order == "sequential":
        pos = st.get("position", -1) + 1
        if pos >= len(keys):
            pos = 0
        st["position"] = pos
        return keys[pos]
    if order == "random":
        pool = [k for k in keys if k != current] or keys
        return rng.choice(pool)
    # shuffle: no repeat until everything has been shown
    queue = [k for k in st.get("queue", []) if k in items]
    if not queue:
        queue = keys[:]
        rng.shuffle(queue)
        if len(queue) > 1 and queue[0] == current:
            queue.append(queue.pop(0))
    pick = queue.pop(0)
    st["queue"] = queue
    return pick


def _remember(ctx: RenderContext, key: str, slot: str) -> None:
    st = ctx.state
    slots: dict[str, str] = st.setdefault("slots", {})
    slots[slot] = key
    while len(slots) > MAX_SLOTS:
        slots.pop(next(iter(slots)))
    if st.get("current") and st.get("current") != key:
        back = st.setdefault("back", [])
        back.append(st["current"])
        del back[:-30]
    st["current"] = key


def _advance(ctx: RenderContext, opts: GalleryOptions) -> bool:
    items = _items(ctx, opts)
    key = _select_next(ctx, opts, items)
    if key is None:
        return False
    _remember(ctx, key, ctx.slot or "manual")
    return True


def _prev(ctx: RenderContext, opts: GalleryOptions) -> bool:
    back = ctx.state.get("back", [])
    items = _items(ctx, opts)
    while back:
        key = back.pop()
        if key in items:
            ctx.state.setdefault("slots", {})[ctx.slot or "manual"] = key
            if ctx.state.get("current"):
                q = ctx.state.setdefault("queue", [])
                q.insert(0, ctx.state["current"])
            ctx.state["current"] = key
            return True
    return False


def _favourite(ctx: RenderContext, opts: GalleryOptions) -> bool:
    cur = ctx.state.get("current")
    if not cur:
        return False
    favs = ctx.state.setdefault("favourites", [])
    if cur in favs:
        favs.remove(cur)
    else:
        favs.append(cur)
    ctx.view["flash"] = "★" if cur in favs else "☆"
    return True


def _hide(ctx: RenderContext, opts: GalleryOptions) -> bool:
    cur = ctx.state.get("current")
    if cur:
        hidden = ctx.state.setdefault("hidden", [])
        if cur not in hidden:
            hidden.append(cur)
    return _advance(ctx, opts)


def _toggle_caption(ctx: RenderContext, opts: GalleryOptions) -> bool:
    ctx.state["caption_off"] = not ctx.state.get("caption_off", False)
    return True


def _hold(ctx: RenderContext, opts: GalleryOptions) -> bool:
    ctx.state["hold"] = not ctx.state.get("hold", False)
    ctx.view["flash"] = "hold" if ctx.state["hold"] else "unhold"
    return True


def _rescan(ctx: RenderContext, opts: GalleryOptions) -> bool:
    for s in _sources(ctx, opts):
        s.force_rescan()
    return True


# --------------------------------------------------------------------- rendering
def _prerender_path(ctx: RenderContext, opts: GalleryOptions, ref: ImageRef) -> Path:
    root = (ctx.store.root if ctx.store else Path("state")) / "prerender"
    h = hashlib.sha1(f"{ref.key}|{ref.version}|{ctx.size}|{opts.fit}|{opts.enhance}|{opts.background}"
                     .encode()).hexdigest()[:24]
    return root / f"{h}.png"


def photo(ctx: RenderContext, opts: GalleryOptions, src: Any, ref: ImageRef) -> Image.Image:
    """Steps 1–3 of the pipeline, cached in state/prerender/."""
    path = _prerender_path(ctx, opts, ref)
    if path.is_file():
        try:
            with Image.open(path) as im:
                return im.convert("RGB")
        except OSError:
            path.unlink(missing_ok=True)
    bg = (255, 255, 255) if opts.background == "white" else (0, 0, 0)
    with src.fetch(ref, ctx.orientation) as fp:
        img = imaging.load(fp, ctx.size)
    prepared = ref.item_id.endswith(".prepared") and img.size == ctx.size
    if not prepared:
        mode = imaging.choose_fit(img.size, ctx.size, opts.fit)
        img = imaging.cover(img, ctx.size) if mode == "cover" else imaging.contain(img, ctx.size, bg)
        if opts.enhance:
            img = imaging.enhance(img)
    elif img.size != ctx.size:
        img = imaging.cover(img, ctx.size)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    _prune(path.parent)
    return img


def _prune(folder: Path, keep: int = 60) -> None:
    files = sorted(folder.glob("*.png"), key=lambda p: p.stat().st_mtime)
    for p in files[:-keep]:
        p.unlink(missing_ok=True)


def _caption_text(ctx: RenderContext, opts: GalleryOptions, src: Any, ref: ImageRef) -> str | None:
    mode = opts.caption
    if mode == "none" or ctx.state.get("caption_off"):
        return None
    if mode in ("sidecar", "auto"):
        cap = src.caption(ref)
        if cap or mode == "sidecar":
            return cap
    if mode == "date" or (mode == "auto" and ref.taken_at):
        if ref.taken_at:
            return ctx.fmt.date_long(ref.taken_at.date())
        return None
    return Path(ref.name).stem


class Gallery:
    name: ClassVar[str] = "gallery"
    Options: ClassVar[type[ScreenOptions]] = GalleryOptions
    actions: ClassVar[dict[str, Action]] = {
        "next_image": Action("next_image", _advance, "next"),
        "prev_image": Action("prev_image", _prev, "back"),
        "favourite": Action("favourite", _favourite, "★"),
        "hide_image": Action("hide_image", _hide, "hide"),
        "toggle_caption": Action("toggle_caption", _toggle_caption, "caption"),
        "hold_image": Action("hold_image", _hold, "hold"),
        "rescan": Action("rescan", _rescan, "rescan"),
    }

    @staticmethod
    def check_config(opts: GalleryOptions, config) -> str | None:
        if not opts.specs():
            return "needs 'source' or 'sources'"
        return None

    def current_ref(self, ctx: RenderContext, opts: GalleryOptions) -> tuple[Any, ImageRef] | None:
        items = _items(ctx, opts)
        if not items:
            return None
        slot = ctx.slot or "manual"
        key = ctx.state.get("slots", {}).get(slot)
        if ctx.state.get("hold") and ctx.state.get("current") in items:
            key = ctx.state["current"]
        if key not in items:
            key = _select_next(ctx, opts, items)
            assert key is not None
            _remember(ctx, key, slot)
        return items[key]

    def render(self, ctx: RenderContext, opts: GalleryOptions) -> Frame:
        found = self.current_ref(ctx, opts)
        if found is None:
            img = new_canvas(ctx.size)
            c = Canvas(img, ctx.theme)
            paths = ", ".join(s.path for s in opts.specs())
            c.placeholder(ctx.t("gallery.empty"), ctx.t("gallery.empty_hint", path=paths))
            return Frame(img)
        src, ref = found
        try:
            img = photo(ctx, opts, src, ref)
        except Exception as e:
            log.warning("can't show %s: %s", ref.key, e)
            ctx.state.setdefault("slots", {}).pop(ctx.slot or "manual", None)
            hidden = ctx.state.setdefault("broken", [])
            hidden.append(ref.key)
            canvas = new_canvas(ctx.size)
            Canvas(canvas, ctx.theme).placeholder(ctx.t("gallery.broken"), ref.name)
            return Frame(canvas)

        overlay = None
        text = _caption_text(ctx, opts, src, ref)
        flash = ctx.view.pop("flash", None)
        if text or flash:
            overlay = new_overlay(ctx.size)
            c = Canvas(overlay, ctx.theme)
            if text:
                size = 17
                tw = min(c.width(text, "bold", size), ctx.size[0] - 40)
                h = 30
                box = (0, ctx.size[1] - h, tw + 24, ctx.size[1])
                c.rect(box, fill=WHITE)
                c.text_fit((12, ctx.size[1] - h / 2), text, "bold", ctx.size[0] - 40, size, 11, BLACK, anchor="lm")
            if flash in ("★", "☆"):
                c.d.ellipse((ctx.size[0] - 56, 10, ctx.size[0] - 10, 56), fill=WHITE, outline=BLACK, width=2)
                _star(c, ctx.size[0] - 33, 33, 16, filled=flash == "★")
        return Frame(img, overlay=overlay, saturation=opts.saturation,
                     meta={"image": ref.key, "taken_at": ref.taken_at.isoformat() if ref.taken_at else None})

    def prefetch(self, ctx: RenderContext, opts: GalleryOptions) -> None:
        """Pre-render the next shuffle pick while idle."""
        queue = ctx.state.get("queue") or []
        if not queue:
            return
        items = _items(ctx, opts)
        if queue[0] in items:
            src, ref = items[queue[0]]
            if not _prerender_path(ctx, opts, ref).is_file():
                try:
                    photo(ctx, opts, src, ref)
                except Exception as e:
                    log.debug("prefetch of %s failed: %s", ref.key, e)


def _star(c: Canvas, cx: float, cy: float, r: float, filled: bool) -> None:
    import math

    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((cx + math.cos(a) * rr, cy + math.sin(a) * rr))
    from inkydash.render.theme import YELLOW

    c.d.polygon(pts, fill=YELLOW if filled else WHITE, outline=BLACK, width=2)
