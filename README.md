# Voisr

A local-only macOS clone of [Wispr Flow](https://wisprflow.ai)'s core loop: **hold a key, speak, and clean text appears at your cursor** — in any app. All processing happens on your machine (whisper.cpp); no audio or text ever leaves it.

## What it does

1. **Capture** — records your mic while you hold the hotkey (or between Enter presses in toggle mode)
2. **Transcribe** — on-device speech-to-text via whisper.cpp (`ggml-base.en`)
3. **Clean up** — strips filler words (um, uh, hmm…), normalizes spacing/punctuation, capitalizes, and applies your custom dictionary (e.g. `"trademake" → "TradeMate"` for names the ASR mishears)
4. **Insert** — pastes the result at the cursor of the frontmost app (Cmd-V via System Events), restoring your clipboard afterwards. If pasting is blocked, the text stays on the clipboard.

## Setup

```bash
bash setup.sh   # installs whisper-cpp (brew), downloads the model (~141 MB), creates venv
```

## Desktop app (dashboard)

A Wispr Flow–style dashboard: record dictations, browse history, track stats (words, WPM, streak), edit your dictionary, and change settings — including 7 animation themes for the recording pill plus a color wheel to recolor any of them.

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

Tests synthesize speech with macOS `say`, so the full audio → text pipeline is verified without a live microphone.
