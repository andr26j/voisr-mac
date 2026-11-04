"""Do snippet triggers work in YOUR voice? About three minutes for 25 clips.

scripts/bench_triggers.py measured synthetic voices only. This records you, in
the room you really dictate in, then runs each clip the way the app does: speech
gate, trim, prompt with its clip-length rule and echo retry, real Whisper on the
kept-loaded server and whisper-cli, and the sustained-speech guard.

Some clips are said while you press and release your push-to-talk key, and some
are silent presses of it, as the key's thumps are what the guard has to cope
with. Voisr would hear that key and dictate, so QUIT VOISR FIRST. record won't
start while it runs, and says how to quit and reopen it:

    pkill -TERM -f "Applications/Voisr.app/Contents/MacOS/Voisr"
    python3 scripts/test_my_triggers.py record
    python3 scripts/test_my_triggers.py score
    open ~/Applications/Voisr.app

record says what to do for each clip; Enter starts and stops the recording.
Speak as you would to the app.

score reports how many triggers would expand, Enter and push-to-talk clips apart,
and how many other clips would, which is the costly mistake, as that pastes saved
text you didn't ask for. It also shows the figures the guard works from.

Clips stay in bench_corpus/triggers (not in git), as NAME.wav + NAME.txt. Your
terminal needs microphone access to record. Nothing leaves this Mac. Voisr's
config.json is only read, for the key's name, and for its Dictionary with --config.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

from bench_triggers import SCREEN, AppPath, gate_and_trim  # noqa: E402
from voisr import speech_gate  # noqa: E402
from voisr.app import load_config  # noqa: E402
from voisr.recorder import Recorder  # noqa: E402
from voisr.ui import HOTKEY_OPTIONS, MIN_SPEECH_S, wav_seconds  # noqa: E402
from voisr.snippets import MIN_SUSTAINED_SPEECH_MS, find_snippet, has_speech_evidence  # noqa: E402

TRIGGERS = ["my email", "my address", "my phone number", "review prompt", "work email", "sign off"]
OTHERS = ["my emails", "my dress", "my mother", "sign up", "review", "in the office",
          "I checked my email", "send the review prompt to Sam"]
WANTED = ("trigger", "hotkey")  # clips that should expand
SILENT_CLIPS = 5
OWN_CLIP = re.compile(r"(?:trigger|hotkey|other|silent)-\d+\.(?:wav|txt)")
MAX_ATTEMPTS = 3  # of one clip, so a real failure can't loop forever
SILENT = "(silent, pressing the key)"
INSTALLED_CONFIG = Path.home() / "Library/Application Support/Voisr/config.json"
APP_PROCESS = "Applications/Voisr.app/Contents/MacOS/Voisr"
QUIT_COMMAND = f'pkill -TERM -f "{APP_PROCESS}"'
REOPEN_COMMAND = "open ~/Applications/Voisr.app"
SILENCE_MESSAGE = (
    "macOS is giving this app silence: that clip was all zeros, so it needs microphone "
    "access.\nRun this from the Mac's Terminal app, not a terminal inside another app, and "
    "check System Settings → Privacy & Security → Microphone.")


def script(triggers=TRIGGERS, others=OTHERS):
    """(clip name, phrase) in the order to say them. trigger: Enter alone.
    hotkey: said around the push-to-talk key, as the app hears it."""
    lines = [(f"trigger-{i + 1:02}", phrase) for i, phrase in enumerate(triggers)]
    lines += [(f"hotkey-{i + 1:02}", phrase) for i, phrase in enumerate(triggers)]
    lines += [(f"other-{i + 1:02}", phrase) for i, phrase in enumerate(others)]
    return lines + [(f"silent-{i + 1:02}", SILENT) for i in range(SILENT_CLIPS)]


def hotkey_name(config_path=INSTALLED_CONFIG) -> str:
    """Only reads: load_config would move a damaged file aside."""
    try:
        cfg = json.loads(Path(config_path).read_text())
        label = cfg.get("hotkey_label")
        if isinstance(label, str) and label:
            return label
        hotkey = cfg.get("hotkey", "alt_r")
    except (OSError, ValueError, AttributeError):
        hotkey = "alt_r"  # what a new install uses
    for option in HOTKEY_OPTIONS:
        if option["value"] == hotkey:
            return option["label"]
    return "your push-to-talk key"


def voisr_is_running() -> bool:
    return subprocess.run(["pgrep", "-f", APP_PROCESS], capture_output=True).returncode == 0


def instruction(name: str, phrase: str, key: str) -> str:
    if phrase == SILENT:
        return (f"Say nothing. After Enter: tap {key} once, then press and hold it about "
                "a second, release.")
    if name.startswith("hotkey"):
        return f'After Enter: press and hold {key}, say "{phrase}", release {key}.'
    return f'After Enter, say: "{phrase}"'


def clear_old_clips(folder: Path) -> int:
    """So an old clip can't be scored with a new one. Only this script's own names."""
    old = [path for path in folder.iterdir() if OWN_CLIP.fullmatch(path.name)]
    for path in old:
        path.unlink()
    return len(old)


def _is_usable(wav: Path) -> bool:
    """False for what an Enter pressed twice leaves. Same floor as the app's."""
    return wav.exists() and wav.stat().st_size >= 1000 and wav_seconds(wav) >= MIN_SPEECH_S


