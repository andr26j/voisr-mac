"""Low items from the 2026-10-12 audit."""
import subprocess
import threading
import time

import pytest

from voisr import transcriber
from voisr.history import HistoryStore
from voisr.ui import Api, HotkeyService


# ---- L1: whisper can hang; quit pastes after the user has left; a rejected
# key press gave no feedback ----

def _fake_whisper_setup(tmp_path, monkeypatch, run):
    model = tmp_path / "ggml-base.en.bin"
    model.write_bytes(b"x")
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"\0" * 32000 * 10)        # ~10 s of 16 kHz mono PCM
    monkeypatch.setattr(transcriber.subprocess, "run", run)
    return wav, model


def test_a_hung_whisper_times_out_as_a_transcription_error(tmp_path, monkeypatch):
    seen = {}

    def run(cmd, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    wav, model = _fake_whisper_setup(tmp_path, monkeypatch, run)
    with pytest.raises(transcriber.TranscriptionError, match="timed out"):
        transcriber.transcribe(wav, model_path=model, whisper_bin="/bin/true")
    assert seen["timeout"] >= 120


def test_the_whisper_timeout_grows_with_the_recording(tmp_path, monkeypatch):
    timeouts = []

    def run(cmd, **kwargs):
        timeouts.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    wav, model = _fake_whisper_setup(tmp_path, monkeypatch, run)
    with pytest.raises(transcriber.TranscriptionError):
        transcriber.transcribe(wav, model_path=model, whisper_bin="/bin/true")
    wav.write_bytes(b"\0" * 32000 * 600)       # the 600 s recording cap
    with pytest.raises(transcriber.TranscriptionError):
        transcriber.transcribe(wav, model_path=model, whisper_bin="/bin/true")
    assert timeouts[1] > timeouts[0]


class FakeRecorder:
    def start(self, path):
        self.path = path

    def stop(self):
        with open(self.path, "wb") as f:
            f.write(b"\0" * 2000)
        return ""


def test_quitting_mid_transcription_keeps_the_dictation_but_never_pastes_it(tmp_path):
    gate = threading.Event()
    pasted, copied = [], []

    def slow_transcribe(wav):
        gate.wait(5)
        return "still mine"

    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "h.jsonl"),
              recorder_factory=FakeRecorder, transcribe_fn=slow_transcribe,
              clock=lambda: 1.0, paste_fn=lambda t: pasted.append(t) or True,
              clipboard_fn=copied.append)
    api.start_recording()
    future = api._release_recording(paste=True)
    closer = threading.Thread(target=api.close)
    closer.start()
    deadline = time.monotonic() + 2
    while not api._quitting and time.monotonic() < deadline:
        time.sleep(0.01)
    gate.set()
    closer.join(5)
    assert future.result(5).to_api_dict()["text"] == "Still mine"
    assert pasted == [] and copied == []
    assert [i["text"] for i in api.get_history()] == ["Still mine"]


class FakeWidget:
    def __init__(self):
        self.calls = []

    def show_recording(self, theme="classic", color=None):
        self.calls.append("recording")

    def show_processing(self):
        self.calls.append("processing")

    def show_done(self):
        self.calls.append("done")

    def show_error(self):
        self.calls.append("error")

    def hide(self):
        self.calls.append("hide")


def test_a_key_press_that_cannot_start_flashes_an_error_once(tmp_path):
    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "h.jsonl"),
              recorder_factory=FakeRecorder, transcribe_fn=lambda w: "x",
              clock=lambda: 1.0, clipboard_fn=lambda t: None)
    widget = FakeWidget()
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    api.start_recording()          # the dock owns the mic
    svc.press()
    svc.press()                    # auto-repeat of a held key: no second flash
    deadline = time.monotonic() + 2
    while widget.calls[-1:] != ["hide"] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert widget.calls == ["error", "hide"]
    svc.release()
    svc.press()                    # a fresh press may flash again
    deadline = time.monotonic() + 2
    while len(widget.calls) < 4 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert widget.calls == ["error", "hide", "error", "hide"]
    api.stop_recording()
