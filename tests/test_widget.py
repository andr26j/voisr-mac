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


def test_press_release_drives_widget_states(tmp_path):
    widget = FakeWidget()
    svc = HotkeyService(make_api(tmp_path), widget=widget, hide_delay=0)
    svc.press()
    assert widget.calls == ["recording"]
    svc.release()
    assert widget.calls[:3] == ["recording", "processing", "done"]
    assert widget.calls[-1] == "hide"


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
    assert "error" in widget.calls and widget.calls[-1] == "hide"
