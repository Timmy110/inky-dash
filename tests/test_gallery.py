from PIL import Image

from inkydash.config import parse_config
from inkydash.providers.registry import Providers
from inkydash.render_service import RenderService
from inkydash.screens.gallery import Gallery, _advance, _favourite, _hide, _prev
from inkydash.state import StateStore

from .conftest import NOW, config_dict


def service(tmp_path, photos, **gallery_opts) -> RenderService:
    data = config_dict(photos)
    data["screens"]["photos"].update(gallery_opts)
    config = parse_config(data)
    store = StateStore(tmp_path / "state")
    return RenderService(config, Providers(config, store.root), store)


def current(rs, slot):
    rs.render("photos", NOW, slot)
    return rs.store.screen("photos")["current"]


def test_same_slot_same_photo_even_after_restart(tmp_path, photos):
    rs = service(tmp_path, photos)
    a = current(rs, "slot-1")
    assert current(rs, "slot-1") == a
    rs2 = service(tmp_path, photos)  # new process, same state dir
    assert current(rs2, "slot-1") == a


def test_shuffle_no_repeat_until_all_shown(tmp_path, photos):
    rs = service(tmp_path, photos)
    seen = [current(rs, f"slot-{i}") for i in range(5)]
    assert len(set(seen)) == 5
    sixth = current(rs, "slot-5")
    assert sixth != seen[-1]


def test_sequential(tmp_path, photos):
    rs = service(tmp_path, photos, order="sequential")
    keys = [current(rs, f"s{i}") for i in range(6)]
    assert keys[:5] == sorted(keys[:5]) and keys[5] == keys[0]


def test_buttons_next_prev_favourite_hide(tmp_path, photos):
    rs = service(tmp_path, photos)
    first = current(rs, "s")
    ctx = rs.context("photos", NOW, "s")
    opts = rs.options("photos")
    assert _advance(ctx, opts)
    second = ctx.state["current"]
    assert second != first and ctx.state["slots"]["s"] == second
    assert _prev(ctx, opts) and ctx.state["current"] == first
    assert _favourite(ctx, opts) and first in ctx.state["favourites"]
    assert _hide(ctx, opts) and first in ctx.state["hidden"]
    for i in range(10):
        assert current(rs, f"later-{i}") != first


def test_favourites_order(tmp_path, photos):
    rs = service(tmp_path, photos, order="favourites")
    first = current(rs, "a")
    _favourite(rs.context("photos", NOW, "a"), rs.options("photos"))
    assert current(rs, "b") == first  # the only favourite


def test_on_this_day_falls_back_to_shuffle(tmp_path, photos):
    rs = service(tmp_path, photos, order="on_this_day")
    assert current(rs, "x")


def test_prerender_cache_and_prepared_images(tmp_path, photos):
    Image.new("RGB", (800, 480), (10, 120, 10)).save(photos / "beach-landscape.png")
    Image.new("RGB", (480, 800), (10, 10, 120)).save(photos / "beach-portrait.png")
    rs = service(tmp_path, photos)
    src = rs.providers.gallery(str(photos))
    items = {r.item_id: r for r in src.list_items()}
    assert "beach.prepared" in items and len(items) == 6  # the pair is one item
    assert src.file_for(items["beach.prepared"], "portrait").name == "beach-portrait.png"
    ctx = rs.context("photos", NOW, "p")
    from inkydash.screens.gallery import photo

    img = photo(ctx, rs.options("photos"), src, items["beach.prepared"])
    assert img.size == (800, 480) and img.getpixel((5, 5)) == (10, 120, 10)  # not re-enhanced
    assert list((rs.store.root / "prerender").glob("*.png"))


def test_empty_gallery_placeholder(tmp_path):
    empty = tmp_path / "nothing"
    empty.mkdir()
    rs = service(tmp_path, empty)
    r = rs.render("photos", NOW, "s")
    assert r.image.mode == "P"


def test_caption_overlay(tmp_path, photos):
    rs = service(tmp_path, photos, caption="filename")
    r = rs.render("photos", NOW, "s")
    assert r.frame.overlay is not None


def test_prefetch_prerenders_next(tmp_path, photos):
    rs = service(tmp_path, photos)
    rs.render("photos", NOW, "s")
    before = len(list((rs.store.root / "prerender").glob("*.png")))
    Gallery().prefetch(rs.context("photos", NOW, "s"), rs.options("photos"))
    assert len(list((rs.store.root / "prerender").glob("*.png"))) == before + 1
