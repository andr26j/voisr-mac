"""Evidence before a snippet fires, hints only where a trigger could be said, and
recited prompts. Whisper is faked, with the prompts it was sent recorded; the
speech gate is real, on clips made here."""
import array
import math
import random
import wave

import pytest

from tests.test_context_api import make_api
from voisr.speech_gate import analyze

SECRET = "12 Home Street"
SNIPPETS = {"my email": SECRET, "review prompt": "Please review"}
RATE = 16000


def noise(seconds, rng, db=-58.0):
    amp = 32768 * 10 ** (db / 20)
    return [rng.gauss(0, amp) for _ in range(int(seconds * RATE))]


def add_thump(samples, at, rng, peak=6000.0, length=0.08):
    start, freq = int(at * RATE), rng.uniform(90, 400)
    for i in range(int(length * RATE)):
        if start + i >= len(samples):
            break
        t = i / RATE
        samples[start + i] += (math.exp(-t / (length / 4)) * peak
                               * (0.7 * math.sin(2 * math.pi * freq * t) + 0.3 * rng.gauss(0, 1)))


def tap_then_hold(seed=1):
    """Room noise and four made-up key thumps: a tap's press and release, the
    hold's press, then its release. Not a recording of a real key."""
    rng = random.Random(seed)
    samples = noise(2.0, rng)
    for at in (0.05, 0.14, 0.32, 1.9):
        add_thump(samples, at, rng)
    return samples


def spoken(seconds=1.0, seed=2):
    """Made-up voiced speech: a noise bed with a steady buzz well above it."""
    rng = random.Random(seed)
    samples = noise(seconds + 0.5, rng)
    for i in range(int(0.25 * RATE), int((0.25 + seconds) * RATE)):
        samples[i] += 4000 * math.sin(2 * math.pi * 140 * i / RATE)
    return samples


def write(path, samples):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(array.array(
            "h", (max(-32768, min(32767, int(s))) for s in samples)).tobytes())


class ClipRecorder:
    samples = None

    def start(self, path):
        self.path = path

    def stop(self):
        write(self.path, type(self).samples)
        return ""

    def cancel(self):
        pass

    def close(self):
        pass


def guard_api(tmp_path, samples, raw="My email.", **kwargs):
    recorder = type("R", (ClipRecorder,), {"samples": samples})
    pasted = []
    api = make_api(tmp_path, transcribe_fn=lambda wav: raw,
                   paste_fn=lambda text: pasted.append(text) or True, **kwargs)
    api._recordings._recorder_factory = recorder
    api.set_config({"snippets": SNIPPETS})
    return api, pasted


def dictate(api):
    api.start_recording(prewarm=False)
    return api.stop_recording(paste=True)


@pytest.mark.parametrize("seed", range(8))
def test_tap_then_hold_clips_pass_the_gate_but_never_fire_a_snippet(tmp_path, seed):
    samples = tap_then_hold(seed)
    assert analyze(array.array("h", (max(-32768, min(32767, int(s))) for s in samples))).verdict == "speech"
    api, pasted = guard_api(tmp_path, samples)
    out = dictate(api)
    assert pasted == ["My email."] and "error" not in out
    item = api.get_history()[0]
    assert item["text"] == "My email." and "snippet" not in item
    assert SECRET not in (tmp_path / "history.jsonl").read_text()
    api.close()


def test_real_speech_still_fires(tmp_path):
    api, pasted = guard_api(tmp_path, spoken())
    dictate(api)
    assert pasted == [SECRET]
    assert api.get_history()[0]["snippet"] is True
    api.close()


def test_no_gate_reading_means_no_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr("voisr.speech_gate.gate_wav", lambda wav: None)
    api, pasted = guard_api(tmp_path, spoken())
    dictate(api)
    assert pasted == ["My email."]
    api.close()


def test_the_gate_itself_is_unchanged_for_other_dictation(tmp_path):
    api, pasted = guard_api(tmp_path, tap_then_hold(), raw="Subs by someone.")
    dictate(api)
    assert pasted == ["Subs by someone."]
    api.close()
