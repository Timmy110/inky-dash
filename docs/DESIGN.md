# inky-dash — Design Document

| | |
|---|---|
| Status | v0.5, implemented as inky-dash 0.1 (see §13) |
| Date | 2026-11-20 |
| Target hardware | Raspberry Pi **Zero 2 WH** + Pimoroni Inky Impression 7.3" **Spectra 6** (E673, 800×480, 6 colours) |
| Repo | https://github.com/Timmy110/inky-dash |

### Changelog
- **v0.5** (review against the `inky` driver source): dashboards render in **palette mode (`P`)** and screens return a **`Frame`**, because RGB drawing dithers at every saturation (§7.3). The refresh check uses a **content hash** that ignores the "updated" footer. **Precedence changed: traffic alert > manual override > schedule.** Gallery picks are tied to a persisted **slot**, so restarts don't change the photo. `until` may cross midnight, and `only_on` is the counterpart of `skip_on`. Clean cycle duration corrected and made configurable. Button action `quiet_toggle` renamed **`pause_toggle`**; shutdown draws a "safe to unplug" screen. **One image pipeline** shared by the PC and the Pi. New tile style **`stadia-toner`** (pure black/white) is the `auto` default. Hardware **test card** added to M0. Clock-sync fallback, watchdog, rollback that reinstalls dependencies, per-user paths.
- **v0.4** (design review): rules can **rotate** between screens. New **`quiet`** screen type (clean, then one image, no refreshes) replaces `night_clean`. **Holidays/off days** with `skip_on`. Navigation **traffic alert takeover**. Weather adds rain nowcast, air quality and pollen, UV and official warnings, using **priority flex slots**. Photos are **prepared on the PC** (`inky-dash prepare`) and uploaded over SSH/SCP. **Install and update** via git clone + `inky-dash update` with rollback.
- **v0.3:** defaults set to **landscape** (primary) and English, with portrait as a secondary layout. Navigation is built around **live traffic**, with **multiple routing providers** (TomTom, HERE, Mapbox, Google, OSRM, ORS) in a fallback chain per mode. Alternatives are shown only when **close in time**, can be switched with a button, and stay selected across refreshes. Comparison against usual times, jams on the map and departure-time forecast. Both map tile styles (Stadia Toner Lite, OSM) are options. A draft context-sensitive button design was added (§6.5, to be revisited).
- **v0.2:** target fixed to Spectra 6 and Pi Zero 2 WH. Global `orientation` and `locale` settings added. Navigation defined as best route + travel time + map + travel type. Gallery source abstraction added. HTML renderer dropped.
- **v0.1:** initial draft.

---

## 1. Goal

A small, unattended appliance that shows different **screens** on an Inky Impression 7.3" Spectra 6 e-paper display, according to a **schedule** defined in one config file.

Example: show the weather at 06:00 every day, the route to work on weekday mornings, a photo from the gallery in the evening, and a monthly calendar on the 1st of each month.

### 1.1 Goals
- **Declarative schedule.** One JSON config holds *when* (every day/week/month at a time) and *what* (screen type plus options).
- **Pluggable screen types.** v1 ships with `weather`, `gallery`, `navigation` and `status`. Adding a new type means adding one module.
- **Pluggable data sources.** For example, gallery images come from a local folder in v1, but the code works against a `GallerySource` interface so remote albums can be added later without touching the screen.
- **Survives reboots and failures.** On startup the program works out the correct screen from the clock and the config alone. A failing data provider never blanks the display.
- **Kind to e-paper and to the Pi Zero.** Refreshes are slow and visible, and the Zero 2 W has only 512 MB RAM. The app never refreshes when nothing changed, keeps memory low, and caches expensive work.
- **Develop off-device.** Everything except the final `show()` runs on Windows/macOS/Linux and renders PNG files, so layouts can be built without a Pi.

### 1.2 Non-goals (v1)
- Interactive UI or touch: the panel only has 4 buttons.
- Real-time data. Nothing should update more often than every few minutes.
- Support for other Inky models. The code goes through `inky.auto()` and palette roles, so others would *probably* work, but only Spectra 6 7.3" is tested.
- Per-screen orientation or language. These are global, since a mounted display doesn't change orientation.
- Distribution to other people. This is a personal project for one device. The multiple providers, tile styles, languages and orientations are kept because they're wanted as options, not to support other installs.

---

## 2. Hardware constraints (drives the design)

Details and code snippets: [`docs/hardware/inky-impression-7.3.md`](hardware/inky-impression-7.3.md).

### 2.1 Display: Inky Impression 7.3" Spectra 6 (E673)

| Constraint | Consequence for the design |
|---|---|
| 800×480 px, driver `inky/inky_e673.py`, EEPROM variant 22/26. `inky.auto()` detects it. | The display backend uses `auto()`, and creates `InkyE673` directly when a non-default `refresh_speed` is set. |
| **6 colours:** BLACK, WHITE, YELLOW, RED, BLUE, GREEN. There is no orange and no CLEAN colour. | The theme uses **palette roles** (§7.3) that map to these six colours. |
| A full refresh takes **tens of seconds** and flashes the screen. There are no partial updates. | No clocks with seconds or minutes. Show "updated 07:00" instead. Skip the refresh if the **content hash** hasn't changed (§5.5). Enforce a minimum refresh interval (default 3 min). |
| Refresh speed `NORMAL` / `MEDIUM` / `FAST`. Faster speeds reduce saturation, especially blue and green. | Global setting `display.refresh_speed`, default `normal`. |
| RGB images are **always dithered**, against a palette blended by `saturation`. No RGB value except black maps 1:1 (even at saturation 1.0, "white" is `(161,164,165)`). Only a mode-`P` image with exactly 6 palette colours skips dithering. | Dashboards (text, icons, charts, maps) are drawn in **mode `P` with palette indices**, so text stays sharp. Only photos are RGB and dithered (§7.3). |
| `set_image(img, saturation=0.5)` | Saturation is set per screen: photos look better at around 0.5–0.8. |
| 4 buttons, A–D, on BCM GPIO **5, 6, 16, 24**, read with `gpiod` + `gpiodevice`. There's also a shine-through LED on the back (BCM GPIO 13). | Optional button service with configurable pins. The LED can blink while a refresh is in progress or on error (nice to have). |
| SPI and I2C must be enabled. | Handled by the install script and checked by `inky-dash doctor`. |
| Ghosting can build up over many refreshes. | The `quiet` screen type (§6.4) can start with a clean cycle that flushes full-screen colours. Upstream `examples/clean.py` is a pHAT script (3 cycles of red/black/white, 9 full refreshes, about 5 min on this panel), so inky-dash has its own version with a configurable cycle count. Every Spectra 6 refresh is already a full refresh, so whether cleaning is needed at all is checked with the M0 test card. |

### 2.2 Computer: Raspberry Pi Zero 2 WH

| Constraint | Consequence for the design |
|---|---|
| 4× Cortex-A53 at 1 GHz, **512 MB RAM** | No headless browser, so the HTML/Chromium rendering idea is dropped. Rendering is done with Pillow only. Large modules are imported lazily. |
| Large JPEGs (for example 24 MP) decode to roughly 70 MB of RGB | Use `Image.draft()` to decode JPEGs at a reduced scale (1/2, 1/4 or 1/8) before resizing. Process one image at a time. |
| Slow SD card I/O, and card wear | Pre-rendered images are cached, but state is written sparingly (only when it changes). Logs go to journald, not files. |
| OS | **Raspberry Pi OS Lite 64-bit (Bookworm or later)**, headless, Python ≥ 3.11. PyPI has aarch64 wheels for numpy, Pillow and pydantic-core. |
| No real-time clock | The clock is wrong after boot until NTP syncs. See §8 for the wait and fallback. |
| Wi-Fi only | Expect drops. Every network call has a timeout, retries, and falls back to cached data. |

---

## 3. Concepts

| Term | Meaning |
|---|---|
| **Screen** | A named, configured instance of a screen *type*, such as `"home-weather"` of type `weather` with a location. |
| **Screen type** | Code that turns options plus data into an image at the global orientation's size. |
| **Provider** | Fetches and caches external data (Open-Meteo, a routing API, map tiles, a gallery source…). Screen types use providers, and providers know nothing about rendering. |
| **Rule** | One schedule entry: a `when` (trigger) plus the `screen` it activates. |
| **Trigger** | The point in time when a rule fires (for example, every weekday at 07:00). |
| **Active screen** | The screen whose rule fired **most recently**. It stays up until another rule fires. |
| **Refresh** | Re-rendering the active screen, for example to update the weather every 30 min. |

