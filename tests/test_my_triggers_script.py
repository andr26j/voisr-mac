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
    assert "silence" in said and "microphone access" in said
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


def test_a_too_short_clip_is_asked_for_again_and_its_file_goes(tmp_path):
    asked = run_record(tmp_path, ScriptedRecorder(["short", "good"]))
    assert any("again" in prompt for prompt in asked)
    assert len(list(tmp_path.glob("*.wav"))) == len(tool.script(["my email"]))
    assert (tmp_path / "trigger-01.txt").exists()


def test_a_clip_shorter_than_the_app_accepts_is_asked_for_again(tmp_path):
    class Brief(ScriptedRecorder):
        def stop(self):
            if self.plan and self.plan[0] == "brief":
                self.plan.pop(0)
                write_clip(self.path, b"\x10\x20" * 1600)  # 0.1 s
                return ""
            return super().stop()

    asked = run_record(tmp_path, Brief(["brief", "good"]))
    assert any("again" in prompt for prompt in asked)


def test_a_clip_that_keeps_failing_stops_with_the_recorders_error(tmp_path):
    with pytest.raises(SystemExit, match="ffmpeg said no"):
        run_record(tmp_path, ScriptedRecorder(["short"] * tool.MAX_ATTEMPTS))
    assert list(tmp_path.glob("*.wav")) == []


def test_a_missing_file_counts_as_a_failed_clip(tmp_path):
    class NoFile(ScriptedRecorder):
        def stop(self):
            return "device gone"

    with pytest.raises(SystemExit, match="device gone"):
        run_record(tmp_path, NoFile([]))


def test_an_earlier_runs_clips_go_before_recording_and_nothing_else_does(tmp_path, capsys):
    for name in ("trigger-01.wav", "trigger-01.txt", "silent-09.wav", "other-99.txt"):
        (tmp_path / name).write_text("old")
    for name in ("notes.txt", "trigger-a.wav", "my-take.wav", "trigger-01.wav.bak"):
        (tmp_path / name).write_text("keep")
    run_record(tmp_path, ScriptedRecorder([]))
    assert "earlier" in capsys.readouterr().out
    assert not (tmp_path / "silent-09.wav").exists() and not (tmp_path / "other-99.txt").exists()
    assert (tmp_path / "trigger-01.wav").read_bytes() != b"old"
    for name in ("notes.txt", "trigger-a.wav", "my-take.wav", "trigger-01.wav.bak"):
        assert (tmp_path / name).read_text() == "keep"


def test_nothing_is_cleared_while_voisr_runs(tmp_path):
    (tmp_path / "trigger-01.wav").write_text("old")
    with pytest.raises(SystemExit):
        tool.record(tmp_path, ["my email"], FakeRecorder, ask=lambda prompt: "", key="k",
                    running=lambda: True)
    assert (tmp_path / "trigger-01.wav").read_text() == "old"


# ---- scoring a bad folder


def zero_clips(tmp_path, names):
    for name in names:
        write_clip(tmp_path / f"{name}.wav", b"\x00\x00" * 16000)
        (tmp_path / f"{name}.txt").write_text("my email\n")
    return tool.clips_in(tmp_path)


def test_a_wav_with_no_text_is_skipped_by_name(tmp_path, capsys):
    zero_clips(tmp_path, ["trigger-01"])
    write_clip(tmp_path / "silent-04.wav", b"")
    clips = tool.clips_in(tmp_path)
    assert [name for name, _, _ in clips] == ["trigger-01"]
    assert "silent-04.wav" in capsys.readouterr().out


def test_all_zero_clips_make_the_run_not_valid_with_the_real_gate(tmp_path, capsys):
    clips = zero_clips(tmp_path, ["trigger-01", "hotkey-01", "other-01"])

    def boom(wav, condition):
        raise AssertionError("silence must not be transcribed")

    tool.report(tool.score(clips, boom, ["run"]), ["run"])
    out = capsys.readouterr().out
    assert "NOT VALID" in out and "3 of 3" in out and "input device" in out
    assert "missed" not in out and "fire" not in out


def test_a_few_all_zero_clips_warn_and_are_not_listed_as_misses(tmp_path, capsys, monkeypatch):
    clips = zero_clips(tmp_path, ["trigger-01"])
    for name in ("trigger-02", "other-01", "other-02"):
        # a quiet room, then something steady well above it
        write_clip(tmp_path / f"{name}.wav", b"\x01\x00\xff\xff" * 4000 + b"\x10\x20" * 16000)
        (tmp_path / f"{name}.txt").write_text("my email\n")
    clips = tool.clips_in(tmp_path)
    tool.report(tool.score(clips, lambda wav, c: "My email.", ["run"]), ["run"])
    out = capsys.readouterr().out
    assert "WARNING" in out and "1 of 4" in out and "trigger-01" in out
    assert "NOT VALID" not in out and 'missed  trigger-01' not in out
    assert "triggers, Enter: fire 1/1" in out, "the silent clip isn't counted"


# ---- the app's own recorder


def test_it_records_with_the_apps_recorder_by_default():
    import inspect

    from voisr.ui import default_recorder
    assert inspect.signature(tool.record).parameters["recorder_factory"].default is default_recorder


def test_a_recorder_that_cannot_start_stops_with_its_error_and_is_closed(tmp_path):
    closed = []

    class NoMic(ScriptedRecorder):
        def start(self, path):
            raise Exception("no default input device")  # as PortAudioError is

        def close(self):
            closed.append(True)

    with pytest.raises(SystemExit, match="no default input device"):
        run_record(tmp_path, NoMic([]))
    assert closed == [True]


def test_a_recorder_that_fails_on_stop_stops_with_its_error(tmp_path):
    class Stuck(ScriptedRecorder):
        def stop(self):
            raise RuntimeError("the mic stream is stuck")

    with pytest.raises(SystemExit, match="the mic stream is stuck"):
        run_record(tmp_path, Stuck([]))
    assert list(tmp_path.glob("*.txt")) == []


def test_stop_returning_nothing_still_gives_a_clear_failure(tmp_path):
    class Quiet(ScriptedRecorder):
        def stop(self):
            return ""  # StreamRecorder's stop always returns ""

    with pytest.raises(SystemExit, match="said: nothing"):
        run_record(tmp_path, Quiet([]))


def test_the_silence_message_names_both_causes_and_blames_no_terminal():
    said = tool.SILENCE_MESSAGE
    assert "microphone access" in said and "input device" in said
    assert "Privacy & Security" in said and "Sound" in said
    assert "another app" not in said and "tccutil" not in said
