# inky-dash

A scheduled e-paper dashboard for the Pimoroni **Inky Impression 7.3" (Spectra 6)** on a **Raspberry Pi Zero 2 WH**. It shows weather, a photo gallery, commute/navigation info and more, according to a JSON schedule.

> Status: v0.1 implemented and tested on the PC (file backend). Not yet run on the panel: start with the test card (step 3 below).

- Design: [docs/DESIGN.md](docs/DESIGN.md)
- Data providers (free tiers, pricing, setup): [docs/providers.md](docs/providers.md)
- Hardware notes: [docs/hardware/inky-impression-7.3.md](docs/hardware/inky-impression-7.3.md)
- Example config: [config.example.json](config.example.json), JSON Schema: [schema/config.schema.json](schema/config.schema.json)
- Upstream Pimoroni library and examples (git-ignored): `./scripts/fetch-references.sh` → `reference/inky/`

## Screens

| Type | Shows |
|---|---|
| `weather` | Current conditions, today, 12-hour strip, 5 days. Flex slots for rain in the next 2 h, UV, pollen, air quality, sunrise/sunset. Official warnings banner (MeteoAlarm). Button B cycles overview → hourly → week. |
| `navigation` | Live travel time vs usual, close-in-time alternative routes (button B switches), jams on the map, incidents, leave-by time and a +15/+30 min forecast. Traffic alert takeover. |
| `gallery` | Photos from a folder: shuffle without repeats, sequential, random, on-this-day, favourites. Optional caption. |
| `status` | IP, Wi-Fi, uptime, disk, provider health, config errors. |
| `quiet` | Night mode: clean the panel, show one frame of another screen, no refreshes. |

## Develop on the PC

```sh
python -m venv .venv
.venv/bin/pip install -e ".[dev]"          # Windows: .venv\Scripts\pip
cp config.example.json config.json         # then edit; API keys go in .env (see docs/providers.md)

inky-dash validate                         # checks the config, regenerates schema/config.schema.json
inky-dash schedule --days 2                # what shows when
inky-dash render weather --preview         # out/weather.png + a panel-coloured preview
inky-dash render --all --orientation portrait
inky-dash render weather --view view=hourly --now 2026-11-16T07:20 --language nl
inky-dash render to-work --now 2026-11-16T07:20
inky-dash run --driver file                # the full service loop, writing out/latest.png
inky-dash testcard                         # palette, fonts, lines, thresholds
inky-dash prepare D:\Photos\Holiday --out prepared --preview   # needs: pip install -e ".[prepare]"
pytest -q && ruff check inkydash tests
```

The state (caches, gallery history, provider usage) goes to `./state`, or `$INKYDASH_STATE`.

## Getting it on the Pi

### 1. Install the OS

1. In Raspberry Pi Imager, choose **Raspberry Pi OS Lite (64-bit)**.
2. In the Imager settings, set:
   - hostname `inkydash`,
   - your own username (there is no default `pi` user any more),
   - Wi-Fi,
   - timezone `Europe/Brussels`,
   - SSH enabled, ideally with your public key.
3. Boot the Pi and connect: `ssh <user>@inkydash.local`.
4. Update and install git:

   ```sh
   sudo apt update && sudo apt full-upgrade
   sudo apt install git
   ```

### 2. Check the panel

Before running any inky-dash code, run two of Pimoroni's examples. If the stripes show, the wiring, SPI and the panel are fine.

```sh
sudo raspi-config nonint do_spi 0 && sudo raspi-config nonint do_i2c 0 && sudo reboot
git clone https://github.com/pimoroni/inky && cd inky && ./install.sh
source ~/.virtualenvs/pimoroni/bin/activate
python examples/identify.py            # should report the 7.3" Spectra 6 (E673)
python examples/spectra6/stripes.py
```

### 3. Install inky-dash

```sh
sudo mkdir -p /opt/inky-dash && sudo chown $USER /opt/inky-dash
git clone https://github.com/Timmy110/inky-dash /opt/inky-dash
/opt/inky-dash/scripts/install.sh
```

`install.sh` enables SPI/I2C, creates the venv (editable install), writes `/etc/inky-dash/config.json` and `/etc/inky-dash/.env`, creates `/opt/inky-dash/photos`, allows passwordless `systemctl poweroff/reboot/restart inky-dash` for the shutdown button and updates, installs the systemd unit and runs `inky-dash doctor`.

Then, **before any layout work**, judge the real panel with the test card:

```sh
cd /opt/inky-dash
.venv/bin/inky-dash testcard --driver inky                       # palette, font sizes, yellow, lines
.venv/bin/inky-dash testcard --driver inky --location home --config /etc/inky-dash/config.json   # + map tile styles
```

Edit `/etc/inky-dash/config.json`, add keys to `/etc/inky-dash/.env`, then `sudo reboot`. Watch it with `journalctl -u inky-dash -f`.

### 4. Develop on the PC, deploy with git

Code is written and rendered on the PC with the file backend, then pushed to GitHub. The Pi only pulls.

```
PC   edit → inky-dash render … → pytest → git commit → git push
Pi   git pull → restart the service
```

Each deploy:

```sh
cd /opt/inky-dash && git pull
.venv/bin/pip install -e ".[inky]"      # only when dependencies changed
sudo systemctl restart inky-dash
```

Or `inky-dash update` (latest release tag, or `--to main`): it validates your config against the new version and does a smoke render first, and rolls back (code and dependencies) if either fails.

### Editing on the Pi

Don't use VS Code Remote-SSH on the Pi Zero 2 W: its server is too heavy for 512 MB of RAM. Edit the config with `nano` over SSH, or edit it on the PC and copy it over with `scp`. The service picks up a valid change within 30 s; an invalid one shows on the status screen and the old config keeps running. Photos go over `scp` or WinSCP to the project's `photos/` folder. Its contents are git-ignored.

## Buttons (default)

| | Short | Long (global) |
|---|---|---|
| A | next screen (dismisses a traffic alert) | resume schedule |
| B | screen action: next image / weather view / next route; else refresh | status |
| C | screen action: previous image / location / reverse route; else previous screen | pause updates |
| D | favourite / refresh / next destination; else status | hold 5 s: shut down |

Remap in `buttons.map` (per screen type or screen name). `inky-dash validate` rejects actions a screen doesn't have.
