#!/usr/bin/env python3
"""How do snippet triggers fare in real Whisper, and what do hints cost?

Speaks each phrase with macOS `say`, shapes it like a mic clip (room noise, a
little silence each side), then takes the app's own path: speech gate, trim,
Api._transcribe_with_terms (prompt, clip-length rule, echo retry), then the
sustained-speech guard. A hit is a clip the app would expand.

Then the costs: speech that isn't a trigger and made-up silent tap-then-holds,
with and without snippets saved. The voices and thumps are synthetic: this says
nothing certain about any person's voice or a real keyboard; for that, see
scripts/test_my_triggers.py. Only reads the model. Never touches Voisr's settings.

    python3 scripts/bench_triggers.py --models /path/to/models --out results.json
"""
import argparse
import array
import json
import math
import random
import subprocess
import sys
import tempfile
import wave
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voisr import speech_gate  # noqa: E402
from voisr.history import HistoryStore  # noqa: E402
from voisr.snippets import find_snippet, find_spoken_snippet  # noqa: E402
from voisr.ui import Api  # noqa: E402
from voisr.whisper_server import WhisperServer  # noqa: E402

PHRASES = ["my email", "my address", "my phone number", "my mobile", "home address",
           "work email", "my website", "email signature", "sign off", "review prompt",
           "code review prompt", "bug report template", "standup notes", "commit message",
           "my ABN", "my LinkedIn", "bank details", "out of office"]
VOICES = ["Samantha", "Daniel", "Karen", "Moira", "Rishi", "Tessa", "Eddy (English (UK))"]
RATES = [None, 230, 110]  # say's default, brisk and slow
# Made up, so no real dictionary or screen is read.
DICTIONARY = {"tariq": "Tareq", "trade mate": "TradeMate", "voicer": "Voisr",
              "clasp": "Clasp", "taqiq": "Takiq"}
SCREEN = ["Zendesk", "Priya", "Kubernetes", "Northcote", "Acme"]
SAVED = {phrase: "saved text" for phrase in PHRASES}
# route, whether snippets are saved, on-screen terms
CONDITIONS = {
    "server, no snippets": ("server", False, []),
    "server, no snippets, screen": ("server", False, SCREEN),
    "server, snippets": ("server", True, []),
    "server, snippets, screen": ("server", True, SCREEN),
    "cli, snippets, screen": ("cli", True, SCREEN),
}
NEAR = ["my emails", "email", "my dress", "my mother", "my mobile phone", "home", "work",
        "at home", "sign on", "sign up", "review", "prompt", "code review", "bug report",
        "stand up", "commit", "the message", "my bank", "bank holiday", "details",
        "in the office", "out of milk", "my link", "thank you", "okay", "yes please"]
SENTENCES = ["I checked my email this morning.",
             "Can you send the review prompt to Sam?",
             "My address changed last year.",
             "We should talk about the bug report template tomorrow.",
             "I am out of office until Monday.",
             "The meeting moved to three o'clock on Friday.",
             "Before we ship this I would like someone to read through the whole bug report "
             "template again, and then send my email address to the people at the office."]
COST_VOICES = VOICES[:3] + VOICES[-1:]
TAP_HOLDS = 60
NOISE_DBFS = -58.0
LEAD_S, TAIL_S = 0.25, 0.35
SAY_TIMEOUT_S = 30


class AppPath:
    """The app's own transcription: the real Api with no mic and no paste."""

    def __init__(self, tmp: Path, models: Path, model: str, *, server: bool):
        self.model = model
        self.api = Api(config_path=tmp / f"config-{server}.json",
                       history=HistoryStore(tmp / f"history-{server}.jsonl"), models_dir=models,
                       warm_server=WhisperServer(tmp / "state.json") if server else None,
                       clipboard_fn=lambda text: None, paste_fn=lambda text: False)

    def transcribe(self, wav, dictionary, snippets, terms) -> str:
        cfg = {"model": self.model, "dictionary": dictionary, "snippets": snippets}
        return self.api._transcribe_with_terms(wav, cfg, terms)[0]

    def close(self) -> None:
        self.api.close()


def gate_and_trim(wav):
    """As _run_session does, before transcribing."""
    span = speech_gate.gate_wav(wav)
    if span is not None and span.verdict == "speech":
        speech_gate.trim_wav(wav, span.start, span.end)
    return span


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


def tap_then_hold(path: Path, seed: int) -> None:
    """Silence but for key thumps: a tap's press and release, the hold's press,
    the hold's release. Made up, from room noise and short decaying thuds. Not a
    recording of a real key."""
    rng = random.Random(seed)
    rate, hold = 16000, rng.uniform(0.8, 3.0)
    samples = [rng.gauss(0, 32768 * 10 ** (rng.choice((-70, -60, -52)) / 20))
               for _ in range(int((0.25 + hold) * rate))]
    peak = rng.choice((500, 2000, 8000))
    gap = rng.uniform(0.1, 0.25)
    for at in (0.0, rng.uniform(0.06, 0.12), gap + 0.12, 0.15 + hold - rng.uniform(0, 0.05)):
        length, freq = rng.uniform(0.03, 0.14), rng.uniform(90, 400)
        loud = peak * rng.uniform(0.5, 1.2)
        for i in range(int(length * rate)):
            at_i = int(at * rate) + i
            if at_i >= len(samples):
                break
            t = i / rate
            samples[at_i] += math.exp(-t / (length / 4)) * loud * (
                0.7 * math.sin(2 * math.pi * freq * t) + 0.3 * rng.gauss(0, 1))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(array.array(
            "h", (max(-32768, min(32767, int(s))) for s in samples)).tobytes())


