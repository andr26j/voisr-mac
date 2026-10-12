"""Live mic level: recorder metering -> queue -> Api -> 30 Hz pill feed."""
import math
import struct

import pytest

from voisr.recording_queue import RecordingQueue, SessionResult
from voisr.stream_recorder import StreamRecorder, level_from_int16


def tone(amplitude, n=1600):
    """A full-cycle sine block of int16 samples at the given peak amplitude."""
    return struct.pack(f"<{n}h", *(int(amplitude * math.sin(2 * math.pi * i / 32))
                                   for i in range(n)))


def dbfs_amplitude(dbfs):
    """Peak amplitude whose sine RMS sits at `dbfs`."""
    return 32767 * math.sqrt(2) * 10 ** (dbfs / 20)


# ---------- level_from_int16 ----------

def test_silence_is_zero():
    assert level_from_int16(b"\x00\x00" * 512) == 0.0


def test_empty_block_is_zero():
    assert level_from_int16(b"") == 0.0


def test_floor_and_ceiling_map_to_the_ends():
    assert level_from_int16(tone(dbfs_amplitude(-60))) == 0.0
    assert level_from_int16(tone(dbfs_amplitude(-55))) == pytest.approx(0.0, abs=0.02)
    assert level_from_int16(tone(dbfs_amplitude(-12))) == pytest.approx(1.0, abs=0.02)
    assert level_from_int16(tone(32767)) == 1.0  # full scale clips at the top


def test_level_is_linear_in_decibels():
    mid = level_from_int16(tone(dbfs_amplitude(-33.5)))
    assert mid == pytest.approx(0.5, abs=0.02)


# ---------- StreamRecorder.level ----------

