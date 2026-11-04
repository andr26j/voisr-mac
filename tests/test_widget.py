import json
import threading

import pytest

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
        self.themes = []
        self.colors = []

    def show_recording(self, theme="classic", color=None):
        self.calls.append("recording")
        self.themes.append(theme)
        self.colors.append(color)

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
               recorder_factory=FakeRecorder,
               transcribe_fn=lambda wav: "hello widget",
               clock=lambda: 1.0,
               paste_fn=lambda text: True,
               clipboard_fn=lambda text: None)


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


def test_release_observes_its_future_without_calling_synchronous_stop(tmp_path):
    from concurrent.futures import Future

    from voisr.recording_queue import SessionResult

    class FutureApi:
        def get_config(self):
            return {"show_widget": True}

        def start_recording(self):
            return {"recording": True}

        def _release_recording(self, paste=False):
            future = Future()
            future.set_result(SessionResult.success(1, text="done"))
            return future

        def stop_recording(self, paste=False):
            raise AssertionError("hotkey completion must not stop shared state")

    widget = FakeWidget()
    service = HotkeyService(FutureApi(), widget=widget, hide_delay=0)
    service.press()
    service.release()
    wait_for_hide(widget, timeout=0.5)
    assert widget.calls == ["recording", "processing", "done", "hide"]


def test_release_keeps_its_epoch_when_enqueue_overlaps_a_new_press(tmp_path):
    from concurrent.futures import Future
    import threading

    from voisr.recording_queue import SessionResult

    class BlockingReleaseApi:
        def __init__(self):
            self.release_entered = threading.Event()
            self.release_may_return = threading.Event()
            self.future = Future()

        def get_config(self):
            return {"show_widget": True}

        def start_recording(self):
            return {"recording": True}

        def _release_recording(self, paste=False):
            self.release_entered.set()
            assert self.release_may_return.wait(2)
            return self.future

    class TrackingWidget(FakeWidget):
        def __init__(self):
            super().__init__()
            self.stale_mutation = threading.Event()

        def show_done(self):
            super().show_done()
            self.stale_mutation.set()

        def hide(self):
            super().hide()
            self.stale_mutation.set()

    api = BlockingReleaseApi()
    widget = TrackingWidget()
    service = HotkeyService(api, widget=widget, hide_delay=0)
    service.press()

    release_thread = threading.Thread(target=service.release)
    release_thread.start()
    assert api.release_entered.wait(2)

    press_started = threading.Event()
    press_finished = threading.Event()

    def re_press():
        press_started.set()
        service.press()
        press_finished.set()

    press_thread = threading.Thread(target=re_press)
    press_thread.start()
    assert press_started.wait(2)
    assert not press_finished.wait(0.2)
    api.release_may_return.set()
    release_thread.join(2)
    assert not release_thread.is_alive()
    assert press_finished.wait(2)
    press_thread.join(2)
    assert not press_thread.is_alive()

    api.future.set_result(SessionResult.success(1, text="old"))
    assert not widget.stale_mutation.wait(0.3)
    assert widget.calls == ["recording", "processing", "recording"]


