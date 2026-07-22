import json

from voisr.history import HistoryStore
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
    assert set(s) == {"total_words", "dictations", "avg_wpm", "streak_days"}


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
        assert second["text"] == "Recoverable text"
        assert "error" not in second
    finally:
        api.close()