def test_recorder_level_follows_the_latest_block(tmp_path):
    rec = StreamRecorder()
    assert rec.level == 0.0
    rec._recording = True
    rec._out = str(tmp_path / "x.wav")
    loud = tone(dbfs_amplitude(-12))
    rec._on_audio(loud, len(loud) // 2, None, None)
    assert rec.level == pytest.approx(1.0, abs=0.02)
    quiet = b"\x00\x00" * 256
    rec._on_audio(quiet, 256, None, None)
    assert rec.level == 0.0


def test_recorder_level_resets_when_capture_ends(tmp_path):
    rec = StreamRecorder()
    rec._recording = True
    rec._out = str(tmp_path / "y.wav")
    loud = tone(dbfs_amplitude(-12))
    rec._on_audio(loud, len(loud) // 2, None, None)
    rec.stop()
    assert rec.level == 0.0

    rec._recording = True
    rec._on_audio(loud, len(loud) // 2, None, None)
    rec.cancel()
    assert rec.level == 0.0


def test_a_meter_failure_never_loses_audio(tmp_path, monkeypatch):
    import voisr.stream_recorder as sr

    def boom(block):
        raise TypeError("odd-length buffer")

    monkeypatch.setattr(sr, "level_from_int16", boom)
    rec = StreamRecorder()
    rec._recording = True
    rec._out = str(tmp_path / "m.wav")
    block = b"\x01\x00" * 64
    rec._on_audio(block, 64, None, None)  # must not raise
    assert rec._frames == [block]


def test_stop_resets_level_after_the_stream_has_closed(tmp_path):
    """A callback still in flight when stop() begins must not leave a
    stale level behind: the reset comes after the stream is closed."""
    rec = StreamRecorder()
    rec._recording = True
    rec._out = str(tmp_path / "z.wav")
    loud = tone(dbfs_amplitude(-12))

    class LateCallbackStream:
        def stop(self):
            rec._recording = True   # simulate a callback that already passed
            rec._on_audio(loud, len(loud) // 2, None, None)  # the check
            rec._recording = False

        def close(self):
            pass

    rec._stream = LateCallbackStream()
    rec.stop()
    assert rec.level == 0.0
    rec._recording = True
    rec._stream = LateCallbackStream()
    rec.cancel()
    assert rec.level == 0.0


def test_level_ignores_blocks_after_stop(tmp_path):
    rec = StreamRecorder()
    loud = tone(dbfs_amplitude(-12))
    rec._on_audio(loud, len(loud) // 2, None, None)  # not recording
    assert rec.level == 0.0


# ---------- RecordingQueue.active_level ----------

class MeteredRecorder:
    def __init__(self, level):
        self.level = level

    def start(self, path):
        self.path = path

    def stop(self):
        self.path.write_bytes(b"audio" * 400)
        return ""

    def cancel(self):
        pass


class UnmeteredRecorder(MeteredRecorder):
    def __init__(self):
        pass


def make_queue(tmp_path, factory):
    return RecordingQueue(
        recorder_factory=factory,
        fallback_recorder_factory=None,
        processor=lambda s: SessionResult.success(s.sequence, text="x"),
        temp_path_factory=lambda: tmp_path / "c.wav",
    )


def test_active_level_is_none_when_idle(tmp_path):
    queue = make_queue(tmp_path, lambda: MeteredRecorder(0.4))
    try:
        assert queue.active_level() is None
    finally:
        queue.close()


def test_active_level_reads_the_capturing_recorder(tmp_path):
    queue = make_queue(tmp_path, lambda: MeteredRecorder(0.4))
    try:
        queue.start()
        assert queue.active_level() == 0.4
        queue.release(False, {}).result(2)
        assert queue.active_level() is None
    finally:
        queue.close()


def test_active_level_is_none_for_a_recorder_without_a_meter(tmp_path):
    queue = make_queue(tmp_path, UnmeteredRecorder)
    try:
        queue.start()
        assert queue.active_level() is None  # e.g. the ffmpeg fallback
    finally:
        queue.cancel()
        queue.close()


# ---------- RecordingWidget feed ----------

def test_level_js_formats_and_clamps():
    from voisr.ui import RecordingWidget
    assert RecordingWidget.level_js(0.42) == "window.setLevel && setLevel(0.420)"
    assert RecordingWidget.level_js(1.7) == "window.setLevel && setLevel(1.000)"
    assert RecordingWidget.level_js(-3) == "window.setLevel && setLevel(0.000)"
    for bad in (None, float("nan"), "0.5", True):
        assert RecordingWidget.level_js(bad) is None


class FakeWebview:
    def __init__(self):
        self.js = []

    def evaluateJavaScript_completionHandler_(self, js, handler):
        self.js.append(js)


class FakePanel:
    def setAlphaValue_(self, a):
        pass

    def orderFrontRegardless(self):
        pass

    def isOnActiveSpace(self):
        return True

    def occlusionState(self):
        return 2


@pytest.fixture
def main_thread(monkeypatch):
    """Run AppHelper.callAfter inline; collect callLater for manual firing."""
    from PyObjCTools import AppHelper
    later = []
    monkeypatch.setattr(AppHelper, "callAfter", lambda fn, *a: fn(*a))
    monkeypatch.setattr(AppHelper, "callLater", lambda delay, fn, *a: later.append((delay, fn, a)))
    return later


def make_widget(level_source):
    from voisr.ui import RecordingWidget
    widget = RecordingWidget(level_source=level_source)
    widget._panel = FakePanel()
    widget._webview = FakeWebview()
    return widget


def fire_feed(later):
    """Fire the pending 30 Hz ticks (skipping the visibility check)."""
    pending = [item for item in later if item[0] < 0.5]
    later.clear()
    for _delay, fn, args in pending:
        fn(*args)
    return pending


def level_calls(widget):
    return [js for js in widget._webview.js if "setLevel" in js]


def test_recording_pushes_levels_at_30hz(main_thread):
    levels = iter([0.1, 0.6, 0.3])
    widget = make_widget(lambda: next(levels))
    widget.show_recording()
    for _ in range(3):
        ticks = fire_feed(main_thread)
        assert [round(d, 4) for d, _f, _a in ticks] == [round(1 / 30, 4)]
    assert level_calls(widget) == [
        "window.setLevel && setLevel(0.100)",
        "window.setLevel && setLevel(0.600)",
        "window.setLevel && setLevel(0.300)",
    ]


@pytest.mark.parametrize("leave", ["show_processing", "show_done", "show_error", "hide"])
def test_feed_stops_on_any_other_state(main_thread, leave):
    widget = make_widget(lambda: 0.5)
    widget.show_recording()
    fire_feed(main_thread)
    getattr(widget, leave)()
    sent = len(level_calls(widget))
    for _ in range(3):
        fire_feed(main_thread)
    assert len(level_calls(widget)) == sent
    assert not [d for d, _f, _a in main_thread if d < 0.5]  # nothing rescheduled


def test_unmetered_recorder_ends_the_feed(main_thread):
    widget = make_widget(lambda: None)  # e.g. the ffmpeg fallback: no meter
    widget.show_recording()
    assert level_calls(widget) == []
    assert not [d for d, _f, _a in main_thread if d < 0.5]  # no 30 Hz no-ops


class FailingWebview(FakeWebview):
    def __init__(self):
        super().__init__()
        self.fail = False

    def evaluateJavaScript_completionHandler_(self, js, handler):
        if self.fail:
            raise RuntimeError("webview gone")
        super().evaluateJavaScript_completionHandler_(js, handler)


@pytest.mark.parametrize("leave", ["show_processing", "hide"])
def test_a_failing_state_change_still_ends_the_old_feed(main_thread, leave):
    widget = make_widget(lambda: 0.5)
    widget._webview = FailingWebview()
    widget.show_recording()
    widget._webview.fail = True
    getattr(widget, leave)()  # raises inside apply(); _set swallows it
    widget._webview.fail = False
    sent = len(level_calls(widget))
    fire_feed(main_thread)
    assert len(level_calls(widget)) == sent


def test_a_failing_level_source_never_breaks_the_feed(main_thread):
    calls = iter([RuntimeError("boom"), 0.25])

    def source():
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    widget = make_widget(source)
    widget.show_recording()
    fire_feed(main_thread)
    fire_feed(main_thread)
    assert level_calls(widget) == ["window.setLevel && setLevel(0.250)"]


def test_a_new_recording_restarts_a_single_feed(main_thread):
    widget = make_widget(lambda: 0.5)
    widget.show_recording()
    widget.show_recording()
    widget._webview.js.clear()  # each show pushes one level immediately
    ticks = fire_feed(main_thread)
    assert len(ticks) == 2  # one stale, one live
    assert len(level_calls(widget)) == 1  # only the live one pushes
    assert len([d for d, _f, _a in main_thread if d < 0.5]) == 1


def test_no_level_source_means_no_feed(main_thread):
    widget = make_widget(None)
    widget.show_recording()
    assert not [d for d, _f, _a in main_thread if d < 0.5]


# ---------- Api.current_level ----------

def test_api_current_level_tracks_the_capture(tmp_path):
    from voisr.history import HistoryStore
    from voisr.ui import Api
    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "h.jsonl"),
              recorder_factory=lambda: MeteredRecorder(0.7),
              transcribe_fn=lambda wav: "hello",
              clock=lambda: 1.0,
              paste_fn=lambda text: True,
              clipboard_fn=lambda text: None)
    try:
        assert api.current_level() is None
        api.start_recording()
        assert api.current_level() == 0.7
        api.stop_recording()
        assert api.current_level() is None
    finally:
        api.close()


def test_current_level_is_not_exposed_to_the_page():
    from voisr.ui import BRIDGE_METHODS
    assert "current_level" not in BRIDGE_METHODS
