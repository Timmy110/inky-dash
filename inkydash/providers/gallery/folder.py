"""Local folder gallery source. The index is cached in state/ and rescanned at most every
`rescan`, so the SD card isn't walked on every render."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from inkydash.providers.gallery.base import ImageRef
from inkydash.state import atomic_write

log = logging.getLogger(__name__)

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
PREPARED_SUFFIXES = ("-landscape", "-portrait")


class FolderSource:
    def __init__(self, path: str | Path, index_dir: Path, rescan_s: float = 3600, name: str | None = None):
        self.path = Path(path).expanduser()
        self.id = f"folder:{name or self.path.name}"
        self.rescan_s = rescan_s
        self.index_path = index_dir / f"gallery-index-{self.id.replace(':', '_').replace('/', '_')}.json"
        self._items: list[ImageRef] | None = None
        self._scanned = 0.0

    def _scan(self) -> list[ImageRef]:
        items: list[ImageRef] = []
        if not self.path.is_dir():
            log.warning("gallery folder %s doesn't exist", self.path)
            return items
        seen_prepared: set[str] = set()
        for p in sorted(self.path.rglob("*")):
            if p.suffix.lower() not in EXTENSIONS or not p.is_file() or "_preview" in p.parts:
                continue
            rel = p.relative_to(self.path).as_posix()
            stem = p.stem
            # prepared pairs "x-landscape.png" + "x-portrait.png" are one item: "x"
            for suf in PREPARED_SUFFIXES:
                if stem.endswith(suf):
                    base = rel[: -len(p.name)] + stem[: -len(suf)]
                    if base in seen_prepared:
                        stem = ""
                    else:
                        seen_prepared.add(base)
                        rel = base + ".prepared"
                    break
            if not stem:
                continue
            st = p.stat()
            items.append(ImageRef(self.id, rel, str(int(st.st_mtime)), _taken_at(p),
                                  p.name if not rel.endswith(".prepared") else Path(rel).stem))
        return items

    def list_items(self) -> list[ImageRef]:
        now = time.time()
        if self._items is not None and now - self._scanned < self.rescan_s:
            return self._items
        if self._items is None:
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                if now - data["t"] < self.rescan_s and data.get("path") == str(self.path):
                    self._items = [ImageRef.from_json(d) for d in data["items"]]
                    self._scanned = data["t"]
                    return self._items
            except (OSError, ValueError, KeyError):
                pass
        self._items = self._scan()
        self._scanned = now
        try:
            atomic_write(self.index_path, json.dumps({"t": now, "path": str(self.path),
                                                      "items": [r.to_json() for r in self._items]}))
        except OSError:
            log.warning("couldn't write gallery index", exc_info=True)
        return self._items

    def force_rescan(self) -> None:
        self._items = None
        self.index_path.unlink(missing_ok=True)

    def file_for(self, ref: ImageRef, orientation: str = "landscape") -> Path:
        if ref.item_id.endswith(".prepared"):
            base = ref.item_id[: -len(".prepared")]
            for o in (orientation, "landscape", "portrait"):
                for ext in (".png", ".jpg", ".jpeg", ".webp"):
                    p = self.path / f"{base}-{o}{ext}"
                    if p.is_file():
                        return p
        return self.path / ref.item_id

    def fetch(self, ref: ImageRef, orientation: str = "landscape") -> BinaryIO:
        return open(self.file_for(ref, orientation), "rb")

    def caption(self, ref: ImageRef) -> str | None:
        f = self.file_for(ref)
        for sidecar in (f.with_suffix(".json"), self.path / (ref.item_id.removesuffix(".prepared") + ".json"),
                        f.with_suffix(".txt")):
            if sidecar.is_file():
                try:
                    if sidecar.suffix == ".json":
                        return json.loads(sidecar.read_text(encoding="utf-8")).get("caption")
                    return sidecar.read_text(encoding="utf-8").strip() or None
                except (OSError, ValueError):
                    return None
        return None


def _taken_at(p: Path) -> datetime | None:
    """EXIF DateTimeOriginal for JPEGs (cheap: reads only the header)."""
    if p.suffix.lower() not in (".jpg", ".jpeg"):
        return None
    try:
        from PIL import Image

        with Image.open(p) as im:
            exif = im.getexif()
            raw = exif.get_ifd(0x8769).get(0x9003) or exif.get(0x0132)
        if raw:
            return datetime.strptime(str(raw).strip("\x00 "), "%Y:%m:%d %H:%M:%S")
    except Exception:
        return None
    return None