def _record_clip(wav: Path, recorder, ask, what: str) -> None:
    """One clip, asked for again if it comes out too short. Only a clip with
    sound in it is left behind."""
    for attempt in range(MAX_ATTEMPTS):
        again = "That clip was too short or empty, so once again.\n" if attempt else ""
        ask(f"\n{again}{what}\n  Enter to start... ")
        recorder.start(wav)
        ask("  recording, Enter to stop... ")
        error = recorder.stop()
        if _is_usable(wav):
            span = speech_gate.gate_wav(wav)
            if span is not None and span.verdict == "all_zeros":
                wav.unlink()
                raise SystemExit(SILENCE_MESSAGE)
            return
        wav.unlink(missing_ok=True)
    raise SystemExit(f"No usable audio after {MAX_ATTEMPTS} tries. The recorder said: "
                     f"{error.strip()[:200] or 'nothing'}")


def record(folder: Path, triggers, recorder_factory=Recorder, ask=input, key=None,
           running=voisr_is_running) -> None:
    if running():
        raise SystemExit(
            "Voisr is running and would hear your push-to-talk key, so quit it first:\n\n"
            f"    {QUIT_COMMAND}\n\nThen run this again. When you've finished, reopen it:\n\n"
            f"    {REOPEN_COMMAND}\n\nIf that says error -600, wait a few seconds and repeat it.")
    key = key or hotkey_name()
    folder.mkdir(parents=True, exist_ok=True)
    cleared = clear_old_clips(folder)
    if cleared:
        print(f"Cleared {cleared} files from an earlier run in {folder}.")
    print(f"Your push-to-talk key: {key}. Voisr stays quit until you reopen it: {REOPEN_COMMAND}")
    recorder = recorder_factory()
    try:
        for name, phrase in script(triggers):
            _record_clip(folder / f"{name}.wav", recorder, ask, instruction(name, phrase, key))
            (folder / f"{name}.txt").write_text(phrase + "\n")
    finally:
        recorder.close()
    print(f"\nRecorded to {folder}. Now run: python3 scripts/test_my_triggers.py score"
          f"\nThen reopen Voisr: {REOPEN_COMMAND}")


def clips_in(folder: Path) -> list[tuple[str, str, Path]]:
    return [(wav.stem, wav.with_suffix(".txt").read_text().strip(), wav)
            for wav in sorted(folder.glob("*.wav"))]


def saved_triggers(clips) -> dict:
    return {phrase: "x" for name, phrase, _ in clips if name.startswith(WANTED)}


def score(clips, transcribe, conditions) -> list[dict]:
    """A row per clip and condition. transcribe(wav, condition) -> Whisper's
    text. A clip the gate refuses counts as a miss: the app would drop it."""
    triggers = saved_triggers(clips)
    rows = []
    for name, phrase, wav in clips:
        span = gate_and_trim(wav)
        passed = span is None or span.verdict == "speech"
        for condition in conditions:
            raw = transcribe(wav, condition) if passed else ""
            unguarded = find_snippet(raw, triggers)
            fired = unguarded if has_speech_evidence(span) else None
            rows.append({"name": name, "phrase": phrase, "condition": condition, "raw": raw,
                         "gate": span.verdict if span else "unread",
                         "sustained_ms": span.sustained_ms if span else 0,
                         "fired": fired.trigger if fired else None,
                         "held_back": unguarded.trigger if unguarded and not fired else None})
    return rows


