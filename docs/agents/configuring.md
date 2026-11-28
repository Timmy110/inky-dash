# Configuring inky-dash (for AI agents)

How to write or change `config.json`: what the dashboard shows, when, and where. For installing and deploying, see [AGENTS.md](../../AGENTS.md).

## Where the config lives

| | Path |
|---|---|
| Live config on the Pi | `admin@192.168.68.231:~/projects/inky-dash/config.json` |
| API keys on the Pi | `~/projects/inky-dash/.env` (`chmod 600`) |
| Working copy on the PC | `config.json` in the repo root (git-ignored) |
| Starting point | [`config.example.json`](../../config.example.json) |

`config.json` and `.env` are never committed. The config may also be `config.jsonc`, which allows comments.

## Sources of truth

Check these before writing an option. Unknown keys are **rejected**: every model is strict (`extra="forbid"`).

- [`schema/config.schema.json`](../../schema/config.schema.json): the full JSON Schema, generated from the models.
- [`inkydash/config.py`](../../inkydash/config.py): global settings, locations, providers, buttons, rules and cross-checks.
- [`inkydash/models.py`](../../inkydash/models.py): `when` (schedule triggers).
- `inkydash/screens/<type>.py`, class `*Options`: the options of each screen type.
- [docs/DESIGN.md](../DESIGN.md) §4 (config), §5 (scheduling semantics), §6 (screen types and buttons).
- [docs/providers.md](../providers.md): which API keys exist, free tiers and limits.

## Workflow

1. **Ask what the user wants** if it's unclear: locations (address or lat/lon), which screens, what times, weekdays vs weekends, language.
2. **Get the current config** from the Pi rather than editing the example:
   ```sh
   scp admin@192.168.68.231:projects/inky-dash/config.json config.json
   ```
3. **Edit** `config.json` on the PC.
4. **Validate and preview on the PC**:
   ```sh
   inky-dash validate                                   # precise errors, e.g. schedule[2].when.at
   inky-dash schedule --days 7                          # what shows when; check weekdays, weekends, holidays
   inky-dash render --all --preview                     # look at out/*-preview.png
   inky-dash render to-work --now 2026-11-16T07:20      # test a screen at a specific time
   ```
   `validate` also rewrites `schema/config.schema.json`. Don't commit that unless a model changed.
5. **Deploy**:
   ```sh
   scp config.json admin@192.168.68.231:projects/inky-dash/config.json
   ssh admin@192.168.68.231 'cd ~/projects/inky-dash && .venv/bin/inky-dash validate && journalctl -u inky-dash -n 20 --no-pager'
   ```
   The service checks the file's modification time every 30 s and reloads it: look for `config reloaded` in the log. An invalid config is shown on the status screen, and the previous config keeps running. No restart is needed.
6. **API keys** go in `.env` on the Pi, never in `config.json`. The config refers to them as `"${TOMTOM_API_KEY}"`. A string that is only an unset `${VAR}` becomes `null`, and that provider is skipped. Append keys without printing them back, and don't echo `.env` contents into the chat.

## Shape at a glance

```jsonc
{
  "$schema": "./schema/config.schema.json",
  "timezone": "Europe/Brussels",                       // IANA name; all schedule times are local
  "display":  { "orientation": "landscape", "flip": false, "refresh_speed": "normal", "min_refresh_interval": "3m" },
  "locale":   { "language": "en", "units": "metric", "time_format": "24h" },   // en, nl, fr, de …
  "holidays": { "country": "BE" },                     // drives skip_on/only_on "holiday"
  "off_days": ["2026-12-24", "2026-12-28/2027-01-02"], // drives "off"
  "locations": { "home": { "lat": 50.85, "lon": 4.35, "label": "Home" },
                 "work": { "address": "Korenmarkt, Gent", "label": "Office" } },  // geocoded once, cached
  "providers": { … },                                  // routing chains, API keys, map tiles
  "screens":   { "<name>": { "type": "weather" | "navigation" | "gallery" | "status" | "quiet", … } },
  "schedule":  [ { "name": "…", "when": { … }, "screen": "<name>" } ],
  "default_screen": "<name>",
  "buttons":   { "enabled": true, "map": { … } }
}
```

A relative gallery `path` is relative to the config file's folder, so `"photos"` is the repo's `photos/` folder. `~` is expanded.

Durations are `\d+[smhd]` (`"30m"`, `"1h"`). Times are `"HH:MM"`.

## Screen types

