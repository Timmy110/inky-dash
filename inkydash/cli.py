"""Command line interface (DESIGN.md §9)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(levelname)s %(name)s: %(message)s" if os.environ.get("JOURNAL_STREAM") else \
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
            sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def _load(args):
    from inkydash.config import ConfigError, load_config

    try:
        return load_config(args.config)
    except ConfigError as e:
        print(f"✗ {e.path}: invalid config")
        for err in e.errors:
            print(f"  - {err}")
        raise SystemExit(2) from None
    except FileNotFoundError as e:
        print(f"✗ {e}")
        raise SystemExit(2) from None


def _parse_now(s: str | None, tz) -> datetime:
    if not s:
        return datetime.now(UTC)
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=tz)
    return d.astimezone(UTC)


def _services(config):
    from inkydash.providers.registry import Providers
    from inkydash.render_service import RenderService
    from inkydash.state import StateStore, state_dir

    store = StateStore(state_dir())
    providers = Providers(config, store.root)
    return RenderService(config, providers, store)


# ---------------------------------------------------------------------- commands
def cmd_run(args) -> int:
    from inkydash.app import run

    return run(args.config, args.driver, args.out)


def cmd_render(args) -> int:
    from inkydash.display.pipeline import preview, to_panel
    from inkydash.render_service import ensure_status_screen

    config, _ = _load(args)
    if args.orientation:
        config.display.orientation = args.orientation
    if args.language:
        config.locale.language = args.language
    status = ensure_status_screen(config)
    rs = _services(config)
    now = _parse_now(args.now, rs.tz)
    screens = list(config.screens) if args.all else [status if args.smoke else args.screen]
    if not args.all and not args.smoke and not args.screen:
        print("✗ name a screen, or use --all")
        return 2
    view: dict = {}
    for kv in args.view or []:
        k, _, v = kv.partition("=")
        view[k] = int(v) if v.lstrip("-").isdigit() else (v == "true" if v in ("true", "false") else v)
    rc = 0
    for screen in screens:
        if screen not in config.screens:
            print(f"✗ unknown screen {screen!r} (screens: {', '.join(config.screens)})")
            return 2
        t0 = time.monotonic()
        try:
            r = rs.render(screen, now, args.slot or "cli", dict(view))
        except Exception as e:
            logging.getLogger("render").exception("render of %s failed", screen)
            print(f"✗ {screen}: {e}")
            rc = 1
            continue
        out = Path(args.out) if args.out and not args.all else Path(args.out or "out") / f"{screen}.png"
        if args.all:
            out = Path(args.out or "out") / f"{screen}-{config.display.orientation}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        r.image.save(out)
        msg = f"✓ {screen} → {out} ({time.monotonic() - t0:.2f}s, hash {r.hash})"
        if args.preview:
            pv = out.with_name(out.stem + "-preview.png")
            preview(r.image).save(pv)
            msg += f", preview {pv.name}"
        if args.panel:
            to_panel(r.image, config.display.orientation, config.display.flip).save(
                out.with_name(out.stem + "-panel.png"))
        if args.profile:
            from inkydash import sysinfo

            rss = sysinfo.rss_mb()
            msg += f", RSS {rss} MB" if rss else ""
        print(msg)
    return rc


def cmd_schedule(args) -> int:
    from inkydash.render_service import config_tz
    from inkydash.scheduler import Scheduler

    config, _ = _load(args)
    tz = config_tz(config)
    start_day = date.fromisoformat(args.start) if args.start else datetime.now(tz).date()
    start = datetime.combine(start_day, datetime.min.time(), tzinfo=tz)
    end = start + timedelta(days=args.days)
    s = Scheduler(config, tz)
    last_day = None
    for t, d in s.timeline(start, end):
        local = t.astimezone(tz)
        if local.date() != last_day:
            last_day = local.date()
            kinds = s.cal.kinds(last_day)
            print(f"\n{local:%a %Y-%m-%d}" + (f"  ({', '.join(sorted(kinds))})" if kinds else ""))
        what = d.screen + (f"  refresh #{d.refresh_index}" if d.refresh_index else "")
        if d.step and d.refresh_index == 0:
            what += f"  rotation step {d.step}"
        print(f"  {local:%H:%M}  {what:<32} {d.reason}")
    return 0


def cmd_validate(args) -> int:
    from inkydash.config import json_schema

    config, path = _load(args)
    print(f"✓ {path} is valid: {len(config.screens)} screens, {len(config.schedule)} rules")
    out = Path(args.schema_out) if args.schema_out else Path(__file__).resolve().parents[1] / "schema" / \
        "config.schema.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(json_schema(), indent=2) + "\n"
        if not out.exists() or out.read_text(encoding="utf-8") != text:
            out.write_text(text, encoding="utf-8")
            print(f"✓ schema written to {out}")
    except OSError as e:
        print(f"⚠ couldn't write the schema: {e}")
    return 0


def cmd_clean(args) -> int:
    from inkydash.display.backends import make_backend

    config, _ = _load(args)
    b = make_backend(args.driver, config.display.refresh_speed, config.display.border)
    print(f"cleaning: {args.cycles} cycle(s), {3 * args.cycles} full refreshes on the {b.name} backend")
    b.clean(args.cycles)
    return 0


def cmd_testcard(args) -> int:
    from inkydash.display.backends import make_backend
    from inkydash.display.pipeline import preview, to_panel
    from inkydash.render import testcard

    tiles = None
    if args.location:
        config, _ = _load(args)
        from inkydash.providers.registry import Providers
        from inkydash.providers.tiles import STYLES, THRESHOLDS, TileSource
        from inkydash.render.map import world_px
        from inkydash.state import state_dir

        p = Providers(config, state_dir())
        lat, lon = p.latlon(args.location)
        z = 14
        x, y = world_px(lat, lon, z)
        tiles = {}
        for style in STYLES:
            if style.startswith("stadia") and not config.providers.tiles.stadia_key:
                tiles[style] = None
                continue
            cfg = config.providers.tiles.model_copy(update={"style": style, "threshold": None})
            ts = TileSource(p.http, cfg, state_dir() / "tiles")
            tiles[style] = ts.tile(z, int(x // 256), int(y // 256))
        thresholds = dict(THRESHOLDS)
    else:
        thresholds = None
    img = testcard.render(tiles_by_style=tiles, thresholds=thresholds)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    preview(img).save(out.with_name(out.stem + "-preview.png"))
    print(f"✓ testcard → {out}")
    if args.driver != "file":
        b = make_backend(args.driver)
        print(f"showing on the {b.name} backend…")
        b.show(img, to_panel(img, "landscape", False))
    return 0


def cmd_doctor(args) -> int:
    from inkydash import __version__, sysinfo
    from inkydash.config import ConfigError, load_config
    from inkydash.state import state_dir

    ok = True

    def line(good: bool | None, text: str) -> None:
        nonlocal ok
        mark = "✓" if good else ("·" if good is None else "✗")
        if good is False:
            ok = False
        print(f" {mark} {text}")

    print(f"inky-dash {__version__} · Python {sys.version.split()[0]} · {sys.platform}")
    line(sys.version_info >= (3, 11), "Python ≥ 3.11")
    config = None
    try:
        config, path = load_config(args.config)
        line(True, f"config {path}")
    except (ConfigError, FileNotFoundError) as e:
        line(False, f"config: {e}")
    on_pi = sysinfo.is_pi()
    line(on_pi or None, "Raspberry Pi" if on_pi else "not a Raspberry Pi (file backend only)")
    if on_pi:
        line(Path("/dev/spidev0.0").exists(), "SPI enabled (/dev/spidev0.0)")
        line(Path("/dev/i2c-1").exists(), "I2C enabled (/dev/i2c-1)")
        try:
            from inky.auto import auto  # type: ignore[import-not-found]

            d = auto()
            line(True, f"display: {type(d).__module__} {d.resolution}")
        except Exception as e:
            line(False, f"display not detected: {e}")
        synced = sysinfo.clock_synced()
        line(synced, "clock synced (NTP)")
    sd = state_dir()
    try:
        sd.mkdir(parents=True, exist_ok=True)
        (sd / ".doctor").write_text("ok")
        (sd / ".doctor").unlink()
        line(True, f"state dir writable: {sd.resolve()}")
    except OSError as e:
        line(False, f"state dir {sd}: {e}")
    df = sysinfo.disk_free(sd if sd.exists() else ".")
    if df:
        line(df[0] > 200 * 2**20, f"disk free: {sysinfo.human_bytes(df[0])}")
    from inkydash.render.theme import FONT_DIR, FONT_FILES

    line(all((FONT_DIR / f).is_file() for f in FONT_FILES.values()), "fonts bundled")
    if config:
        from inkydash.providers.cache import Usage

        usage = Usage(sd / "usage.json")
        pc = config.providers
        print(" providers (calls today / this month):")
        for name in ("tomtom", "here", "mapbox", "google", "ors"):
            key = getattr(pc, name).key
            used = [m for m, chain in pc.routing.items() if name in chain]
            if used or key:
                state = "key set" if key else "no key: skipped"
                print(f"   {name:<9} {state:<16} {usage.today(name):>4} / {usage.month(name):>5}  "
                      f"(limit {getattr(pc, name).max_daily_calls}/day)")
        for name in ("osrm", "open-meteo", "meteoalarm", "nominatim", "stadia", "osm-tiles"):
            if usage.month(name):
                print(f"   {name:<9} {'no key needed':<16} {usage.today(name):>4} / {usage.month(name):>5}")
        tiles = pc.tiles
        style = tiles.style if tiles.style != "auto" else ("stadia-toner" if tiles.stadia_key else "osm")
        line(True, f"map tiles: {style}")
        live = [n for n in pc.routing.get("car", []) if n != "osrm" and n != "ors" and getattr(pc, n).key]
        line(bool(live) or None, f"live traffic providers with a key: {', '.join(live) or 'none (OSRM only)'}")
    print("all good" if ok else "problems found")
    return 0 if ok else 1


def cmd_update(args) -> int:
    from inkydash.update import update

    return update(args.to, args.config, restart=not args.no_restart)


def cmd_prepare(args) -> int:
    from inkydash.prepare import prepare_folder

    orientations = ["landscape", "portrait"] if args.orientation == "both" else [args.orientation]
    written = prepare_folder(Path(args.folder), Path(args.out), orientations, args.preview, args.saturation)
    print(f"✓ {len(written)} images → {args.out}" + (f" (previews in {Path(args.out) / '_preview'})"
                                                    if args.preview else ""))
    return 0


# ---------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="inky-dash", description="Scheduled e-paper dashboard")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def cfg(sp):
        sp.add_argument("--config", help="config file (default: $INKYDASH_CONFIG, ./config.json, "
                                         "/etc/inky-dash/config.json)")
        return sp

    sp = cfg(sub.add_parser("run", help="run the service"))
    sp.add_argument("--driver", choices=["auto", "inky", "file"], default="auto")
    sp.add_argument("--out", default="out", help="output folder for the file backend")
    sp.set_defaults(fn=cmd_run)

    sp = cfg(sub.add_parser("render", help="render a screen to PNG"))
    sp.add_argument("screen", nargs="?")
    sp.add_argument("--all", action="store_true", help="render every screen")
    sp.add_argument("--smoke", action="store_true", help="render the status screen (update check)")
    sp.add_argument("--now", help="pretend time, e.g. 2026-11-16T07:20 (config time zone)")
    sp.add_argument("--out", help="output file (or folder with --all)")
    sp.add_argument("--preview", action="store_true", help="also write a panel-coloured preview")
    sp.add_argument("--panel", action="store_true", help="also write the rotated panel image")
    sp.add_argument("--orientation", choices=["landscape", "portrait"])
    sp.add_argument("--language")
    sp.add_argument("--slot", help="schedule slot id (gallery picks)")
    sp.add_argument("--view", action="append", metavar="KEY=VALUE", help="view state, e.g. view=hourly")
    sp.add_argument("--profile", action="store_true", help="print peak memory")
    sp.set_defaults(fn=cmd_render)

    sp = cfg(sub.add_parser("schedule", help="print what shows when"))
    sp.add_argument("--from", dest="start", help="first day (YYYY-MM-DD), default today")
    sp.add_argument("--days", type=int, default=7)
    sp.set_defaults(fn=cmd_schedule)

    sp = cfg(sub.add_parser("validate", help="validate the config and write the JSON schema"))
    sp.add_argument("--schema-out")
    sp.set_defaults(fn=cmd_validate)

    sp = cfg(sub.add_parser("clean", help="anti-ghosting cycle"))
    sp.add_argument("--cycles", type=int, default=1)
    sp.add_argument("--driver", choices=["auto", "inky", "file"], default="auto")
    sp.set_defaults(fn=cmd_clean)

    sp = cfg(sub.add_parser("testcard", help="hardware test card (palette, fonts, lines, map thresholds)"))
    sp.add_argument("--out", default="out/testcard.png")
    sp.add_argument("--driver", choices=["auto", "inky", "file"], default="file")
    sp.add_argument("--location", help="add real map tiles around this location key")
    sp.set_defaults(fn=cmd_testcard)

    sp = cfg(sub.add_parser("doctor", help="check the installation"))
    sp.set_defaults(fn=cmd_doctor)

    sp = cfg(sub.add_parser("update", help="update to the latest release (with rollback)"))
    sp.add_argument("--to", help="tag, 'latest' (default) or 'main'")
    sp.add_argument("--no-restart", action="store_true")
    sp.set_defaults(fn=cmd_update)

    sp = sub.add_parser("prepare", help="prepare photos on the PC")
    sp.add_argument("folder")
    sp.add_argument("--out", required=True)
    sp.add_argument("--preview", action="store_true")
    sp.add_argument("--orientation", choices=["landscape", "portrait", "both"], default="both")
    sp.add_argument("--saturation", type=float, default=0.6)
    sp.set_defaults(fn=cmd_prepare)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    return int(args.fn(args) or 0)
