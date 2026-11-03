#!/usr/bin/env python3
"""Compare today's per-dictation whisper-cli with the kept-loaded server.

    python3 scripts/bench_warm.py [--model small.en] [--runs 5]

Uses synthetic speech (macOS `say`) of about 1, 5 and 30 seconds, so the
numbers are for synthetic clips, not human speech. Reads the model from
Voisr's models folder and never writes there. Starts one whisper-server on
127.0.0.1 and stops it before exiting."""
import argparse
import difflib
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voisr.audio import to_wav16k  # noqa: E402
from voisr.models import MODELS_DIR, filename_for  # noqa: E402
from voisr.transcriber import transcribe  # noqa: E402
from voisr.whisper_server import WhisperServer  # noqa: E402

PROMPT = "Glossary: Voisr, TradeMate."
CLIPS = {
    "1 s": "Send it to Sam.",
    "5 s": "Please email the team about the demo on Friday, and remind "
           "them to bring the signed contract.",
    "30 s": (
        "Good morning everyone. Thanks for joining the call on short notice. "
        "I want to walk through three things today. First, the Melbourne "
        "launch is on track, and the tram wrap artwork goes to print on "
        "Monday. Second, the new dashboard is almost ready, so please test "
        "your logins before Wednesday and report anything odd. Third, we "
        "need a volunteer to book the venue for the end of year dinner. "
        "Let me know by Thursday, and I will confirm the numbers with the "
        "caterer. Thanks again, and have a great week."),
}


def make_clip(text, workdir):
    aiff = workdir / "c.aiff"
    subprocess.run(["say", "-v", "Samantha", "-o", str(aiff), text], check=True)
    wav = workdir / f"c{abs(hash(text))}.wav"
    to_wav16k(aiff, wav)
    return wav


def seconds(wav):
    return wav.stat().st_size / 32000


def rss_mb(pid):
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                         capture_output=True, text=True).stdout.strip()
    return int(out) / 1024 if out else float("nan")


def cli_peak_mb(model, wav):
    """Peak resident memory of one whisper-cli run, from /usr/bin/time -l."""
    from voisr.paths import find_binary
    out = subprocess.run(
        ["/usr/bin/time", "-l", find_binary("whisper-cli"), "-m", str(model),
         "-f", str(wav), "-l", "en", "--beam-size", "5", "--no-prints",
         "--output-txt", "--output-file", str(wav) + ".m"],
        capture_output=True, text=True).stderr
    m = re.search(r"(\d+)\s+maximum resident set size", out)
    Path(str(wav) + ".m.txt").unlink(missing_ok=True)
    return int(m.group(1)) / 1048576 if m else float("nan")


def norm(text):
    return " ".join(text.split())


def timed(fn):
    t = time.perf_counter()
    result = fn()
    return (time.perf_counter() - t) * 1000, result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="small.en")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--models-dir", type=Path, default=MODELS_DIR)
    args = ap.parse_args()
    model = args.models_dir / filename_for(args.model)
    if not model.is_file():
        sys.exit(f"model not installed: {model}")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        clips = {k: make_clip(v, work) for k, v in CLIPS.items()}
        server = WhisperServer(work / "state.json")
        rows = []
        try:
            for name, wav in clips.items():
                cli = [timed(lambda: transcribe(wav, model_path=model, prompt=PROMPT))
                       for _ in range(args.runs)]
                cli_text = cli[-1][1]
                # first dictation after an unload, with no head start at all
                first = []
                for _ in range(3):
                    server.unload()
                    first.append(timed(lambda: server.transcribe(
                        wav, model, prompt=PROMPT)))
                # first dictation when the load began 2 s earlier (a 2 s hold)
                head = []
                for _ in range(3):
                    server.unload()
                    server.prewarm(model)
                    time.sleep(2.0)
                    head.append(timed(lambda: server.transcribe(
                        wav, model, prompt=PROMPT)))
                server.transcribe(wav, model, prompt=PROMPT)   # ensure loaded
                warm = [timed(lambda: server.transcribe(wav, model, prompt=PROMPT))
                        for _ in range(args.runs)]
                warm_texts = [norm(r[0]) for _, r in warm]
                warm_text = warm[-1][1][0]
                pid = int(re.search(r'"server": (\d+)',
                                    (work / "state.json").read_text()).group(1))
                rows.append({
                    "clip": name, "secs": seconds(wav),
                    "cli_ms": statistics.median(t for t, _ in cli),
                    "warm_ms": statistics.median(t for t, _ in warm),
                    "first_ms": statistics.median(t for t, _ in first),
                    "head2_ms": statistics.median(t for t, _ in head),
                    "server_rss": rss_mb(pid),
                    "cli_peak": cli_peak_mb(model, wav),
                    "same": sum(t == norm(cli_text) for t in warm_texts),
                    "runs": len(warm_texts),
                    "similarity": statistics.mean(
                        difflib.SequenceMatcher(None, norm(cli_text).split(),
                                                t.split()).ratio()
                        for t in warm_texts),
                    "cli_text": cli_text, "warm_text": warm_text})
        finally:
            server.shutdown()
    print(f"\nmodel {args.model}, {args.runs} runs, medians, SYNTHETIC clips (macOS say)\n")
    print("| clip | audio | whisper-cli today | warm | first after unload (no head start) "
          "| first after unload (2 s head start) | server resident | cli peak | warm text identical to cli | word similarity |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['clip']} | {r['secs']:.1f} s | {r['cli_ms']:.0f} ms | "
              f"{r['warm_ms']:.0f} ms | {r['first_ms']:.0f} ms | {r['head2_ms']:.0f} ms | "
              f"{r['server_rss']:.0f} MB | {r['cli_peak']:.0f} MB | "
              f"{r['same']}/{r['runs']} | {r['similarity']*100:.1f}% |")
    for r in rows:
        if r["same"] != r["runs"]:
            print(f"\nDIFF {r['clip']}\n cli : {r['cli_text']!r}\n warm: {r['warm_text']!r}")


if __name__ == "__main__":
    main()
