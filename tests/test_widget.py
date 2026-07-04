import json

from voisr.app import load_config
from voisr.history import HistoryStore
from voisr.ui import Api, HotkeyService


class FakeRecorder:
    def start(self, path):
        self.path = path

    def stop(self):
        with open(self.path, "wb") as f:
            f.write(b"\0" * 2000)
        return ""


class FakeWidget:
    def __init__(self):
        self.calls = []

    def show_recording(self):
        self.calls.append("recording")

    def show_processing(self):
        self.calls.append("processing")

    def show_done(self):
        self.calls.append("done")

    def show_error(self):
        self.calls.append("error")

    def hide(self):
        self.calls.append("hide")


def make_api(tmp_path, config=None):
    cfg_path = tmp_path / "config.json"
    if config:
        cfg_path.write_text(json.dumps(config))
    return Api(config_path=cfg_path,
               history=HistoryStore(tmp_path / "h.jsonl"),
               recorder=FakeRecorder(),
               transcribe_fn=lambda wav: "hello widget",
               clock=lambda: 1.0)


def test_show_widget_defaults_true(tmp_path):
    cfg = load_config(tmp_path / "missing.json")
    assert cfg["show_widget"] is True


def wait_for_hide(widget, timeout=3.0):
    import time
    deadline = time.monotonic() + timeout
    while widget.calls[-1:] != ["hide"] and time.monotonic() < deadline:
        time.sleep(0.05)


def test_press_release_drives_widget_states(tmp_path):
    widget = FakeWidget()
    svc = HotkeyService(make_api(tmp_path), widget=widget, hide_delay=0)
    svc.press()
    assert widget.calls == ["recording"]
    svc.release()
    wait_for_hide(widget)
    assert widget.calls == ["recording", "processing", "done", "hide"]


def test_widget_disabled_via_config(tmp_path):
    widget = FakeWidget()
    api = make_api(tmp_path, config={"show_widget": False})
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    svc.press()
    svc.release()
    assert widget.calls == []


def test_release_without_press_is_noop(tmp_path):
    widget = FakeWidget()
    svc = HotkeyService(make_api(tmp_path), widget=widget, hide_delay=0)
    svc.release()
    assert widget.calls == []


def test_error_state_shown_when_transcription_empty(tmp_path):
    widget = FakeWidget()
    api = make_api(tmp_path)
    api._transcribe_fn = lambda wav: ""
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    svc.press()
    svc.release()
    wait_for_hide(widget)
    assert "error" in widget.calls and widget.calls[-1] == "hide"


def test_release_returns_fast_while_processing_happens_async(tmp_path):
    import time
    widget = FakeWidget()
    api = make_api(tmp_path)
    slow_calls = []

    def slow_transcribe(wav):
        time.sleep(0.4)
        slow_calls.append(1)
        return "slow hello"

    api._transcribe_fn = slow_transcribe
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    svc.press()
    t0 = time.monotonic()
    svc.release()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.2, f"release blocked for {elapsed:.2f}s (would stall the event tap)"
    deadline = time.monotonic() + 3
    while widget.calls[-1:] != ["hide"] and time.monotonic() < deadline:
        time.sleep(0.05)
    assert slow_calls == [1]
    assert widget.calls == ["recording", "processing", "done", "hide"]


def test_press_does_not_claim_someone_elses_recording(tmp_path):
    widget = FakeWidget()
    api = make_api(tmp_path)
    api.start_recording()          # dashboard recording in progress
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    svc.press()                    # must fail to start and not mark pressed
    svc.release()                  # must not stop the dashboard's recording
    assert api._wav is not None    # dashboard recording still running
    assert "processing" not in widget.calls


def test_push_to_talk_end_to_end_real_transcription(tmp_path, spoken_wav):
    import shutil
    import time as _time

    from voisr.inject import get_clipboard, set_clipboard
    from voisr.transcriber import transcribe

    wav_src = spoken_wav("Push to talk works end to end.")

    class SpeechRecorder:
        def start(self, path):
            self.path = path

        def stop(self):
            shutil.copy(wav_src, self.path)
            return ""

    # paste disabled so the test doesn't post real Cmd-V keystrokes at
    # whatever window is focused; delivery then goes to the clipboard
    (tmp_path / "config.json").write_text('{"paste": false}')
    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "h.jsonl"),
              recorder=SpeechRecorder(), transcribe_fn=transcribe,
              clock=_time.monotonic)
    widget = FakeWidget()
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    old_clip = get_clipboard()
    try:
        svc.press()
        svc.release()
        wait_for_hide(widget, timeout=20)
        assert widget.calls[-2:] == ["done", "hide"]
        assert "push to talk" in get_clipboard().lower()
        assert "push to talk" in api.get_history()[0]["text"].lower()
    finally:
        set_clipboard(old_clip)


