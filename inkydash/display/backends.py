"""Display backends: the real panel (InkyBackend) and PNG files (FileBackend)."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Protocol

from PIL import Image

from inkydash.display.pipeline import PANEL_SIZE, preview, solid
from inkydash.render.theme import BLACK, RED, WHITE

log = logging.getLogger(__name__)


class DisplayBackend(Protocol):
    name: str

    def show(self, logical: Image.Image, panel: Image.Image) -> None: ...
    def clean(self, cycles: int) -> None: ...
    def close(self) -> None: ...


class FileBackend:
    """Writes out/latest.png (the exact palette pixels, logical orientation) and
    out/latest-preview.png (coloured like the real panel)."""

    name = "file"

    def __init__(self, out_dir: str | Path = "out", history: bool = False):
        self.out = Path(out_dir)
        self.history = history
        self.shown = 0

    def show(self, logical: Image.Image, panel: Image.Image) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        logical.save(self.out / "latest.png")
        preview(logical).save(self.out / "latest-preview.png")
        if self.history:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            logical.save(self.out / f"{stamp}-{self.shown:03d}.png")
        self.shown += 1
        log.info("file backend: wrote %s", self.out / "latest.png")

    def clean(self, cycles: int) -> None:
        log.info("file backend: clean cycle ×%d (no-op)", cycles)

    def close(self) -> None:
        pass


SPEEDS = {"normal": "NORMAL", "medium": "MEDIUM", "fast": "FAST"}


class InkyBackend:
    """The Spectra 6 panel via Pimoroni's driver. Only importable on the Pi."""

    name = "inky"

    def __init__(self, refresh_speed: str = "normal", border: str = "white"):
        try:
            from inky.inky_e673 import Inky  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError("the 'inky' package is missing: pip install -e '.[inky]'") from e
        if refresh_speed == "normal":
            try:
                from inky.auto import auto  # type: ignore[import-not-found]

                self.inky = auto()
            except Exception:
                log.warning("inky.auto() failed, falling back to InkyE673 directly", exc_info=True)
                self.inky = Inky()
        else:
            self.inky = Inky(speed=getattr(Inky, SPEEDS[refresh_speed]))
        if tuple(self.inky.resolution) != PANEL_SIZE:
            raise RuntimeError(f"unexpected panel resolution {self.inky.resolution}, expected {PANEL_SIZE}")
        self.border = self.inky.WHITE if border == "white" else self.inky.BLACK

    def show(self, logical: Image.Image, panel: Image.Image) -> None:
        t0 = time.monotonic()
        self.inky.set_border(self.border)
        # P image with exactly 6 palette colours → the driver skips dithering.
        self.inky.set_image(panel)
        self.inky.show()
        log.info("panel refreshed in %.1fs", time.monotonic() - t0)

    def clean(self, cycles: int) -> None:
        """Full-screen red / black / white flushes against ghosting (3 refreshes per cycle)."""
        for i in range(cycles):
            for color, border in ((RED, self.inky.RED), (BLACK, self.inky.BLACK), (WHITE, self.inky.WHITE)):
                log.info("clean cycle %d/%d: %s", i + 1, cycles, color)
                img = solid(color)
                self.inky.set_border(border)
                self.inky.set_image(img)
                self.inky.show()
                time.sleep(1)
        self.inky.set_border(self.border)

    def close(self) -> None:
        pass


def make_backend(kind: str, refresh_speed: str = "normal", border: str = "white",
                 out_dir: str | Path = "out") -> DisplayBackend:
    if kind == "inky":
        return InkyBackend(refresh_speed, border)
    if kind == "file":
        return FileBackend(out_dir)
    if kind == "auto":
        try:
            return InkyBackend(refresh_speed, border)
        except Exception as e:
            log.info("no panel (%s): using the file backend", e)
            return FileBackend(out_dir)
    raise ValueError(f"unknown display driver {kind!r}")