def test_deferred_widget_callbacks_recheck_epoch_before_mutating(tmp_path):
    from concurrent.futures import Future
    import threading

    from voisr.recording_queue import SessionResult

    class ReadyApi:
        def __init__(self):
            self.future = Future()
            self.future.set_result(SessionResult.success(1, text="old"))

        def get_config(self):
            return {"show_widget": True}

        def start_recording(self):
            return {"recording": True}

        def _release_recording(self, paste=False):
            return self.future

    class DeferredWidget:
        _supports_ownership_action = True

        def __init__(self):
            self.calls = []
            self.pending = []
            self.condition = threading.Condition()

        def _queue(self, state, is_current=None, if_current=None):
            with self.condition:
                self.pending.append((state, is_current, if_current))
                self.condition.notify_all()

        def show_recording(self, theme="classic", color=None, is_current=None,
                           if_current=None):
            self._queue("recording", is_current, if_current)

        def show_processing(self, is_current=None, if_current=None):
            self._queue("processing", is_current, if_current)

        def show_done(self, is_current=None, if_current=None):
            self._queue("done", is_current, if_current)

        def show_error(self, is_current=None, if_current=None):
            self._queue("error", is_current, if_current)

        def hide(self, is_current=None, if_current=None):
            self._queue("hide", is_current, if_current)

        def wait_for_pending(self, count):
            with self.condition:
                return self.condition.wait_for(lambda: len(self.pending) >= count, 2)

        def flush(self):
            with self.condition:
                pending, self.pending = self.pending, []
            for state, is_current, if_current in pending:
                def apply(state=state):
                    self.calls.append(state)
                if if_current is not None:
                    if_current(apply)
                elif is_current is None or is_current():
                    apply()

    widget = DeferredWidget()
    service = HotkeyService(ReadyApi(), widget=widget, hide_delay=0)
    service.press()
    assert widget.wait_for_pending(1)
    widget.flush()
    service.release()
    assert widget.wait_for_pending(3)
    service.press()
    assert widget.wait_for_pending(4)
    widget.flush()
    assert widget.calls == ["recording", "recording"]


def test_widget_mutation_holds_epoch_ownership_after_callback_check(tmp_path):
    from concurrent.futures import Future
    import threading

    from voisr.recording_queue import SessionResult

    class ReadyApi:
        def __init__(self):
            self.future = Future()
            self.future.set_result(SessionResult.success(1, text="old"))

        def get_config(self):
            return {"show_widget": True}

        def start_recording(self):
            return {"recording": True}

        def _release_recording(self, paste=False):
            return self.future

    class PausingWidget:
        _supports_ownership_guard = True
        _supports_ownership_action = True

        def __init__(self):
            self.calls = []
            self.done_queued = threading.Event()
            self.after_owner_check = threading.Event()
            self.allow_done = threading.Event()

        def _apply(self, state, is_current=None, if_current=None):
            def apply():
                self.calls.append(state)
            if if_current is not None:
                if_current(apply)
            elif is_current is None or is_current():
                apply()

        def show_recording(self, theme="classic", color=None, is_current=None,
                           if_current=None):
            self._apply("recording", is_current, if_current)

        def show_processing(self, is_current=None, if_current=None):
            self._apply("processing", is_current, if_current)

        def show_done(self, is_current=None, if_current=None):
            self._done = (is_current, if_current)
            self.done_queued.set()

        def show_error(self, is_current=None, if_current=None):
            self._apply("error", is_current, if_current)

        def hide(self, is_current=None, if_current=None):
            pass

        def run_done(self):
            is_current, if_current = self._done

            def apply():
                self.after_owner_check.set()
                assert self.allow_done.wait(2)
                self.calls.append("done")

            if if_current is not None:
                if_current(apply)
            elif is_current is None or is_current():
                apply()

    widget = PausingWidget()
    service = HotkeyService(ReadyApi(), widget=widget, hide_delay=0)
    service.press()
    service.release()
    assert widget.done_queued.wait(2)

    done_thread = threading.Thread(target=widget.run_done)
    done_thread.start()
    assert widget.after_owner_check.wait(2)

    press_finished = threading.Event()

    def re_press():
        service.press()
        press_finished.set()

    press_thread = threading.Thread(target=re_press)
    press_thread.start()
    try:
        assert not press_finished.wait(0.2)
    finally:
        widget.allow_done.set()
        done_thread.join(2)
        press_thread.join(2)
    assert not done_thread.is_alive()
    assert press_finished.is_set()
    assert not press_thread.is_alive()
    assert widget.calls == ["recording", "processing", "done", "recording"]


