import json
import os
import threading

import pytest

from voisr.history import HistoryStore
from voisr.pasteboard import PasteDeliveryError
from voisr.recording_queue import RecordingQueueError
from voisr.ui import Api


class FakeRecorder:
    def __init__(self):
        self.recording = False

    def start(self, path):
        self.recording = True
        self.path = path

    def stop(self):
        self.recording = False
        with open(self.path, "wb") as f:
            f.write(b"\0" * 2000)
        return ""


def make_api(tmp_path, **overrides):
    kwargs = dict(config_path=tmp_path / "config.json",
                  history=HistoryStore(tmp_path / "h.jsonl"),
                  recorder_factory=FakeRecorder,
                  transcribe_fn=lambda wav: "um, hello world",
                  clock=lambda: 10.0,
                  clipboard_fn=lambda text: None)
    kwargs.update(overrides)
    return Api(**kwargs)


class FakeStatus:
    """Menu-bar stand-in. `recording` is the glyph as it looks right now;
    `states` is every push it received, so tests can pin both the value and
    the fact that redundant pushes are not made."""

    def __init__(self):
        self.recording = False
        self.states = []
        self.release_calls = 0

    def set_recording(self, value):
        self.recording = bool(value)
        self.states.append(bool(value))

    def release(self):
        self.release_calls += 1


def test_record_cycle_produces_cleaned_history(tmp_path):
    api = make_api(tmp_path)
    assert api.start_recording()["recording"] is True
    out = api.stop_recording()
    assert out["text"] == "Hello world"
    items = api.get_history()
    assert len(items) == 1 and items[0]["text"] == "Hello world"


def test_stop_without_start_errors(tmp_path):
    api = make_api(tmp_path)
    assert "error" in api.stop_recording()


def test_config_roundtrip_and_dictionary(tmp_path):
    api = make_api(tmp_path)
    cfg = api.get_config()
    assert cfg["paste"] is True
    api.set_config({"paste": False, "dictionary": {"trademake": "TradeMate"}})
    assert api.get_config()["paste"] is False
    assert json.loads((tmp_path / "config.json").read_text())["dictionary"] == {"trademake": "TradeMate"}


def test_successful_dictation_notifies_window(tmp_path):
    import time

    class FakeWindow:
        def __init__(self):
            self.calls = []

        def evaluate_js(self, js):
            self.calls.append(js)

    api = make_api(tmp_path)
    win = FakeWindow()
    api.attach_window(win)
    api.start_recording()
    api.stop_recording()
    deadline = time.monotonic() + 2.0  # the notify is fire-and-forget off-thread
    while not win.calls and time.monotonic() < deadline:
        time.sleep(0.02)
    assert any("voisr:new-dictation" in c for c in win.calls)


def test_corrupt_config_falls_back_to_defaults(tmp_path):
    (tmp_path / "config.json").write_text("{not json")
    api = make_api(tmp_path)
    cfg = api.get_config()
    assert cfg["paste"] is True and cfg["hotkey"] == "alt_r"


def test_stats_shape(tmp_path):
    api = make_api(tmp_path)
    s = api.get_stats()
    assert set(s) == {"total_words", "dictations", "avg_wpm", "streak_days", "total_seconds", "time_saved_seconds", "daily"}


def test_delete_history_item(tmp_path):
    api = make_api(tmp_path)
    api.start_recording()
    api.stop_recording()
    item_id = api.get_history()[0]["id"]
    api.delete_history_item(item_id)
    assert api.get_history() == []


def test_default_config_includes_pill_theme(tmp_path):
    api = make_api(tmp_path)
    assert api.get_config()["pill_theme"] == "classic"


def test_pill_theme_roundtrip(tmp_path):
    api = make_api(tmp_path)
    api.set_config({"pill_theme": "ember"})
    assert api.get_config()["pill_theme"] == "ember"


def test_get_pill_themes_shape(tmp_path):
    api = make_api(tmp_path)
    themes = api.get_pill_themes()
    assert [t["value"] for t in themes] == [
        "classic", "ember", "aurora", "ocean", "neon", "breathe", "scanner"]
    assert all(set(t) == {"value", "label"} for t in themes)


def test_default_config_includes_pill_color_none(tmp_path):
    api = make_api(tmp_path)
    assert api.get_config()["pill_color"] is None


def test_pill_color_roundtrip(tmp_path):
    api = make_api(tmp_path)
    api.set_config({"pill_color": "#FF00AA"})
    assert api.get_config()["pill_color"] == "#FF00AA"


def test_released_sessions_paste_and_enter_history_fifo(tmp_path):
    import threading

    first_started = threading.Event()
    allow_first = threading.Event()
    recorders = []
    paste_log = []
    call_number = 0
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"paste": True}))

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
        nonlocal call_number
        call_number += 1
        if call_number == 1:
            first_started.set()
            assert allow_first.wait(2)
            return "first"
        return "second"

    api = Api(
        config_path=cfg,
        history=HistoryStore(tmp_path / "ordered.jsonl"),
        recorder_factory=recorder_factory,
        transcribe_fn=transcribe_audio,
        paste_fn=lambda text: paste_log.append(text) or True,
        clipboard_fn=lambda text: None,
    )
    try:
        api.start_recording()
        first = api._release_recording(paste=True)
        assert first_started.wait(2)

        api.start_recording()
        second = api._release_recording(paste=True)
        assert recorders[1].stopped.wait(2)
        assert second.done() is False

        allow_first.set()
        assert first.result(timeout=2).to_api_dict()["text"] == "First"
        assert second.result(timeout=2).to_api_dict()["text"] == "Second"
        assert paste_log == ["First", " Second"]
        stored = [json.loads(line)["text"]
                  for line in (tmp_path / "ordered.jsonl").read_text().splitlines()]
        assert stored == ["First", "Second"]
    finally:
        allow_first.set()
        api.close()


