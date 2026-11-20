#!/usr/bin/env bash
# Clone/update upstream Pimoroni reference material into reference/ (git-ignored).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p reference
if [ -d reference/inky/.git ]; then
  git -C reference/inky pull --ff-only
else
  git clone --depth 1 https://github.com/pimoroni/inky.git reference/inky
fi
echo "Pimoroni inky library + examples in reference/inky (see docs/hardware/inky-impression-7.3.md)"
