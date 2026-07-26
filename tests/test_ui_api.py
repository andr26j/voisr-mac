import json
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


def make_api(tmp_path):
    return Api(config_path=tmp_path / "config.json",
               history=HistoryStore(tmp_path / "h.jsonl"),
               recorder_factory=FakeRecorder,
               transcribe_fn=lambda wav: "um, hello world",
               clock=lambda: 10.0,
               clipboard_fn=lambda text: None)


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
        clock=iter([1.0, 2.0]).__next__,
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


def test_get_permissions_returns_three_panes(tmp_path):
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


def test_a_failed_start_does_not_mark_recording(tmp_path):
    api = make_api(tmp_path)

    def raise_rejected():
        raise RecordingQueueError("start", "already recording")

    api._recordings.start = raise_rejected

    result = api.start_recording()

    assert result.get("error")
    assert api.is_recording() is False


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