def test_release_snapshots_dictionary_before_queue_wait(tmp_path):
    import threading

    first_started = threading.Event()
    allow_first = threading.Event()
    calls = 0
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "paste": False,
        "dictionary": {"trade mate": "TradeMate"},
    }))

    def transcribe_audio(wav):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            assert allow_first.wait(2)
            return "first"
        return "trade mate"

    api = Api(
        config_path=cfg,
        history=HistoryStore(tmp_path / "snapshot.jsonl"),
        recorder_factory=FakeRecorder,
        transcribe_fn=transcribe_audio,
        clipboard_fn=lambda text: None,
    )
    try:
        api.start_recording()
        first = api._release_recording()
        assert first_started.wait(2)
        api.start_recording()
        second = api._release_recording()
        api.set_config({"dictionary": {"trade mate": "Changed Later"}})

        allow_first.set()
        first.result(timeout=2)
        assert second.result(timeout=2).to_api_dict()["text"] == "TradeMate"
    finally:
        allow_first.set()
        api.close()


class ExplodingHistory:
    def add(self, text, duration_s):
        raise OSError("history disk unavailable")

    def list(self):
        return []


def test_history_failure_warns_but_still_delivers(tmp_path):
    copied = []
    api = Api(
        config_path=tmp_path / "config.json",
        history=ExplodingHistory(),
        recorder_factory=FakeRecorder,
        transcribe_fn=lambda wav: "still usable",
        clipboard_fn=copied.append,
        clock=iter([1.0, 2.0] + [3.0] * 20).__next__,
    )
    try:
        api.start_recording()
        result = api.stop_recording()
        assert result["text"] == "Still usable"
        assert result["warning"].startswith("history failed:")
        assert "item" not in result
        assert copied == ["Still usable"]
    finally:
        api.close()


def test_paste_failure_uses_clipboard_and_queue_continues(tmp_path):
    copied = []
    calls = iter([RuntimeError("paste exploded"), None])

    def paste(text):
        failure = next(calls)
        if failure:
            raise failure
        return True

    api = Api(
        config_path=tmp_path / "config.json",
        history=HistoryStore(tmp_path / "paste.jsonl"),
        recorder_factory=FakeRecorder,
        transcribe_fn=lambda wav: "hello",
        paste_fn=paste,
        clipboard_fn=copied.append,
    )
    api.set_config({"paste": True})
    try:
        api.start_recording()
        first = api.stop_recording(paste=True)
        api.start_recording()
        second = api.stop_recording(paste=True)
        assert first["pasted"] is False and first["copied"] is True
        assert copied == ["Hello"]
        assert second["pasted"] is True
    finally:
        api.close()


def test_clipboard_failure_returns_text_and_history_then_advances(tmp_path):
    calls = 0

    def clipboard(text):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("pasteboard unavailable")

    api = Api(
        config_path=tmp_path / "config.json",
        history=HistoryStore(tmp_path / "clipboard.jsonl"),
        recorder_factory=FakeRecorder,
        transcribe_fn=lambda wav: "recoverable text",
        clipboard_fn=clipboard,
    )
    try:
        api.start_recording()
        first = api.stop_recording()
        api.start_recording()
        second = api.stop_recording()
        assert first["text"] == "Recoverable text"
        assert first["item"]["text"] == "Recoverable text"
        assert first["error"].startswith("delivery failed:")
        assert first["copied"] is False
        assert second["text"] == "Recoverable text"
        assert "error" not in second
    finally:
        api.close()


@pytest.mark.parametrize("failure", ["history", "paste", "clipboard"])
def test_admitted_session_after_delivery_failure_completes_in_fifo_order(
    tmp_path, failure
):
    first_processing = threading.Event()
    allow_first = threading.Event()
    transcriptions = 0
    clipboard = []
    pasted = []
    paste_attempts = 0

    class FirstHistoryFails:
        def __init__(self):
            self.items = []

        def add(self, text, duration_s):
            if not self.items:
                self.items.append("failed")
                raise OSError("history unavailable")
            self.items.append(text)
            return {"text": text}

        def list(self):
            return []

    def transcribe_audio(wav):
        nonlocal transcriptions
        transcriptions += 1
        if transcriptions == 1:
            first_processing.set()
            assert allow_first.wait(2)
            return "first"
        return "second"

    def paste(text):
        nonlocal paste_attempts
        paste_attempts += 1
        if failure == "paste" and paste_attempts == 1:
            raise OSError("paste unavailable")
        pasted.append(text)
        return True

    def copy(text):
        if failure == "clipboard" and not clipboard:
            clipboard.append("failed")
            raise OSError("clipboard unavailable")
        clipboard.append(text)

    history = FirstHistoryFails() if failure == "history" else HistoryStore(
        tmp_path / f"{failure}.jsonl"
    )
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"paste": failure == "paste"}))
    api = Api(
        config_path=cfg,
        history=history,
        recorder_factory=FakeRecorder,
        transcribe_fn=transcribe_audio,
        paste_fn=paste,
        clipboard_fn=copy,
    )
    try:
        api.start_recording()
        first = api._release_recording(paste=True)
        assert first_processing.wait(2)
        api.start_recording()
        second = api._release_recording(paste=True)
        assert second.done() is False

        allow_first.set()
        first_result = first.result(timeout=2).to_api_dict()
        second_result = second.result(timeout=2).to_api_dict()
        assert [first_result["text"], second_result["text"]] == ["First", "Second"]
        assert "error" not in second_result
        if failure == "history":
            assert first_result["warning"].startswith("history failed:")
        elif failure == "paste":
            assert first_result["pasted"] is False and first_result["copied"] is True
            assert second_result["pasted"] is True
        else:
            assert first_result["copied"] is False
            assert first_result["error"].startswith("delivery failed:")
    finally:
        allow_first.set()
        api.close()


