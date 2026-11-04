import array
import builtins
import sys
import wave
from types import SimpleNamespace

import pytest

from voisr import app


class FakeRecorder:
    def __init__(self):
        self.path = None
        self.cancelled = False
        self.closed = False

    def start(self, path):
        self.path = path

    def stop(self):
        return ""

    def cancel(self):
        self.cancelled = True

    def close(self):
        self.closed = True


def test_toggle_ctrl_c_cancels_recorder_and_removes_temp_wav(monkeypatch):
    recorder = FakeRecorder()
    prompts = 0

    def fake_input(prompt):
        nonlocal prompts
        prompts += 1
        if prompts == 1:
            return ""
        raise KeyboardInterrupt

    monkeypatch.setattr(app, "Recorder", lambda device: recorder)
    monkeypatch.setattr(builtins, "input", fake_input)

    with pytest.raises(KeyboardInterrupt):
        app.toggle_mode({"device": ":default", "dictionary": {}, "paste": False})

    assert recorder.cancelled is True
    assert recorder.closed is True
    assert recorder.path.exists() is False


def test_toggle_start_failure_cancels_recorder_and_removes_temp_wav(monkeypatch):
    class StartFailingRecorder(FakeRecorder):
        def start(self, path):
            self.path = path
            raise RuntimeError("microphone unavailable")

    recorder = StartFailingRecorder()
    monkeypatch.setattr(app, "Recorder", lambda device: recorder)
    monkeypatch.setattr(builtins, "input", lambda prompt: "")

    with pytest.raises(RuntimeError, match="microphone unavailable"):
        app.toggle_mode({"device": ":default", "dictionary": {}, "paste": False})

    assert recorder.cancelled is True
    assert recorder.closed is True
    assert recorder.path.exists() is False


FFMPEG_MISSING_DEVICE = (
    "[AVFoundation indev @ 0x7516c14140] Audio device not found\n"
    "[in#0 @ 0x7516c14000] Error opening input: Input/output error\n")


