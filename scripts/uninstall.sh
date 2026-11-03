#!/bin/bash
# Removes Voisr: the app, its runtime, the launch-at-login agent and its caches.
# Asks before deleting your dictation history and your settings/dictionary.
#
#   bash scripts/uninstall.sh [--dry-run] [--keep-data | --delete-data]
#
#   --dry-run      print what would be removed, change nothing
#   --keep-data    keep history and settings without asking
#   --delete-data  delete history and settings without asking
#
# With no flag and no terminal to ask on, your data is kept.
set -euo pipefail

DRY=""; DATA=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --keep-data) DATA=keep ;;
    --delete-data) DATA=delete ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

[ -n "${HOME:-}" ] && [ "$HOME" != "/" ] || { echo "HOME is not set; refusing to continue." >&2; exit 1; }

APPDIR="$HOME/Applications/Voisr.app"
APPSUP="$HOME/Library/Application Support/Voisr"
PLIST="$HOME/Library/LaunchAgents/com.local.voisr.plist"
LABEL="com.local.voisr"
LOG="$HOME/Library/Logs/Voisr.log"
EXTRAS=("$HOME/Library/Caches/$LABEL" "$HOME/Library/WebKit/$LABEL")

run() {
  if [ -n "$DRY" ]; then echo "  would run: $*"; else "$@"; fi
}
remove() {
  [ -e "$1" ] || [ -L "$1" ] || return 0
  echo "  removing $1"
  run rm -rf -- "$1"
}
# ask QUESTION: yes only on an explicit y/yes typed at a terminal
ask() {
  local reply=""
  [ -t 0 ] || [ -n "${VOISR_UNINSTALL_STDIN:-}" ] || return 1
  printf '%s [y/N] ' "$1"
  read -r reply || return 1
  case "$reply" in y|Y|yes|YES|Yes) return 0 ;; *) return 1 ;; esac
}
want_delete() {  # want_delete QUESTION
  case "$DATA" in
    delete) return 0 ;;
    keep) return 1 ;;
  esac
  ask "$1"
}

echo "==> Stopping Voisr"
if pgrep -f "$APPDIR/Contents/MacOS/Voisr" >/dev/null 2>&1; then
  run pkill -f "$APPDIR/Contents/MacOS/Voisr" || true
  [ -n "$DRY" ] || sleep 1
fi

echo "==> Removing launch at login"
if [ -e "$PLIST" ]; then
  # only touch launchd when HOME is the account's real home; a different HOME
  # (a test, a sandbox) must not unload the real user's agent
  if [ "$HOME" = "$(eval echo "~$(id -un)")" ]; then
    run launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  fi
  remove "$PLIST"
fi

echo "==> Removing the app"
remove "$APPDIR"

echo "==> Removing the runtime"
if [ -d "$APPSUP" ]; then
  HISTORY="$APPSUP/data"
  SETTINGS=()
  for f in "$APPSUP"/config.json "$APPSUP"/config.json.*; do
    [ -e "$f" ] && SETTINGS+=("$f")
  done

  KEEP_HISTORY=""; KEEP_SETTINGS=""
  if [ -e "$HISTORY" ]; then
    want_delete "Delete your dictation history and learned corrections ($HISTORY)?" || KEEP_HISTORY=1
  fi
  if [ ${#SETTINGS[@]} -gt 0 ]; then
    want_delete "Delete your settings and custom dictionary ($APPSUP/config.json)?" || KEEP_SETTINGS=1
  fi

  # everything except the data the user chose to keep
  for item in "$APPSUP"/* "$APPSUP"/.[!.]*; do
    [ -e "$item" ] || [ -L "$item" ] || continue
    [ -n "$KEEP_HISTORY" ] && [ "$item" = "$HISTORY" ] && continue
    if [ -n "$KEEP_SETTINGS" ]; then
      skip=""
      for f in "${SETTINGS[@]}"; do [ "$item" = "$f" ] && skip=1; done
      [ -n "$skip" ] && continue
    fi
    remove "$item"
  done
  if [ -n "$KEEP_HISTORY$KEEP_SETTINGS" ]; then
    echo "  kept your data in $APPSUP"
  else
    [ -n "$DRY" ] || rmdir "$APPSUP" 2>/dev/null || true
  fi
fi

echo "==> Removing log and caches"
remove "$LOG"
for d in "${EXTRAS[@]}"; do remove "$d"; done

cat <<NOTE

Voisr is uninstalled${DRY:+ (dry run: nothing was changed)}.
Not removed:
  - the folder you cloned Voisr from (delete it yourself if you like)
  - whisper.cpp and ffmpeg from Homebrew: brew uninstall whisper-cpp ffmpeg
  - Voisr's entries in System Settings → Privacy & Security (Microphone,
    Accessibility, Input Monitoring). Select Voisr and press "–" to remove them.
NOTE