def test_preserved_clipboard_error_suppresses_plain_text_fallback(tmp_path):
    copied = []

    def paste(text):
        raise PasteDeliveryError(
            "unrepresentable clipboard", pasted=False, fallback_safe=False)

    api = Api(
        config_path=tmp_path / "config.json",
        history=HistoryStore(tmp_path / "preserved.jsonl"),
        recorder_factory=FakeRecorder,
        transcribe_fn=lambda wav: "safe in history",
        paste_fn=paste,
        clipboard_fn=copied.append,
    )
    api.set_config({"paste": True})
    try:
        api.start_recording()
        result = api.stop_recording(paste=True)
        assert result["pasted"] is False
        assert result["copied"] is False
        assert result["item"]["text"] == "Safe in history"
        assert result["error"].startswith("delivery failed:")
        assert copied == []
    finally:
        api.close()


def test_restore_error_reports_paste_landed_without_fallback(tmp_path):
    copied = []

    def paste(text):
        raise PasteDeliveryError(
            "restore failed", pasted=True, fallback_safe=False)

    api = Api(
        config_path=tmp_path / "config.json",
        history=HistoryStore(tmp_path / "restore.jsonl"),
        recorder_factory=FakeRecorder,
        transcribe_fn=lambda wav: "already delivered",
        paste_fn=paste,
        clipboard_fn=copied.append,
    )
    api.set_config({"paste": True})
    try:
        api.start_recording()
        result = api.stop_recording(paste=True)
        assert result["pasted"] is True
        assert result["copied"] is True
        assert result["error"].startswith("delivery failed:")
        assert copied == []
    finally:
        api.close()


def test_preservation_failure_resolves_then_next_queued_paste_runs(tmp_path):
    attempts = 0
    copied = []

    def paste(text):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PasteDeliveryError(
                "snapshot failed", pasted=False, fallback_safe=False)
        return True

    api = Api(
        config_path=tmp_path / "config.json",
        history=HistoryStore(tmp_path / "queue-preserved.jsonl"),
        recorder_factory=FakeRecorder,
        transcribe_fn=lambda wav: "queued",
        paste_fn=paste,
        clipboard_fn=copied.append,
    )
    api.set_config({"paste": True})
    try:
        api.start_recording()
        first = api.stop_recording(paste=True)
        api.start_recording()
        second = api.stop_recording(paste=True)
        assert first["error"].startswith("delivery failed:")
        assert first["copied"] is False
        assert second["pasted"] is True
        assert copied == []
        assert attempts == 2
    finally:
        api.close()


def test_paste_landed_error_keeps_smart_spacing_for_next_dictation(tmp_path):
    pasted = []
    copied = []

    def paste(text):
        pasted.append(text)
        if len(pasted) == 1:
            raise PasteDeliveryError(
                "restore failed", pasted=True, fallback_safe=False)
        return True

    api = Api(
        config_path=tmp_path / "config.json",
        history=HistoryStore(tmp_path / "paste-landed.jsonl"),
        recorder_factory=FakeRecorder,
        transcribe_fn=lambda wav: "queued",
        paste_fn=paste,
        clipboard_fn=copied.append,
        clock=lambda: 1.0,
    )
    api.set_config({"paste": True})
    try:
        api.start_recording()
        first = api.stop_recording(paste=True)
        api.start_recording()
        second = api.stop_recording(paste=True)
        assert first["error"].startswith("delivery failed:")
        assert first["pasted"] is True and first["copied"] is True
        assert second["pasted"] is True
        assert pasted == ["Queued", " Queued"]
        assert copied == []
    finally:
        api.close()


import json as _json

from voisr import launch_agent


def test_get_permissions_defaults_to_microphone_and_accessibility(tmp_path):
    api = make_api(tmp_path)
    panes = api.get_permissions()["panes"]
    assert [p["key"] for p in panes] == ["microphone", "accessibility"]
    assert [p["key"] for p in panes if p["hotkey"]] == ["accessibility"]


def test_get_permissions_returns_three_panes_in_listen_mode(tmp_path):
    (tmp_path / "config.json").write_text('{"event_tap": "listen"}')
    api = make_api(tmp_path)
    panes = api.get_permissions()["panes"]
    assert [p["key"] for p in panes] == [
        "microphone", "input_monitoring", "accessibility"]
    assert all("settings_url" in p for p in panes)


def test_open_settings_pane_rejects_unknown_key(tmp_path):
    api = make_api(tmp_path)
    assert api.open_settings_pane("bogus") == {"ok": False}


def test_restore_history_item_puts_it_back(tmp_path):
    api = make_api(tmp_path)
    api.start_recording()
    api.stop_recording()
    item = api.get_history()[0]
    api.delete_history_item(item["id"])
    assert api.get_history() == []
    assert api.restore_history_item(item) == {"ok": True}
    assert api.get_history()[0]["id"] == item["id"]


def test_get_models_lists_known_models_with_installed_flags(tmp_path):
    api = make_api(tmp_path)
    out = api.get_models()
    values = [m["value"] for m in out["models"]]
    assert values == ["small.en", "base.en"]
    assert all(isinstance(m["installed"], bool) for m in out["models"])