def run_triggers(paths, tmp, out, rows) -> None:
    seed = 0
    for phrase in PHRASES:
        for voice in VOICES:
            for rate in RATES:
                seed += 1
                wav = tmp / f"{seed}.wav"
                speak(phrase, voice, rate, wav)
                as_mic_clip(wav, seed)
                span = gate_and_trim(wav)
                base = dict(kind="trigger", phrase=phrase, voice=voice, rate=rate, gate=span.verdict,
                            sustained_ms=span.sustained_ms, seconds=wav.stat().st_size / 32000)
                for name, (route, saved, terms) in CONDITIONS.items():
                    raw = paths[route].transcribe(wav, DICTIONARY, SAVED if saved else {}, terms)
                    found = find_spoken_snippet(raw, SAVED, span)
                    rows.append(dict(base, condition=name, raw=raw,
                                     hit=found is not None and found.trigger == phrase))
                wav.unlink()
        out.write_text(json.dumps(rows, indent=1))  # a hang loses one phrase
        print(f"done: {phrase}", flush=True)


def run_costs(paths, tmp, rows) -> None:
    server, seed = paths["server"], 1000

    def measure(kind, label, wav, voice=None):
        span = gate_and_trim(wav)
        base = dict(kind=kind, phrase=label, voice=voice, gate=span.verdict,
                    speech_ms=span.speech_ms, sustained_ms=span.sustained_ms)
        if span.verdict != "speech":
            rows.append(dict(base, condition="gated", raw="", fired=None, unguarded=None))
            return
        for name, saved in (("no snippets", {}), ("snippets", SAVED)):
            for terms, suffix in (([], ""), (SCREEN, ", screen")):
                raw = server.transcribe(wav, DICTIONARY, saved, terms)
                fired, unguarded = find_spoken_snippet(raw, SAVED, span), find_snippet(raw, SAVED)
                rows.append(dict(base, condition=name + suffix, raw=raw,
                                 fired=fired.trigger if fired else None,
                                 unguarded=unguarded.trigger if unguarded else None))

    for kind, items in (("near", NEAR), ("sentence", SENTENCES)):
        for text in items:
            for voice in COST_VOICES:
                seed += 1
                wav = tmp / f"{seed}.wav"
                speak(text, voice, None, wav)
                as_mic_clip(wav, seed)
                measure(kind, text, wav, voice)
                wav.unlink()
        print(f"done: {kind}", flush=True)
    for i in range(TAP_HOLDS):
        wav = tmp / f"tap{i}.wav"
        tap_then_hold(wav, 5000 + i)
        measure("tap_hold", f"tap{i}", wav)
        wav.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", required=True, type=Path)
    ap.add_argument("--model", default="small.en")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--costs-only", action="store_true")
    args = ap.parse_args()
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        paths = {"server": AppPath(tmp, args.models, args.model, server=True),
                 "cli": AppPath(tmp, args.models, args.model, server=False)}
        try:
            if not args.costs_only:
                run_triggers(paths, tmp, args.out, rows)
            run_costs(paths, tmp, rows)
        finally:
            for path in paths.values():
                path.close()
    args.out.write_text(json.dumps(rows, indent=1))
    summarise(rows)


def summarise(rows) -> None:
    triggers = [r for r in rows if r["kind"] == "trigger"]
    if triggers:
        first = [r for r in triggers if r["condition"] == next(iter(CONDITIONS))]
        print("gate:", Counter(r["gate"] for r in first),
              f"longest clip {max(r['seconds'] for r in first):.2f} s",
              f"least sustained {min(r['sustained_ms'] for r in first)} ms")
        by = defaultdict(list)
        for row in triggers:
            by[row["condition"]].append(row["hit"])
        for name, hits in by.items():
            print(f"{name:30} {sum(hits)}/{len(hits)} = {sum(hits) / len(hits):.0%}")
        final = [r for r in triggers if r["condition"] == "server, snippets, screen"]
        per = defaultdict(list)
        for row in final:
            per[row["phrase"]].append(row)
        for phrase, group in per.items():
            misses = Counter(r["raw"] for r in group if not r["hit"])
            print(f"{phrase:22} {sum(r['hit'] for r in group)}/{len(group)}  {dict(misses)}")
    for kind in ("near", "sentence", "tap_hold"):
        group = [r for r in rows if r["kind"] == kind]
        gated = sum(1 for r in group if r["condition"] == "gated")
        for name in ("no snippets", "no snippets, screen", "snippets", "snippets, screen"):
            part = [r for r in group if r["condition"] == name]
            fired = Counter((r["phrase"], r["fired"]) for r in part if r["fired"])
            held = Counter((r["phrase"], r["unguarded"]) for r in part
                           if r["unguarded"] and not r["fired"])
            print(f"{kind:9} {name:20} fired {sum(fired.values())}/{len(part)} {dict(fired)}"
                  f" held back {sum(held.values())} {dict(held)}"
                  + (f" gate refused {gated}" if gated and name == "no snippets" else ""))
        for terms in ("", ", screen"):
            with_terms = {(r["phrase"], r["voice"]): r["raw"] for r in group
                          if r["condition"] == "no snippets" + terms}
            changed = [(r["phrase"], with_terms[(r["phrase"], r["voice"])], r["raw"])
                       for r in group if r["condition"] == "snippets" + terms
                       and with_terms[(r["phrase"], r["voice"])] != r["raw"]]
            print(f"{kind:9} words changed by hints{terms}: {len(changed)}/{len(with_terms)}")
            if kind == "sentence":
                for change in changed:
                    print("   ", change)


if __name__ == "__main__":
    main()