| Type | Key options (see the `*Options` class for all of them) |
|---|---|
| `weather` | `location` (a key, or a list of keys: button C cycles them); `blocks` (`warnings`, `rain_nowcast`, `uv`, `pollen`, `air_quality`, `sun`: `false` hides one); `pollen_types`; `uv_threshold`; `warnings_region` (substring of the MeteoAlarm area, e.g. `"Brussels"`); `view` (`overview`/`hourly`/`daily`); `theme` (`auto`/`light`/`dark`; `auto` is dark between sunset and sunrise) |
| `navigation` | `from`, `to` (a key or a list of keys); `modes` (`car`/`bike`/`walk`/`transit`); `arrive_by` or `depart_at`; `alternatives` (`{max, within, within_pct}` or `false`); `forecast` (`["+15m","+30m"]`); `delay_threshold`; `map` (`{padding, show_alternatives}` or `false`); `route_names` (rename via-labels); `alert` (see below) |
| `gallery` | `source` (`{"kind":"folder","path":"photos","rescan":"1h"}`) or `sources`; `order` (`shuffle`/`sequential`/`random`/`on_this_day`/`favourites`); `fit` (`auto`/`cover`/`contain`); `saturation` 0–1 (0.6 suits the panel); `caption` (`none`/`filename`/`date`/`sidecar`/`auto`) |
| `status` | no options |
| `quiet` | `show` (another screen, rendered once; omit for a blank panel); `clean` (`"before"`/`true`/`false`: anti-ghosting cycle); `clean_cycles` 1–5 |

Navigation `alert` takes over the panel when traffic is bad, even over other rules:
```json
"alert": { "watch": { "every": "week", "days": ["mon","tue","wed","thu","fri"], "at": "06:30", "until": "08:45", "skip_on": ["holiday","off"] },
           "poll": "15m", "delay_over": "10m" }
```

## Scheduling: how a rule wins

Read DESIGN.md §5 before writing a non-trivial schedule. In short:

- A rule is **live** from its `at` trigger until its `until`. Without an `until`, it stays live until another rule triggers.
- The live rule with the **latest trigger** is shown. On a tie, the more specific rule wins (`month` > `week` > `day`), then the one listed first.
- `default_screen` shows only when no rule is live. If some rule has no `until`, that never happens after the first trigger.
- `until` at or before `at` means the next day (`"at": "22:00", "until": "06:00"`).
- `every`: `day`, `week` (+ `days`) or `month` (+ `day`, `-1` = last day). `at` can be a list of times.
- `refresh`: re-render while live (minimum `1m`). The panel only updates if the content changed and `min_refresh_interval` has passed.
- `skip_on` / `only_on`: `["holiday", "off"]`, judged on the date of the trigger.
- A rotation is `"screens": [...]` with `"rotate_every"` (minimum `1m`). A `quiet` screen can't be part of a rotation.
- A rule needs exactly one of `screen` / `screens`. Rule names must be unique.

Precedence: traffic alert > button override > schedule.

Always run `inky-dash schedule --days 7` and check the output against what the user asked for, including a weekend and, if relevant, a public holiday (`--from 2026-12-25`).

## Buttons

`buttons.map` has contexts `default`, a screen type (`gallery`, `weather`, `navigation`) or a screen name. Keys are `A`..`D` and `A_long`..`D_long`. Global actions: `next_screen`, `prev_screen`, `refresh`, `resume_schedule`, `status`, `pause_toggle`, `clean`, `shutdown`, `reboot`, `led_toggle`, `none`, `goto:<screen>`. Screen actions: `next_image`, `prev_image`, `favourite` and `hide_image` (gallery); `cycle_view` and `cycle_location` (weather); `next_route`, `prev_route`, `reverse_route` and `next_destination` (navigation); `rescan` (status). `validate` rejects an action that the screen in that context doesn't have.

## Panel-friendly choices

The panel is a Spectra 6 e-paper display: a full refresh takes about 30 s, flashes, and wears the panel over time.

- Keep `refresh` at 10 m or more for most screens. The default `min_refresh_interval` is `3m`. Don't lower it below `2m`.
- Prefer one `quiet` rule at night, with `"clean": "before"`, so the panel gets an anti-ghosting cycle daily.
- Navigation and other keyed providers have daily call limits (`max_daily_calls`). A commute screen refreshing every 10 m for 90 minutes uses about 10 calls a day, plus alert polls. Check the limits in docs/providers.md and keep the total under them.
- Every keyed provider is optional. Without keys, routing falls back to OSRM (no live traffic) and map tiles to OSM.

## Common mistakes

- Referring to a location or screen key that doesn't exist (`validate` catches this).
- Using `days` with `every: day`, or `day` with `every: week`.
- Forgetting that a rule without `until` hides `default_screen` permanently.
- Putting an API key straight into `config.json`.
- Editing the example config or the schema file instead of `config.json`.
