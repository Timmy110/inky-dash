# AGENTS.md

Instructions for AI coding agents working on inky-dash: a scheduled e-paper dashboard for the Pimoroni Inky Impression 7.3" (Spectra 6, E673) on a Raspberry Pi Zero 2 W.

- Changing what the dashboard shows (screens, schedule, locations, buttons, API keys): read [docs/agents/configuring.md](docs/agents/configuring.md).
- Design and behaviour: [docs/DESIGN.md](docs/DESIGN.md). Providers and API keys: [docs/providers.md](docs/providers.md). Panel notes: [docs/hardware/inky-impression-7.3.md](docs/hardware/inky-impression-7.3.md).

## Development (on the PC)

```sh
python -m venv .venv && .venv/bin/pip install -e ".[dev]"     # Windows: .venv\Scripts\pip
pytest -q && ruff check inkydash tests                         # both must pass before a commit
inky-dash render weather --preview                             # writes out/weather.png + preview
inky-dash run --driver file                                    # full loop, writes out/latest.png
```

- Look at rendered PNGs (out/) to judge layout changes; the `-preview.png` shows panel colours.
- Every screen needs a landscape (800×480) and a portrait (480×800) layout.
- Screen modules must not import `inkydash.config` at module level (circular import via the screen registry).
- `config.json`, `.env`, `state/`, `out/` are git-ignored. Never commit them: `.env` holds API keys.
- `inky-dash validate` regenerates `schema/config.schema.json`. Commit that file only when a config model changed.

## The Pi

| | |
|---|---|
| Host | `admin@192.168.68.231` (hostname `pi-zero-2`), SSH key access |
| OS | Raspberry Pi OS Lite 64-bit, Debian 13 (trixie), Python 3.13 |
| Project | `~/projects/inky-dash` (a git clone of this repo), venv in `.venv` |
| Config / keys | `~/projects/inky-dash/config.json`, `~/projects/inky-dash/.env` |
| State | `/var/lib/inky-dash` under systemd; `./state` when run by hand |
| Photos | `~/projects/inky-dash/photos` (contents git-ignored) |
| Service | `inky-dash.service`, unit in `/etc/systemd/system/` |

This Pi does **not** use the `/opt/inky-dash` + `/etc/inky-dash` layout that `scripts/install.sh` and the README describe. It uses `~/projects/inky-dash` for code, config and keys. Keep it that way unless the user asks otherwise.

### Sudo

`admin` needs a password for sudo, which an agent can't type over SSH. Passwordless sudo is allowed only for:

```
/usr/bin/systemctl poweroff, /usr/bin/systemctl reboot, /usr/bin/systemctl restart inky-dash
```

For anything else (apt, raspi-config, `systemctl enable/stop`, editing `/boot` or `/etc`):
1. Write the steps to a script on the Pi (`ssh pi 'cat > ~/x.sh' <<'EOF' … EOF`), check it with `bash -n`.
2. Ask the user to run `sudo bash ~/x.sh`.

Don't hand the user long one-liners to paste: they wrap in the terminal and break at the wrap.

Use `sudo -n …` when you try sudo yourself, so it fails instead of hanging.

### Setting up a fresh Pi (the `~/projects` layout)

What was done on this Pi, in order. Each step was needed.

1. **OS**: Raspberry Pi OS Lite 64-bit, SSH enabled, user `admin`, Wi-Fi, timezone `Europe/Brussels`.
2. **Code** (as `admin`):
   ```sh
   mkdir -p ~/projects && git clone https://github.com/Timmy110/inky-dash ~/projects/inky-dash
   ```
3. **Root steps** (the user runs these as a script):
   ```sh
   apt-get install -y python3-dev           # spidev (part of the [inky] extra) compiles from source and needs Python.h
   raspi-config nonint do_spi 0
   raspi-config nonint do_i2c 0             # needed: inky.auto identifies the panel from its EEPROM over I2C
   echo 'dtoverlay=spi0-0cs' >> /boot/firmware/config.txt   # the driver toggles chip select itself
   echo "admin ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff, /usr/bin/systemctl reboot, /usr/bin/systemctl restart inky-dash" > /etc/sudoers.d/inky-dash
   chmod 440 /etc/sudoers.d/inky-dash && visudo -c -f /etc/sudoers.d/inky-dash
   P=/home/admin/projects/inky-dash
   sed -e "s/@USER@/admin/g" -e "s#/opt/inky-dash#$P#g" \
       -e "s#INKYDASH_CONFIG=/etc/inky-dash/config.json#INKYDASH_CONFIG=$P/config.json#" \
       $P/deploy/inky-dash.service > /etc/systemd/system/inky-dash.service
   systemctl daemon-reload
   reboot
   ```
   `admin` must be in the `gpio`, `spi` and `i2c` groups (default on Raspberry Pi OS).
4. **Python** (as `admin`; takes a few minutes on the Zero):
   ```sh
   cd ~/projects/inky-dash
   python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip
   .venv/bin/pip install -e ".[inky]"
   cp -n config.example.json config.json
   touch .env && chmod 600 .env
   ```
5. **Check**: `INKYDASH_STATE=/tmp/idd .venv/bin/inky-dash doctor` should show SPI, I2C and `display: inky.inky_e673 (800, 480)`.
6. **Panel test**: `.venv/bin/inky-dash testcard --driver inky`, about 35 s. Ask the user what the panel shows: you can't see it.
7. **Loop test**: `timeout 120 .venv/bin/inky-dash run --driver inky` and read the log.
8. **Enable** (user, sudo): `sudo systemctl enable --now inky-dash`.

### Deploying a change

Commit and push from the PC, then on the Pi:

```sh
cd ~/projects/inky-dash && git pull --ff-only
.venv/bin/pip install -e ".[inky]"        # only when dependencies changed
sudo systemctl restart inky-dash          # allowed without a password
```

`inky-dash update [--to main]` does the same, with a config check, a smoke render and a rollback. It works from `~/projects`.

If you copied a file to the Pi with `scp` to test it, run `git checkout -- <file>` before pulling.

### Watching and testing on the Pi

```sh
journalctl -u inky-dash -f                                  # service log
systemctl is-active inky-dash
.venv/bin/inky-dash render <screen> --preview               # file only, doesn't touch the panel
scp admin@192.168.68.231:projects/inky-dash/out/<screen>-preview.png .   # then look at it
```

- Only one process may drive the panel. Stop the service (needs the user's sudo) before `--driver inky` commands, or use `--driver file`.
- A full refresh takes 30–35 s and wears the panel. Don't loop panel refreshes when testing: render to file and look at the PNG.
- `Busy Wait: Timed out after 32.00s` warnings from the `inky` library are harmless. The busy pin works, and a refresh just takes slightly longer than the library's 32 s timeout.
- The Pi has 512 MB of RAM and the service has `MemoryMax=300M`. Don't run heavy tools on it (no VS Code Remote-SSH).

### Known issues

- The status screen shows the Wi-Fi network name as `?` on trixie, which uses NetworkManager.
