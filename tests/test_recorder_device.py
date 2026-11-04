import builtins
import json
import sys

import pytest

from voisr import app
from voisr import recorder as recorder_module
from voisr.app import load_config
from voisr.history import HistoryStore
from voisr.recorder import DEFAULT_DEVICE, Recorder
from voisr.ui import Api, default_recorder


class FakeProcess:
    stderr = None

    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def record_ffmpeg_commands(monkeypatch):
    commands = []

    def fake_popen(command, **_kwargs):
        commands.append(command)
        return FakeProcess()

    monkeypatch.setattr(recorder_module.subprocess, "Popen", fake_popen)
    return commands


def input_of(command):
    return command[command.index("-i") + 1]


def ffmpeg_input(recorder, monkeypatch, tmp_path):
    """The -i value ffmpeg would really be started with."""
    commands = record_ffmpeg_commands(monkeypatch)
    recorder.start(tmp_path / "out.wav")
    return input_of(commands[0])


def test_the_default_device_is_the_system_input_not_a_number(monkeypatch, tmp_path):
    assert DEFAULT_DEVICE == ":default"
    assert ffmpeg_input(Recorder(), monkeypatch, tmp_path) == ":default"


def test_the_apps_fallback_recorder_uses_the_system_input(monkeypatch, tmp_path):
    def broken():
        raise RuntimeError("no input stream")

    commands = record_ffmpeg_commands(monkeypatch)
    history = HistoryStore(tmp_path / "h.jsonl")
    try:
        api = Api(config_path=tmp_path / "config.json", history=history,
                  recorder_factory=broken, clock=lambda: 1.0)
        try:
            assert api.start_recording().get("recording") is True
            assert [input_of(command) for command in commands] == [":default"]
        finally:
            api.close()
    finally:
        history.close()


def test_default_recorder_falls_back_to_the_system_input(monkeypatch, tmp_path):
    import voisr.stream_recorder

    def broken():
        raise RuntimeError("no sounddevice")

    monkeypatch.setattr(voisr.stream_recorder, "StreamRecorder", broken)
    recorder = default_recorder()
    assert isinstance(recorder, Recorder)
    assert ffmpeg_input(recorder, monkeypatch, tmp_path) == ":default"


def saved_device(tmp_path, device):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"device": device}))
    return load_config(path)["device"]


def test_a_config_saved_with_the_old_default_loads_as_the_system_input(tmp_path):
    assert saved_device(tmp_path, ":0") == DEFAULT_DEVICE


def test_loading_the_old_default_leaves_the_file_alone(tmp_path):
    saved_device(tmp_path, ":0")
    assert json.loads((tmp_path / "config.json").read_text()) == {"device": ":0"}


def test_a_config_without_a_device_gets_the_system_input(tmp_path):
    assert load_config(tmp_path / "missing.json")["device"] == ":default"


@pytest.mark.parametrize("device", [":1", ":MacBook Air Microphone", ":AirPods Pro"])
def test_a_device_the_user_chose_is_kept(device, monkeypatch, tmp_path):
    recorder = Recorder(saved_device(tmp_path, device))
    assert ffmpeg_input(recorder, monkeypatch, tmp_path) == device


def test_the_recorder_opens_exactly_the_device_it_is_given(monkeypatch, tmp_path):
    assert ffmpeg_input(Recorder(":0"), monkeypatch, tmp_path) == ":0"


@pytest.mark.parametrize("saved, opened", [
    (":MacBook Air Microphone", ":MacBook Air Microphone"),
    (":1", ":1"),
    (":0", ":default"),
])
def test_toggle_mode_opens_the_device_saved_in_the_config(
        saved, opened, monkeypatch, tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"device": saved}))
    commands = record_ffmpeg_commands(monkeypatch)
    answers = iter(["", KeyboardInterrupt()])

    def fake_input(_prompt):
        answer = next(answers)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(builtins, "input", fake_input)
    monkeypatch.setattr(sys, "argv", ["voisr", "--config", str(config)])

    with pytest.raises(SystemExit):
        app.main()

    assert [input_of(command) for command in commands] == [opened]
