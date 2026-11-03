#!/usr/bin/env python3
"""How often does a short snippet trigger come out of real Whisper exactly?

Speaks each phrase with macOS `say`, shapes it like a mic clip (room noise, a
little silence each side), then takes the app's path: speech gate, trim, and
transcribe_routed with a Glossary prompt. A hit is what snippets would match.

The voices are synthetic: this says nothing certain about any person's voice.
Only reads the model. Never touches Voisr's settings.

    python3 scripts/bench_triggers.py --models /path/to/models --out results.json
"""
import argparse
import array
import json
import random
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voisr import speech_gate  # noqa: E402
from voisr.snippets import normalize_trigger  # noqa: E402
from voisr.transcriber import transcribe_routed  # noqa: E402
from voisr.ui import glossary_prompt  # noqa: E402
from voisr.whisper_server import WhisperServer  # noqa: E402

PHRASES = ["my email", "my address", "my phone number", "my mobile", "home address",
           "work email", "my website", "email signature", "sign off", "review prompt",
           "code review prompt", "bug report template", "standup notes", "commit message",
           "my ABN", "my LinkedIn", "bank details", "out of office"]
VOICES = ["Samantha", "Daniel", "Karen", "Moira", "Rishi", "Tessa", "Eddy (English (UK))"]
RATES = [None, 230]  # say's default, and brisk
# Made up, so no real dictionary or screen is read.
DICTIONARY = {"tariq": "Tareq", "trade mate": "TradeMate", "voicer": "Voisr",
              "clasp": "Clasp", "taqiq": "Takiq"}
SCREEN = ["Zendesk", "Priya", "Kubernetes", "Northcote", "Acme"]
PROMPTS = {
    "none": (None, ""),
    "dictionary": (glossary_prompt(DICTIONARY), glossary_prompt(DICTIONARY)),
    "dictionary+screen": (glossary_prompt(DICTIONARY, SCREEN), glossary_prompt(DICTIONARY, SCREEN)),
    "triggers-as-hints": (glossary_prompt(dict(DICTIONARY, **{p: p for p in PHRASES})),) * 2,
}
NOISE_DBFS = -58.0
LEAD_S, TAIL_S = 0.25, 0.35
SAY_TIMEOUT_S = 30


def speak(text: str, voice: str, rate, out: Path) -> None:
    cmd = ["say", "-v", voice, "-o", str(out), "--data-format=LEI16@16000", "--file-format=WAVE"]
    if rate:
        cmd += ["-r", str(rate)]
    # say can hang for good (seen: 49 min on one clip), so bound it and retry
    for attempt in range(3):
        try:
            subprocess.run(cmd + [text], check=True, timeout=SAY_TIMEOUT_S)
            return
        except subprocess.TimeoutExpired:
            print(f"say timed out: {voice!r} {text!r} (try {attempt + 1})", flush=True)
    raise RuntimeError(f"say kept hanging on {voice!r}")


def as_mic_clip(path: Path, seed: int) -> None:
    """In place: padded, with steady noise, so the gate sees a room and not
    digital silence."""
    import wave
    with wave.open(str(path), "rb") as w:
        params = w.getparams()
        speech = array.array("h")
        speech.frombytes(w.readframes(w.getnframes()))
    rng = random.Random(seed)
    amp = 32768 * 10 ** (NOISE_DBFS / 20)
    lead, tail = [int(s * params.framerate) for s in (LEAD_S, TAIL_S)]
    samples = [0] * lead + list(speech) + [0] * tail
    noisy = array.array("h", (max(-32768, min(32767, int(s + rng.gauss(0, amp))))
                              for s in samples))
    with wave.open(str(path), "wb") as w:
        w.setparams(params)
        w.writeframes(noisy.tobytes())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", required=True, type=Path)
    ap.add_argument("--model", default="ggml-small.en.bin")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    model = args.models / args.model
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        server = WhisperServer(tmp / "state.json")
        try:
            seed = 0
            for phrase in PHRASES:
                for voice in VOICES:
                    for rate in RATES:
                        seed += 1
                        wav = tmp / f"{seed}.wav"
                        speak(phrase, voice, rate, wav)
                        as_mic_clip(wav, seed)
                        span = speech_gate.gate_wav(wav)
                        if span is not None and span.verdict == "speech":
                            speech_gate.trim_wav(wav, span.start, span.end)
                        base = dict(phrase=phrase, voice=voice, rate=rate,
                                    gate=span.verdict, speech_ms=span.speech_ms,
                                    seconds=wav.stat().st_size / 32000)
                        runs = [("warm", name, server) for name in PROMPTS]
                        runs.append(("cli", "dictionary", None))
                        for route, name, srv in runs:
                            prompt, cli_prompt = PROMPTS[name]
                            raw, info = transcribe_routed(wav, model, prompt=prompt,
                                                          cli_prompt=cli_prompt, server=srv)
                            rows.append(dict(base, route=info["mode"], prompt=name, raw=raw,
                                             hit=normalize_trigger(raw) == normalize_trigger(phrase)))
                        wav.unlink()
                args.out.write_text(json.dumps(rows, indent=1))  # a hang loses one phrase
                print(f"done: {phrase}", flush=True)
        finally:
            server.shutdown()
    args.out.write_text(json.dumps(rows, indent=1))
    summarise(rows)


def summarise(rows) -> None:
    gated = [r for r in rows if r["route"] == "warm" and r["prompt"] == "none"]
    print("gate:", Counter(r["gate"] for r in gated),
          f"shortest {min(r['seconds'] for r in gated):.2f} s",
          f"least speech {min(r['speech_ms'] for r in gated)} ms")
    by = defaultdict(list)
    for row in rows:
        by[row["route"], row["prompt"]].append(row["hit"])
    for key, hits in sorted(by.items()):
        print(f"{key[0]:5} {key[1]:18} {sum(hits)}/{len(hits)} = {sum(hits) / len(hits):.0%}")
    per = defaultdict(list)
    for row in rows:
        if row["route"] == "warm" and row["prompt"] == "dictionary":
            per[row["phrase"]].append(row)
    for phrase, group in per.items():
        misses = Counter(r["raw"] for r in group if not r["hit"])
        print(f"{phrase:22} {sum(r['hit'] for r in group)}/{len(group)}  {dict(misses)}")


if __name__ == "__main__":
    main()