---

## 4. Configuration

### 4.1 Format
**JSON**, validated with a published **JSON Schema** (`schema/config.schema.json`). Referencing it through `"$schema"` gives autocompletion and validation in VS Code. At load time we validate with **pydantic**, so errors are precise ("`schedule[2].when.at`: expected HH:MM"). JSON with comments (`config.jsonc`) is also accepted.

Secrets (API keys) are **not** stored in the config. They come from environment variables or a `.env` file, referenced as `"${ORS_API_KEY}"`.

### 4.2 Global settings

These apply to every screen. People don't remount the display or switch language per screen.

| Setting | Values | Default | Effect |
|---|---|---|---|
| `display.orientation` | `"landscape"` (800×480) \| `"portrait"` (480×800) | **`landscape`** | Every screen type **must** provide a layout for both. **Landscape is primary** (designed and polished first). Portrait is secondary but must stay functional and tested. Screens receive `ctx.size` and `ctx.orientation`. |
| `display.flip` | `true` \| `false` | `false` | Rotates 180°, for mounting the frame upside down (for example, cable on top). |
| `display.refresh_speed` | `normal` \| `medium` \| `fast` | `normal` | Spectra 6 refresh speed. |
| `display.min_refresh_interval` | duration | `"3m"` | Rate limit (§5). |
| `holidays.country` | ISO code, e.g. `"BE"` \| `null` | `null` | Public holidays, computed offline with the `holidays` library. Used by `skip_on`. |
| `off_days` | list of `"YYYY-MM-DD"` or `"YYYY-MM-DD/YYYY-MM-DD"` ranges | `[]` | Personal days off (holidays, leave). Used by `skip_on`. |
| `locale.language` | `"en"`, `"nl"`, `"fr"`, `"de"` … | **`en`** | UI strings (from `inkydash/i18n/<lang>.json`) plus day and month names and date formats (via `babel`). Unknown keys fall back to English. |
| `locale.units` | `"metric"` \| `"imperial"` | `metric` | Temperatures, wind, distances. |
| `locale.time_format` | `"24h"` \| `"12h"` | `24h` | |
| `timezone` | IANA name | system time zone | Used for scheduling and display. |

### 4.3 Shape

```jsonc
{
  "$schema": "./schema/config.schema.json",
  "timezone": "Europe/Brussels",

  "display": {
    "orientation": "landscape",
    "flip": false,
    "refresh_speed": "normal",
    "min_refresh_interval": "3m"
  },
  "locale": { "language": "en", "units": "metric", "time_format": "24h" },
  "holidays": { "country": "BE" },
  "off_days": ["2026-12-24", "2026-12-28/2027-01-02"],

  "locations": {
    "home": { "lat": 50.85, "lon": 4.35, "label": "Home" },
    "work": { "address": "Korenmarkt, Gent", "label": "Office" }   // geocoded once, cached
  },

  "screens": {
    "weather": { "type": "weather", "location": "home" },
    "to-work": { "type": "navigation", "from": "home", "to": "work",
                 "modes": ["car"], "arrive_by": "09:00",
                 "alert": { "watch": { "every": "week", "days": ["mon","tue","wed","thu","fri"],
                                       "at": "06:30", "until": "08:45", "skip_on": ["holiday","off"] },
                            "poll": "15m", "delay_over": "10m" } },
    "photos":  { "type": "gallery",
                 "source": { "kind": "folder", "path": "~/photos" },
                 "order": "shuffle", "fit": "auto", "saturation": 0.6 },
    "night":   { "type": "quiet", "show": "photos", "clean": "before" }
  },

  "schedule": [
    { "name": "weather-daytime",
      "when": { "every": "day", "at": "06:00", "until": "22:00", "refresh": "30m" },
      "screen": "weather" },
    { "name": "weekday-commute",
      "when": { "every": "week", "days": ["mon","tue","wed","thu","fri"],
                "at": "07:15", "until": "08:45", "refresh": "10m",
                "skip_on": ["holiday", "off"] },
      "screen": "to-work" },
    { "name": "evening",
      "when": { "every": "day", "at": "18:00" },
      "screens": ["weather", "photos"], "rotate_every": "20m" },
    { "name": "night",
      "when": { "every": "day", "at": "23:00" },
      "screen": "night" }
  ],

  "default_screen": "photos",

  "buttons": { "enabled": true }      // default mapping, see §6.5
}
```

The real example lives in [`config.example.json`](../config.example.json).

### 4.4 `when` reference

| Field | Applies to | Meaning |
|---|---|---|
| `every` | all | `"day"` \| `"week"` \| `"month"` (later: `"year"`, `"once"` with `date`) |
| `at` | all | `"HH:MM"` local time, or a list such as `["07:00","12:00"]` for several triggers a day |
| `days` | week | `["mon".."sun"]`. Default is all days. |
| `day` | month | `1..31`, or `-1` for the last day of the month. If a month has fewer days, the rule fires on its last day. |
| `until` | all | Optional `"HH:MM"`. When it passes, the rule stops being active and the schedule falls back to the next rule (see §5). An `until` at or before `at` means **the next day** (`"at": "22:00", "until": "06:00"` crosses midnight). |
| `refresh` | all | Optional duration (`"10m"`, `"1h"`): how often to re-render while the rule is active. Without it, the screen renders once per trigger. |
| `skip_on` | all | `["holiday", "off"]`: the rule doesn't trigger on public holidays (`holidays.country`) or personal `off_days`. Default `[]`. |
| `only_on` | all | `["holiday", "off"]`: the opposite. The rule triggers **only** on those days ("on days off, show photos all morning"). Default `[]` (no restriction). A rule can't list the same day kind in both. |
| `enabled` | rule | Default `true`. |

**Rule targets:** a rule has either `"screen": "name"` or `"screens": ["a", "b", …]` with `"rotate_every": "20m"`. A rotating rule cycles through its screens in order while it's active, starting with the first at each trigger. Each step is a normal render (hash check, rate limit). `refresh` still applies to the screen currently shown.

`skip_on` and `only_on` look at the local date of the **trigger** (`at`), so a rule that crosses midnight is judged by the day it starts.

Durations use `\d+[smhd]`. Times are always in the config `timezone`, and DST is handled with `zoneinfo`.

---

## 5. Scheduling semantics

1. Each enabled rule produces trigger instants (a day-by-day iterator with `zoneinfo`; all comparisons in UTC).
2. A rule is **live** at time *t* if its most recent trigger ≤ *t* and its `until` (if set) hasn't passed for that trigger.
3. **Active rule** at *t* = the live rule with the **latest last trigger**. If two rules triggered at the same moment, the more specific one wins (`month` > `week` > `day`), and after that the one listed first.
4. If no rule is live, the active screen is `default_screen`. A rule without `until` stays live until another rule triggers, so `default_screen` only appears when every rule has an `until` (or before the first trigger ever). In the example config the `night` rule has no `until`, so `default_screen` is a safety net there and is never shown.
5. A **render** happens when:
   - the active rule changes,
   - the active rule fired again,
   - its `refresh` interval elapsed, or
   - a button requests one.

   A render only leads to a **display update** if the **content hash** differs from what is on the panel *and* `min_refresh_interval` has passed. Scheduled changes are deferred until that interval is over, never dropped.

   The content hash covers the final palette-indexed image **without its volatile regions** (the "updated 07:00" footer, declared by the screen in its `Frame`, §6). Otherwise the timestamp alone would force a refresh on every render. The footer therefore means "the panel last changed at 07:00". Stale data is flagged separately with the ⚠ badge (§8).
6. **Stateless recovery.** Steps 2–4 depend only on (config, now), so a restart lands on the correct screen right away. The only persisted state is the last content hash, gallery history and slot picks (§6.2), and data caches (in `state/`).
7. **Manual override.** Button "next screen" pins a screen until the **next trigger of any rule**, then normal scheduling resumes. Rotation steps and refreshes are not triggers and don't end an override.
8. **Rotation.** For a rotating rule, the shown screen is `screens[floor((t − last_trigger) / rotate_every) mod n]`. That's still a pure function of (config, now), so recovery stays stateless.
9. **Skipped days.** `skip_on` removes trigger instants on holidays or off days, and `only_on` keeps only those. The rule is then simply not live that day, and the previous rule stays active.
10. **Precedence:** **traffic alert takeover (§6.3) > manual override (button) > schedule** (including rotation and `quiet`). An alert interrupts a manual override: a button pressed at 06:10 must not hide a jam at 06:50. The alert ends when traffic returns to normal, when its watch window ends, or when you **dismiss** it with `next_screen` or `resume_schedule` (button A). A dismissed alert stays away for the rest of that watch window. The other buttons keep their navigation actions on the alert screen.

