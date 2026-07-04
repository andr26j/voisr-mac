import json

from voisr.history import HistoryStore
from voisr.ui import (Api, HotkeyService, event_vk, key_label,
                      legacy_name_to_vk, TOGGLE_VKS)


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

    def show_recording(self): self.calls.append("recording")
    def show_processing(self): self.calls.append("processing")
    def show_done(self): self.calls.append("done")
    def show_error(self): self.calls.append("error")
    def hide(self): self.calls.append("hide")


class CallbackListener:
    """Fake listener that exposes the handler so tests can fire events."""
    def __init__(self, handler):
        self.handler = handler

    def stop(self):
        pass


def cb_factory():
    made = []
    def factory(handler):
        l = CallbackListener(handler)
        made.append(l)
        return l
    factory.made = made
    return factory


def make_api(tmp_path, **kw):
    return Api(config_path=tmp_path / "config.json",
               history=HistoryStore(tmp_path / "h.jsonl"),
               recorder=FakeRecorder(),
               transcribe_fn=lambda wav: "hello",
               clock=lambda: 1.0, **kw)


def wait_for_hide(widget, timeout=3.0):
    import time
    deadline = time.monotonic() + timeout
    while widget.calls[-1:] != ["hide"] and time.monotonic() < deadline:
        import time as t
        t.sleep(0.05)


def test_event_vk_extracts_from_keycode_and_enum():
    from pynput import keyboard
    assert event_vk(keyboard.KeyCode.from_vk(63)) == 63
    assert event_vk(keyboard.Key.alt_r) == 61


def test_legacy_names_map_to_vks():
    assert legacy_name_to_vk("alt_r") == 61
    assert legacy_name_to_vk("f13") == 105
    assert legacy_name_to_vk("unknown-key") is None


def test_key_labels():
    assert "Fn" in key_label(63)
    assert "Option" in key_label(61)
    assert key_label(105) == "F13"


def test_fn_toggle_semantics_drive_full_cycle(tmp_path):
    # pynput reports BOTH Fn edges as on_release(KeyCode vk=63); the service
    # must treat the first event as press and the second as release.
    (tmp_path / "config.json").write_text(json.dumps({"hotkey_vk": 63, "paste": False}))
    widget = FakeWidget()
    factory = cb_factory()
    svc = HotkeyService(make_api(tmp_path), widget=widget, hide_delay=0,
                        listener_factory=factory)
    svc.start()
    listener = factory.made[-1]
    assert 63 in TOGGLE_VKS
    listener.handler(63, "flags", 0x800000)   # hardware Fn press edge
    assert widget.calls == ["recording"]
    listener.handler(63, "flags", 0)           # hardware Fn release edge
    wait_for_hide(widget)
    assert widget.calls == ["recording", "processing", "done", "hide"]
    svc.stop()


def test_normal_key_ignores_autorepeat(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"hotkey_vk": 105, "paste": False}))
    widget = FakeWidget()
    factory = cb_factory()
    svc = HotkeyService(make_api(tmp_path), widget=widget, hide_delay=0,
                        listener_factory=factory)
    svc.start()
    listener = factory.made[-1]
    listener.handler(105, "down", 0)
    listener.handler(105, "down", 0)    # auto-repeat while held
    listener.handler(105, "down", 0)
    assert widget.calls == ["recording"]
    listener.handler(105, "up", 0)
    wait_for_hide(widget)
    assert widget.calls == ["recording", "processing", "done", "hide"]
    svc.stop()


def test_legacy_config_name_still_works(tmp_path):
    # old configs only have {"hotkey": "alt_r"}; events for vk 61 must match
    (tmp_path / "config.json").write_text(json.dumps({"hotkey": "alt_r", "paste": False}))
    widget = FakeWidget()
    factory = cb_factory()
    svc = HotkeyService(make_api(tmp_path), widget=widget, hide_delay=0,
                        listener_factory=factory)
    svc.start()
    listener = factory.made[-1]
    listener.handler(61, "flags", 0x80140)   # hardware right-option press edge
    assert widget.calls == ["recording"]
    svc.stop()


def test_capture_hotkey_stores_vk_and_restarts_service(tmp_path):
    class FakeSvc:
        def __init__(self): self.events = []
        def stop(self): self.events.append("stop")
        def start(self, key_name=None): self.events.append("start")
        status = "active"

    svc = FakeSvc()
    api = make_api(tmp_path, hotkey_service=svc,
                   capture_fn=lambda timeout: (63, "Fn / Globe"))
    res = api.capture_hotkey()
    assert res == {"vk": 63, "label": "Fn / Globe"}
    cfg = json.loads((tmp_path / "config.json").read_text())
    assert cfg["hotkey_vk"] == 63 and cfg["hotkey_label"] == "Fn / Globe"
    assert svc.events == ["stop", "start"]


def test_capture_hotkey_timeout_restores_service(tmp_path):
    class FakeSvc:
        def __init__(self): self.events = []
        def stop(self): self.events.append("stop")
        def start(self, key_name=None): self.events.append("start")
        status = "active"

    svc = FakeSvc()
    api = make_api(tmp_path, hotkey_service=svc, capture_fn=lambda timeout: None)
    res = api.capture_hotkey()
    assert "error" in res
    assert svc.events == ["stop", "start"]
