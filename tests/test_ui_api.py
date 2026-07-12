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
               recorder=FakeRecorder(),
               transcribe_fn=lambda wav: "um, hello world",
               clock=lambda: 10.0)


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