The main loop computes the next wake-up as `min(next trigger, next until, next refresh, deferred update)` and sleeps until then. Button events wake it early.

The worked example with the config above, on a Monday:

| Time | Live rules | Shown | Why |
|---|---|---|---|
| 05:00 | night (from Sun 23:00) | night image | `quiet`: cleaned at 23:00, one image, no refreshes |
| 06:00 | weather, night | weather | latest trigger |
| 07:05 | weather, night | **to-work (alert)** | alert poll: +14 min vs usual > 10 min |
| 07:15 | commute, weather, night | to-work | commute rule triggers, refreshed every 10 min |
| 08:45 | weather, night | weather | commute `until` passed, falls back to 06:00 |
| 18:00 | evening, weather, night | weather → photos → weather … | rotation every 20 min |
| 22:00 | evening, night | rotation continues | weather `until` passed, but `evening` is the latest trigger |
| 23:00 | night | night image | `quiet` triggers |

On a public holiday the 07:15 commute rule and the alert watch don't fire, so weather stays up all morning.

---

## 6. Screen types

Each type implements:

```python
class ScreenType(Protocol):
    name: ClassVar[str]                     # "weather"
    Options: ClassVar[type[BaseModel]]      # pydantic model for its config block
    def render(self, ctx: RenderContext, opts: Options) -> Frame: ...

@dataclass
class Frame:
    image: Image.Image                   # mode "P" with the theme palette (shown undithered),
                                         # or "RGB" for a photo (dithered by the display layer)
    overlay: Image.Image | None = None   # mode "P" + transparent index, pasted on top after
                                         # dithering (caption bar, button legend)
    saturation: float = 0.5              # only used when image is RGB
    volatile: Sequence[Box] = ()         # regions left out of the content hash ("updated 07:00")
```

Dashboards return a `P` image and never dither. The gallery returns an RGB photo, with its caption as an overlay so the caption text stays sharp. Why this split is needed: §7.3.

`RenderContext` provides:
- `size` and `orientation` (from global settings),
- `now`,
- `theme` (palette roles and fonts),
- `t()` (translation) and `fmt` (locale-aware date, time, number and unit formatting),
- `providers`,
- `state` (a persistent dict per screen).

Each type ships a **landscape (primary) and a portrait (secondary) layout**. The render tests cover both.