def test_repress_records_while_first_transcribes_and_preserves_widget_owner(tmp_path):
    import threading

    first_transcribing = threading.Event()
    allow_first = threading.Event()
    recorders = []
    pasted = []
    calls = 0

    def recorder_factory():
        recorder = FakeRecorder()
        recorder.stopped = threading.Event()
        original_stop = recorder.stop

        def stop():
            result = original_stop()
            recorder.stopped.set()
            return result

        recorder.stop = stop
        recorders.append(recorder)
        return recorder

    def transcribe_audio(wav):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_transcribing.set()
            assert allow_first.wait(2)
            return "first"
        return "second"

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"paste": True}))
    api = Api(
        config_path=cfg,
        history=HistoryStore(tmp_path / "rapid.jsonl"),
        recorder_factory=recorder_factory,
        transcribe_fn=transcribe_audio,
        paste_fn=lambda text: pasted.append(text) or True,
        clipboard_fn=lambda text: None,
    )
    widget = FakeWidget()
    service = HotkeyService(api, widget=widget, hide_delay=0)
    try:
        service.press()
        service.release()
        assert first_transcribing.wait(2)

        service.press()
        assert widget.calls[-1] == "recording"
        service.release()
        assert recorders[1].stopped.wait(2)
        assert pasted == []

        allow_first.set()
        wait_for_hide(widget)
        assert pasted == ["First", " Second"]
        assert widget.calls[-2:] == ["done", "hide"]
        assert widget.calls.count("done") == 1
    finally:
        allow_first.set()
        service.stop()
        api.close()


def test_widget_disabled_via_config(tmp_path):
    widget = FakeWidget()
    api = make_api(tmp_path, config={"show_widget": False})
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    svc.press()
    svc.release()
    assert widget.calls == []


@pytest.fixture
def settle_widget():
    """Waits out the dictations finishing since the test began, so nothing
    more can reach the widget."""
    before = set(threading.enumerate())

    def settle():
        for thread in set(threading.enumerate()) - before:
            if thread.name.startswith("voisr-widget-"):
                thread.join(5)

    return settle


# (hands-free?, how the dictation ends or is cancelled)
DICTATION_ENDINGS = [
    pytest.param(False, lambda svc: svc.release(at=100.1), id="held-key-let-go"),
    pytest.param(True, lambda svc: svc.press(at=100.1), id="hands-free-second-press"),
    pytest.param(True, lambda svc: svc._stop_hands_free(), id="toggle-or-menu"),
    pytest.param(False, lambda svc: svc.cancel_chord(at=100.1), id="chord-drop"),
    pytest.param(True, lambda svc: svc.cancel_escape(), id="esc"),
]


@pytest.mark.parametrize("latched, end", DICTATION_ENDINGS)
def test_pill_comes_down_when_the_setting_went_off_during_the_dictation(
        tmp_path, settle_widget, latched, end):
    widget = FakeWidget()
    api = make_api(tmp_path)
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    svc.press(latched=latched, at=100.0)
    assert widget.calls == ["recording"]

    api.set_config({"show_widget": False})
    end(svc)
    settle_widget()

    api.close()
    assert widget.calls == ["recording", "hide"]


@pytest.mark.parametrize("latched, end", DICTATION_ENDINGS)
def test_pill_never_starts_when_the_setting_came_on_during_the_dictation(
        tmp_path, settle_widget, latched, end):
    widget = FakeWidget()
    api = make_api(tmp_path, config={"show_widget": False})
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    svc.press(latched=latched, at=100.0)

    api.set_config({"show_widget": True})
    end(svc)
    settle_widget()

    api.close()
    assert [call for call in widget.calls if call != "hide"] == []


def test_pill_finishes_and_comes_down_when_the_setting_went_off_during_processing(
        tmp_path, settle_widget):
    allow = threading.Event()
    widget = FakeWidget()
    api = make_api(tmp_path)
    api._transcribe_fn = lambda wav: allow.wait(5) and "hello widget"
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    try:
        svc.press()
        svc.release()
        assert widget.calls == ["recording", "processing"]

        api.set_config({"show_widget": False})
    finally:
        allow.set()
    settle_widget()

    api.close()
    assert widget.calls == ["recording", "processing", "done", "hide"]