def test_set_launch_at_login_round_trips(tmp_path, monkeypatch):
    plist = tmp_path / "agent.plist"
    monkeypatch.setattr(launch_agent, "PLIST_PATH", plist)
    api = make_api(tmp_path)
    assert api.set_launch_at_login(True)["enabled"] is True
    assert plist.exists() is True
    assert api.set_launch_at_login(False)["enabled"] is False
    assert plist.exists() is False
    assert api.get_config()["launch_at_login"] is False


def test_clear_history_wipes_everything(tmp_path):
    api = make_api(tmp_path)
    api.start_recording()
    api.stop_recording()
    assert api.clear_history() == {"ok": True, "removed": 1}
    assert api.get_history() == []


def test_export_history_txt_and_json(tmp_path):
    api = make_api(tmp_path)
    api.start_recording()
    api.stop_recording()
    txt = api.export_history("txt")
    assert "Hello world" in txt["text"]
    assert txt["suggested_name"].endswith(".txt")
    parsed = _json.loads(api.export_history("json")["text"])
    assert parsed[0]["text"] == "Hello world"
    assert api.export_history("exe").get("error")


def test_set_appearance_accepts_only_known_modes(tmp_path):
    api = make_api(tmp_path)
    assert api.set_appearance("dark")["appearance"] == "dark"
    assert api.get_config()["appearance"] == "dark"
    assert api.set_appearance("light")["appearance"] == "light"
    assert api.set_appearance("system")["appearance"] == "system"
    assert api.set_appearance("hot-pink").get("error")
    assert api.get_config()["appearance"] == "system"  # unchanged by the bad value


def test_is_recording_tracks_the_record_cycle(tmp_path):
    api = make_api(tmp_path)
    assert api.is_recording() is False
    api.start_recording()
    assert api.is_recording() is True
    api.stop_recording()
    assert api.is_recording() is False


def test_status_item_follows_the_record_cycle(tmp_path):
    class FakeStatus:
        def __init__(self):
            self.states = []

        def set_recording(self, value):
            self.states.append(bool(value))

        def release(self):
            pass

    api = make_api(tmp_path)
    status = FakeStatus()
    api.attach_status_item(status)
    api.start_recording()
    api.stop_recording()
    assert status.states == [True, False]


def test_the_glyph_stays_busy_for_the_whole_dictation(tmp_path):
    """Releasing the key closes the mic, but the dictation is still in flight
    until whisper has finished -- and transcription is the slow part. The
    glyph used to clear the moment the mic closed, so the menu bar claimed
    idle while the app was still working. Observe the glyph from inside the
    transcription itself: that is the window the old code got wrong."""
    status = FakeStatus()
    seen = []

    def transcribe(wav):
        seen.append(status.recording)
        return "um, hello world"

    api = make_api(tmp_path, transcribe_fn=transcribe)
    api.attach_status_item(status)

    api.start_recording()
    api.stop_recording()

    assert seen == [True], "the glyph went idle while whisper was still running"
    assert status.recording is False
    assert status.states == [True, False]


def test_the_mic_flag_still_clears_when_the_mic_closes(tmp_path):
    """is_recording() is the mic, not the whole dictation: main()'s
    menu_dictate uses it to decide start-vs-stop, so it must NOT grow the
    glyph's longer lifetime."""
    api = make_api(tmp_path)
    api.start_recording()
    assert api.is_recording() is True
    api._release_recording(paste=False)
    assert api.is_recording() is False


def test_a_failed_start_does_not_mark_recording(tmp_path):
    api = make_api(tmp_path)
    status = FakeStatus()
    api.attach_status_item(status)

    def raise_rejected():
        raise RecordingQueueError("start", "already recording")

    api._recordings.start = raise_rejected

    result = api.start_recording()

    assert result.get("error")
    assert api.is_recording() is False
    assert status.recording is False
    assert status.states == [], "a rejected start must not light the glyph"


def test_a_failed_release_does_not_strand_a_busy_glyph(tmp_path):
    """If the release itself blows up, nothing downstream will ever retire
    the session -- the glyph would sit lit forever."""
    api = make_api(tmp_path)
    status = FakeStatus()
    api.attach_status_item(status)
    api.start_recording()
    assert status.recording is True

    def boom(**kwargs):
        raise RuntimeError("release blew up")

    api._recordings.release = boom

    with pytest.raises(RuntimeError):
        api._release_recording(paste=False)

    assert status.recording is False
    assert api.is_recording() is False


def test_a_recorder_whose_stop_throws_still_retires_the_glyph(tmp_path):
    """The other half of the same hole, and the one nothing covered.

    _release_recording's release() succeeds here, so the `claimed is not None`
    finally above does NOT fire -- the session is handed to the queue and the
    glyph is retired by whatever finishes it. Normally that is
    _process_session's own finally. But when recorder.stop() raises,
    RecordingQueue._finalize_capture sets session.capture_error, and the
    worker loop (recording_queue.py, `if result is None:`) skips the
    processor entirely. _process_session never runs, so its finally never
    runs, and the only thing left that retires the session is the
    add_done_callback safety net at the end of _release_recording. Without
    it the menu-bar glyph stays lit forever, with the mic already closed and
    nothing in flight to turn it off.

    Mutation check: replace the `future.add_done_callback(...)` call in
    voisr/ui.py with `pass` and this test fails on `status.recording`.
    """
    class ExplodingStopRecorder(FakeRecorder):
        def stop(self):
            raise RuntimeError("mic went away")

    api = make_api(tmp_path, recorder_factory=ExplodingStopRecorder)
    status = FakeStatus()
    api.attach_status_item(status)

    api.start_recording()
    assert status.recording is True, "the glyph lights for the dictation"

    future = api._release_recording(paste=False)

    # Callbacks fire in the order they were added, and _release_recording
    # added its retire first -- so once this one has run, the retire has too.
    # No polling, no sleep: if the safety net is gone, `done` still fires and
    # the glyph assertion below is what catches it.
    done = threading.Event()
    future.add_done_callback(lambda _f: done.set())
    assert done.wait(5), "the session never completed"

    assert future.result(5).to_api_dict().get("error"), \
        "a recorder that cannot stop must surface as a failed session"
    assert api.is_recording() is False, "the mic is closed either way"
    assert status.recording is False, \
        "nothing downstream retires this session -- the glyph would stay lit"
    assert status.states == [True, False]


