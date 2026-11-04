#!/bin/bash
# Builds assets/Voisr.icns, assets/icon_1024.png and assets/logo.svg.
# make_icon.py draws every size itself; scaling one master with sips would lose
# the different curve the small sizes use.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=./venv/bin/python
[ -x "$PY" ] || PY=python3
"$PY" -c "import PIL" 2>/dev/null || "$PY" -m pip install -q pillow

ICONSET=assets/Voisr.iconset
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
"$PY" scripts/make_icon.py "$ICONSET"
iconutil -c icns "$ICONSET" -o assets/Voisr.icns
rm -rf "$ICONSET"
echo "wrote assets/Voisr.icns"
