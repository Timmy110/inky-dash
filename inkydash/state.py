"""Small persistent state in state/state.json. Written only when something changed, and
atomically (temp file + rename), to spare the SD card."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def state_dir() -> Path:
    return Path(os.environ.get("INKYDASH_STATE", "state"))


def atomic_write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(tmp, mode, **({} if isinstance(data, bytes) else {"encoding": "utf-8"})) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class StateStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else state_dir()
        self.path = self.root / "state.json"
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self._saved = ""
        try:
            self._saved = self.path.read_text(encoding="utf-8")
            self._data = json.loads(self._saved)
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            log.warning("state file unreadable, starting fresh", exc_info=True)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    def section(self, key: str) -> dict[str, Any]:
        """A mutable sub-dict (created on first use); call save() after changing it."""
        with self._lock:
            return self._data.setdefault(key, {})

    def screen(self, name: str) -> dict[str, Any]:
        return self.section("screens").setdefault(name, {})

    def save(self) -> bool:
        with self._lock:
            text = json.dumps(self._data, indent=1, sort_keys=True, default=str)
            if text == self._saved:
                return False
            atomic_write(self.path, text)
            self._saved = text
            return True
