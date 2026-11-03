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
# shellcheck source=install_lib.sh
source "$PROJECT/scripts/install_lib.sh"

# Read before anything changes: compared with the result after signing, it
# tells whether macOS will still honour the user's permission grants.
OLD_CDHASH="$(bundle_cdhash "$APPDIR")"
RUNNING=""
pgrep -f "$APPDIR/Contents/MacOS/Voisr" >/dev/null 2>&1 && RUNNING=1

echo "==> Installing runtime to $APPSUP"
mkdir -p "$APPSUP/models"
rsync -a --delete "$PROJECT/voisr/" "$APPSUP/voisr/" --exclude __pycache__
cp "$PROJECT/requirements.txt" "$APPSUP/"
rsync -a "$PROJECT/models/" "$APPSUP/models/"
# No config.json is shipped: the app starts from DEFAULT_CONFIG and writes the
# user's own on first save. An existing one is never touched.

echo "==> Creating venv"
PYVER="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')"
if ! venv_usable "$APPSUP/venv" "$PYVER"; then
  # missing, or built by another Python (e.g. 3.14 -> 3.15): rebuild it, or
  # the launcher below looks for site-packages the venv doesn't have
  rm -rf "$APPSUP/venv"
  python3 -m venv "$APPSUP/venv"
fi
"$APPSUP/venv/bin/pip" install -q -r "$APPSUP/requirements.txt"

echo "==> Creating $APPDIR"
mkdir -p "$APPDIR/Contents/MacOS" "$APPDIR/Contents/Resources"
cp "$PROJECT/assets/Voisr.icns" "$APPDIR/Contents/Resources/Voisr.icns"
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
    <key>CFBundleIconFile</key>
    <string>Voisr</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSArchitecturePriority</key>
    <array>
        <string>arm64</string>
        <string>x86_64</string>
    </array>
</dict>
</plist>
PLIST

# Native stub (not a script): keeps the process identity as Voisr.app so
# TCC lists "Voisr" in Privacy & Security instead of "Python", and the
# process always runs the machine's native architecture.
#
# The stub dlopen()s the Python framework dylib, so its paths must match the
# python3 that built the venv — derive them here instead of hardcoding.
PYFW="$(python3 -c 'import sys; print(sys.base_prefix)')"
LAUNCHER="$APPDIR/Contents/MacOS/Voisr"

# macOS ties an ad-hoc-signed app's Input Monitoring / Accessibility grants to
# the launcher binary's code hash (cdhash). Recompiling the stub produces a new
# cdhash, which SILENTLY revokes those grants on every reinstall — the toggle
# in System Settings stays ON but no longer matches the new binary, so the
# hotkey tap is denied. To keep grants alive across reinstalls, only rebuild the
# launcher when its inputs actually change (stub source, Python version/prefix);
# otherwise reuse the existing binary so its cdhash stays constant.
# The sig file lives OUTSIDE the .app bundle: an app's cdhash is hashed over the
# executable AND its sealed Info.plist/resources, so a tracking file inside the
# bundle would itself change the cdhash and defeat the purpose.
BUILD_SIG="stub:$(shasum -a 256 "$PROJECT/scripts/voisr_stub.c" | cut -d' ' -f1) pyver:$PYVER pyfw:$PYFW"
SIGFILE="$APPSUP/.launcher-build-sig"

if [ -f "$LAUNCHER" ] && [ -f "$SIGFILE" ] && [ "$(cat "$SIGFILE")" = "$BUILD_SIG" ]; then
  echo "==> Launcher unchanged — reusing it"
elif command -v cc >/dev/null && [ -f "$PYFW/Python" ]; then
  had_launcher=""; [ -f "$LAUNCHER" ] && had_launcher=1
  cc -O2 -DPYFW="\"$PYFW\"" -DPYVER="\"$PYVER\"" \
     -o "$LAUNCHER" "$PROJECT/scripts/voisr_stub.c"
  chmod +x "$LAUNCHER"
  printf '%s' "$BUILD_SIG" > "$SIGFILE"
  [ -n "$had_launcher" ] && echo "==> Launcher rebuilt (stub or Python changed)"
else
  if [ ! -f "$PYFW/Python" ]; then
    echo "WARNING: python3 is not a framework build ($PYFW/Python missing); falling back to script launcher (TCC will attribute permissions to Python)"
  else
    echo "WARNING: no C compiler; falling back to script launcher (TCC will attribute permissions to Python)"
  fi
  cat > "$LAUNCHER" <<'LAUNCH'
#!/bin/bash
APPSUP="$HOME/Library/Application Support/Voisr"
LOG="$HOME/Library/Logs/Voisr.log"
export PYTHONPATH="$APPSUP"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
exec "$APPSUP/venv/bin/python" -m voisr.ui >> "$LOG" 2>&1
LAUNCH
  chmod +x "$LAUNCHER"
  rm -f "$SIGFILE"  # script launcher has no stable native binary to track
fi

xattr -cr "$APPDIR" 2>/dev/null || true
codesign --force --deep -s - "$APPDIR" 2>/dev/null || true

# Grants follow the cdhash, which the launcher, Info.plist and icon all feed:
# report what actually happened rather than what the launcher check assumed.
NEW_CDHASH="$(bundle_cdhash "$APPDIR")"
if [ -n "$OLD_CDHASH" ] && [ "$OLD_CDHASH" != "$NEW_CDHASH" ]; then
  echo "==> App identity changed — macOS no longer matches your Input Monitoring /"
  echo "    Accessibility grants, even where the toggle still shows ON. Run:"
  echo "      tccutil reset ListenEvent com.local.voisr && tccutil reset Accessibility com.local.voisr"
  echo "    then open Voisr and allow both again."
elif [ -n "$OLD_CDHASH" ]; then
  echo "==> App identity unchanged — permission grants carry over"
fi

if [ -n "$RUNNING" ]; then
  echo "==> Voisr is still running the old version: quit it from the menu bar and reopen it."
fi
echo "==> Done. Open with: open '$APPDIR'  (or double-click Voisr in ~/Applications)"
