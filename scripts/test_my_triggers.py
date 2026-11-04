"""Do snippet triggers work in YOUR voice? About three minutes for 25 clips.

scripts/bench_triggers.py measured synthetic voices only. This records you, in
the room you really dictate in, then runs each clip the way the app does: speech
gate, trim, prompt with its clip-length rule and echo retry, real Whisper on the
kept-loaded server and whisper-cli, and the sustained-speech guard.

    python3 scripts/test_my_triggers.py record
    python3 scripts/test_my_triggers.py score

record says each phrase; press Enter to start and Enter to stop. Speak as you
would to the app. For the silent clips, say nothing and tap the right Command
key a few times, as a real keyboard's thumps are what the guard is for. Don't
tap your push-to-talk key, as Voisr would hear it.

score reports how many triggers would expand, and how many other clips would,
which is the costly mistake, as that pastes saved text you didn't ask for. It
also shows the sustained-speech figures the guard works from.

Clips stay in bench_corpus/triggers (not in git), as NAME.wav + NAME.txt. Your
terminal needs microphone access to record. Nothing leaves this Mac, and the
real app and its settings are not touched, unless --config points at them: that
only reads the Dictionary, so the prompt is like your real one.
"""
import argparse
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

from bench_triggers import SCREEN, AppPath, gate_and_trim  # noqa: E402
from voisr.app import load_config  # noqa: E402
from voisr.recorder import Recorder  # noqa: E402
from voisr.snippets import MIN_SUSTAINED_SPEECH_MS, find_snippet, has_speech_evidence  # noqa: E402

TRIGGERS = ["my email", "my address", "my phone number", "review prompt", "work email", "sign off"]
OTHERS = ["my emails", "my dress", "my mother", "sign up", "review", "in the office",
          "I checked my email", "send the review prompt to Sam"]
TAKES = 2  # of each trigger
SILENT_CLIPS = 5
SILENT = "(silent, tapping keys)"


def script(triggers=TRIGGERS, others=OTHERS):
    """(clip name, phrase) in the order to say them."""
    lines = [(f"trigger-{i + 1:02}", phrase)
             for i, phrase in enumerate(p for p in triggers for _ in range(TAKES))]
    lines += [(f"other-{i + 1:02}", phrase) for i, phrase in enumerate(others)]
    return lines + [(f"silent-{i + 1:02}", SILENT) for i in range(SILENT_CLIPS)]


def record(folder: Path, triggers, recorder_factory=Recorder, ask=input) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    recorder = recorder_factory()
    try:
        for name, phrase in script(triggers):
            if phrase == SILENT:
                ask("\nSay nothing. Tap right Command 3 or 4 times, about a second, "
                    "then hold it.   Enter to start... ")
            else:
                ask(f'\nSay: "{phrase}"   Enter to start... ')
            wav = folder / f"{name}.wav"
            recorder.start(wav)
            ask("  recording, Enter to stop... ")
            error = recorder.stop()
            if not wav.exists() or wav.stat().st_size < 1000:
                raise SystemExit(f"no audio recorded (microphone access?) {error.strip()[:200]}")
            (folder / f"{name}.txt").write_text(phrase + "\n")
    finally:
        recorder.close()
    print(f"\nRecorded to {folder}. Now run: python3 scripts/test_my_triggers.py score")


def clips_in(folder: Path) -> list[tuple[str, str, Path]]:
    return [(wav.stem, wav.with_suffix(".txt").read_text().strip(), wav)
            for wav in sorted(folder.glob("*.wav"))]


def saved_triggers(clips) -> dict:
    return {phrase: "x" for name, phrase, _ in clips if name.startswith("trigger")}


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


def report(rows, conditions) -> None:
    once = [r for r in rows if r["condition"] == conditions[0]]
    print(f"speech gate: {dict(Counter(r['gate'] for r in once))}")
    said = [r["sustained_ms"] for r in once if r["name"].startswith("trigger")]
    silent = [r["sustained_ms"] for r in once if r["name"].startswith("silent")]
    print(f"sustained speech, guard needs {MIN_SUSTAINED_SPEECH_MS} ms: "
          f"triggers lowest {min(said, default='-')} ms, silent clips highest {max(silent, default='-')} ms")
    for condition in conditions:
        group = [r for r in rows if r["condition"] == condition]
        wanted = [r for r in group if r["name"].startswith("trigger")]
        hits = [r for r in wanted if r["fired"] == r["phrase"]]
        wrong = [r for r in group if r["fired"] is not None and r not in hits]
        others = len(group) - len(wanted)
        print(f"\n{condition}: triggers fire {len(hits)}/{len(wanted)}; "
              f"other clips fire {len([r for r in wrong if r not in wanted])}/{others}")
        for row in wanted:
            if row not in hits and row not in wrong:
                print(f'  missed  "{row["phrase"]}" came out {row["raw"]!r}')
        for row in wrong:
            print(f'  MISFIRE "{row["phrase"]}" came out {row["raw"]!r}, pastes "{row["fired"]}"')
        for row in group:
            if row["held_back"]:
                print(f'  held back by the guard: "{row["phrase"]}" came out {row["raw"]!r}, '
                      f'{row["sustained_ms"]} ms')


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