def test_new_dictation_takes_down_an_older_pill_when_the_setting_is_off(
        tmp_path, settle_widget):
    """The new epoch orphans the older pill's own hide, and with the setting
    off nothing else would replace it."""
    allow = threading.Event()
    widget = FakeWidget()
    api = make_api(tmp_path)
    api._transcribe_fn = lambda wav: allow.wait(5) and "hello widget"
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    try:
        svc.press()
        svc.release()
        assert widget.calls == ["recording", "processing"]

        api.set_config({"show_widget": False})
        svc.press()
        assert widget.calls == ["recording", "processing", "hide"]

        svc.release()
    finally:
        allow.set()
    settle_widget()

    api.close()
    assert widget.calls == ["recording", "processing", "hide"]


class QueuedWidget:
    """Like the real pill: each call waits for the main thread, where a newer
    dictation's epoch can still drop it."""
    _supports_ownership_action = True

    def __init__(self):
        self.calls = []
        self.pending = []

    def show_recording(self, theme="classic", color=None, if_current=None):
        self.pending.append(("recording", if_current))

    def show_processing(self, if_current=None):
        self.pending.append(("processing", if_current))

    def show_done(self, if_current=None):
        self.pending.append(("done", if_current))

    def hide(self, if_current=None):
        self.pending.append(("hide", if_current))

    def flush(self):
        pending, self.pending = self.pending, []
        for state, if_current in pending:
            if_current(lambda state=state: self.calls.append(state))


def test_a_hide_dropped_by_a_newer_dictation_does_not_count_as_down(
        tmp_path, settle_widget):
    widget = QueuedWidget()
    api = make_api(tmp_path)
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    svc.press()
    widget.flush()
    assert widget.calls == ["recording"]

    # the first dictation's hide is queued but hasn't run when the next press
    # lands, so that press's new epoch drops it
    svc.release()
    settle_widget()
    assert widget.pending[-1][0] == "hide"

    api.set_config({"show_widget": False})
    svc.press()
    widget.flush()

    api.close()
    assert widget.calls == ["recording", "hide"]


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
    assert api._recordings.has_active_capture is True
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
              recorder_factory=SpeechRecorder, transcribe_fn=transcribe,
              clock=_time.monotonic,
              clipboard_fn=set_clipboard)  # real: the assertion reads it back
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
              recorder_factory=ExplodingRecorder,
              transcribe_fn=lambda wav: "recovered",
              clock=lambda: 1.0,
              fallback_recorder_factory=lambda: fallback,
              clipboard_fn=lambda text: None)
    res = api.start_recording()
    assert res.get("recording") is True
    assert api._recordings.has_active_capture is True
    assert fallback.path is not None
    out = api.stop_recording()
    assert out["text"] == "Recovered"


def test_recorder_failure_without_fallback_returns_error(tmp_path):
    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "h.jsonl"),
              recorder_factory=ExplodingRecorder,
              transcribe_fn=lambda wav: "x",
              clock=lambda: 1.0,
              fallback_recorder_factory=ExplodingRecorder,
              clipboard_fn=lambda text: None)
    res = api.start_recording()
    assert "error" in res
    assert api._recordings.has_active_capture is False


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

    def factory(handler, mode="listen"):
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
              recorder_factory=SilentRecorder,
              transcribe_fn=lambda wav: "You",  # whisper hallucinates on silence
              clock=lambda: 1.0, clipboard_fn=lambda text: None)
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
              recorder_factory=NoisyRecorder,
              transcribe_fn=lambda wav: "real words",
              clock=lambda: 1.0, clipboard_fn=lambda text: None)
    api.start_recording()
    res = api.stop_recording()
    assert res["text"] == "Real words"


