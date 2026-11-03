#!/usr/bin/env python3
"""Do snippet triggers work in YOUR voice? About two minutes for 20 clips.

scripts/bench_triggers.py measured synthetic voices only. This records you, in
the room you really dictate in, then runs each clip the way the app does:
speech gate, trim, then real Whisper on the kept-loaded server and whisper-cli,
with and without triggers as hints.

    python3 scripts/test_my_triggers.py record
    python3 scripts/test_my_triggers.py score

record says each phrase; press Enter to start and Enter to stop. Speak as you
would to the app. score reports two things: how many triggers came out exact,
and how many other phrases came out as a trigger, which is the costly mistake,
as that pastes saved text you didn't ask for.

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
sys.path.insert(0, str(ROOT))

from voisr import speech_gate  # noqa: E402
from voisr.app import load_config  # noqa: E402
from voisr.recorder import Recorder  # noqa: E402
from voisr.snippets import find_snippet, normalize_trigger  # noqa: E402
from voisr.transcriber import transcribe_routed  # noqa: E402
from voisr.ui import glossary_prompt  # noqa: E402
from voisr.whisper_server import WhisperServer  # noqa: E402

TRIGGERS = ["my email", "my address", "my phone number", "review prompt", "work email", "sign off"]
OTHERS = ["my emails", "my dress", "my mother", "sign up", "review", "in the office",
          "I checked my email", "send the review prompt to Sam"]
TAKES = 2  # of each trigger


def script(triggers=TRIGGERS, others=OTHERS):
    """(clip name, phrase) in the order to say them."""
    lines = [(f"trigger-{i + 1:02}", phrase)
             for i, phrase in enumerate(p for p in triggers for _ in range(TAKES))]
    return lines + [(f"other-{i + 1:02}", phrase) for i, phrase in enumerate(others)]


def record(folder: Path, triggers, recorder_factory=Recorder, ask=input) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    recorder = recorder_factory()
    try:
        for name, phrase in script(triggers):
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


def score(clips, transcribe, conditions) -> list[dict]:
    """A row per clip and condition. transcribe(wav, condition) -> Whisper's
    text. A clip the gate refuses counts as a miss: the app would drop it."""
    triggers = {phrase: "x" for name, phrase, _ in clips if name.startswith("trigger")}
    rows = []
    for name, phrase, wav in clips:
        span = speech_gate.gate_wav(wav)
        passed = span is None or span.verdict == "speech"
        if span is not None and passed:
            speech_gate.trim_wav(wav, span.start, span.end)
        for condition in conditions:
            raw = transcribe(wav, condition) if passed else ""
            found = find_snippet(raw, triggers)
            rows.append({"name": name, "phrase": phrase, "condition": condition, "raw": raw,
                         "gate": span.verdict if span else "unread",
                         "fired": found.trigger if found else None})
    return rows


def report(rows, conditions) -> None:
    gates = Counter(r["gate"] for r in rows if r["condition"] == conditions[0])
    print(f"speech gate: {dict(gates)}")
    for condition in conditions:
        group = [r for r in rows if r["condition"] == condition]
        wanted = [r for r in group if r["name"].startswith("trigger")]
        other = [r for r in group if not r["name"].startswith("trigger")]
        hits = [r for r in wanted if r["fired"] is not None
                and normalize_trigger(r["fired"]) == normalize_trigger(r["phrase"])]
        wrong = [r for r in group if r not in hits and r["fired"] is not None]
        print(f"\n{condition}: triggers exact {len(hits)}/{len(wanted)}; "
              f"misfires {len(wrong)}/{len(other) + len(wanted) - len(hits)} chances")
        for row in wanted:
            if row not in hits and row not in wrong:
                print(f'  missed  "{row["phrase"]}" came out {row["raw"]!r}')
        for row in wrong:
            print(f'  MISFIRE "{row["phrase"]}" came out {row["raw"]!r}, pastes "{row["fired"]}"')


def run_score(folder: Path, model: Path, dictionary: dict) -> None:
    clips = clips_in(folder)
    if not clips:
        raise SystemExit(f"no clips in {folder}: run record first")
    triggers = list(dict.fromkeys(p for n, p, _ in clips if n.startswith("trigger")))
    plain, hinted = glossary_prompt(dictionary), glossary_prompt(dictionary, (), triggers)
    prompts = {"server, no trigger hints": (plain, True), "server, trigger hints": (hinted, True),
               "whisper-cli, no trigger hints": (plain, False),
               "whisper-cli, trigger hints": (hinted, False)}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        server = WhisperServer(tmp / "state.json")

        def transcribe(wav, condition):
            prompt, warm = prompts[condition]
            return transcribe_routed(wav, model, prompt=prompt, cli_prompt=prompt or "",
                                     server=server if warm else None)[0]

        try:
            # trimmed copies: the recordings themselves stay as they were
            copies = [(n, p, Path(shutil.copy(w, tmp / w.name))) for n, p, w in clips]
            report(score(copies, transcribe, list(prompts)), list(prompts))
        finally:
            server.shutdown()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["record", "score"])
    ap.add_argument("--folder", type=Path, default=ROOT / "bench_corpus" / "triggers")
    ap.add_argument("--triggers", nargs="+", default=TRIGGERS, help="phrases you'd really use")
    ap.add_argument("--models-dir", type=Path, default=ROOT / "models")
    ap.add_argument("--model", default="ggml-small.en.bin")
    ap.add_argument("--config", type=Path, help="config.json, read for its Dictionary")
    args = ap.parse_args()
    if args.action == "record":
        record(args.folder, args.triggers)
    else:
        dictionary = load_config(args.config)["dictionary"] if args.config else {}
        run_score(args.folder, args.models_dir / args.model, dictionary)


if __name__ == "__main__":
    main()
