"""Gallery source abstraction (DESIGN.md §6.2)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class ImageRef:
    source_id: str          # "folder:photos"
    item_id: str            # stable within the source (relative path, remote id, …)
    version: str            # mtime / etag / checksum → cache invalidation
    taken_at: datetime | None
    name: str

    @property
    def key(self) -> str:
        return f"{self.source_id}:{self.item_id}"

    def to_json(self) -> dict:
        d = asdict(self)
        d["taken_at"] = self.taken_at.isoformat() if self.taken_at else None
        return d

    @classmethod
    def from_json(cls, d: dict) -> ImageRef:
        d = dict(d)
        d["taken_at"] = datetime.fromisoformat(d["taken_at"]) if d.get("taken_at") else None
        return cls(**d)


class GallerySource(Protocol):
    id: str

    def list_items(self) -> Iterable[ImageRef]: ...
    def fetch(self, ref: ImageRef) -> BinaryIO: ...
    def caption(self, ref: ImageRef) -> str | None: ...
