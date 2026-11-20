"""Button mapping: defaults, lookup order and validation (DESIGN.md §6.5)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from inkydash.config import Config

GLOBAL_ACTIONS = {
    "next_screen", "prev_screen", "refresh", "resume_schedule", "status", "pause_toggle", "clean",
    "shutdown", "reboot", "led_toggle", "none",
}
DESTRUCTIVE = {"shutdown", "reboot"}

DEFAULT_MAP: dict[str, dict[str, str]] = {
    "default": {"A": "next_screen", "B": "refresh", "C": "prev_screen", "D": "status",
                "A_long": "resume_schedule", "B_long": "status", "C_long": "pause_toggle", "D_long": "shutdown"},
    "gallery": {"B": "next_image", "C": "prev_image", "D": "favourite"},
    "weather": {"B": "cycle_view", "C": "cycle_location", "D": "refresh"},
    "navigation": {"B": "next_route", "C": "reverse_route", "D": "next_destination"},
    "status": {"B": "rescan", "C": "clean"},
}


GLOBAL_LABELS = {
    "next_screen": "next", "prev_screen": "back", "refresh": "refresh", "resume_schedule": "resume",
    "status": "status", "pause_toggle": "pause", "clean": "clean", "shutdown": "off", "reboot": "reboot",
    "led_toggle": "LED", "none": "",
}


def label(config: Config, screen: str, action: str) -> str:
    if action.startswith("goto:"):
        return action[5:][:8]
    if action in GLOBAL_LABELS:
        return GLOBAL_LABELS[action]
    from inkydash.registry import screen_types

    a = getattr(screen_types()[config.screen_type(screen)], "actions", {}).get(action)
    return (a.label or action) if a else action


def merged_map(config: Config) -> dict[str, dict[str, str]]:
    out = {k: dict(v) for k, v in DEFAULT_MAP.items()}
    for ctx, m in config.buttons.map.items():
        out.setdefault(ctx, {}).update(m)
    return out


def resolve(config: Config, screen: str, button: str, long: bool) -> str:
    """Lookup order: screen name → screen type → default. Long presses are global."""
    key = f"{button}_long" if long else button
    m = merged_map(config)
    if not long:
        for ctx in (screen, config.screen_type(screen)):
            if key in m.get(ctx, {}):
                return m[ctx][key]
    elif key in m.get(screen, {}):
        return m[screen][key]
    return m["default"].get(key, "none")


def _type_actions(type_name: str) -> set[str]:
    from inkydash.registry import screen_types

    return set(getattr(screen_types()[type_name], "actions", {}) or {})


def validate_actions(config: Config) -> None:
    from inkydash.registry import screen_types

    types = screen_types()
    for ctx, m in config.buttons.map.items():
        if ctx == "default":
            allowed = set(GLOBAL_ACTIONS)
        elif ctx in config.screens:
            allowed = GLOBAL_ACTIONS | _type_actions(config.screens[ctx].type)
        elif ctx in types:
            allowed = GLOBAL_ACTIONS | _type_actions(ctx)
        else:
            continue  # reported by the config validator
        for key, action in m.items():
            if action.startswith("goto:"):
                if action[5:] not in config.screens:
                    raise ValueError(f"buttons.map.{ctx}.{key}: unknown screen in {action!r}")
                continue
            if action not in allowed:
                raise ValueError(f"buttons.map.{ctx}.{key}: {action!r} isn't available there "
                                 f"(choose from {sorted(allowed)})")
