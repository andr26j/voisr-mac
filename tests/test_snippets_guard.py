"""Evidence before a snippet fires, hints only where a trigger could be said, and
recited prompts. Whisper is faked, with the prompts it was sent recorded; the
speech gate is real, on clips made here."""
import array
import math
import random
import wave

import pytest

from tests.test_context_api import make_api
from voisr.snippets import trigger_hints, triggers_for_clip
from voisr.speech_gate import analyze
from voisr.ui import wav_seconds

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


def test_a_held_back_snippet_is_logged_without_naming_it(tmp_path, capsys):
    api, _ = guard_api(tmp_path, tap_then_hold())
    dictate(api)
    api.close()
    out = capsys.readouterr().out
    assert "snippet held back" in out and "email" not in out.casefold() and SECRET not in out


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


# ---- hints by clip length


def test_only_triggers_a_clip_could_hold_are_hinted():
    short, long_ = "my email", "the standard code review prompt for pull requests"
    assert triggers_for_clip([short, long_], 1.0) == [short, long_]
    assert triggers_for_clip([short, long_], 5.0) == [long_]
    assert triggers_for_clip([short, long_], 30.0) == []
    assert triggers_for_clip([short, long_], -1) == [], "unknown length: no hints"


def prompts_for(tmp_path, monkeypatch, samples, replies):
    sent = []
    replies = iter(replies)

    def routed(wav, model, prompt=None, server=None, unload="min5", cli_prompt=None):
        sent.append((prompt, cli_prompt))
        return next(replies), {"mode": "cli", "timings": {}}

    monkeypatch.setattr("voisr.transcriber.transcribe_routed", routed)
    from voisr.history import HistoryStore
    from voisr.ui import Api
    recorder = type("R", (ClipRecorder,), {"samples": samples})
    pasted = []
    api = Api(config_path=tmp_path / "config.json", history=HistoryStore(tmp_path / "history.jsonl"),
              recorder_factory=recorder, clipboard_fn=lambda text: None,
              paste_fn=lambda text: pasted.append(text) or True)
    api.set_config({"snippets": SNIPPETS, "dictionary": {"tariq": "Tareq"}})
    return api, sent, pasted


def test_a_long_dictation_is_sent_no_triggers_on_either_prompt(tmp_path, monkeypatch):
    api, sent, _ = prompts_for(tmp_path, monkeypatch, spoken(seconds=12.0), ["A long thought."])
    dictate(api)
    assert sent == [("Glossary: Tareq.", "Glossary: Tareq.")]
    api.close()


def test_a_short_clip_is_sent_them_on_both_prompts(tmp_path, monkeypatch):
    api, sent, pasted = prompts_for(tmp_path, monkeypatch, spoken(), ["My email."])
    dictate(api)
    wanted = "Glossary: Tareq, my email, review prompt."
    assert sent == [(wanted, wanted)] and pasted == [SECRET]
    assert wav_seconds is not None
    api.close()


# ---- recited prompts


@pytest.mark.parametrize("recital", [
    "Glossary: my email, review prompt.", "my email, review prompt.", "My email. Review prompt.",
    "Tareq, my email, review prompt.", "Glossary: my email.",
])
def test_a_recited_list_is_redone_without_triggers(tmp_path, monkeypatch, recital):
    api, sent, pasted = prompts_for(tmp_path, monkeypatch, spoken(), [recital, "Hello there."])
    dictate(api)
    assert len(sent) == 2
    assert "my email" not in sent[1][0] and "my email" not in sent[1][1]
    assert pasted == ["Hello there."]
    assert "review prompt" not in (tmp_path / "history.jsonl").read_text()
    api.close()


def test_one_spoken_trigger_is_not_an_echo(tmp_path, monkeypatch):
    api, sent, pasted = prompts_for(tmp_path, monkeypatch, spoken(), ["My email."])
    dictate(api)
    assert len(sent) == 1 and pasted == [SECRET]
    api.close()


def test_a_trigger_whose_words_are_also_screen_terms_is_not_an_echo(tmp_path, monkeypatch):
    api, sent, pasted = prompts_for(tmp_path, monkeypatch, spoken(), ["Review prompt."])
    monkeypatch.setattr(api, "_session_terms", lambda session, wav: ["Review", "Prompt"])
    dictate(api)
    assert len(sent) == 1 and pasted == ["Please review"]
    api.close()


def test_saying_a_trigger_inside_a_sentence_is_not_a_recital(tmp_path, monkeypatch):
    api, sent, pasted = prompts_for(tmp_path, monkeypatch, spoken(), ["Send my email, please."])
    dictate(api)
    assert len(sent) == 1 and pasted == ["Send my email, please."]
    api.close()


@pytest.mark.parametrize("recital", [
    "my email my address review prompt", "My email my address.", "Glossary my email review prompt",
    "Tareq my email review prompt",
])
def test_a_recital_with_no_punctuation_is_redone(tmp_path, monkeypatch, recital):
    api, sent, pasted = prompts_for(tmp_path, monkeypatch, spoken(), [recital, "Hello there."])
    api.set_config({"snippets": {**SNIPPETS, "my address": "1 Road"}})
    dictate(api)
    assert len(sent) == 2 and pasted == ["Hello there."]
    api.close()


@pytest.mark.parametrize("said", [
    "Please email my boss", "my email is down", "review prompt please", "my email review",
    "email my address", "Tareq", "review",
])
def test_ordinary_words_are_not_a_recital(tmp_path, monkeypatch, said):
    api, sent, pasted = prompts_for(tmp_path, monkeypatch, spoken(), [said])
    api.set_config({"snippets": {**SNIPPETS, "my address": "1 Road"}})
    dictate(api)
    assert len(sent) == 1
    api.close()
