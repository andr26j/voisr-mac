# Voisr

Hold a key, speak, and clean text appears at your cursor, in any app. Voisr is a local-only macOS push-to-talk dictation app in the spirit of [Wispr Flow](https://wisprflow.ai): speech-to-text runs on your Mac with [whisper.cpp](https://github.com/ggml-org/whisper.cpp), and no audio or text is sent anywhere.

English only. macOS only. Free for personal use under the MIT licence. There is no prebuilt download: the installer builds the app on your Mac.

## Requirements

| You need | Why | Get it |
|---|---|---|
| macOS | Voisr uses macOS-only APIs. Developed and tested on an Apple-silicon Mac (M1, 8 GB). Intel Macs and the lowest supported macOS version have not been tested. | |
| [Homebrew](https://brew.sh) | installs whisper.cpp and ffmpeg | brew.sh |
| Xcode Command Line Tools (C compiler) | the installer compiles a small launcher so macOS lists the app as "Voisr" in Privacy & Security | `xcode-select --install` |
| Python 3.14 or newer, as a *framework build* | runs the app. Homebrew's and python.org's Python are framework builds. Older versions fail to import the code (checked on 3.10; nothing between 3.11 and 3.13 was run). | `brew install python@3.14` |
| about 630 MB free for models, plus room for Python packages | `small.en` (~490 MB) is the default, `base.en` (~150 MB) is the faster option | |
| internet during install, and for any optional model you download | Homebrew, the models from Hugging Face, and Python packages | |

`install.sh` checks all of this first and tells you exactly what is missing before it changes anything.

## Install

```bash
git clone https://github.com/andr26j/voisr-mac.git
cd voisr-mac
./install.sh
open ~/Applications/Voisr.app
```

`install.sh` runs `setup.sh` (installs `whisper-cpp` and `ffmpeg` with Homebrew, downloads both models to `models/`, creates a development venv) and then `scripts/install_app.sh` (copies the runtime to `~/Library/Application Support/Voisr`, builds `~/Applications/Voisr.app`). macOS blocks apps from reading Desktop and Documents without per-app grants, which is why the app runs from Application Support and not from the cloned folder.

A new install starts with no custom dictionary and default settings. Voisr writes its own `config.json` the first time you change a setting.

### ffmpeg

Voisr records with a built-in audio stream, which does not use ffmpeg. `setup.sh` still installs it because it is used by:

- the fallback recorder, if the audio stream cannot be opened (the dictation would otherwise fail);
- CLI toggle mode (`python -m voisr.app`, see below);
- `--mode file`, to convert non-WAV audio.

Without ffmpeg, those three stop working; normal dictation from the app keeps working while the audio stream does.

## Permissions

Open the app and follow the strip on the Home screen. Each chip opens the right pane of System Settings → Privacy & Security.

| Permission | Needed for | When it is asked |
|---|---|---|
| Microphone | recording | on your first recording |
| Accessibility | the push-to-talk key (the default `event_tap` mode), pasting with Cmd-V, and reading the text around your cursor (see **Screen context**) | from the strip on Home |
| Input Monitoring | the push-to-talk key, only if you set `"event_tap": "listen"` | from the strip on Home |

Pasting works by asking System Events to press Cmd-V (`osascript`); if macOS asks whether Voisr may control System Events, allow it.

**Permissions and reinstalling.** macOS ties the grants to the launcher's code hash. The installer rebuilds the launcher only when the launcher source, Python version or Python location changes, and prints whether your grants carry over. If it says the app identity changed, or the toggles are ON but the key does nothing, use **Reset** on the Home screen's permission strip (or run the two `tccutil reset` commands the installer prints) and allow the permissions again.

## The dashboard

The dashboard follows your macOS appearance setting automatically, with an option to pin to Light or Dark mode in Settings.

**Home** leads with your push-to-talk key rendered as a keycap (`Hold <key> to dictate`) and a headline stat showing **Time saved** (measured against typing at 40 wpm) with a 14-day sparkline.

A **permission health strip** shows a chip for Microphone and Accessibility (plus Input Monitoring if `event_tap` is `"listen"`). Any missing grant turns amber; clicking it opens the exact System Settings pane. When everything is granted, it collapses to a single quiet line.

**⌘K command palette** lets you start dictation, jump between views, change the push-to-talk key, toggle appearance, and copy the last dictation.

**History** supports ⌘F search with match highlighting, arrow-key navigation, ⌘C to copy, and Backspace to delete the selected row. It pages past 200 entries and can export to `.txt`, `.md`, or `.json`. Deleting a row shows an Undo toast instead of a confirmation dialog.

**Dictionary** entries can be edited in place instead of deleted and retyped. Corrections Voisr learned appear at the top with Accept and Dismiss; entries it added by itself carry an **Auto-added** badge. See **Screen context**.

A **menu-bar item** keeps Voisr reachable without opening its window and shows when a dictation is in flight.

**Settings** are grouped into Dictation, Push-to-talk, Appearance, and Privacy & Data sections. You can set launch-at-login and choose a transcription model.

**Keep model loaded.** Starting `whisper-cli` for every dictation reloads the model each time. By default Voisr instead starts one `whisper-server` (from the same `whisper-cpp` package) once a press outlasts the quick-tap window (about half a second) or a double-tap starts hands-free, so an accidental tap loads nothing and the model loads while you speak. It keeps the model for the next dictation. **Settings → Keep model loaded** chooses when it is freed, with the same choices as [Handy](https://github.com/cjpais/Handy): Never, Immediately, 2, 5 (default), 10 or 15 minutes, 1 hour. The timer never runs out during a recording or transcription. While loaded, the server holds about 0.7 GB of memory for `small.en` (measured), and that memory is released when it unloads. **Immediately** keeps no memory and reloads for every dictation, as before. If the server is missing, crashes, times out or answers badly, that dictation is transcribed with `whisper-cli` instead and nothing is lost; the server is then left alone for a minute. Switching or deleting a model unloads the old one. The server runs under a small guard (`voisr/whisper_guard.py`) that stops it when Voisr quits, relaunches, crashes or is force quit, and a leftover one from an earlier run is stopped at the next start; a `whisper-server` you started yourself is never touched. History records which way each dictation was transcribed (`warm`, `cli` or `cli-fallback`).

**Models.** Voisr runs everything on your Mac. `setup.sh` installs `small.en` (default) and `base.en`. In **Settings → Transcription model** you can download two larger optional models, `medium.en-q5_0` (~540 MB) and `large-v3-turbo-q5_0` (~575 MB), and delete any model you aren't using. Downloads are checked against a pinned SHA-256 and need free disk space for the model plus 512 MB. Settings warns when a model asks for more memory than your Mac has. You can also drop your own `ggml-*.bin` file into `models/` and it appears as a Custom model. Voisr is English only; every model is run with `-l en`. All listed model weights are OpenAI Whisper's (MIT licence).

**Screen context.** On by default: **Settings → Read the text around your cursor**. Once a press is a real dictation (an accidental tap reads nothing), Voisr reads the field you are typing in, the window title and nearby labels through the Accessibility API, and it does three things with them:

- **Names on screen are spelled right.** Names and identifiers near your cursor are added to the glossary whisper is given, after your dictionary terms. They only bias recognition; they never replace anything.
- **It checks the paste landed.** After the paste it reads the field again. If the text isn't there, your dictation stays on the clipboard and the pill says "Copied — press ⌘V". It never presses Cmd-V a second time. An app where a missing paste proves nothing (Electron apps, terminals, browsers) just restores the clipboard as usual. History records the verdict as `delivery`.
- **It learns from your corrections.** Off separately with **Learn from my corrections**. For up to a minute after a verified paste, Voisr re-reads that field (at 3, 10, 30 and 60 seconds; it does not watch your keystrokes) to find words you fixed, and suggests them in Dictionary.

**Auto-add** (**Settings → Add safe corrections automatically**, on by default) adds a correction without asking only when you have made the same fix in two separate dictations and it is a capitalization change or a close spelling fix of a single term. It never adds common English words, a change between two valid words (`Tuesday` to `Thursday`), numbers, multi-word fixes, or anything from a rewrite: those stay suggestions. A brief notice says when it happens. Remove an auto-added entry with its one button and it won't be added again; a dismissed suggestion won't come back either.

**Password fields and password managers are never read**: no text at all from a secure field, and no Accessibility call at all into a denylisted app (1Password, Bitwarden, LastPass, Dashlane, KeePassXC, Apple Passwords, Keychain Access, System Settings and others; see `DENYLIST` in `voisr/ax_context.py`). A field that can't be read counts as unreadable, not empty. Voisr never sets `AXManualAccessibility`, so it doesn't change how other apps behave. Every read is time-limited (0.15 s), runs off the push-to-talk path, and a failure or a hung app only costs that dictation its context. Measured on an M1 8 GB against TextEdit, this added under 1 ms before the paste and about 6 ms to the clipboard restore, and 27 ms to a 6-second clip on the kept-loaded server.

**Electron apps** (Slack, VS Code, Notion, Discord) mostly don't expose their text without `AXManualAccessibility`, so expect little or no context and no edit learning there, unless the app turns accessibility on itself. Dictation and pasting work as before.

## Updating

Quit Voisr from the menu bar, then:

```bash
cd voisr-mac
git pull
./install.sh
```

Your settings, dictionary and history are left alone. The installer tells you if the app is still running the old version.

## Uninstalling

```bash
bash scripts/uninstall.sh            # asks before deleting history and settings
bash scripts/uninstall.sh --dry-run  # show what would be removed
```

It removes `~/Applications/Voisr.app`, `~/Library/Application Support/Voisr` (runtime, venv, models), the launch-at-login agent, the log and Voisr's caches. It asks separately before deleting your **dictation history** and your **settings and dictionary**, and keeps them if you say no. With no terminal to ask on, it keeps them. It does not remove the folder you cloned, the Homebrew packages (`brew uninstall whisper-cpp ffmpeg`), or Voisr's rows in System Settings → Privacy & Security (select Voisr and press "–").

## What stays on your Mac

- **Audio** is recorded to a temporary WAV, transcribed locally, and deleted when the dictation is done. It is never uploaded.
- **Text** you dictate is stored in `~/Library/Application Support/Voisr/data/history.jsonl` (the text, time, word count and duration) so the History view works. Delete rows in the app or delete the file.
- **Text on your screen** is read only with **Read the text around your cursor** on (see **Screen context**), stays in memory for that dictation, and is never written to history, logs or any file, and never sent anywhere. With the setting off, Voisr reads no text from other apps.
- **Learned corrections**: `~/Library/Application Support/Voisr/data/suggestions.json` holds only corrected word pairs (`voiser` → `Voisr`), what happened to each, and how often each dictionary entry fired. Never the sentence around them.
- History also records which app you dictated into (its bundle id, not a window title; left out for password managers and with screen context off), and the paste verdict and how many words you corrected.
- **Settings and dictionary** live in `~/Library/Application Support/Voisr/config.json`.
- **Log**: `~/Library/Logs/Voisr.log` holds app errors and events. The dashboard does not write what you dictated into it (checked by searching the code); the CLI modes print dictated text to the terminal.
- **Network**: the app's only network call to the internet downloads a model from Hugging Face when you click Download in Settings; it sends none of your audio or text. Otherwise the internet is used only while installing (Homebrew, the models from Hugging Face, Python packages from PyPI). Voisr also talks to its own `whisper-server` over a loopback-only connection (`127.0.0.1`, a free port, never another interface; system proxies are bypassed), which never leaves your Mac. See **Keep model loaded**. With the server off, `whisper-cli` gets the glossary, screen terms included, as a command-line argument for the length of the transcription; other processes of your user could see that, and it doesn't leave your Mac either.
- Voisr pastes through your clipboard and restores what was there afterwards.

## CLI usage (optional)

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

All of this is editable in Settings; hand-editing is optional. A file that cannot be parsed is set aside as `config.json.corrupt-<time>` and defaults are used.

```json
{
  "hotkey": "alt_r",          // legacy push-to-talk key name (used when no hotkey_vk)
  "hotkey_vk": 63,             // macOS virtual keycode captured via Settings → Change…
  "hotkey_label": "Fn / Globe",// display label for that key
  "paste": true,               // false = clipboard only
  "device": ":0",              // CLI toggle mode only: avfoundation device for ffmpeg (ffmpeg -f avfoundation -list_devices true -i ""); the app uses the macOS default input
  "model": "small.en",         // a model id from Settings (e.g. base.en, medium.en-q5_0) or a custom ggml-<name>.bin
  "show_widget": true,         // floating recording pill
  "pill_theme": "classic",     // classic | ember | aurora | ocean | neon | breathe | scanner
  "pill_color": null,          // "#RRGGBB" to recolour the bars, null = theme default
  "appearance": "system",      // system | light | dark
  "launch_at_login": false,    // mirrors ~/Library/LaunchAgents/com.local.voisr.plist
  "event_tap": "active",       // "active" (needs Accessibility) | "listen" (needs Input Monitoring too)
  "use_screen_context": true,  // false = no text is read from other apps
  "learn_from_edits": true,    // false = no re-reads after a paste, no suggestions
  "dictionary_auto_add": true, // false = learned corrections always wait for Accept
  "dictionary": {              // spoken/misheard variant -> written form; empty on a new install
    "open a i": "OpenAI",
    "brunswik": "Brunswick"
  }
}
```

The launch-at-login setting is read from the LaunchAgent plist itself, so removing the plist by hand is reflected in Settings. The dictionary's written forms are also passed to whisper as a glossary prompt (most recent 40 terms, then terms from the screen if room is left) to bias spelling.

Add a dictionary entry for **every variant the ASR produces** for a term: dictate the word once, see what comes out, and map it.

## Known limitations

- English only; larger models are optional downloads and no speed or accuracy comparison has been measured yet.
- With the fallback ffmpeg recorder, capture takes about 1 to 1.5 s to start: begin speaking after the cue, or hold the key a beat before talking.
- No Command Mode (voice editing).
- No prebuilt app: building needs Homebrew, the Command Line Tools and Python 3.14+.
- Not tested on Intel Macs or on a clean macOS account yet.

## Licence

MIT (see `LICENSE`). Third-party software and models are listed in `THIRD_PARTY_NOTICES`.

## Development

```bash
./venv/bin/pytest -v
```

Tests synthesize speech with macOS `say`, so the full audio → text pipeline is verified without a live microphone. Tests that need `say`, `whisper-cli`, or a real input device are tagged `audio`; run the rest on a machine without them with:

```bash
./venv/bin/pytest -q -m "not audio"
```

One opt-in test drives the real Accessibility API and a real Cmd-V against TextEdit, taking focus for a few seconds. `scripts/bench_context.py` prints what screen context costs on your Mac.

```bash
VOISR_RUN_AX_INTEGRATION=1 ./venv/bin/pytest -q tests/test_ax_real.py -s
```

The dashboard's JS tests run under Node (use the glob: a bare `tests/js/` directory fails on Node 24):

```bash
node --test tests/js/*.test.mjs
```

Dependencies are pinned in `requirements.txt`; bump them deliberately and re-run the full suite.