def test_overlapping_dictations_keep_the_glyph_lit(tmp_path):
    """The queue admits a fresh dictation while an earlier one is still
    transcribing (that is what max_in_flight is for). Retiring the older
    session must not clear the glyph out from under the newer one.

    The two dictations genuinely overlap here: transcription parks on a gate,
    so session 2 starts and is released while session 1 is still inside
    whisper. Each transcription samples the glyph as it runs -- session 2's
    sample is taken *after* session 1 has been retired, which is the moment a
    single boolean (or a `clear()` instead of a `discard()`) goes dark on a
    live dictation."""
    status = FakeStatus()
    entered = [threading.Event(), threading.Event()]
    gates = [threading.Event(), threading.Event()]
    seen = []

    def transcribe(wav):
        n = len(seen)
        seen.append(status.recording)
        entered[n].set()
        assert gates[n].wait(5), "transcription gate was never released"
        return "um, hello world"

    api = make_api(tmp_path, transcribe_fn=transcribe)
    api.attach_status_item(status)

    first = api.start_recording()["session_id"]
    first_future = api._release_recording(paste=False)
    assert entered[0].wait(5), "session 1 never reached transcription"
    assert api.is_recording() is False, "session 1's mic should be closed"
    assert status.recording is True, "the glyph went idle mid-transcription"

    # Session 2 starts while session 1 is still in whisper -- the real overlap.
    second = api.start_recording()["session_id"]
    assert second != first
    second_future = api._release_recording(paste=False)
    assert status.recording is True

    gates[0].set()  # session 1 finishes and is retired
    assert entered[1].wait(5), "session 2 never reached transcription"
    assert status.recording is True, \
        "retiring session 1 cleared the glyph out from under session 2"
    # A stale retire of the already-finished first session is a no-op.
    api._mark_session(first, False)
    assert status.recording is True

    gates[1].set()
    assert first_future.result(5).to_api_dict().get("error") is None
    assert second_future.result(5).to_api_dict().get("error") is None

    assert seen == [True, True], "the glyph was dark during a live dictation"
    assert status.recording is False
    assert status.states == [True, False], "the glyph flickered mid-overlap"


def test_a_session_marked_before_the_status_item_is_attached_stays_in_sync(
        tmp_path):
    """_glyph_busy is a mirror of what the status item was last told. If it
    were committed with no status item to push to, the later attach would
    inherit a state it never rendered and the next push would be skipped as
    "no change" -- a glyph stuck idle for a live dictation."""
    api = make_api(tmp_path)
    status = FakeStatus()

    api._mark_session(1, True)  # no status item yet
    api.attach_status_item(status)
    api._mark_session(2, True)

    assert status.states == [True], "the busy state was swallowed"
    assert status.recording is True


class WatchedLock:
    """Reentrant stand-in for _busy_lock that fires `contended` when a
    *different* thread tries to enter while someone is inside. Reentry by the
    holder does not count, so this stays honest even though _mark_session
    re-enters _busy_lock."""

    def __init__(self, contended=None):
        self._inner = threading.RLock()
        self._count_lock = threading.Lock()
        self._holders = []
        self.contended = contended or threading.Event()

    def __enter__(self):
        me = threading.get_ident()
        with self._count_lock:
            if any(h != me for h in self._holders):
                self.contended.set()
            self._holders.append(me)
        return self._inner.__enter__()

    def __exit__(self, *exc):
        try:
            return self._inner.__exit__(*exc)
        finally:
            with self._count_lock:
                self._holders.remove(threading.get_ident())


def test_two_concurrent_releases_do_not_both_claim_the_session(tmp_path):
    """HotkeyService.release() holds _state_lock; Api.stop_recording() comes
    off the pywebview/JS bridge and holds nothing, so both can be inside
    _release_recording at once.

    What has to hold is that the caller which claims _active_sequence is the
    same one RecordingQueue.release() hands the live session to. Break that
    -- by leaving the read-modify-write unlocked, or by locking the claim but
    not the hand-off -- and one caller ends up holding a sequence while
    getting back the queue's *already-resolved* "not recording" failure. Its
    done-callback fires immediately and retires a session that is still
    transcribing: the glyph goes dark mid-dictation.

    Driven deterministically, with no sleeps: the queue's release() parks its
    first caller, and the second thread is only let through once it is
    provably in position -- blocked on the lock if the two are paired, or
    already at the queue itself if they are not.
    """
    status = FakeStatus()
    transcribing = threading.Event()
    finish = threading.Event()

    def transcribe(wav):
        transcribing.set()
        assert finish.wait(5), "transcription gate was never released"
        return "um, hello world"

    api = make_api(tmp_path, transcribe_fn=transcribe)
    in_position = threading.Event()
    api._busy_lock = WatchedLock(contended=in_position)
    api.attach_status_item(status)

    api.start_recording()
    assert status.recording is True

    real_release = api._recordings.release
    at_the_queue = threading.Event()
    resume = threading.Event()
    arrivals = []

    def gated_release(**kwargs):
        """Park the first caller inside the hand-off, so the second one can
        be let in while the first has claimed but not yet handed off."""
        arrivals.append(threading.current_thread().name)
        if len(arrivals) == 1:
            at_the_queue.set()
            assert resume.wait(5), "the parked caller was never released"
        else:
            in_position.set()  # a second caller reached the queue unblocked
        return real_release(**kwargs)

    api._recordings.release = gated_release

    futures = {}

    def release(name):
        futures[name] = api._release_recording(paste=False)

    first = threading.Thread(target=release, args=("a",), daemon=True)
    first.start()
    assert at_the_queue.wait(5), "the first caller never reached the queue"

    second = threading.Thread(target=release, args=("b",), daemon=True)
    second.start()
    assert in_position.wait(5), "the second caller never got into position"

    resume.set()
    first.join(5)
    second.join(5)
    assert not first.is_alive() and not second.is_alive()

    assert transcribing.wait(5), "the claimed session never reached whisper"
    assert status.recording is True, \
        "a release that lost the claim retired a live, transcribing session"
    assert status.states == [True]

    finish.set()
    errors = [futures[name].result(5).to_api_dict().get("error")
              for name in ("a", "b")]
    assert sum(1 for e in errors if e and "not recording" in e) == 1, \
        f"exactly one release should have claimed the session, got {errors}"
    assert len(arrivals) == 2, "both callers must reach the queue"
    assert status.recording is False
    assert status.states == [True, False]


