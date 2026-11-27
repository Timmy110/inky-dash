"""Config models, JSONC loading and ${ENV} interpolation (DESIGN.md §4)."""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal, Union

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from inkydash import jsonc
from inkydash.models import DayKind, Mode, Strict, Weekday, When  # noqa: F401  (re-exported)
from inkydash.util import Duration

WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ROUTING_PROVIDERS = ("tomtom", "here", "mapbox", "google", "osrm", "ors")


# ---------------------------------------------------------------- global settings
class DisplayConfig(Strict):
    orientation: Literal["landscape", "portrait"] = "landscape"
    flip: bool = False
    refresh_speed: Literal["normal", "medium", "fast"] = "normal"
    min_refresh_interval: Duration = Field(default="3m", validate_default=True)
    border: Literal["white", "black"] = "white"


class LocaleConfig(Strict):
    language: str = "en"
    units: Literal["metric", "imperial"] = "metric"
    time_format: Literal["24h", "12h"] = "24h"


class HolidaysConfig(Strict):
    country: str | None = None
    subdiv: str | None = None


class Location(Strict):
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    address: str | None = None
    label: str | None = None

    @model_validator(mode="after")
    def _either(self) -> Location:
        has_coords = self.lat is not None and self.lon is not None
        if not has_coords and not self.address:
            raise ValueError("a location needs lat + lon, or an address")
        if (self.lat is None) != (self.lon is None):
            raise ValueError("give both lat and lon")
        return self


class Rule(Strict):
    name: str
    when: When
    screen: str | None = None
    screens: list[str] | None = None
    rotate_every: Duration | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def _target(self) -> Rule:
        if (self.screen is None) == (self.screens is None):
            raise ValueError("a rule needs exactly one of 'screen' or 'screens'")
        if self.screens is not None:
            if not self.screens:
                raise ValueError("'screens' must not be empty")
            if self.rotate_every is None:
                raise ValueError("'screens' needs 'rotate_every'")
            if self.rotate_every.total_seconds() < 60:
                raise ValueError("'rotate_every' must be at least 1m")
        elif self.rotate_every is not None:
            raise ValueError("'rotate_every' needs 'screens'")
        return self

    @property
    def targets(self) -> list[str]:
        return self.screens if self.screens is not None else [self.screen]  # type: ignore[list-item]


# ---------------------------------------------------------------- providers
class KeyedProvider(Strict):
    key: str | None = None
    max_daily_calls: int = 500
    base_url: str | None = None


class OsrmConfig(Strict):
    base_url: str = "https://routing.openstreetmap.de"
    max_daily_calls: int = 500


class TilesConfig(Strict):
    style: Literal["auto", "stadia-toner", "stadia-toner-lite", "osm", "none"] = "auto"
    stadia_key: str | None = None
    threshold: int | None = Field(None, ge=1, le=254)