def write_wav(path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(array.array("h", samples).tobytes())


def toggle_one_recording(monkeypatch, capsys, write_capture, stderr=""):
    """One start and stop in toggle mode: write_capture makes the file ffmpeg
    would have left. Returns what was printed and the files sent to Whisper."""

    class CapturingRecorder(FakeRecorder):
        def stop(self):
            write_capture(self.path)
            return stderr

    sent = []
    answers = iter(["", "", KeyboardInterrupt()])

    def fake_input(_prompt):
        answer = next(answers)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def fake_process_audio(wav, _dictionary):
        sent.append(wav)
        return "heard"

    monkeypatch.setattr(app, "Recorder", lambda device: CapturingRecorder())
    monkeypatch.setattr(builtins, "input", fake_input)
    monkeypatch.setattr(app, "process_audio", fake_process_audio)
    monkeypatch.setattr(app, "deliver", lambda text, paste: None)

    with pytest.raises(KeyboardInterrupt):
        app.toggle_mode({"device": ":Studio Mic", "dictionary": {}, "paste": False})
    return capsys.readouterr().out, sent


def test_toggle_failed_capture_names_the_device_and_what_ffmpeg_said(monkeypatch, capsys):
    out, sent = toggle_one_recording(
        monkeypatch, capsys, lambda path: path.write_bytes(b""),
        stderr=FFMPEG_MISSING_DEVICE)

    assert sent == []
    assert ":Studio Mic" in out
    assert "Audio device not found" in out
    assert "-list_devices" in out
    assert "permission" not in out.lower()


def test_toggle_failed_capture_says_when_ffmpeg_reported_nothing(monkeypatch, capsys):
    out, sent = toggle_one_recording(
        monkeypatch, capsys, lambda path: path.write_bytes(b""))

    assert sent == []
    assert "ffmpeg reported no error" in out


def test_toggle_all_zero_capture_says_silence_and_skips_whisper(monkeypatch, capsys):
    out, sent = toggle_one_recording(
        monkeypatch, capsys, lambda path: write_wav(path, [0] * 16000))

    assert sent == []
    assert "only silence" in out
    assert ":Studio Mic" in out
    assert "-list_devices" in out


def test_toggle_capture_with_any_sound_goes_to_whisper(monkeypatch, capsys):
    out, sent = toggle_one_recording(
        monkeypatch, capsys, lambda path: write_wav(path, [0] * 15999 + [1]))

    assert len(sent) == 1
    assert "only silence" not in out


def test_toggle_capture_the_gate_cant_read_still_goes_to_whisper(monkeypatch, capsys):
    out, sent = toggle_one_recording(
        monkeypatch, capsys, lambda path: path.write_bytes(b"not a wav" * 200))

    assert len(sent) == 1
    assert "only silence" not in out


def test_hotkey_ctrl_c_stops_listener_then_closes_api(monkeypatch):
    events = []

    class FakeApi:
        def current_level(self):
            return None

        def __init__(self, **kwargs):
            events.append("api-created")

        def attach_status_item(self, status):
            pass

        def close(self):
            events.append("api-closed")

    class FakeService:
        status = "active"

        def __init__(self, api):
            events.append("service-created")

        def start(self):
            events.append("service-started")

        def stop(self):
            events.append("service-stopped")

    monkeypatch.setattr("voisr.ui.Api", FakeApi)
    monkeypatch.setattr("voisr.ui.HotkeyService", FakeService)
    monkeypatch.setattr("voisr.ui.request_permissions", lambda: None)
    monkeypatch.setattr("time.sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt))

    app.hotkey_mode({"hotkey": "alt_r"}, "/tmp/config.json")

    assert events[-2:] == ["service-stopped", "api-closed"]


def test_hotkey_stop_failure_still_closes_api(monkeypatch):
    events = []

    class FakeApi:
        def current_level(self):
            return None

        def __init__(self, **kwargs):
            pass

        def attach_status_item(self, status):
            pass

        def close(self):
            events.append("api-closed")

    class FakeService:
        status = "active"

        def __init__(self, api):
            pass

        def start(self):
            pass

        def stop(self):
            events.append("service-stopped")
            raise RuntimeError("listener stop failed")

    monkeypatch.setattr("voisr.ui.Api", FakeApi)
    monkeypatch.setattr("voisr.ui.HotkeyService", FakeService)
    monkeypatch.setattr("voisr.ui.request_permissions", lambda: None)
    monkeypatch.setattr("time.sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt))

    with pytest.raises(RuntimeError, match="listener stop failed"):
        app.hotkey_mode({"hotkey": "alt_r"}, "/tmp/config.json")

    assert events == ["service-stopped", "api-closed"]


def test_hotkey_service_constructor_failure_closes_api(monkeypatch):
    events = []

    class FakeApi:
        def current_level(self):
            return None

        def __init__(self, **kwargs):
            events.append("api-created")

        def attach_status_item(self, status):
            pass

        def close(self):
            events.append("api-closed")

    class FailingService:
        def __init__(self, api):
            raise RuntimeError("listener unavailable")

    monkeypatch.setattr("voisr.ui.Api", FakeApi)
    monkeypatch.setattr("voisr.ui.HotkeyService", FailingService)
    monkeypatch.setattr("voisr.ui.request_permissions", lambda: None)

    with pytest.raises(RuntimeError, match="listener unavailable"):
        app.hotkey_mode({"hotkey": "alt_r"}, "/tmp/config.json")

    assert events == ["api-created", "api-closed"]


def test_gui_shutdown_stops_listener_then_closes_api(monkeypatch):
    from voisr import ui

    events = []
    wiring = []

    class FakeApi:
        def __init__(self, **kwargs):
            pass

        def current_level(self):
            return None

        def attach_hotkey_service(self, service):
            pass

        def attach_window(self, window):
            pass

        def attach_status_item(self, status):
            pass

        def close(self):
            events.append("api-closed")

    class FakeService:
        def __init__(self, api, widget, fallback_check=None, key_state=None):
            wiring.append((fallback_check, key_state))

        def start(self):
            events.append("service-started")

        def stop(self):
            events.append("service-stopped")

    class FakeWidget:
        def __init__(self, level_source=None):
            pass

        def create(self):
            pass

    def start(callback):
        callback()
        raise KeyboardInterrupt

    monkeypatch.setattr(ui, "Api", FakeApi)
    monkeypatch.setattr(ui, "HotkeyService", FakeService)
    monkeypatch.setattr(ui, "RecordingWidget", FakeWidget)
    monkeypatch.setattr("voisr.inject.prepare_paste_controller", lambda: None)
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(
        # main() exposes the JS bridge on the window (L3)
        create_window=lambda *args, **kwargs: SimpleNamespace(
            expose=lambda *funcs: None),
        start=start))

    with pytest.raises(KeyboardInterrupt):
        ui.main()

    assert events[-2:] == ["service-stopped", "api-closed"]
    # main() must hand the service the real Input Monitoring probe, or the
    # listen-only fallback silently never happens; likewise the real key
    # state, or the stuck-key check never runs
    assert wiring == [(ui.listen_tap_granted, ui.hotkey_key_down)]


def test_gui_stop_failure_still_closes_api(monkeypatch):
    from voisr import ui

    events = []

    class FakeApi:
        def __init__(self, **kwargs):
            pass

        def current_level(self):
            return None

        def attach_hotkey_service(self, service):
            pass

        def attach_window(self, window):
            pass

        def attach_status_item(self, status):
            pass

        def close(self):
            events.append("api-closed")

    class FakeService:
        def __init__(self, api, widget, fallback_check=None, key_state=None):
            pass

        def start(self):
            pass

        def stop(self):
            events.append("service-stopped")
            raise RuntimeError("listener stop failed")

    class FakeWidget:
        def __init__(self, level_source=None):
            pass

        def create(self):
            pass

    def start(callback):
        callback()
        raise KeyboardInterrupt

    monkeypatch.setattr(ui, "Api", FakeApi)
    monkeypatch.setattr(ui, "HotkeyService", FakeService)
    monkeypatch.setattr(ui, "RecordingWidget", FakeWidget)
    monkeypatch.setattr("voisr.inject.prepare_paste_controller", lambda: None)
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(
        create_window=lambda *args, **kwargs: object(), start=start))

    with pytest.raises(RuntimeError, match="listener stop failed"):
        ui.main()

    assert events == ["service-stopped", "api-closed"]


def test_gui_service_constructor_failure_closes_api(monkeypatch):
    from voisr import ui

    events = []

    class FakeApi:
        def __init__(self, **kwargs):
            pass

        def current_level(self):
            return None

        def attach_status_item(self, status):
            pass

        def close(self):
            events.append("api-closed")

    class FailingService:
        def __init__(self, api, widget, fallback_check=None, key_state=None):
            raise RuntimeError("listener unavailable")

    monkeypatch.setattr(ui, "Api", FakeApi)
    monkeypatch.setattr(ui, "HotkeyService", FailingService)
    monkeypatch.setattr("voisr.inject.prepare_paste_controller", lambda: None)
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace())

    with pytest.raises(RuntimeError, match="listener unavailable"):
        ui.main()

    assert events == ["api-closed"]


def test_gui_window_constructor_failure_stops_listener_and_closes_api(monkeypatch):
    from voisr import ui

    events = []

    class FakeApi:
        def __init__(self, **kwargs):
            pass

        def current_level(self):
            return None

        def attach_hotkey_service(self, service):
            pass

        def attach_status_item(self, status):
            pass

        def close(self):
            events.append("api-closed")

    class FakeService:
        def __init__(self, api, widget, fallback_check=None, key_state=None):
            pass

        def stop(self):
            events.append("service-stopped")

    class FakeWidget:
        def __init__(self, level_source=None):
            pass

        def create(self):
            pass

    monkeypatch.setattr(ui, "Api", FakeApi)
    monkeypatch.setattr(ui, "HotkeyService", FakeService)
    monkeypatch.setattr(ui, "RecordingWidget", FakeWidget)
    monkeypatch.setattr("voisr.inject.prepare_paste_controller", lambda: None)
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(
        create_window=lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("window unavailable"))))

    with pytest.raises(RuntimeError, match="window unavailable"):
        ui.main()

    assert events == ["service-stopped", "api-closed"]