def test_unknown_theme_sanitized_to_classic():
    from voisr.ui import sanitize_pill_theme
    assert sanitize_pill_theme("ember") == "ember"
    assert sanitize_pill_theme("classic") == "classic"
    assert sanitize_pill_theme('x"); alert(1); ("') == "classic"
    assert sanitize_pill_theme(None) == "classic"
    assert sanitize_pill_theme([]) == "classic"
    assert sanitize_pill_theme({}) == "classic"


def test_press_passes_configured_theme_to_widget(tmp_path):
    widget = FakeWidget()
    api = make_api(tmp_path, config={"pill_theme": "ember"})
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    svc.press()
    assert widget.themes == ["ember"]


def test_press_defaults_to_classic_theme(tmp_path):
    widget = FakeWidget()
    svc = HotkeyService(make_api(tmp_path), widget=widget, hide_delay=0)
    svc.press()
    assert widget.themes == ["classic"]


def test_widget_html_covers_all_pill_themes():
    """widget.html's JS whitelist and CSS rules must track PILL_THEMES."""
    from voisr.ui import PILL_THEMES, WEB_DIR
    html = (WEB_DIR / "widget.html").read_text()
    for theme in PILL_THEMES:
        assert f'"{theme["value"]}"' in html  # THEME_VALUES entry
        if theme["value"] != "classic":       # classic = no attribute by design
            assert f'[data-theme="{theme["value"]}"]' in html  # CSS rule exists
    # custom-color bridge must exist alongside the themes
    assert "--bar-color" in html
    assert "setColor" in html


def test_pill_color_sanitizer():
    from voisr.ui import sanitize_pill_color
    assert sanitize_pill_color("#FF00AA") == "#FF00AA"
    assert sanitize_pill_color("#ff00aa") == "#ff00aa"
    assert sanitize_pill_color(None) is None
    assert sanitize_pill_color("red") is None
    assert sanitize_pill_color("#FFF") is None
    assert sanitize_pill_color('#12345"); alert(1); ("') is None
    assert sanitize_pill_color(["#FF00AA"]) is None
    assert sanitize_pill_color({}) is None


def test_press_passes_configured_color_to_widget(tmp_path):
    widget = FakeWidget()
    api = make_api(tmp_path, config={"pill_color": "#FF00AA"})
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    svc.press()
    assert widget.colors == ["#FF00AA"]


def test_press_passes_none_color_by_default_and_for_invalid(tmp_path):
    widget = FakeWidget()
    svc = HotkeyService(make_api(tmp_path), widget=widget, hide_delay=0)
    svc.press()
    assert widget.colors == [None]

    widget2 = FakeWidget()
    api2 = make_api(tmp_path, config={"pill_color": "not-a-color"})
    svc2 = HotkeyService(api2, widget=widget2, hide_delay=0)
    svc2.press()
    assert widget2.colors == [None]


def test_stop_wakes_and_joins_the_permission_watcher(tmp_path):
    """L1: stop() used to just flag the poll thread and leave it sleeping
    (up to poll_interval); a start() racing its exit could find _watcher
    still set and spawn no new one. stop() now wakes the thread and joins
    it, so start() always begins with a clean slate."""
    import time
    granted = {"ok": False}
    factory = _counting_factory()
    svc = HotkeyService(make_api(tmp_path), widget=FakeWidget(), hide_delay=0,
                        permissions_check=lambda: granted["ok"],
                        poll_interval=999, listener_factory=factory)
    svc.start("alt_r")
    t0 = time.monotonic()
    svc.stop()
    assert time.monotonic() - t0 < 1.0, "stop() waited on a sleeping poll"
    assert svc._watcher is None
    svc._poll_interval = 0.05
    svc.start("alt_r")
    assert svc._watcher is not None
    granted["ok"] = True
    deadline = time.monotonic() + 2
    while "waiting" in svc.status and time.monotonic() < deadline:
        time.sleep(0.02)
    assert svc.status.startswith("active on"), svc.status
    svc.stop()