class ProvidersConfig(Strict):
    routing: dict[Mode, list[str]] = {
        "car": ["tomtom", "here", "mapbox", "google", "osrm"],
        "bike": ["osrm"],
        "walk": ["osrm"],
    }
    geocoder: Literal["nominatim", "tomtom", "here", "mapbox"] = "nominatim"
    tomtom: KeyedProvider = KeyedProvider()
    here: KeyedProvider = KeyedProvider()
    mapbox: KeyedProvider = KeyedProvider()
    google: KeyedProvider = KeyedProvider(max_daily_calls=200)
    ors: KeyedProvider = KeyedProvider()
    osrm: OsrmConfig = OsrmConfig()
    tiles: TilesConfig = TilesConfig()

    @field_validator("routing")
    @classmethod
    def _known(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        for mode, chain in v.items():
            for p in chain:
                if p not in ROUTING_PROVIDERS:
                    raise ValueError(f"routing.{mode}: unknown provider {p!r} (known: {ROUTING_PROVIDERS})")
        return v


# ---------------------------------------------------------------- buttons
ButtonName = Literal["A", "B", "C", "D"]


class ButtonsConfig(Strict):
    enabled: bool = True
    pins: dict[ButtonName, int] = {"A": 5, "B": 6, "C": 16, "D": 24}
    led_pin: int | None = 13
    long_press: Duration = Field(default="1s", validate_default=True)
    shutdown_hold: Duration = Field(default="5s", validate_default=True)
    coalesce: Duration = Field(default="2s", validate_default=True)
    legend: bool = False
    map: dict[str, dict[str, str]] = {}

    @field_validator("map")
    @classmethod
    def _keys(cls, v: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        ok = {f"{b}{s}" for b in "ABCD" for s in ("", "_long")}
        for ctx, m in v.items():
            bad = set(m) - ok
            if bad:
                raise ValueError(f"buttons.map.{ctx}: unknown keys {sorted(bad)} (use A..D or A_long..D_long)")
        return v


# ---------------------------------------------------------------- top level
def _screen_union() -> Any:
    from inkydash.registry import screen_types

    models = tuple(t.Options for t in screen_types().values())
    return Annotated[Union[models], Field(discriminator="type")]  # noqa: UP007


# Screen modules must not import inkydash.config at module level (this line imports them).
ScreenOpts = _screen_union()


class Config(Strict):
    schema_: str | None = Field(None, alias="$schema")
    timezone: str | None = None
    display: DisplayConfig = DisplayConfig()
    locale: LocaleConfig = LocaleConfig()
    holidays: HolidaysConfig = HolidaysConfig()
    off_days: list[str] = []
    locations: dict[str, Location] = {}
    providers: ProvidersConfig = ProvidersConfig()
    screens: dict[str, ScreenOpts]  # type: ignore[valid-type]
    schedule: list[Rule] = []
    default_screen: str
    buttons: ButtonsConfig = ButtonsConfig()

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if v is not None:
            from zoneinfo import ZoneInfo

            try:
                ZoneInfo(v)
            except Exception as e:
                raise ValueError(f"unknown time zone {v!r}") from e
        return v

    @field_validator("off_days")
    @classmethod
    def _off(cls, v: list[str]) -> list[str]:
        for item in v:
            parse_off_day(item)
        return v

    @model_validator(mode="after")
    def _references(self) -> Config:
        from inkydash.registry import screen_types

        types = screen_types()
        names = set(self.screens)
        if self.default_screen not in names:
            raise ValueError(f"default_screen {self.default_screen!r} is not a screen")
        seen: set[str] = set()
        for i, rule in enumerate(self.schedule):
            if rule.name in seen:
                raise ValueError(f"schedule[{i}]: duplicate rule name {rule.name!r}")
            seen.add(rule.name)
            for s in rule.targets:
                if s not in names:
                    raise ValueError(f"schedule[{i}] ({rule.name}): unknown screen {s!r}")
            if rule.screens is not None:
                for s in rule.screens:
                    if self.screens[s].type == "quiet":
                        raise ValueError(f"schedule[{i}] ({rule.name}): a quiet screen can't be in a rotation")
        for name, opts in self.screens.items():
            check = getattr(types[opts.type], "check_config", None)
            if check:
                problem = check(opts, self)
                if problem:
                    raise ValueError(f"screens.{name}: {problem}")
        for ctx in self.buttons.map:
            if ctx != "default" and ctx not in names and ctx not in types:
                raise ValueError(f"buttons.map.{ctx}: not 'default', a screen name or a screen type")
        from inkydash.actions import validate_actions

        validate_actions(self)
        return self

    # convenience
    def screen_type(self, screen: str) -> str:
        return self.screens[screen].type

    def location(self, key: str) -> Location:
        return self.locations[key]


def parse_off_day(item: str) -> tuple[date, date]:
    parts = item.split("/")
    try:
        if len(parts) == 1:
            d = date.fromisoformat(parts[0])
            return d, d
        if len(parts) == 2:
            a, b = date.fromisoformat(parts[0]), date.fromisoformat(parts[1])
            if b < a:
                raise ValueError
            return a, b
    except ValueError:
        pass
    raise ValueError(f"off_days: expected 'YYYY-MM-DD' or 'YYYY-MM-DD/YYYY-MM-DD', got {item!r}")


# ---------------------------------------------------------------- loading
_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def load_dotenv(path: Path) -> None:
    """KEY=VALUE lines; doesn't override variables that are already set."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        os.environ.setdefault(k, v)


def interpolate(value: Any, env: dict[str, str] | None = None) -> Any:
    """Replace ${VAR}. A string that is only an unset ${VAR} becomes None (provider skipped)."""
    env = os.environ if env is None else env
    if isinstance(value, dict):
        return {k: interpolate(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(v, env) for v in value]
    if isinstance(value, str):
        m = _ENV_RE.fullmatch(value)
        if m:
            return env.get(m.group(1)) or None
        return _ENV_RE.sub(lambda mm: env.get(mm.group(1), ""), value)
    return value


CONFIG_CANDIDATES = ("config.json", "config.jsonc", "/etc/inky-dash/config.json", "/etc/inky-dash/config.jsonc")


def find_config(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("INKYDASH_CONFIG")
    if env:
        return Path(env)
    for c in CONFIG_CANDIDATES:
        p = Path(c)
        if p.is_file():
            return p
    raise FileNotFoundError(
        "no config found: pass --config, set INKYDASH_CONFIG, or create config.json "
        "(start from config.example.json)"
    )


class ConfigError(Exception):
    def __init__(self, path: Path, errors: list[str]):
        self.path = path
        self.errors = errors
        super().__init__(f"{path}: " + "; ".join(errors))


def format_validation_error(e: ValidationError) -> list[str]:
    out = []
    for err in e.errors():
        loc = ""
        for part in err["loc"]:
            if isinstance(part, int):
                loc += f"[{part}]"
            elif part in ("function-after[_either()]",) or str(part).startswith(("function-", "tagged-union")):
                continue
            else:
                loc += ("." if loc else "") + str(part)
        msg = err["msg"].removeprefix("Value error, ")
        out.append(f"{loc}: {msg}" if loc else msg)
    return out


def parse_config(data: Any, path: Path = Path("<config>")) -> Config:
    try:
        return Config.model_validate(interpolate(data))
    except ValidationError as e:
        raise ConfigError(path, format_validation_error(e)) from None


def load_config(path: str | os.PathLike[str] | None = None) -> tuple[Config, Path]:
    p = find_config(path)
    load_dotenv(p.parent / ".env")
    load_dotenv(Path.cwd() / ".env")
    try:
        data = jsonc.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigError(p, [str(e)]) from None
    config = parse_config(data, p)
    base = p.resolve().parent
    for opts in config.screens.values():
        resolve = getattr(opts, "resolve_paths", None)
        if resolve:
            resolve(base)
    return config, p


def json_schema() -> dict[str, Any]:
    schema = Config.model_json_schema(by_alias=True, mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "inky-dash config"
    return schema
