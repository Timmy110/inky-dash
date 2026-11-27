"""`inky-dash prepare`: PC-side photo preparation (DESIGN.md §6.2).

Runs the same pipeline as the Pi (inkydash.imaging), plus a smart crop window (OpenCV face
detection when installed, else saliency by edge density) and palette previews.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

from inkydash import imaging
from inkydash.display.pipeline import dither, preview
from inkydash.providers.gallery.folder import EXTENSIONS

log = logging.getLogger(__name__)
SIZES = {"landscape": (800, 480), "portrait": (480, 800)}


def faces(img: Image.Image) -> list[tuple[float, float, float, float]]:
    """Face boxes as fractions (x0, y0, x1, y1). Empty if OpenCV isn't installed."""
    try:
        import cv2  # type: ignore[import-not-found]
        import numpy as np
    except ImportError:
        return []
    if not hasattr(cv2, "CascadeClassifier"):   # OpenCV 5+: no Haar cascades, fall back to saliency
        log.warning("this OpenCV has no CascadeClassifier: face detection off (install opencv-python-headless<5)")
        return []
    small = img.copy()
    small.thumbnail((900, 900))
    gray = cv2.cvtColor(np.array(small), cv2.COLOR_RGB2GRAY)
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    found = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(30, 30))
    w, h = small.size
    return [(x / w, y / h, (x + fw) / w, (y + fh) / h) for x, y, fw, fh in found]


def saliency_focus(img: Image.Image, target: tuple[int, int]) -> tuple[float, float, float, float]:
    """Crop window with the most edge detail, sliding along the axis that gets cropped."""
    small = img.copy()
    small.thumbnail((300, 300))
    edges = small.convert("L").filter(ImageFilter.FIND_EDGES)
    w, h = edges.size
    tw, th = target
    scale = max(tw / w, th / h)
    cw, ch = tw / scale, th / scale
    best, best_box = -1.0, (0.0, 0.0, 1.0, 1.0)
    steps = 12
    for i in range(steps + 1):
        x0 = (w - cw) * i / steps
        y0 = (h - ch) * i / steps
        box = (int(x0), int(y0 * 0.85), int(x0 + cw), int(y0 * 0.85 + ch))  # slight upward bias
        score = ImageStat.Stat(edges.crop(box)).mean[0]
        if score > best:
            best, best_box = score, (box[0] / w, box[1] / h, box[2] / w, box[3] / h)
    return best_box


def focus_for(img: Image.Image, target: tuple[int, int]) -> tuple[float, float, float, float] | None:
    f = faces(img)
    if f:
        return (min(b[0] for b in f), min(b[1] for b in f), max(b[2] for b in f), max(b[3] for b in f))
    return saliency_focus(img, target)


def prepare_folder(src: Path, out: Path, orientations: list[str], make_preview: bool = True,
                   saturation: float = 0.6, fit: str = "auto") -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in src.rglob("*") if p.suffix.lower() in EXTENSIONS and p.is_file())
    written: list[Path] = []
    for p in files:
        rel = p.relative_to(src).with_suffix("")
        for o in orientations:
            target = SIZES[o]
            try:
                img = imaging.load(str(p), target)
                mode = imaging.choose_fit(img.size, target, fit)
                if mode == "cover":
                    fitted = imaging.cover(img, target, focus_for(img, target))
                else:
                    fitted = imaging.contain(img, target)
                final = imaging.enhance(fitted)
            except Exception as e:
                log.warning("skipping %s: %s", p, e)
                break
            dest = out / f"{rel.as_posix().replace('/', '_')}-{o}.png"
            final.save(dest, optimize=True)
            written.append(dest)
            if make_preview:
                pv = out / "_preview"
                pv.mkdir(exist_ok=True)
                preview(dither(final, saturation)).save(pv / dest.name)
        sidecar = out / f"{rel.as_posix().replace('/', '_')}.json"
        if not sidecar.exists():
            sidecar.write_text(json.dumps({"source": p.name, "caption": None}), encoding="utf-8")
    return written