def _sustained(once, prefix) -> list[int]:
    return [r["sustained_ms"] for r in once if r["name"].startswith(prefix)]


def report(rows, conditions) -> None:
    once = [r for r in rows if r["condition"] == conditions[0]]
    print(f"speech gate: {dict(Counter(r['gate'] for r in once))}")
    print(f"sustained speech, guard needs {MIN_SUSTAINED_SPEECH_MS} ms: "
          f"Enter clips lowest {min(_sustained(once, 'trigger'), default='-')} ms, "
          f"push-to-talk clips lowest {min(_sustained(once, 'hotkey'), default='-')} ms, "
          f"silent key clips highest {max(_sustained(once, 'silent'), default='-')} ms")
    for condition in conditions:
        group = [r for r in rows if r["condition"] == condition]
        print(f"\n{condition}:")
        for prefix, title in (("trigger", "triggers, Enter"), ("hotkey", "triggers, push-to-talk key")):
            part = [r for r in group if r["name"].startswith(prefix)]
            if part:
                fired = sum(r["fired"] == r["phrase"] for r in part)
                print(f"  {title}: fire {fired}/{len(part)}")
        for prefix, title in (("other", "other phrases"), ("silent", "silent key clips")):
            part = [r for r in group if r["name"].startswith(prefix)]
            if part:
                print(f"  {title}: fire {sum(r['fired'] is not None for r in part)}/{len(part)}")
        for row in group:
            wanted = row["name"].startswith(WANTED)
            if row["fired"] is not None and row["fired"] != (row["phrase"] if wanted else None):
                print(f'  MISFIRE {row["name"]} "{row["phrase"]}" came out {row["raw"]!r}, '
                      f'pastes "{row["fired"]}"')
            elif row["held_back"]:
                print(f'  held back by the guard: {row["name"]} "{row["phrase"]}" came out '
                      f'{row["raw"]!r}, {row["sustained_ms"]} ms')
            elif wanted and row["fired"] is None:
                print(f'  missed  {row["name"]} "{row["phrase"]}" came out {row["raw"]!r}')


def run_score(folder: Path, models: Path, model: str, dictionary: dict) -> None:
    clips = clips_in(folder)
    if not clips:
        raise SystemExit(f"no clips in {folder}: run record first")
    triggers = saved_triggers(clips)
    # route, saved snippets, on-screen terms
    conditions = {"server, no snippets": ("server", {}, []),
                  "server, snippets": ("server", triggers, []),
                  "server, snippets, screen": ("server", triggers, SCREEN),
                  "whisper-cli, no snippets": ("cli", {}, []),
                  "whisper-cli, snippets, screen": ("cli", triggers, SCREEN)}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        paths = {"server": AppPath(tmp, models, model, server=True),
                 "cli": AppPath(tmp, models, model, server=False)}

        def transcribe(wav, condition):
            route, snippets, terms = conditions[condition]
            return paths[route].transcribe(wav, dictionary, snippets, terms)

        try:
            # trimmed copies: the recordings themselves stay as they were
            copies = [(n, p, Path(shutil.copy(w, tmp / w.name))) for n, p, w in clips]
            report(score(copies, transcribe, list(conditions)), list(conditions))
        finally:
            for path in paths.values():
                path.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["record", "score"])
    ap.add_argument("--folder", type=Path, default=ROOT / "bench_corpus" / "triggers")
    ap.add_argument("--triggers", nargs="+", default=TRIGGERS, help="phrases you'd really use")
    ap.add_argument("--models-dir", type=Path, default=ROOT / "models")
    ap.add_argument("--model", default="small.en")
    ap.add_argument("--config", type=Path, help="config.json, read for its Dictionary")
    args = ap.parse_args()
    if args.action == "record":
        record(args.folder, args.triggers)
    else:
        dictionary = load_config(args.config)["dictionary"] if args.config else {}
        run_score(args.folder, args.models_dir, args.model, dictionary)


if __name__ == "__main__":
    main()
