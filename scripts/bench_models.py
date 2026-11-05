#!/usr/bin/env python3
"""Benchmark the installed speech models on your own labelled clips.

Reports, per model: word error rate (WER), median and p95 latency, and peak
memory of whisper-cli.

CORPUS (about 30 clips, recorded by you, in the room you really dictate in)
  Synthesized `say` audio is too clean to choose a model with. Record real
  speech, at your normal distance from the mic, with Voisr's own recorder or
  Voice Memos exported as WAV. Put each clip in one folder as

      01-short-hello.wav  +  01-short-hello.txt     (what you actually said)

  The .txt is the reference: the exact words you spoke, written the way the
  result should read. Punctuation and capitals are ignored when scoring.
  Mix:
    ~8  short (2-5 s): a sentence or a fragment, "send it over tonight"
    ~6  long (20-40 s): a paragraph, thinking out loud, with a few ums
    ~6  dictionary names: your dictionary terms in a sentence (TradeMate,
        Voisr, suburbs, clients), said the way you do in real dictation
    ~5  numbers: prices, dates, times, phone digits, "twelve hundred and
        fifty dollars", "the 3rd of March"
    ~5  noisy room: same kind of sentences with a fan, a street, the TV or
        a café going. Name them noisy-*.wav so they are easy to find.
  Say each reference once, naturally. Do not re-read or tidy it afterwards:
  fix the .txt to match what you said, not what you meant.

  Clips and results stay out of git (the default folder `bench_corpus/` and
  `bench_results/` are in .gitignore). They contain your voice and words.

USAGE
  python3 scripts/bench_models.py bench_corpus
  python3 scripts/bench_models.py bench_corpus --models base.en small.en
  python3 scripts/bench_models.py bench_corpus --config config.json
  python3 scripts/bench_models.py bench_corpus --runs 3 --json bench_results/baseline.json

  --config uses that config's dictionary for the glossary prompt and the
  cleanup, as the app does. Run with nothing else heavy open: latency and
  memory depend on it. Each model runs in its own process, so its memory peak
  is its own.
"""
from __future__ import annotations

import argparse
import json
import re
import resource
import statistics
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_WORD_RE = re.compile(r"[^\w\s']|_", re.UNICODE)


def normalize(text: str) -> list[str]:
    """Words for scoring: lowercase, punctuation dropped, apostrophes inside
    words kept. Case and punctuation are not what a model is judged on."""
    text = text.lower().replace("’", "'")
    text = _WORD_RE.sub(" ", text)
    return [w.strip("'") for w in text.split() if w.strip("'")]


def edit_distance(ref: list[str], hyp: list[str]) -> int:
    """Word-level Levenshtein distance (substitutions + deletions + inserts)."""
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1]


def word_errors(reference: str, hypothesis: str) -> tuple[int, int]:
    """(errors, reference word count) for one clip."""
    ref, hyp = normalize(reference), normalize(hypothesis)
    return edit_distance(ref, hyp), len(ref)


def corpus_wer(pairs: list[tuple[str, str]]) -> float | None:
    """Corpus WER: total errors over total reference words, so long clips
    weigh more than short ones. None when there are no reference words."""
    errors = words = 0
    for reference, hypothesis in pairs:
        e, n = word_errors(reference, hypothesis)
        errors += e
        words += n
    return errors / words if words else None


def percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolated percentile (pct in 0..100); None for no data."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * pct / 100
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def find_clips(folder: Path) -> list[tuple[Path, str]]:
    """(wav, reference) for every WAV with a same-named non-empty .txt."""
    clips = []
    for wav in sorted(Path(folder).glob("*.wav")):
        txt = wav.with_suffix(".txt")
        if not txt.is_file():
            print(f"skip {wav.name}: no {txt.name}", file=sys.stderr)
            continue
        reference = txt.read_text(encoding="utf-8").strip()
        if not normalize(reference):
            print(f"skip {wav.name}: empty reference", file=sys.stderr)
            continue
        clips.append((wav, reference))
    return clips


def installed_models(models_dir: Path) -> list[str]:
    return sorted(p.name[len("ggml-"):-len(".bin")]
                  for p in Path(models_dir).glob("ggml-*.bin"))


def summarize(model: str, rows: list[dict], peak_mb: float | None) -> dict:
    latencies = [r["seconds"] for r in rows if r["seconds"] is not None]
    return {
        "model": model,
        "clips": len({r["clip"] for r in rows}),
        "wer": corpus_wer([(r["reference"], r["hypothesis"]) for r in rows]),
        "median_s": statistics.median(latencies) if latencies else None,
        "p95_s": percentile(latencies, 95),
        "peak_mb": peak_mb,
    }