def test_close_releases_the_status_item(tmp_path):
    class FakeStatus:
        def __init__(self):
            self.release_calls = 0

        def set_recording(self, value):
            pass

        def release(self):
            self.release_calls += 1

    api = make_api(tmp_path)
    status = FakeStatus()
    api.attach_status_item(status)

    api.close()

    assert status.release_calls == 1


def test_save_text_file_writes_through_the_injected_panel(tmp_path):
    api = make_api(tmp_path)
    target = tmp_path / "out.txt"
    api._save_panel_fn = lambda name: str(target)
    out = api.save_text_file("hello there", "out.txt")
    assert out["ok"] is True
    assert target.read_text() == "hello there"


def test_save_text_file_reports_cancellation(tmp_path):
    api = make_api(tmp_path)
    api._save_panel_fn = lambda name: None
    assert api.save_text_file("x", "a.txt") == {"ok": False, "path": None}


def test_run_save_panel_waits_without_a_timeout(monkeypatch):
    """_run_save_panel used to cap done.wait(120): if the user left the real
    NSSavePanel open past that, the calling thread gave up and reported a
    cancel while the panel was still on screen. runModal() blocks the AppKit
    main thread until the user acts, so the callback set()s `done` sooner or
    later regardless -- there is no reason for the caller to time out on it.
    This asserts the wait is untimed (called with no args), driving the
    fake AppKit/AppHelper below through the real function body rather than
    trusting a comment."""
    import AppKit
    import PyObjCTools.AppHelper  # noqa: F401 -- import so the attr exists to patch
    import PyObjCTools

    from voisr import ui as ui_module

    wait_calls = []
    real_wait = threading.Event.wait

    def spy_wait(self, *args, **kwargs):
        wait_calls.append((args, kwargs))
        return real_wait(self, *args, **kwargs)

    monkeypatch.setattr(threading.Event, "wait", spy_wait)

    class FakeURL:
        def path(self):
            return "/tmp/voisr-save-panel-test.txt"

    class FakePanel:
        def setNameFieldStringValue_(self, name):
            pass

        def runModal(self):
            return AppKit.NSModalResponseOK

        def URL(self):
            return FakeURL()

    class FakeNSSavePanel:
        @staticmethod
        def savePanel():
            return FakePanel()

    monkeypatch.setattr(AppKit, "NSSavePanel", FakeNSSavePanel)

    class FakeAppHelper:
        @staticmethod
        def callAfter(fn):
            # Stand-in for the real run loop dispatch: invokes synchronously,
            # which is fine here since we only care about the done.wait() call.
            fn()

    monkeypatch.setattr(PyObjCTools, "AppHelper", FakeAppHelper)

    path = ui_module._run_save_panel("out.txt")

    assert path == "/tmp/voisr-save-panel-test.txt"
    assert wait_calls == [((), {})], \
        f"done.wait() must be called with no timeout, got {wait_calls}"


def test_set_config_failed_write_leaves_config_intact_and_no_temp_file(tmp_path, monkeypatch):
    """H1: a config write that dies mid-way must not leave a truncated file
    (which load_config would replace with defaults, wiping the dictionary)."""
    import os
    api = make_api(tmp_path)
    api.set_config({"dictionary": {"trademake": "TradeMate"}})
    before = (tmp_path / "config.json").read_bytes()

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        api.set_config({"paste": False})

    assert (tmp_path / "config.json").read_bytes() == before
    assert [p.name for p in tmp_path.iterdir() if p.name != "config.json"
            and not p.name.startswith("h.jsonl")] == []


def test_concurrent_set_config_calls_keep_every_update(tmp_path, monkeypatch):
    """H1: two bridge calls updating different keys at once must not clobber
    each other's write."""
    import time
    from voisr import ui as ui_mod
    real_load = ui_mod.load_config

    def slow_load(path, **kwargs):
        cfg = real_load(path, **kwargs)
        time.sleep(0.02)   # widen the read-modify-write window
        return cfg

    monkeypatch.setattr(ui_mod, "load_config", slow_load)
    api = make_api(tmp_path)
    # eight real settings (set_config rejects unknown keys), none at default
    updates = [("device", ":1"), ("pill_theme", "ember"),
               ("pill_color", "#112233"), ("appearance", "dark"),
               ("model", "base.en"), ("hotkey_label", "X"),
               ("paste", False), ("show_widget", False)]
    n = len(updates)
    barrier = threading.Barrier(n)

    def worker(i):
        barrier.wait()
        api.set_config(dict([updates[i]]))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    cfg = api.get_config()
    assert all(cfg.get(k) == v for k, v in updates), cfg


