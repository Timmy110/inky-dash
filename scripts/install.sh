#!/usr/bin/env bash
# First install on Raspberry Pi OS Lite (64-bit). Run as your normal user over SSH:
#   git clone https://github.com/Timmy110/inky-dash /opt/inky-dash   (see README)
#   /opt/inky-dash/scripts/install.sh
set -euo pipefail

DEST=/opt/inky-dash
REPO_URL=${REPO_URL:-https://github.com/Timmy110/inky-dash}
USER_NAME=${SUDO_USER:-$USER}
USER_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6)
ETC=/etc/inky-dash

if [ "$(id -u)" -eq 0 ] && [ -z "${SUDO_USER:-}" ]; then
  echo "Run this as your normal user (it uses sudo where needed)." >&2
  exit 1
fi

step() { printf '\n==> %s\n' "$*"; }

step "Enabling SPI and I2C"
if command -v raspi-config >/dev/null; then
  sudo raspi-config nonint do_spi 0
  sudo raspi-config nonint do_i2c 0
else
  echo "raspi-config not found: enable SPI and I2C yourself" >&2
fi

step "Installing system packages"
sudo apt-get update -q
sudo apt-get install -y -q git python3-venv python3-dev

step "Getting the code into $DEST"
if [ ! -d "$DEST/.git" ]; then
  sudo mkdir -p "$DEST"
  sudo chown "$USER_NAME:" "$DEST"
  git clone "$REPO_URL" "$DEST"
else
  echo "already cloned"
fi

step "Creating the virtual environment"
[ -d "$DEST/.venv" ] || python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install -q --upgrade pip
"$DEST/.venv/bin/pip" install -q -e "$DEST[inky]"

step "Config in $ETC"
sudo mkdir -p "$ETC"
sudo chown "$USER_NAME:" "$ETC"
if [ ! -f "$ETC/config.json" ]; then
  # absolute $schema so editors find it from /etc; absolute photo folder, because relative
  # gallery paths resolve against the config's folder (/etc/inky-dash)
  sed -e "s#\"./schema/config.schema.json\"#\"$DEST/schema/config.schema.json\"#" \
      -e "s#\"path\": \"photos\"#\"path\": \"$DEST/photos\"#" \
    "$DEST/config.example.json" > "$ETC/config.json"
  echo "created $ETC/config.json from the example: edit it"
fi
if [ ! -f "$ETC/.env" ]; then
  cat > "$ETC/.env" <<'EOF'
# API keys (see docs/providers.md). Leave empty to skip a provider.
TOMTOM_API_KEY=
HERE_API_KEY=
MAPBOX_TOKEN=
GOOGLE_MAPS_API_KEY=
ORS_API_KEY=
STADIA_API_KEY=
EOF
  chmod 600 "$ETC/.env"
fi

step "Photo folder"
mkdir -p "$DEST/photos"

step "Permissions: GPIO/SPI/I2C groups, passwordless poweroff/reboot/restart"
sudo usermod -aG gpio,spi,i2c "$USER_NAME"
SUDOERS=/etc/sudoers.d/inky-dash
echo "$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff, /usr/bin/systemctl reboot, /usr/bin/systemctl restart inky-dash" \
  | sudo tee "$SUDOERS" >/dev/null
sudo chmod 440 "$SUDOERS"
sudo visudo -c -f "$SUDOERS" >/dev/null

step "systemd service"
sed "s/@USER@/$USER_NAME/g" "$DEST/deploy/inky-dash.service" | sudo tee /etc/systemd/system/inky-dash.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable inky-dash

step "Checking the installation"
INKYDASH_CONFIG="$ETC/config.json" INKYDASH_STATE="${INKYDASH_STATE:-/tmp/inky-dash-doctor}" \
  "$DEST/.venv/bin/inky-dash" doctor || true

cat <<EOF

Done. Next:
  1. Edit $ETC/config.json and put API keys in $ETC/.env
  2. sudo reboot            (SPI/I2C and group changes need it; the service starts on boot)
  3. journalctl -u inky-dash -f    to watch it
EOF
