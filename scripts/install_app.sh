#!/bin/bash
# Installs Voisr as a double-clickable Mac app.
#
# Runtime (code, venv, whisper model, config, history) goes to
#   ~/Library/Application Support/Voisr
# and a launcher bundle goes to
#   ~/Applications/Voisr.app
#
# App Support is used because macOS TCC blocks apps from reading
# Desktop/Documents paths without per-app permission grants.
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
APPSUP="$HOME/Library/Application Support/Voisr"
APPDIR="$HOME/Applications/Voisr.app"

echo "==> Installing runtime to $APPSUP"
mkdir -p "$APPSUP/models"
rsync -a --delete "$PROJECT/voisr/" "$APPSUP/voisr/" --exclude __pycache__
cp "$PROJECT/requirements.txt" "$APPSUP/"
cp "$PROJECT/models/ggml-base.en.bin" "$APPSUP/models/"
[ -f "$APPSUP/config.json" ] || cp "$PROJECT/config.json" "$APPSUP/"

echo "==> Creating venv"
[ -d "$APPSUP/venv" ] || python3 -m venv "$APPSUP/venv"
"$APPSUP/venv/bin/pip" install -q -r "$APPSUP/requirements.txt"

echo "==> Creating $APPDIR"
mkdir -p "$APPDIR/Contents/MacOS"
cat > "$APPDIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Voisr</string>
    <key>CFBundleDisplayName</key>
    <string>Voisr</string>
    <key>CFBundleIdentifier</key>
    <string>com.local.voisr</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleExecutable</key>
    <string>Voisr</string>
    <key>NSMicrophoneUsageDescription</key>
    <string>Voisr records your voice to transcribe it on-device.</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST

cat > "$APPDIR/Contents/MacOS/Voisr" <<'LAUNCH'
#!/bin/bash
APPSUP="$HOME/Library/Application Support/Voisr"
LOG="$HOME/Library/Logs/Voisr.log"
export PYTHONPATH="$APPSUP"
exec "$APPSUP/venv/bin/python" -m voisr.ui >> "$LOG" 2>&1
LAUNCH
chmod +x "$APPDIR/Contents/MacOS/Voisr"

xattr -cr "$APPDIR" 2>/dev/null || true
codesign --force --deep -s - "$APPDIR" 2>/dev/null || true

echo "==> Done. Open with: open '$APPDIR'  (or double-click Voisr in ~/Applications)"