def test_capture_hotkey_write_is_atomic(tmp_path, monkeypatch):
    import os
    api = make_api(tmp_path, capture_fn=lambda timeout, mode="listen": (63, "Fn / Globe"))
    api.set_config({"dictionary": {"a": "b"}})
    before = (tmp_path / "config.json").read_bytes()
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(OSError):
        api.capture_hotkey()
    assert (tmp_path / "config.json").read_bytes() == before


def test_launch_at_login_reflects_the_plist_not_the_stale_flag(tmp_path, monkeypatch):
    """L3: the config flag drifts if the user removes the LaunchAgent by
    hand; get_config must report what the system will actually do."""
    plist = tmp_path / "agent.plist"
    monkeypatch.setattr(launch_agent, "PLIST_PATH", plist)
    api = make_api(tmp_path)
    api.set_launch_at_login(True)
    assert api.get_config()["launch_at_login"] is True
    plist.unlink()
    assert api.get_config()["launch_at_login"] is False


def test_glossary_prompt_is_capped_to_the_most_recent_terms(tmp_path):
    """L4: whisper.cpp truncates --prompt at ~224 tokens with no warning, so
    a big dictionary silently loses its newest (most useful) terms."""
    from voisr.ui import glossary_prompt
    dictionary = {f"spoken{i}": f"Written{i}" for i in range(200)}
    prompt = glossary_prompt(dictionary)
    assert prompt.startswith("Glossary: ")
    assert "Written199" in prompt          # newest kept
    assert "Written0" not in prompt        # oldest dropped
    assert len(prompt) <= 600


def test_glossary_prompt_is_none_for_an_empty_dictionary():
    from voisr.ui import glossary_prompt
    assert glossary_prompt({}) is None
    assert glossary_prompt(None) is None


def test_get_config_does_not_reread_an_unchanged_file(tmp_path, monkeypatch):
    """L5: get_config runs on the tap thread on every press/release; an
    unchanged file must be served from memory."""
    from voisr import ui as ui_mod
    calls = []
    real = ui_mod.load_config

    def counting(path, **kwargs):
        calls.append(1)
        return real(path, **kwargs)

    monkeypatch.setattr(ui_mod, "load_config", counting)
    api = make_api(tmp_path)
    api.set_config({"paste": False})
    calls.clear()
    assert api.get_config()["paste"] is False
    assert api.get_config()["paste"] is False
    assert len(calls) == 1


def test_get_config_sees_an_external_edit(tmp_path):
    api = make_api(tmp_path)
    api.set_config({"paste": False})
    assert api.get_config()["paste"] is False
    (tmp_path / "config.json").write_text(json.dumps({"paste": True}))
    assert api.get_config()["paste"] is True


def test_get_config_returns_a_private_copy(tmp_path):
    api = make_api(tmp_path)
    api.set_config({"dictionary": {"a": "b"}})
    cfg = api.get_config()
    cfg["dictionary"]["zzz"] = "mutated"
    assert "zzz" not in api.get_config()["dictionary"]


@pytest.mark.parametrize("key", ["accessibility", "input_monitoring", "microphone"])
def test_reset_permission_relists_voisr_then_opens_the_pane(tmp_path, monkeypatch, key):
    # tccutil reset deletes Voisr's row from the list: without asking again
    # the pane that opens has no Voisr switch to turn on.
    from voisr import permissions
    events = []
    monkeypatch.setattr(permissions, "reset_grant",
                        lambda key: {"ok": True})
    monkeypatch.setattr(permissions, "open_pane",
                        lambda key: events.append(("open", key)) or True)
    api = make_api(tmp_path, grant_requester=lambda k: events.append(("request", k)))
    assert api.reset_permission(key) == {"ok": True}
    assert events == [("request", key), ("open", key)]


def test_reset_permission_does_not_open_the_pane_on_failure(tmp_path, monkeypatch):
    from voisr import permissions
    events = []
    monkeypatch.setattr(permissions, "reset_grant",
                        lambda key: {"ok": False, "error": "nope"})
    monkeypatch.setattr(permissions, "open_pane",
                        lambda key: events.append(("open", key)) or True)
    api = make_api(tmp_path, grant_requester=lambda k: events.append(("request", k)))
    assert api.reset_permission("accessibility") == {"ok": False, "error": "nope"}
    assert events == []


def test_reset_permission_still_opens_the_pane_if_the_re_request_raises(tmp_path, monkeypatch):
    from voisr import permissions
    opened = []
    monkeypatch.setattr(permissions, "reset_grant", lambda key: {"ok": True})
    monkeypatch.setattr(permissions, "open_pane",
                        lambda key: opened.append(key) or True)

    def boom(key):
        raise RuntimeError("no Quartz")

    api = make_api(tmp_path, grant_requester=boom)
    assert api.reset_permission("accessibility") == {"ok": True}
    assert opened == ["accessibility"]


def test_api_defaults_to_the_real_grant_requester(tmp_path):
    # conftest injects an inert requester; left unset, Api uses the real one
    from voisr import permissions
    api = make_api(tmp_path, grant_requester=None)
    assert api._grant_requester is permissions.request_grant


def test_reset_permission_never_runs_tccutil_unbundled(tmp_path, monkeypatch):
    # Under pytest the main bundle is not com.local.voisr; nothing may run.
    import subprocess
    from voisr import permissions
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: pytest.fail("tccutil must not run"))
    monkeypatch.setattr(permissions, "open_pane",
                        lambda key: pytest.fail("pane must not open"))
    assert make_api(tmp_path).reset_permission("accessibility")["ok"] is False