class ExplodingRecorder:
    def start(self, path):
        raise RuntimeError("boom: incompatible architecture")

    def stop(self):
        return ""


def test_recorder_failure_falls_back_and_recovers(tmp_path):
    fallback = FakeRecorder()
    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "h.jsonl"),
              recorder=ExplodingRecorder(),
              transcribe_fn=lambda wav: "recovered",
              clock=lambda: 1.0,
              fallback_recorder_factory=lambda: fallback)
    res = api.start_recording()
    assert res.get("recording") is True
    assert api._recorder is fallback
    out = api.stop_recording()
    assert out["text"] == "Recovered"


def test_recorder_failure_without_fallback_returns_error(tmp_path):
    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "h.jsonl"),
              recorder=ExplodingRecorder(),
              transcribe_fn=lambda wav: "x",
              clock=lambda: 1.0,
              fallback_recorder_factory=ExplodingRecorder)
    res = api.start_recording()
    assert "error" in res
    assert api._wav is None  # not stuck in a phantom recording state


def test_restart_survives_dead_listener(tmp_path):
    class DeadListener:
        def stop(self):
            raise OSError("run loop already torn down")

    svc = HotkeyService(make_api(tmp_path), widget=FakeWidget(), hide_delay=0)
    svc._listener = DeadListener()
    svc.restart("f13")  # must not raise (this crashed the app from Settings)


class FakeListener:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


def _counting_factory():
    made = []

    def factory(handler):
        listener = FakeListener()
        listener.handler = handler
        made.append(listener)
        return listener

    factory.made = made
    return factory


def test_start_attempts_listener_even_unpermitted_then_activates(tmp_path):
    # Registering the app in Input Monitoring REQUIRES creating the tap, so
    # the listener must be spawned even while unpermitted; status then flips
    # to active once the grant lands, without a relaunch.
    import time

    granted = {"ok": False}
    factory = _counting_factory()
    svc = HotkeyService(make_api(tmp_path), widget=FakeWidget(), hide_delay=0,
                        permissions_check=lambda: granted["ok"],
                        poll_interval=0.05, listener_factory=factory)
    svc.start("alt_r")
    assert len(factory.made) == 1          # attempted despite no permission
    assert "waiting for permission" in svc.status
    granted["ok"] = True                   # user grants in System Settings
    deadline = time.monotonic() + 3
    while "waiting" in svc.status and time.monotonic() < deadline:
        time.sleep(0.05)
    assert svc.status.startswith("active on Right Option")
    assert len(factory.made) == 2          # respawned to get a live tap
    svc.stop()


def test_restart_while_unpermitted_does_not_crash(tmp_path):
    factory = _counting_factory()
    svc = HotkeyService(make_api(tmp_path), widget=FakeWidget(), hide_delay=0,
                        permissions_check=lambda: False, poll_interval=999,
                        listener_factory=factory)
    svc.start("alt_r")
    svc.restart("f13")  # the exact user action that crashed the app
    svc.restart("cmd_r")
    assert svc._listener is not None       # a listener is always live
    svc.stop()
    assert svc._listener is None


def test_transcriber_failure_surfaces_as_error_not_exception(tmp_path):
    def broken_transcribe(wav):
        raise RuntimeError("whisper-cli not found")

    api = make_api(tmp_path)
    api._transcribe_fn = broken_transcribe
    api.start_recording()
    res = api.stop_recording()
    assert "error" in res and "whisper-cli" in res["error"]

    widget = FakeWidget()
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    svc.press()
    svc.release()
    wait_for_hide(widget)
    assert "error" in widget.calls and widget.calls[-1] == "hide"


def _write_wav(path, samples):
    import array
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(array.array("h", samples).tobytes())


def test_silent_recording_reports_mic_problem_not_hallucination(tmp_path):
    class SilentRecorder:
        def start(self, path):
            self.path = path

        def stop(self):
            _write_wav(self.path, [0] * 16000)  # 1s of digital silence
            return ""

    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "h.jsonl"),
              recorder=SilentRecorder(),
              transcribe_fn=lambda wav: "You",  # whisper hallucinates on silence
              clock=lambda: 1.0)
    api.start_recording()
    res = api.stop_recording()
    assert "error" in res and "Microphone" in res["error"]
    assert api.get_history() == []  # no junk entries


def test_real_audio_passes_silence_gate(tmp_path):
    class NoisyRecorder:
        def start(self, path):
            self.path = path

        def stop(self):
            _write_wav(self.path, [0, 900, -800, 1200] * 4000)
            return ""

    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "h.jsonl"),
              recorder=NoisyRecorder(),
              transcribe_fn=lambda wav: "real words",
              clock=lambda: 1.0)
    api.start_recording()
    res = api.stop_recording()
    assert res["text"] == "Real words"