### 6.1 `weather` (v1)
- **Providers:**
  - [Open-Meteo](https://open-meteo.com/) forecast: current, 15-minutely, hourly and daily. Free, no key, cached for 15 min.
  - Open-Meteo **Air Quality** API: European AQI, pollen and UV. Free, no key.
  - **MeteoAlarm** feeds for official warnings (KMI/IRM code yellow/orange/red). Free, no key.

  Costs are listed in [`docs/providers.md`](providers.md).
- **Landscape layout (primary):** big current temperature and icon on the left. On the right, today's high/low, rain probability, wind, sunrise and sunset. Along the bottom, a strip with the next 12 hours (temperature line plus rain bars) and the next 5 days.
- **Portrait layout (secondary):** the same blocks stacked: current conditions on top, then today's details, the hourly strip and the 5-day list.
- **Fixed layout with priority flex slots:**
  - The core blocks are always shown: current conditions, today's high/low/wind, the 12-hour strip and the 5-day forecast.
  - **Optional blocks** compete for 2 flex slots (landscape) or 3 (portrait), and a block only competes when it's *relevant*. The highest-priority relevant blocks win:

    | Block | Relevant when | Default priority |
    |---|---|---|
    | `warnings` | an official warning is active. Shown as a full-width RED banner on top, outside the flex slots. | always first |
    | `rain_nowcast` | rain is expected in the next 2 h ("Rain starts 08:30, stops ~09:15", 15-min bars) | 1 |
    | `uv` | daily max UV ≥ `uv_threshold` (default 6) | 2 |
    | `pollen` | any pollen type ≥ "moderate" (in season) | 3 |
    | `air_quality` | European AQI ≥ "moderate" | 4 |
    | `sun` | sunrise/sunset, the filler when nothing else is relevant | 9 |

- **Options:** `location`, `blocks` (enable or disable each, and override its priority), `uv_threshold`, `pollen_types` (for example `["grass", "birch"]`). Units and language come from the global `locale`.
- **Icons:** an OFL/MIT weather icon font (*Weather Icons* / *Meteocons*), drawn in palette colours: sun in yellow, rain in blue, warnings in red.

### 6.2 `gallery` (v1: local folder, built for remote sources)

#### Source abstraction
Screens never touch the file system or network directly. They talk to a `GallerySource`:

```python
@dataclass(frozen=True)
class ImageRef:
    source_id: str          # "folder:photos"
    item_id: str            # stable within source (relative path, remote asset id, …)
    version: str            # mtime / etag / checksum → cache invalidation
    taken_at: datetime | None
    name: str

class GallerySource(Protocol):
    id: str
    def list_items(self) -> Iterable[ImageRef]: ...        # cheap metadata only
    def fetch(self, ref: ImageRef) -> BinaryIO: ...        # original bytes
    def caption(self, ref: ImageRef) -> str | None: ...    # optional (sidecar, album title…)
```

- Sources are registered by `kind` in the same way as screen types. `"source": "/path"` is shorthand for `{"kind": "folder", "path": "/path"}`.
- **v1:** `folder` source. It scans recursively for `.jpg/.jpeg/.png/.webp/.heic*`. The index is cached in `state/` and refreshed at most every `rescan` (default `1h`), so the SD card isn't walked on every render.
  <br>\*HEIC needs `pillow-heif`, which is optional.
- **Later (no screen changes needed):** `immich` (API key and album ID), `webdav`/`nextcloud`, `smb`, and `url_list` (an RSS/JSON feed of image URLs). Remote sources download through `fetch()` into a **bounded download cache** (for example 200 MB, least-recently-used entries removed first).
- Multiple sources per screen are allowed (`"sources": [...]`). Items are merged and keyed by `source_id:item_id`.

#### Selection (independent of the source)
- `order`:
  - `shuffle`: no repeat until every image has been shown. History is persisted.
  - `sequential`
  - `random`
  - `on_this_day`: photos taken on today's date in earlier years, falling back to shuffle.
- The `next_image` button action advances the selection.
- **Picks are tied to a slot.** A slot is (rule name, trigger instant, rotation step or refresh number). The first render of a slot advances the selection and stores `slot → image` in `state/`. Rendering the same slot again (after a restart, a config reload or a deferred update) returns the same image. So a restart never changes the photo or costs a refresh, and recovery stays stateless in effect (§5.6).

#### Preparing photos on the PC (`inky-dash prepare`)
Photos reach the Pi over **SSH/SCP** (WinSCP or `scp`). No Samba or sync service runs on the Pi. The heavy, quality-critical work is done **on your PC** before upload:

```
PC> inky-dash prepare D:\Photos\Holiday --out .\prepared --preview
    ✓ 42 images → prepared\ (800×480, 480×800, palette previews in prepared\_preview\)
PC> scp prepared\*.png pi@inkydash.local:photos/
```

- **Smart crop:** face and saliency detection (OpenCV) picks the crop window, so heads aren't cut off. Portraits on a landscape panel are either cropped around the faces or paired side by side (`--pair-portraits`).
- **E-paper tuning:** contrast, saturation and gamma boost tuned for Spectra 6 (no backlight).
- Output is PNG at the exact panel size, one per orientation (or only `--orientation landscape`). A **preview** shows the image dithered to the 6-colour palette, so you can reject bad ones before uploading.
- A sidecar `photo.json` keeps the source name, date and caption.
- Installed on the PC with `pip install inky-dash[prepare]` (OpenCV is not installed on the Pi).
- The Pi detects prepared images by size and filename suffix, picks the one matching the current orientation, and skips its own crop and enhance steps.
- **One pipeline, two places.** `prepare` runs the same code as the Pi (`inkydash/imaging.py`, steps 1–3 below). Its only additions are the smart crop window and the previews. There is no second implementation to keep in sync, and an unprepared photo gets the same tuning with a simpler crop.

#### Image pipeline (shared; on the Pi it is the fallback for unprepared photos)
1. `Image.open()` → `draft("RGB", target)` for JPEG, so it decodes at reduced scale. → `ImageOps.exif_transpose`.
2. Fit to `ctx.size`:
   - `cover` crops to fill, centre-weighted with an upper-third bias (heads tend to sit high).
   - `contain` letterboxes with a palette-colour background.
   - `auto` uses `cover` unless the aspect ratio differs by more than ~25%.
3. Optional enhancement: contrast and saturation boost (`enhance` option), because e-paper has no backlight.
4. Optional caption bar (filename, EXIF date, or sidecar `.txt`), returned as the `Frame` **overlay** in palette indices.
5. The display layer dithers the photo to the 6 colours at `opts.saturation`, then pastes the overlay, so the caption is not dithered (§7.3).
6. The result of steps 1–3 is cached in `state/prerender/<hash(ref, version, size, opts)>.png`. After showing an image, the **next** one is pre-rendered while the app is idle.

### 6.3 `navigation` (v1): best route with live traffic, map, travel type

**Purpose:** you already know the route and how long it *normally* takes. What you don't know is what's different **right now**. The screen answers four questions:
1. **Is it worse than usual today, and by how much?** ("+12 min vs usual")
2. **Which of my usual routes is fastest right now?** ("via R0 instead of E40")
3. **Where is the problem?** (jams coloured on the map, plus the top incidents listed)
4. **Should I leave now or wait?** ("leaving at 07:45 saves 9 min")

So **live traffic is a hard requirement for `car`**. A car route without it is a static number you already know. The keyless OSM routers are only used for modes where traffic doesn't matter (bike, walk), or as an explicitly labelled fallback.

#### Options
| Option | Meaning |
|---|---|
| `from`, `to` | A location key (`locations.home`). Locations are given as `lat/lon` or as an `address`, which is geocoded once and cached. `to` may be a list, which the buttons cycle through. |
| `modes` | Travel types: `car` (v1), later `bike`, `walk`, `transit`. Default `["car"]`. |
| `alternatives` | `{ "max": 3, "within": "10m", "within_pct": 20 }`. Alternatives are kept only if they're **close in time** to the fastest (see *Alternative routes* below). `false` turns them off. |
| `arrive_by` / `depart_at` | Optional `"HH:MM"`. With `arrive_by`, the screen shows "leave by HH:MM", based on live traffic. |
| `forecast` | Departure offsets to compare, default `["+15m", "+30m"]`. Set `[]` to turn off. |
| `delay_threshold` | Below this delay compared with usual, show "normal" (default `"3m"`). |
| `map` | `{ "padding": 0.1, "show_alternatives": true }` or `false`. |
| `alert` | Optional traffic alert takeover: `{ "watch": <when>, "poll": "15m", "delay_over": "10m" }` (see below). |

#### Traffic alert takeover
A scheduled commute screen at 07:15 doesn't help if the jam starts at 06:50. With `alert` configured:
- During the `watch` window (a normal `when` block, including `skip_on`), the route is **polled in the background** every `poll`, without rendering.
- If the best route's delay compared with usual is above `delay_over`, the navigation screen **takes over** the display, whatever rule is active. It shows a RED banner: "⚠ Traffic: +14 min vs usual".
- The alert ends when the delay drops below `delay_over` minus a hysteresis (default 3 min), when the watch window ends, or when you dismiss it with button A. The schedule (or your manual override) then resumes. The alert ranks above a manual override. Precedence is in §5.10.
- Cost: about 9 polls per weekday morning (1 request each, no forecast requests), so about 200 extra calls a month.

#### Alternative routes (like other navigation apps)
Navigation apps don't list every possible route. They only offer alternatives that are *competitive*. inky-dash does the same:
1. Ask the provider for alternatives (TomTom `maxAlternatives` ≤ 5, HERE `alternatives` ≤ 6, Mapbox `alternatives=true` (≤ 2 extra), Google `computeAlternativeRoutes` (≤ 3)).
2. **Keep only routes that are close in time.** A route is kept if `duration ≤ fastest + max(within, fastest × within_pct%)`. With the defaults, a 40-minute fastest route keeps alternatives up to 50 minutes (+10 min beats +8 min).
3. **Remove near-duplicates.** If two routes share more than 85% of their geometry, they are the same route for the user, so only the faster one is kept.
4. Take at most `max` routes, sort them by live duration, and number them **1** (fastest), **2**, **3**.

**Switching routes with a button.** `next_route` (navigation, button B in the draft mapping) cycles the **selected** route: 1 → 2 → 3 → 1.
- The selected route is drawn thick with jam colouring. The others are thin, dashed and numbered.
- The info panel shows the selected route's details: "Route 2 of 3 · via R0 · 44′ · +4′ vs fastest". Its incidents and forecast are shown too.
- **Selection is sticky across refreshes.** Providers don't return stable route IDs, so on each refresh the selection is re-matched by geometry similarity to the previously selected route. If that route disappears, for example because it's no longer close in time, the selection falls back to route 1 with a small note.
- The selection resets to route 1 when the rule triggers again (same behaviour as the manual override in §5.7).

#### Data per refresh (TomTom example, a single request)
`traffic=true`, `maxAlternatives=N`, `computeTravelTimeFor=all`, `sectionType=traffic`, `routeRepresentation=encodedPolyline`. This returns, per route:

| Field | Used for |
|---|---|
| `travelTimeInSeconds` | **Live** travel time (hero number) |
| `historicTrafficTravelTimeInSeconds` | **"Usual"** for this weekday and time → the "+12 min vs usual" figure |
| `noTrafficTravelTimeInSeconds` | Free-flow baseline (small text) |
| `trafficDelayInSeconds` | Total delay caused by current traffic |
| traffic sections: `simpleCategory` (JAM / ROAD_WORK / ROAD_CLOSURE), `magnitudeOfDelay` (0–4), `delayInSeconds`, `effectiveSpeedInKmh`, start/end point index | Colouring the jams on the map and the incident list |
| `points` (geometry) | Drawing the map |

The **forecast** makes one extra request per offset (`departAt=now+15m`, …). Those times come from TomTom's predictive and historic model, so they're labelled as estimates.

**"Via" label:** derived from the guidance by taking the longest named or numbered road on each route (`via E40`, `via R0 / A12`). The fallback is "Route 1/2/3".

#### Content
- **Hero:** travel-type icon (🚗) + **live duration** (big) + ETA, or "leave by HH:MM" when `arrive_by` is set.
- **Compared with usual:** "+12 min vs usual" in **RED**, "normal" in **GREEN**, or "−4 min" in GREEN.
- **Route list:** numbered routes with their "via" label, live time and difference from the fastest (`1 via E40 42′ · 2 via R0 44′ (+2′)`). The **selected** route is highlighted (YELLOW background, black text).
- **Incidents:** the top 2–3 traffic sections on the selected route ("⚠ Jam E40 Groot-Bijgaarden +9 min", "⛔ Closure …").
- **Leave-time strip:** `now 42′ · +15m 38′ · +30m 33′`, with the best option highlighted.
- **Footer:** "Live traffic · TomTom · updated 07:32". If data is stale: "⚠ traffic data from 07:10".

#### Layout
- **Landscape (primary):** map on the left (~500×480), info panel on the right (~300 px): hero, comparison with usual, routes, incidents, and the leave-time strip.
- **Portrait (secondary):** map on top (480×440), info panel below.

#### Routing providers (`RoutingProvider` interface)

```python
class RoutingProvider(Protocol):
    modes: set[Mode]
    live_traffic: bool
    def routes(self, a: LatLon, b: LatLon, mode: Mode, *, depart: datetime | None = None,
               arrive: datetime | None = None, alternatives: int = 0) -> list[Route]: ...

@dataclass
class Route:
    duration: timedelta              # live (or predicted for future departures)
    duration_typical: timedelta | None
    duration_free_flow: timedelta | None
    traffic_delay: timedelta | None
    distance_m: int
    geometry: list[LatLon]
    traffic_sections: list[TrafficSection]   # start_idx, end_idx, category, magnitude, delay
    via: str | None
    live: bool                        # False → screen shows "no live traffic" label
    attribution: str
```

**Multiple providers, all implemented behind the same interface.** Free tiers, paid prices and setup steps for every provider are in [`docs/providers.md`](providers.md). Each provider is an adapter that normalises its API into the `Route` model above. The screen never knows which provider answered.

| Provider | Modes | Live traffic | "Usual" time | Jam sections | Alternatives | Key / free tier (verify at sign-up) |
|---|---|---|---|---|---|---|
| **TomTom Routing** | car, bike, walk | ✓ | ✓ historic | ✓ `sectionType=traffic` | ≤ 5 | Free key, no card. 20,000 routing requests/month. Paid ≈ $0.75/1,000 (unconfirmed). |
| **HERE Routing v8** | car, bike, walk | ✓ | ✓ (`typicalDuration`) | ✓ (`spans=dynamicSpeedInfo` / incidents) | ≤ 6 | Free key. Free allowance unclear (reported 5k–30k/month). Paid ≈ $1.50/1,000 (unconfirmed). |
| **Mapbox Directions** (`driving-traffic`) | car, bike, walk | ✓ | ✗ (a second free-flow request is used as the baseline) | ✓ per-segment `congestion` | ≤ 2 | Free key. 100,000 requests/month free, then $2.00/1,000. |
| **Google Routes API** | car, bike, walk, **transit** | ✓ | ✗ (free-flow via `staticDuration`) | ✓ `speedReadingIntervals` | ≤ 3 | Billing account required. 5,000 traffic-aware requests/month free, then $10/1,000. |
| **OSRM** (FOSSGIS or self-hosted) | car, bike, walk | ✗ | — | ✗ | ✓ | **No key**. At most 1 request/s, valid User-Agent. |
| **OpenRouteService** | car, bike, walk | ✗ | — | ✗ | ≤ 3 | Free key. |
| iRail (later) | transit (trains) | delays ✓ | — | — | ✓ connections | No key. |

**Capabilities degrade gracefully.** Each adapter declares what it supports (`live_traffic`, `typical_duration`, `traffic_sections`, `max_alternatives`, `depart_at`). The screen hides or adapts what's missing:
- no "usual" time → shows "+x min vs free-flow" instead,
- no jam sections → plain route line,
- no live traffic → a clear "no live traffic" label.

**Provider chain per mode (primary + fallbacks):**

```jsonc
"providers": {
  "routing": {
    "car":  ["tomtom", "here", "osrm"],   // first that answers wins; osrm = last resort, labelled
    "bike": ["osrm"],
    "walk": ["osrm"]
  }
}
```

- The **first provider in the list** is tried first. On an error, timeout, missing key or exhausted daily cap, the next one is tried.
- A provider that fails 3 times in a row is **skipped for 30 min** (circuit breaker), so a dead API doesn't slow every refresh.
- A provider without a configured key is skipped silently, so you can list them all and only fill in the keys you have.
- The footer always names the provider that answered ("Live traffic · HERE · 07:32"), so a fallback is visible.
- Each provider has its own `max_daily_calls` guard.

**Keys and personal use:** whether you need a key is set by each provider and doesn't depend on personal use. Personal / non-commercial use only decides which free terms apply. One traffic key (any of TomTom, HERE, Mapbox, Google) is enough. More keys just mean more fallbacks. Geocoding uses keyless **Nominatim** (once per address, cached forever), with TomTom/HERE/Mapbox geocoders as options.

**Testing:** every adapter gets contract tests against recorded real responses (fixtures), asserting the same normalised `Route` output, including alternatives and jam sections.

**Volume:** each refresh is 1 route request (alternatives are included in the same response) plus 2 forecast requests, so 3 calls. Every 10 min over a 1.5 h window that's about 27 calls a day, or about 600 a month, far below any free tier. Mapbox needs one extra free-flow request.

**Failure behaviour:** the screen never silently shows a free-flow time as if it were live. If all traffic providers in the chain fail, it shows the last live result with a stale badge. If that's older than 1 h, it uses the non-traffic last resort (OSRM), clearly labelled "no live traffic data".

Config:

```jsonc
"providers": {
  "routing":  { "car": ["tomtom", "here", "mapbox", "google", "osrm"] },
  "geocoder": "nominatim",
  "tomtom": { "key": "${TOMTOM_API_KEY}",       "max_daily_calls": 500 },
  "here":   { "key": "${HERE_API_KEY}",         "max_daily_calls": 500 },
  "mapbox": { "key": "${MAPBOX_TOKEN}",         "max_daily_calls": 500 },
  "google": { "key": "${GOOGLE_MAPS_API_KEY}",  "max_daily_calls": 200 },
  "osrm":   { "base_url": "https://routing.openstreetmap.de" }   // configurable, never hardcoded
}
```

Every request carries a User-Agent like `inky-dash/0.1 (+https://github.com/Timmy110/inky-dash)`, as the OSM, FOSSGIS and Nominatim policies require.

#### Map rendering (palette-pure)
- The view (bbox and zoom) is fitted to the union of all route geometries plus padding.
- **Base map** (`providers.tiles.style`). All three are supported, and you choose:

  | Style | Look | Key | Notes |
  |---|---|---|---|
  | `stadia-toner` | Stamen Toner: pure black and white, high contrast | Free Stadia account (no card). Server-side apps need a key. | Already two-tone, so the WHITE/BLACK threshold loses almost nothing. Expected to be the cleanest result. |
  | `stadia-toner-lite` | Stamen Toner Lite: white with **grey** roads and labels | Same Stadia key | Lighter and calmer, but it is greyscale: a plain threshold drops the light-grey roads. It gets its own lower threshold. Compare both on the M0 test card. |
  | `osm` | OSM standard (Carto): coloured, busy | None, but needs a custom User-Agent, a ≥ 7-day cache and visible attribution | Noisier after posterising. The OSM tile policy targets maps someone is actively viewing, so we fetch only when the route view changes. |
  | `auto` (default) | | | `stadia-toner` if `STADIA_API_KEY` is set, otherwise `osm`. |

  Tiles are posterised to WHITE/BLACK with a per-style threshold, which gives a crisp map with no dithering noise. They're stored in a disk cache that honours `Cache-Control`/`ETag` and keeps tiles for at least 7 days. The route view rarely changes, so tiles are fetched about once a week.
- **Overlays** are drawn with Pillow in pure palette colours. Traffic is coloured like a navigation app:
  - **best route:** thick BLACK line with a white casing,
  - **jam sections** on it: magnitude 1–2 (minor/moderate) in **YELLOW** with a black outline, magnitude 3 (major) in **RED**, closures in RED with a ✕ marker,
  - **alternative routes:** thin dashed BLACK lines with a small label ("2", "3") matching the route list,
  - **start** marker in **GREEN**, **destination** marker in **BLUE**,
  - incident markers ⚠ at the start of each listed incident.
- **Attribution** goes in a small corner label ("© OpenStreetMap contributors, © Stadia Maps, © Stamen", "Traffic © TomTom"). This is required by the licences.
- Implementation: a small custom tile stitcher (`render/map.py`, about 150 lines: Web Mercator math, fetch, stitch, crop). This avoids the `staticmap` dependency and gives control over posterisation and caching.

### 6.4 `status` and `quiet` (v1)

#### `quiet`: night / do-not-disturb mode
A screen type that wraps another screen and **freezes the panel**:
1. When its rule triggers, it runs the **clean cycle** first: full-screen colour flushes against ghosting. One cycle is 3 full refreshes (red, black, white), about 1.5–2 min of flashing. `"clean_cycles"` sets how many (default `1`; upstream uses 3, about 5 min). Set `"clean": false` to skip it. If the M0 test card shows no ghosting after a day of refreshes, the default becomes `false`.
2. Then it renders the wrapped screen **once** (`"show": "photos"` takes the next gallery image), and nothing refreshes after that: no `refresh`, no rotation.
3. It stays until the next rule triggers. Buttons still work (a manual override, as usual).

```jsonc
"night": { "type": "quiet", "show": "photos", "clean": "before" }
```

This replaces the old global `display.night_clean`. Schedule the quiet rule for when you go to bed, so the morning screen starts on a freshly cleaned panel.

`quiet` is a scheduled screen. It is not the same as the **`pause_toggle`** button action (§6.5), which stops all updates by hand until you press it again.

#### `status`
IP address, Wi-Fi signal, uptime, last successful fetch per provider, config errors, and SD card free space. It is shown on button D, when the config is invalid, or after repeated render failures.

### 6.5 Buttons (context-sensitive) — DRAFT, to be revisited

The Impression has 4 buttons (A–D, BCM GPIO 5/6/16/24). A refresh takes ~20–30 s, so buttons must feel responsive *without* the screen changing straight away. The design has five parts.

#### Two layers
Each button has a **short press** (< `long_press`, default `1s`) and a **long press**, which gives 8 slots. Destructive actions (`shutdown`, `reboot`) need a longer hold, `shutdown_hold` (default `5s`), whatever button they're mapped to.
- **Long presses are global.** They do the same thing on every screen, so they're predictable and easy to remember.
- **Short presses are context-sensitive.** The active screen type defines what they do. If a screen type doesn't define a button, it falls back to the global default.

Lookup order for a press is: screen **name** → screen **type** → `default`. So you can override a button for one specific screen.

#### Default mapping

| Button | Short: default | Short: `gallery` | Short: `weather` | Short: `navigation` | Short: `status` | **Long (global)** |
|---|---|---|---|---|---|---|
| **A** | next screen | next screen | next screen | next screen | next screen | **resume schedule** (cancels any override) |
| **B** | refresh | next image | cycle view: overview → hourly → 7-day | **next route** (1 → 2 → 3, close-in-time alternatives) | rescan gallery | **status** screen |
| **C** | previous screen | previous image | cycle location (if several) | **reverse route** (the way home) | run clean cycle | **pause** on/off (`pause_toggle`: stop all updates, for example while away) |
| **D** | status | ★ favourite (toggle) | refresh | next destination (if `to` is a list) | show QR code with the device IP and SSH hint | **hold 5 s: safe shutdown** (LED countdown) |

A is always "next screen", so you can never get stuck on a screen. Long-press D for a safe shutdown matters on a Pi Zero: pulling the power can corrupt the SD card. Before halting, the app draws a **"Safe to unplug · power-cycle to restart"** screen. E-paper keeps it without power, so the panel tells you the state of the Pi.

#### More actions that can be mapped
`goto:<screen>`, `prev_screen`, `refresh`, `resume_schedule`, `status`, `pause_toggle`, `clean`, `shutdown`, `reboot`, `led_toggle`, plus each screen type's own actions:
- **gallery:** `next_image`, `prev_image`, `favourite`, `hide_image` (never show again), `toggle_caption`, `hold_image` (pause rotation), `order:favourites`
- **weather:** `cycle_view`, `cycle_location`
- **navigation:** `next_route`, `prev_route`, `cycle_mode`, `reverse_route`, `next_destination`
- **calendar** (later): `next_day`, `prev_day`, `toggle_month_view`

Screen types declare their actions (`actions: ClassVar[dict[str, Action]]`), and config validation rejects unknown actions per type.

#### Feedback and behaviour
- **Instant acknowledgement:** the rear LED blinks on each press (the Spectra 6 board has one). It pulses until the refresh finishes.
- **Coalescing:** presses are collected for ~1.5 s before rendering. Three quick presses of "next image" skip three images but cause only one refresh.
- Context actions (next image, cycle view, reverse route) are a **manual override**. The screen and its view state stay as you set them until the next rule trigger (§5.7). View state such as `view: hourly` resets when the active rule changes.
- `min_refresh_interval` does **not** apply to button presses, because the user explicitly asked for them. A small hard minimum (~10 s) still protects the panel.
- **Optional on-screen legend** (`buttons.legend: true`): small labels next to the physical buttons ("▶ next · ↻ image · ⇄ home · ★"). Their placement is computed from `orientation` + `flip`, since the buttons sit along one edge of the panel. The exact edge in portrait is to be verified on the device.

#### Config

```jsonc
"buttons": {
  "enabled": true,
  "pins": { "A": 5, "B": 6, "C": 16, "D": 24 },
  "long_press": "1s",
  "shutdown_hold": "5s",
  "legend": false,
  "map": {
    "default":    { "A": "next_screen", "B": "refresh", "C": "prev_screen", "D": "status",
                    "A_long": "resume_schedule", "B_long": "status",
                    "C_long": "pause_toggle", "D_long": "shutdown" },
    "gallery":    { "B": "next_image", "C": "prev_image", "D": "favourite" },
    "navigation": { "B": "next_route", "C": "reverse_route", "D": "next_destination" },
    "photos":     { "D": "hide_image" }          // per-screen-name override
  }
}
```

Leaving out `map` gives the default table above.

### 6.6 Suggested extra screen types (later)

| Type | What | Effort |
|---|---|---|
| `calendar` | Agenda for today and tomorrow, or a month grid, from ICS URLs. | M |
| `today` (composite) | Weather + agenda + commute on one screen. A layout type that reuses the other screens' widgets. | M |
| `waste` | Bin collection reminders from ICS. | S |
| `art_of_the_day` | NASA APOD, Wikimedia Picture of the Day, or public-domain art from museum APIs. Implemented as a `GallerySource`, so it reuses the gallery screen. | S |
| `comic` | Random comic cover (upstream `examples/spectra6/comics`). Also a `GallerySource`. | S |
| `home_assistant` | Selected entity states over the HA REST API. | M |
| `message` | A note pushed through a file drop or a small HTTP endpoint. | S |
| `quote` / `word` | Quote of the day or word of the day. | S |

~~`html`~~ is dropped because a headless browser is not viable on a Pi Zero 2 W.

---

## 7. Architecture

```
               ┌───────────────┐
config.json ──►│  config       │  load JSONC → env interpolation → pydantic validate
               └──────┬────────┘
                      ▼
               ┌───────────────┐   wakeups    ┌──────────────┐
               │  scheduler    │◄─────────────│ buttons      │ (gpiod thread, optional)
               │ (pure logic)  │   events     └──────────────┘
               └──────┬────────┘
                      │ active screen + reason
                      ▼
               ┌───────────────┐   uses   ┌─────────────────────────────────────┐
               │  renderer     │─────────►│ providers (cached, httpx)           │
               │  screen types │          │ weather: open-meteo                 │
               │  theme/i18n   │          │ routing: tomtom here mapbox google  │
               │               │          │          osrm ors (fallback chain)  │
               │  widgets/map  │          │ tiles: stadia/osm geocode: nominatim│
               └──────┬────────┘          │ gallery sources: folder, (immich…)  │
                      │                   └─────────────────────────────────────┘
                      │ Frame (P image, or RGB photo + P overlay), size per orientation
                      ▼
               ┌───────────────┐
               │ display       │  InkyBackend (E673) │ FileBackend (PNG + palette preview)
               │ dither photos,│
               │ rotate/flip,  │
               │ hash, limit   │
               └───────────────┘
```

### 7.1 Package layout

```
inky-dash/
├── inkydash/
│   ├── __main__.py         # CLI entry
│   ├── cli.py              # run | render | schedule | validate | clean | doctor | testcard | update | prepare
│   ├── imaging.py          # shared photo pipeline (decode, fit, enhance), used by the Pi and by prepare
│   ├── config.py           # models, JSONC loading, ${ENV} interpolation
│   ├── scheduler.py        # pure: (rules, now) -> active rule, next wake-up
│   ├── app.py              # main loop, wiring, signal handling
│   ├── registry.py         # screen types, gallery sources, routing providers
│   ├── i18n/               # en.json, nl.json, fr.json …
│   ├── display/
│   │   ├── base.py         # DisplayBackend protocol
│   │   ├── inky_backend.py # wraps inky (E673); only imported on Pi
│   │   └── file_backend.py # writes out/latest.png + palette-quantised preview
│   ├── render/
│   │   ├── theme.py        # palette roles → Spectra 6 colours, fonts
│   │   ├── widgets.py      # text block, icon, sparkline, bar chart, table, header
│   │   ├── map.py          # tile stitcher, posterise, route/marker overlay
│   │   └── fonts/          # bundled OFL fonts + icon fonts
│   ├── screens/            # weather.py, gallery.py, navigation.py, status.py
│   ├── providers/
│   │   ├── cache.py        # TTL + stale fallback, disk-backed
│   │   ├── openmeteo.py
│   │   ├── geocode.py      # nominatim (v1), cached forever
│   │   ├── tiles.py
│   │   ├── routing/        # base.py (Route, capabilities), chain.py (fallback + circuit breaker),
│   │   │                   # alternatives.py (close-in-time filter, dedupe, selection match),
│   │   │                   # tomtom.py, here.py, mapbox.py, google.py, osrm.py, ors.py; irail.py (later)
│   │   └── gallery/        # base.py (GallerySource, ImageRef), folder.py
│   └── buttons.py
├── schema/config.schema.json   # generated from pydantic models
├── config.example.json
├── deploy/inky-dash.service    # systemd unit
├── scripts/install.sh, scripts/fetch-references.sh
├── inkydash/prepare/           # PC-side additions to imaging.py: smart crop, previews (optional extra: opencv)
├── tests/                      # scheduler unit tests, golden-image render tests (both orientations)
├── docs/                       # this file, hardware notes
└── reference/                  # (git-ignored) cloned Pimoroni inky repo + examples
```

### 7.2 Key technology choices

| Concern | Choice | Reason |
|---|---|---|
| Language | Python ≥ 3.11 (the Pi OS system version: 3.11 on Bookworm, newer on later releases) | It's what the `inky` library uses. CI runs the oldest and newest supported versions. |
| Rendering | **Pillow** with a small widget layer | Light enough for a Zero 2 W. Deterministic, with exact palette control. |
| Config validation | pydantic v2 | Clear errors, and the JSON Schema is generated for free. |
| Recurrence | Own day iterator + `zoneinfo` (no dateutil) | The rules are simple (day/week/month), and owning the code makes month-end, DST gaps and `until` across midnight explicit and tested. Instants are compared in UTC, because Python compares same-zone datetimes by wall clock. |
| i18n | JSON string tables + `babel` for dates and numbers | Small and simple. |
| HTTP | `httpx` with timeouts and retries | |
| Cache | Files in `state/cache/` with a TTL | Survives restarts and allows stale fallback. |
| Process | **systemd service** (`Restart=always`, `WatchdogSec=300`, `After=network-online.target`), run as the install user | Buttons need a resident process, and the scheduler already knows when to wake. The main loop pings the watchdog (`sd_notify`), so a hung process is restarted. `show()` blocks for up to ~40 s, so it runs in a worker thread while the loop keeps pinging and collecting button presses. Clock sync is handled in the app (§8), not by unit ordering. |
| Packaging | `pyproject.toml`, installed into a venv. `inky` is an extra that's only installed on the Pi. | |

### 7.3 Theme and rendering rules
- **Palette roles → Spectra 6:**

  | Role | Colour |
  |---|---|
  | `bg` | WHITE |
  | `fg` | BLACK |
  | `accent` | RED (alerts, route line) |
  | `highlight` | YELLOW (sun, best option, used sparingly since yellow on white is weak, so add a black outline or use a yellow background with black text) |
  | `info` | BLUE (rain, transit, destination) |
  | `ok` | GREEN (start marker, "on time") |

- **Dashboards are drawn in mode `P`, not RGB.** The theme owns a 6-entry palette in the order BLACK, WHITE, YELLOW, RED, BLUE, GREEN (indices 0–5), and roles resolve to those indices. This is what makes "only photos dither" true:
  - For an RGB image, the driver's `set_image` always runs Floyd–Steinberg against a palette blended from `SATURATED_PALETTE` and `DESATURATED_PALETTE` by `saturation`. No RGB value except black survives that 1:1, so RGB text and flat fills would speckle.
  - For a `P` image with exactly 6 palette colours, the driver turns dithering off and maps the indices straight to the panel.
  - The theme uses its **own sequential indices 0–5**, never the driver constants: `inky.BLUE` is 5 and `inky.GREEN` is 6 (raw panel values, index 4 is unused), which would show the wrong colours in a `P` image.
- **Display layer pipeline** (identical for both backends, so the PNG preview matches the panel):
  1. `Frame.image` in `P` → used as is. In RGB (a photo) → quantised to the 6 colours with Floyd–Steinberg against the saturation-blended palette, the same maths as the driver.
  2. `Frame.overlay`, if any, is pasted on top (caption bar, button legend).
  3. Content hash over the indices, minus `Frame.volatile` regions (§5.5).
  4. Rotate/flip, then hand the 6-colour `P` image to the driver. The file backend writes it as PNG, plus a preview coloured with the blended palette to approximate the real panel.
- Fonts: bundle *Atkinson Hyperlegible* (UI) and a condensed numeric font (for example *Barlow Condensed*), both OFL. In mode `P`, Pillow draws text without anti-aliasing. Whether these fonts stay legible that way at small sizes is **unverified** and is one of the things the M0 test card checks. The fallback is a pixel/bitmap font for small text.
- Orientation and flip are applied in the display layer. Screens only see `ctx.size`.

---

## 7.4 Installation and updates
- **First install** (on Pi OS Lite 64-bit): `scripts/install.sh`, run over SSH. It:
  - enables SPI and I2C,
  - installs the system packages,
  - clones the repo to `/opt/inky-dash` and creates the venv (aarch64 wheels from PyPI),
  - creates `/etc/inky-dash/config.json` from the example, plus `.env`, and sets `"$schema"` to the absolute path `/opt/inky-dash/schema/config.schema.json` so editor autocompletion works from `/etc`,
  - creates the photo folder `~/photos` for the user who runs the installer. The service runs as that user, so `scp` uploads need no extra permissions. Paths in the config expand `~`. (Pi OS no longer has a default `pi` user.)  - installs and enables the systemd unit,
  - runs `inky-dash doctor`.
- **Updates:** `inky-dash update` does `git fetch`, then checks out the latest release tag (or `main` with `--to main`). Then it:
  1. runs `pip install`,
  2. **validates the current config** against the new schema,
  3. runs a **smoke render** of the `status` screen to the file backend,
  4. restarts the service.

  If step 2 or 3 fails, it checks out the previous commit again, **runs `pip install` again** so the dependencies match that commit, and keeps the old service running. The smoke render only proves the app starts and can draw. It doesn't cover the other screens.
- **Development deploys:** develop on the PC, push to GitHub, `git pull` on the Pi (editable install). The steps are in the [README](../README.md#getting-it-on-the-pi).
- **Editing config:** `nano` over SSH, or edit on the PC (with JSON Schema autocompletion) and copy it over with `scp`. VS Code Remote-SSH is not used: its server is too heavy for the Zero's 512 MB. The config watcher applies valid changes automatically. Invalid ones show up on the `status` screen.
- **Releases:** semantic version tags (`v0.1.0` …) with a CHANGELOG. Config schema changes that break existing configs bump the minor version (while < 1.0) and ship a migration note.

---

## 8. Failure handling

| Failure | Behaviour |
|---|---|
| Provider down or timeout | Render with **cached data** plus a small "⚠ data from 06:30" badge. With no cache, render the screen's own "unavailable" state. |
| Routing provider fails for one mode | Drop that mode from the comparison and note it in small text. |
| Map tiles unavailable | Draw the route on a blank background (still useful), or hide the map and widen the info panel. |
| Gallery source empty or unreachable | Use pre-rendered cached images. If there are none, show a "no images" placeholder. |
| Exception in a screen's render | Log the traceback and keep the previous image. After N consecutive failures, show `status`. |
| Invalid config at startup or reload | Show `status` with the validation errors. Keep running the last valid config if there is one. |
| Config file changed | Watch the file's mtime, reload and validate, and apply only if valid. |
| No RTC / clock not synced | At startup the app waits up to 2 min for NTP (`/run/systemd/timesync/synchronized`). If there's still no sync (Wi-Fi down), it carries on with the clock it has, shows a small "⚠ clock not synced" badge, and re-evaluates the schedule as soon as the sync arrives. It never blocks forever. (`time-sync.target` alone doesn't wait for a real sync unless `systemd-time-wait-sync` is enabled, and with it enabled it waits without limit, so the unit doesn't depend on it.) |
| Process hangs (stuck busy pin, blocked call) | The systemd watchdog restarts the service after 5 min without a ping (§7.2). |

---

## 9. CLI

```
inky-dash run        [--config PATH] [--driver file|inky]   # the service
inky-dash render     SCREEN [--now 2026-11-16T07:20] [--out out.png] [--preview] [--orientation portrait]
inky-dash schedule   [--from DATE] [--days 7]               # prints a timeline of what shows when
inky-dash validate   [--config PATH]                        # also emits schema/config.schema.json
inky-dash clean      [--cycles N]                           # anti-ghosting cycle
inky-dash testcard   [--out out.png]                        # palette, font sizes, line widths, map samples: for judging the real panel
inky-dash doctor                                            # SPI/I2C, display, API keys, provider usage today/month
inky-dash update     [--to TAG|main]                        # git pull → pip install → validate config → smoke render → restart; rollback on failure
inky-dash prepare    FOLDER --out DIR [--preview] [--orientation landscape|portrait|both]   # PC-side photo prep
```

`--preview` writes a PNG quantised to the Spectra 6 palette, so you see on your PC roughly what the e-paper will show.

---

## 10. Testing

- **Scheduler:** table-driven unit tests covering DST transitions, the 31st in short months, overlapping rules, `until` fallback and ties.
- **Screens:** golden-image tests for **both orientations** and at least 2 languages, with recorded provider fixtures and a fixed `now`. Text rasterises differently per FreeType build, so the goldens are generated and compared exactly **on Linux CI only**, with Pillow pinned. On Windows and macOS the same tests only check that rendering succeeds and stays palette-pure.
- **Display layer:** a `P` frame reaches the backend with exactly 6 colours and unchanged indices; an RGB frame is dithered and its overlay is not; volatile regions don't change the content hash.
- **Providers:** contract tests against recorded HTTP responses (`respx`). The gallery `folder` source is tested on a temporary directory.
- **Map:** Web Mercator math and bbox/zoom fitting are unit tested. The tile stitcher is tested with fixture tiles.
- **Config:** the example config validates, plus negative cases.
- **Performance budget on the Zero 2 W:** each render < 10 s (excluding network), peak RSS < 150 MB. Measured by `inky-dash render --profile`.
- CI: GitHub Actions on Linux runs ruff, mypy and pytest using the file backend.

---

## 11. Milestones

| # | Deliverable |
|---|---|
| M0 | Repo skeleton, pyproject, FileBackend, theme/i18n/widgets, `status` screen, CLI `render`. **Hardware spike:** a minimal InkyBackend and `inky-dash testcard` on the real panel, to settle before any layout work: `P`-mode frames show undithered, small-font legibility, yellow on white, thin lines, `stadia-toner` vs `stadia-toner-lite` vs `osm` after thresholding, real refresh time, and whether ghosting needs the clean cycle. |
| M1 | Config models + JSON Schema, scheduler (rotation, `skip_on`/holidays/off days, precedence) + `schedule` CLI, main loop with FileBackend |
| M2 | Full InkyBackend on the Zero 2 WH (worker thread, watchdog, clock-sync fallback), systemd unit, `install.sh` + `update` with rollback, `doctor`, hash/rate-limit, `quiet` screen + clean cycle |
| M3 | `weather` screen: core layout + priority flex slots (rain nowcast, UV, pollen, air quality), MeteoAlarm warnings banner, both orientations |
| M4 | `gallery` screen: `GallerySource` + `folder` source, shuffle history, on-device fallback pipeline, pre-render cache; **`inky-dash prepare`** on the PC (smart crop, e-paper tuning, previews) |
| M5a | `navigation` core: `Route` model, provider chain, close-in-time alternatives + `next_route` selection, Nominatim geocoding, map renderer with jam colouring (Stadia/OSM tiles), **traffic alert takeover**, **TomTom + OSRM** adapters |
| M5b | Further adapters: **HERE, Mapbox, Google, ORS**, each with recorded-fixture contract tests. Later: transit (Google / iRail) |
| M6 | Buttons: short/long press, context actions, coalescing, LED feedback, safe shutdown, legend |
| M7 | Extras: `calendar`, `today`, `art_of_the_day` (as a gallery source), remote gallery sources (Immich…) |

---

## 12. Decisions and open questions

### Decided
- **Panel:** Spectra 6 (E673), 7.3".
- **Computer:** Pi Zero 2 WH, running Pi OS Lite 64-bit.
- **Navigation:** car with **live traffic** (TomTom first in the chain), comparing against usual times, with jams on the map and a departure forecast. Keyless OSRM is used only for bike/walk or as a labelled fallback.
- **Gallery:** local folder behind the `GallerySource` abstraction.
- **Global settings:** orientation and locale. Defaults are **landscape** (primary layout; portrait is secondary) and **English**.
- **Scope:** personal use on one device. The multiple providers, tile styles, languages and both orientations stay, as wanted options.
- **Rendering:** dashboards in mode `P` with theme palette indices; photos in RGB, dithered in the display layer; screens return a `Frame` (§6, §7.3).
- **Map tiles:** `stadia-toner`, `stadia-toner-lite` and `osm` are supported. `auto` picks `stadia-toner` when a key is set.
- **Navigation providers:** multiple adapters in a fallback chain per mode. Alternatives are kept when close in time and can be switched with a button.
- **Schedule:** rules can rotate between screens (`screens` + `rotate_every`). `skip_on` / `only_on` handle holidays (offline, per country) and `off_days`. Precedence is traffic alert > manual override > schedule, and button A dismisses an alert.
- **Night:** the `quiet` screen type runs the clean cycle first, then shows one gallery image with no refreshes until the next rule.
- **Weather:** fixed layout with priority flex slots: rain nowcast, UV, pollen, air quality, plus a warnings banner.
- **Photos:** prepared on the PC (`inky-dash prepare`), uploaded over SSH/SCP only, with an on-device fallback for unprepared photos.
- **Install and updates:** `install.sh` + `inky-dash update` (git tags, validate, smoke test, rollback).

### Open
1. **Buttons** (§6.5): the draft mapping is to be revisited later.
2. **Provider keys:** which traffic keys will you create (TomTom / HERE / Mapbox / Google)? This only sets the order of the default chain. Confirm each free allowance at sign-up.
3. **Route names:** the automatic "via E40" label is implemented, plus an optional `route_names` map on the navigation screen (`{"E40": "Highway"}`) to rename known routes.
4. **To settle on the M0 test card:** small-font legibility without anti-aliasing, the best tile style, and whether the clean cycle is needed (and how many cycles).
5. **Button edge:** the legend assumes buttons A–D run top to bottom along the left edge of the panel buffer (`App.BUTTON_Y`). Check on the device, and the portrait rotation direction with it.

---

## 13. Implementation status (inky-dash 0.1)

Everything in milestones M0–M6 is implemented and covered by tests on the PC (150 tests, `pytest`). Not yet verified on the real panel or Pi.

| Area | State |
|---|---|
| Config, JSON Schema, scheduler, `schedule` CLI | Done. Scheduler tests cover the §5 worked example, DST gap and fall-back, month-end clamping, `until` across midnight, `skip_on`/`only_on`, ties and rotation. |
| Display layer | Done: `Frame`, P-mode pipeline, own dithering (tested equal to the driver's maths), content hash with volatile regions, rotate/flip, File and Inky backends, clean cycle. A test replays the driver's `set_image` logic to prove P frames pass undithered. |
| Service loop | Done: precedence alert > override > schedule, render-on-key-change, rate limit (3 min scheduled, 10 s buttons), deferral, worker thread for `show()`, quiet clean per slot, config hot-reload with fallback, clock-sync wait + badge, systemd notify + watchdog, pause. |
| `weather` | Done: three views, both orientations, flex slots, MeteoAlarm banner, stale badge. |
| `gallery` | Done: folder source, prepared-pair detection, slot picks, all five orders, buttons, caption overlay, pre-render cache + prefetch. |
| `navigation` | Done: chain with circuit breaker, close-in-time alternatives, sticky selection, map with jam colouring and fractional zoom, leave-by, forecast, alert polling, `route_names`. |
| Routing adapters | **TomTom and OSRM** are the core and OSRM was exercised live. **HERE, Mapbox, Google, ORS** are implemented from their API docs and tested against hand-made fixtures, **not yet against live responses**: record a real response for each when you create a key and add it as a fixture. |
| Buttons + LED | Done (gpiod both-edge reading, long press, 5 s hold for shutdown, coalescing, LED pulse). Untested on hardware. |
| `prepare` | Done: shared pipeline, OpenCV face crop when installed, edge-density saliency otherwise, previews, sidecars. `--pair-portraits` is not implemented. |
| Install, update, CI | `install.sh`, systemd unit, `update` with rollback, GitHub Actions (ruff + pytest on 3.11 and 3.13). mypy is not in CI yet. |
| Later (M7) | `calendar`, `today`, remote gallery sources, transit. |

Glyphs: the bundled fonts have no ⚠ → ↑ ↓ ★ ′, so warning marks, arrows and stars are drawn as shapes.