class FakeWindow:
    def __init__(self):
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


def _fake_bundle(monkeypatch, path, bundle_id="com.local.voisr"):
    import Foundation

    class Bundle:
        def bundlePath(self):
            return path

        def bundleIdentifier(self):
            return bundle_id

    class NSBundle:
        @staticmethod
        def mainBundle():
            return Bundle()

    monkeypatch.setattr(Foundation, "NSBundle", NSBundle)


def test_relaunch_app_refuses_outside_an_app_bundle(tmp_path, monkeypatch):
    import subprocess
    _fake_bundle(monkeypatch, "/usr/local/bin/python3")
    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **k: pytest.fail("must not spawn"))
    api = make_api(tmp_path)
    api.attach_window(FakeWindow())
    assert api.relaunch_app()["ok"] is False
    assert api._window.destroyed is False


def test_relaunch_app_refuses_python_app_bundle(tmp_path, monkeypatch):
    # `python -m voisr.ui` runs inside Python.app, which also ends in ".app"
    import subprocess
    _fake_bundle(monkeypatch, "/Library/Frameworks/Python.app",
                 bundle_id="org.python.python")
    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **k: pytest.fail("must not spawn"))
    api = make_api(tmp_path)
    api.attach_window(FakeWindow())
    assert api.relaunch_app()["ok"] is False
    assert api._window.destroyed is False


def test_relaunch_app_refuses_without_a_window(tmp_path, monkeypatch):
    import subprocess
    _fake_bundle(monkeypatch, "/Users/x/Applications/Voisr.app")
    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **k: pytest.fail("must not spawn"))
    assert make_api(tmp_path).relaunch_app()["ok"] is False


def test_relaunch_app_reopens_the_bundle_then_quits(tmp_path, monkeypatch):
    import subprocess
    _fake_bundle(monkeypatch, "/Users/x/Applications/Voisr.app")
    spawned = []
    monkeypatch.setattr(subprocess, "Popen",
                        lambda argv, **kw: spawned.append((argv, kw)))
    api = make_api(tmp_path)
    window = FakeWindow()
    api.attach_window(window)
    assert api.relaunch_app() == {"ok": True}
    assert window.destroyed is True
    argv, kw = spawned[0]
    # waits for THIS process to be gone (shutdown can take seconds), then
    # opens the bundle: a fixed sleep raced it and just re-activated us
    assert argv == ["/bin/sh", "-c",
                    'while kill -0 "$1" 2>/dev/null; do sleep 0.2; done; '
                    '/usr/bin/open "$0"',
                    "/Users/x/Applications/Voisr.app", str(os.getpid())]
    assert kw["start_new_session"] is True


def test_relaunch_app_reports_a_spawn_failure_and_stays_open(tmp_path, monkeypatch):
    import subprocess
    _fake_bundle(monkeypatch, "/Users/x/Applications/Voisr.app")

    def boom(*a, **k):
        raise OSError("no /bin/sh")

    monkeypatch.setattr(subprocess, "Popen", boom)
    api = make_api(tmp_path)
    window = FakeWindow()
    api.attach_window(window)
    res = api.relaunch_app()
    assert res["ok"] is False and "no /bin/sh" in res["error"]
    assert window.destroyed is False


def test_relaunch_app_cancels_the_waiter_if_quitting_fails(tmp_path, monkeypatch):
    # a waiter left behind would relaunch Voisr whenever it next quits
    import subprocess
    _fake_bundle(monkeypatch, "/Users/x/Applications/Voisr.app")

    class Proc:
        killed = False

        def kill(self):
            Proc.killed = True

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: Proc())

    class StuckWindow(FakeWindow):
        def destroy(self):
            raise RuntimeError("stuck")

    api = make_api(tmp_path)
    api.attach_window(StuckWindow())
    assert api.relaunch_app() == {"ok": False, "error": "couldn't quit"}
    assert Proc.killed is True


def _ticking_clock(step=0.25):
    state = {"t": 100.0}

    def now():
        state["t"] += step
        return state["t"]
    return now


def test_history_item_gets_stage_timings_after_paste(tmp_path):
    api = make_api(tmp_path, clock=_ticking_clock(), paste_fn=lambda text: True)
    api.start_recording()
    api.stop_recording(paste=True)
    item = api._history.list()[0]
    t = item["timings_ms"]
    assert set(t) == {"transcribe", "cleanup", "paste", "total"}
    assert t["transcribe"] == 250 and t["cleanup"] == 250
    assert t["total"] >= t["transcribe"] + t["cleanup"] + t["paste"]
    assert "model" not in item  # injected transcriber: no model to name


def test_history_is_written_before_paste_and_survives_timing_failure(tmp_path):
    seen = {}

    class Store(HistoryStore):
        def merge_timings(self, *a, **k):
            raise OSError("disk full")

    store = Store(tmp_path / "h.jsonl")

    def paste(text):
        seen["in_history_before_paste"] = [i["text"] for i in store.list()]
        return True

    api = make_api(tmp_path, history=store, paste_fn=paste, clock=_ticking_clock())
    api.start_recording()
    out = api.stop_recording(paste=True)
    assert seen["in_history_before_paste"] == ["Hello world"]
    assert out.get("pasted") is True
    assert "error" not in out
    assert [i["text"] for i in store.list()] == ["Hello world"]
    assert "paste" not in store.list()[0]["timings_ms"]


def test_no_paste_records_total_without_paste_time(tmp_path):
    api = make_api(tmp_path, clock=_ticking_clock())
    api.start_recording()
    api.stop_recording()
    t = api._history.list()[0]["timings_ms"]
    assert "total" in t and "paste" not in t
