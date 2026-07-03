#!/bin/bash
# Builds assets/Voisr.icns from the 1024px master rendered by make_icon.py
set -euo pipefail
cd "$(dirname "$0")/.."

./venv/bin/pip show pillow >/dev/null 2>&1 || ./venv/bin/pip install -q pillow
./venv/bin/python scripts/make_icon.py

ICONSET=assets/Voisr.iconset
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z $size $size assets/icon_1024.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z $double $double assets/icon_1024.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o assets/Voisr.icns
rm -rf "$ICONSET"
echo "wrote assets/Voisr.icns"
