"""scripts/test_my_triggers.py: the guided recording and its scoring, with no
microphone or Whisper."""
import importlib.util
import wave
from pathlib import Path

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
    tool.record(tmp_path, ["my email", "sign off"], FakeRecorder, ask=lambda prompt: "")
    return tool.clips_in(tmp_path)


def test_about_twenty_clips_by_default():
    assert len(tool.script()) == 20


def test_record_writes_a_clip_and_its_phrase(tmp_path):
    clips = recorded(tmp_path)
    assert [(n, p) for n, p, _ in clips if p == "sign off"] == [
        ("trigger-03", "sign off"), ("trigger-04", "sign off")]
    assert all(wav.stat().st_size > 1000 for _, _, wav in clips)


def test_score_tells_hits_misses_and_misfires(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tool.speech_gate, "gate_wav", lambda wav: None)
    heard = {"my email": "My email.", "sign off": "Sign of.", "sign up": "Sign off."}
    clips = recorded(tmp_path)
    rows = tool.score(clips, lambda wav, c: heard.get(
        next(p for _, p, w in clips if w == wav), "something else"), ["run"])
    tool.report(rows, ["run"])
    out = capsys.readouterr().out
    assert "triggers exact 2/4" in out
    assert 'missed  "sign off"' in out
    assert 'MISFIRE "sign up"' in out and 'pastes "sign off"' in out


def test_a_clip_the_gate_refuses_is_not_transcribed(tmp_path, monkeypatch):
    from voisr.speech_gate import SpeechSpan
    monkeypatch.setattr(tool.speech_gate, "gate_wav", lambda wav: SpeechSpan("no_speech", 0, 0, 0))
    def boom(wav, condition):
        raise AssertionError("transcribed")
    rows = tool.score(recorded(tmp_path), boom, ["run"])
    assert all(r["raw"] == "" and r["fired"] is None for r in rows)
