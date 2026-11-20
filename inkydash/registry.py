"""Screen type registry. Adding a screen type = adding a module and listing it here."""

from __future__ import annotations

from functools import lru_cache
from typing import Any


@lru_cache(maxsize=1)
def screen_types() -> dict[str, Any]:
    from inkydash.screens.gallery import Gallery
    from inkydash.screens.navigation import Navigation
    from inkydash.screens.quiet import Quiet
    from inkydash.screens.status import Status
    from inkydash.screens.weather import Weather

    return {t.name: t for t in (Weather, Gallery, Navigation, Status, Quiet)}


def screen_type(name: str) -> Any:
    return screen_types()[name]
