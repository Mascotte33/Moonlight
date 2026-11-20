#!/usr/bin/env bash
# Sets up Moonlight on Ubuntu/Debian (X11): system packages, Arduino access,
# the Python environment and the map from the Tibia client. Safe to run again.
set -euo pipefail
cd "$(dirname "$0")"

echo "== System packages"
sudo apt-get update -qq
sudo apt-get install -y python3-venv python3-tk tesseract-ocr

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    echo "Moonlight needs Python 3.12 or newer; this system has $(python3 --version)." >&2
    exit 1
fi

echo "== Arduino access (dialout group)"
relogin=no
if id -nG "$USER" | grep -qw dialout; then
    echo "$USER is already in dialout"
else
    sudo usermod -aG dialout "$USER"
    relogin=yes
fi

echo "== Python environment"
python3 -m venv .venv
.venv/bin/pip install -q -e .

echo "== Map from the Tibia client"
.venv/bin/python -c 'from moonlight.core.wiring import sync_client_map; print(sync_client_map(), "tiles copied")'

chmod +x start-moonlight-gui.sh
echo
echo "Done. Start the bot with ./start-moonlight-gui.sh"
if [ "$relogin" = yes ]; then
    echo "Log out and back in first, so the bot can open the Arduino."
fi
echo "First run: log in to Tibia, then Calibration tab > Start calibration and press Enter through the areas."
