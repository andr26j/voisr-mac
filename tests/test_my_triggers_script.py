"""scripts/test_my_triggers.py: the guided recording and its scoring, with no
microphone or Whisper. The speech gate is real on the fake clips, or stubbed."""
import importlib.util
import wave
from pathlib import Path

import pytest

from voisr.speech_gate import SpeechSpan

spec = importlib.util.spec_from_file_location(
    "test_my_triggers_tool", Path(__file__).resolve().parent.parent / "scripts" / "test_my_triggers.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class FakeRecorder:
    def start(self, path):
        self.path = path

    def stop(self):
        with wave.open(str(self.path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(b"\x10\x20" * 16000)
        return ""

    def close(self):
        pass


def recorded(tmp_path):
    tool.record(tmp_path, ["my email", "sign off"], FakeRecorder, ask=lambda prompt: "",
                key="Right Option", running=lambda: False)
    return tool.clips_in(tmp_path)


def test_about_twenty_five_clips_by_default():
    assert len(tool.script()) == 25


def test_record_writes_a_clip_and_its_phrase(tmp_path):
    clips = recorded(tmp_path)
    assert [(n, p) for n, p, _ in clips if p == "sign off"] == [
        ("hotkey-02", "sign off"), ("trigger-02", "sign off")]
    assert all(wav.stat().st_size > 1000 for _, _, wav in clips)


def test_record_refuses_while_voisr_runs_and_says_how_to_quit_and_reopen(tmp_path):
    with pytest.raises(SystemExit) as stop:
        tool.record(tmp_path, ["my email"], FakeRecorder, ask=lambda prompt: "", key="k",
                    running=lambda: True)
    assert tool.QUIT_COMMAND in str(stop.value) and tool.REOPEN_COMMAND in str(stop.value)
    assert list(tmp_path.iterdir()) == []


def test_the_key_clips_tell_you_to_use_your_key(tmp_path):
    asked = []
    tool.record(tmp_path, ["my email"], FakeRecorder, ask=lambda prompt: asked.append(prompt) or "",
                key="Right Option", running=lambda: False)
    said = "\n".join(asked)
    assert 'press and hold Right Option, say "my email", release Right Option' in said
    assert "tap Right Option once, then press and hold" in said


@pytest.mark.parametrize("config,name", [
    ('{"hotkey_label": "Fn"}', "Fn"), ('{"hotkey": "alt_r"}', "Right Option (⌥)"),
    ('{"hotkey": "cmd_r"}', "Right Command (⌘)"), ('{"hotkey": "weird"}', "your push-to-talk key"),
    ("{not json", "Right Option (⌥)"), ('["list"]', "Right Option (⌥)"),
])
def test_the_key_is_named_from_config_and_the_file_is_left_alone(tmp_path, config, name):
    path = tmp_path / "config.json"
    path.write_text(config)
    assert tool.hotkey_name(path) == name
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"] and path.read_text() == config


def test_a_missing_config_means_the_default_key(tmp_path):
    assert tool.hotkey_name(tmp_path / "none.json") == "Right Option (⌥)"


def span(ms):
    return SpeechSpan("speech", ms, 0, 16000, sustained_ms=ms)


def test_score_reports_each_kind_of_clip_apart(tmp_path, monkeypatch, capsys):
    clips = recorded(tmp_path)
    by_name = {n: w for n, _, w in clips}
    silent, held = by_name["silent-01"], by_name["hotkey-01"]
    monkeypatch.setattr(tool, "gate_and_trim",
                        lambda wav: span(100 if wav in (silent, held) else 500))
    heard = {"my email": "My email.", "sign off": "Sign of.", "sign up": "Sign off."}

    def transcribe(wav, condition):
        if wav == silent:
            return "My email."
        return heard.get(next(p for _, p, w in clips if w == wav), "something else")

    tool.report(tool.score(clips, transcribe, ["run"]), ["run"])
    out = capsys.readouterr().out
    assert "triggers, Enter: fire 1/2" in out
    assert "triggers, push-to-talk key: fire 0/2" in out
    assert "other phrases: fire 1/8" in out and "silent key clips: fire 0/5" in out
    assert 'missed  trigger-02 "sign off"' in out
    assert 'MISFIRE other-04 "sign up"' in out and 'pastes "sign off"' in out
    assert 'held back by the guard: silent-01' in out and 'held back by the guard: hotkey-01' in out
    assert "push-to-talk clips lowest 100 ms" in out and "silent key clips highest 500 ms" in out


def test_a_clip_the_gate_refuses_is_not_transcribed(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "gate_and_trim", lambda wav: SpeechSpan("no_speech", 0, 0, 0))

    def boom(wav, condition):
        raise AssertionError("transcribed")

    rows = tool.score(recorded(tmp_path), boom, ["run"])
    assert all(r["raw"] == "" and r["fired"] is None for r in rows)


# ---- silence and bad clips while recording


def write_clip(path, data):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(data)


class ScriptedRecorder:
    """Writes the next clip of a script: "good", "zeros", "quiet" or "short"."""

    def __init__(self, plan):
        self.plan = list(plan)
        self.errors = "ffmpeg said no"

    def __call__(self):
        return self

    def start(self, path):
        self.path = path

    def stop(self):
        kind = self.plan.pop(0) if self.plan else "good"
        if kind == "good":
            write_clip(self.path, b"\x10\x20" * 16000)
        elif kind == "zeros":
            write_clip(self.path, b"\x00\x00" * 16000)
        elif kind == "quiet":  # room noise only, which a silent key clip can be
            write_clip(self.path, b"\x01\x00\xff\xff" * 8000)
        elif kind == "short":
            write_clip(self.path, b"")
        return self.errors

    def close(self):
        pass


def run_record(tmp_path, recorder):
    asked = []
    tool.record(tmp_path, ["my email"], recorder, ask=lambda prompt: asked.append(prompt) or "",
                key="k", running=lambda: False)
    return asked


def test_silence_from_the_first_clip_stops_at_once_and_says_why(tmp_path):
    asked = []
    with pytest.raises(SystemExit) as stop:
        tool.record(tmp_path, ["my email"], ScriptedRecorder(["zeros"]),
                    ask=lambda prompt: asked.append(prompt) or "", key="k", running=lambda: False)
    said = str(stop.value)
    assert "silence" in said and "microphone access" in said and "Terminal app" in said
    assert "Privacy & Security" in said and "tccutil" not in said
    assert len(asked) == 2, "no more clips asked for"
    assert [p for p in tmp_path.iterdir() if p.suffix in (".wav", ".txt")] == []


def test_silence_on_a_later_clip_stops_too(tmp_path):
    with pytest.raises(SystemExit, match="silence"):
        run_record(tmp_path, ScriptedRecorder(["good", "good", "zeros"]))
    assert sorted(p.stem for p in tmp_path.glob("*.txt")) == ["hotkey-01", "trigger-01"]


def test_a_quiet_clip_is_not_silence(tmp_path):
    plan = ["good"] * 10 + ["quiet"] * 5
    run_record(tmp_path, ScriptedRecorder(plan))
    assert len(list(tmp_path.glob("silent-*.wav"))) == 5