def prepare_clip(wav: Path, workdir: Path) -> Path | None:
    """The audio the app would really transcribe: gated, then trimmed, on a
    copy (trim_wav edits in place; the corpus files must never change).
    None when the app's speech gate would reject the clip."""
    from voisr import speech_gate
    copy = Path(workdir) / wav.name
    shutil.copyfile(wav, copy)
    span = speech_gate.gate_wav(copy)
    if span is None:
        return copy  # unreadable to the gate: the app skips it and transcribes
    if span.verdict in ("all_zeros", "no_speech"):
        return None
    try:
        speech_gate.trim_wav(copy, span.start, span.end)
    except Exception:
        pass  # an untrimmed clip still transcribes
    return copy


def run_model(model: str, clips, runs: int, config: dict, models_dir: Path) -> dict:
    """Transcribe every clip with one model, in this process. whisper-cli is
    a child, so RUSAGE_CHILDREN's high-water mark is its peak (bytes on macOS)."""
    from voisr.cleanup import clean
    from voisr.transcriber import transcribe
    from voisr.ui import glossary_prompt

    dictionary = config.get("dictionary", {})
    prompt = glossary_prompt(dictionary)
    from voisr.models import filename_for
    path = Path(models_dir) / filename_for(model)
    rows = []
    with tempfile.TemporaryDirectory(prefix="voisr-bench-") as work:
        prepared = [(wav, prepare_clip(wav, Path(work)), ref) for wav, ref in clips]
        usable = [p for _, p, _ in prepared if p is not None]
        if usable:
            transcribe(usable[0], model_path=path, prompt=prompt)  # warm-up, not timed
        for wav, audio, reference in prepared:
            if audio is None:
                # the app would refuse this clip, so there is nothing to
                # time; it still counts against accuracy (empty result)
                rows.append({"clip": wav.name, "seconds": None, "gated": True,
                             "reference": reference, "hypothesis": ""})
                continue
            for _ in range(runs):
                started = time.perf_counter()
                raw = transcribe(audio, model_path=path, prompt=prompt)
                seconds = time.perf_counter() - started
                rows.append({"clip": wav.name, "seconds": seconds,
                             "reference": reference,
                             "hypothesis": clean(raw, dictionary=dictionary)})
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / (1024 * 1024)
    result = summarize(model, rows, peak)
    result["rows"] = rows
    return result


def _fmt(value, spec, suffix=""):
    return "-" if value is None else format(value, spec) + suffix


def print_table(results: list[dict]) -> None:
    print(f"{'model':<14}{'clips':>6}{'WER':>9}{'median':>10}{'p95':>10}{'peak MB':>10}")
    for r in results:
        wer = None if r["wer"] is None else r["wer"] * 100
        print(f"{r['model']:<14}{r['clips']:>6}{_fmt(wer, '.1f', '%'):>9}"
              f"{_fmt(r['median_s'], '.2f', ' s'):>10}"
              f"{_fmt(r['p95_s'], '.2f', ' s'):>10}{_fmt(r['peak_mb'], '.0f'):>10}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Benchmark installed whisper models on labelled clips.",
        epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpus", type=Path, help="folder of NAME.wav + NAME.txt pairs")
    ap.add_argument("--models", nargs="+", help="model ids (default: all installed)")
    ap.add_argument("--models-dir", type=Path, default=ROOT / "models")
    ap.add_argument("--config", type=Path, help="config.json for its dictionary")
    ap.add_argument("--runs", type=int, default=1, help="timed runs per clip")
    ap.add_argument("--json", type=Path, help="also write full results here")
    ap.add_argument("--single", help=argparse.SUPPRESS)  # internal: one model
    args = ap.parse_args(argv)

    clips = find_clips(args.corpus)
    if not clips:
        print(f"no WAV+TXT pairs in {args.corpus} — see --help for the recording guide",
              file=sys.stderr)
        return 2
    config = json.loads(args.config.read_text()) if args.config else {}

    if args.single:  # child process: print one model's result as JSON
        print(json.dumps(run_model(args.single, clips, args.runs, config,
                                   args.models_dir)))
        return 0

    models = args.models or installed_models(args.models_dir)
    if not models:
        print(f"no ggml-*.bin models in {args.models_dir}", file=sys.stderr)
        return 2
    print(f"{len(clips)} clips, {args.runs} run(s) each, models: {', '.join(models)}")
    results = []
    for model in models:
        cmd = [sys.executable, str(Path(__file__).resolve()), str(args.corpus),
               "--single", model, "--runs", str(args.runs),
               "--models-dir", str(args.models_dir)]
        if args.config:
            cmd += ["--config", str(args.config)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"{model}: failed: {proc.stderr.strip()[-300:]}", file=sys.stderr)
            continue
        results.append(json.loads(proc.stdout.strip().splitlines()[-1]))
    print_table(results)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results, indent=2))
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
