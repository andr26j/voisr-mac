# Voisr

A local-only macOS clone of [Wispr Flow](https://wisprflow.ai)'s core loop: **hold a key, speak, and clean text appears at your cursor** — in any app. All processing happens on your machine (whisper.cpp); no audio or text ever leaves it.

## What it does

1. **Capture** — records your mic while you hold the hotkey (or between Enter presses in toggle mode)
2. **Transcribe** — on-device speech-to-text via whisper.cpp (`ggml-base.en`)
3. **Clean up** — strips filler words (um, uh, hmm…), normalizes spacing/punctuation, capitalizes, and applies your custom dictionary (e.g. `"trademake" → "TradeMate"` for names the ASR mishears)
4. **Insert** — pastes the result at the cursor of the frontmost app (Cmd-V via System Events), restoring your clipboard afterwards. If a default paste is blocked, your original clipboard is preserved. In the desktop app, the dictation remains in history; CLI toggle mode has no history store. Use `paste: false` or `--no-paste` for clipboard-only/manual paste.

## Setup

```bash
bash setup.sh   # installs whisper-cpp (brew), downloads the model (~141 MB), creates venv
```

## Desktop app (dashboard)

A macOS dashboard: record dictations, browse history, track stats, edit your dictionary, and change settings.

```bash
bash scripts/install_app.sh   # installs to ~/Applications/Voisr.app
open ~/Applications/Voisr.app
```

The installer copies the runtime to `~/Library/Application Support/Voisr` (macOS blocks apps from reading Desktop paths without per-app grants, so the app can't run out of this folder directly). The installed app keeps its own `config.json` and history there; re-run the installer after pulling code changes.

For development, run the same dashboard straight from the repo:

```bash
./venv/bin/python -m voisr.ui
```

On first recording, macOS will ask for Microphone access — allow it once.

### Dashboard

The dashboard follows your macOS appearance setting automatically, with an option to pin to Light or Dark mode in Settings.

**Home** leads with your push-to-talk key rendered as a keycap (`Hold <key> to dictate`) and a headline stat showing **Time saved** (measured against typing at 40 wpm) with a 14-day sparkline.

A **permission health strip** shows three chips for Microphone, Input Monitoring, and Accessibility. Any missing grant turns amber; clicking it opens the exact System Settings pane. When all three are granted, it collapses to a single quiet line.

**⌘K command palette** lets you start dictation, jump between views, change the push-to-talk key, toggle appearance, and copy the last dictation.

**History** supports ⌘F search with match highlighting, arrow-key navigation, ⌘C to copy, and Backspace to delete the selected row. It pages past 200 entries and can export to `.txt`, `.md`, or `.json`. Deleting a row shows an Undo toast instead of a confirmation dialog.

**Dictionary** entries can be edited in place instead of deleted and retyped.

A **menu-bar item** keeps Voisr reachable without opening its window and shows when a dictation is in flight.

**Settings** are grouped into Dictation, Push-to-talk, Appearance, and Privacy & Data sections. You can set launch-at-login and choose a transcription model (`small.en` for accuracy, `base.en` for speed).

## CLI usage

```bash
# Toggle mode (default; no special permissions beyond Microphone)

./venv/bin/python -m voisr.app

# Push-to-talk: hold right-Option to record, release to transcribe + paste
./venv/bin/python -m voisr.app --mode hotkey

# Transcribe an audio file
./venv/bin/python -m voisr.app --mode file --file memo.m4a

# Copy to clipboard instead of pasting
./venv/bin/python -m voisr.app --no-paste
```

You can release and immediately hold the hotkey again while an earlier
dictation is transcribing. Voisr queues each recording and adds it to history
and pastes it in the order recorded.

## Configuration (`config.json`)

```json
{
  "hotkey": "alt_r",          // pynput key name for push-to-talk
  "paste": true,               // false = clipboard only
  "device": ":0",              // avfoundation audio device (ffmpeg -f avfoundation -list_devices true -i "")
  "dictionary": {              // spoken/misheard variant -> written form
    "trade mate": "TradeMate",
    "trademake": "TradeMate"
  }
}
```

Add a dictionary entry for **every variant the ASR produces** for a term — dictate the word once, see what comes out, and map it.

## macOS permissions

Grant these to your terminal app in System Settings → Privacy & Security:

| Permission | Needed for |
|---|---|
| Microphone | recording (all modes) |
| Accessibility / Automation | pasting via Cmd-V (`paste: true`) |
| Input Monitoring | the global hotkey (`--mode hotkey`) |

Toggle mode with `--no-paste` works with Microphone permission only.

## Known limitations

- ffmpeg takes ~1–1.5 s to start capturing — begin speaking after the `● recording...` cue, or hold the key a beat before talking.
- English-only model by default (swap in a multilingual `ggml-*.bin` in `models/` and adjust `voisr/transcriber.py` if needed).
- No Command Mode (voice editing) — out of scope for the basics.

## Development

```bash
./venv/bin/pytest -v
```

Tests synthesize speech with macOS `say`, so the full audio → text pipeline is verified without a live microphone. Tests that need `say`, `whisper-cli`, or a real input device are tagged `audio`; CI (`.github/workflows/ci.yml`, macOS runner) runs the rest:

```bash
./venv/bin/pytest -q -m "not audio"
```

Dependencies are pinned in `requirements.txt`; bump them deliberately and re-run the full suite.
