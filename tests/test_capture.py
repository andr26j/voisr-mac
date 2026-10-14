import json

import pytest

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
        self.themes = []
        self.colors = []

    def show_recording(self, theme="classic", color=None): self.calls.append("recording"); self.themes.append(theme); self.colors.append(color)
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
    def factory(handler, mode="listen"):
        l = CallbackListener(handler)
        made.append(l)
        return l
    factory.made = made
    return factory


def make_api(tmp_path, **kw):
    kwargs = dict(config_path=tmp_path / "config.json",
                  history=HistoryStore(tmp_path / "h.jsonl"),
                  recorder_factory=FakeRecorder,
                  transcribe_fn=lambda wav: "hello",
                  clock=lambda: 1.0, clipboard_fn=lambda text: None)
    kwargs.update(kw)
    return Api(**kwargs)


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
    wait_for(lambda: widget.calls == ["recording"])
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
    wait_for(lambda: widget.calls == ["recording"])
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
    wait_for(lambda: widget.calls == ["recording"])
    svc.stop()


def test_missed_release_edge_does_not_invert_state(tmp_path):
    # Two consecutive press edges (release missed while the tap was disabled)
    # must NOT flip recording off — the flag bit carries the true key state.
    (tmp_path / "config.json").write_text(json.dumps({"hotkey_vk": 63, "paste": False}))
    widget = FakeWidget()
    factory = cb_factory()
    svc = HotkeyService(make_api(tmp_path), widget=widget, hide_delay=0,
                        listener_factory=factory)
    svc.start()
    listener = factory.made[-1]
    listener.handler(63, "flags", 0x800000)   # press
    listener.handler(63, "flags", 0x800000)   # press again (release was missed)
    wait_for(lambda: widget.calls == ["recording"])   # not inverted
    listener.handler(63, "flags", 0)          # release
    wait_for_hide(widget)
    assert widget.calls == ["recording", "processing", "done", "hide"]
    svc.stop()


def test_new_recording_is_not_hidden_by_stale_finish(tmp_path):
    # Re-pressing during the previous dictation's hide delay must not hide
    # the widget while the new recording is live.
    import time
    (tmp_path / "config.json").write_text(json.dumps({"hotkey_vk": 63, "paste": False}))
    widget = FakeWidget()
    factory = cb_factory()
    svc = HotkeyService(make_api(tmp_path), widget=widget, hide_delay=0.3,
                        listener_factory=factory)
    svc.start()
    listener = factory.made[-1]
    listener.handler(63, "flags", 0x800000)
    listener.handler(63, "flags", 0)
    deadline = time.monotonic() + 3.0
    while "done" not in widget.calls and time.monotonic() < deadline:
        time.sleep(0.02)
    listener.handler(63, "flags", 0x800000)   # new recording inside hide delay
    time.sleep(0.6)                            # let the stale hide timer fire
    assert widget.calls[-1] == "recording"     # not hidden by the stale thread
    svc.stop()


def test_capture_hotkey_stores_vk_and_restarts_service(tmp_path):
    class FakeSvc:
        def __init__(self): self.events = []
        def stop(self): self.events.append("stop")
        def start(self, key_name=None): self.events.append("start")
        status = "active"

    svc = FakeSvc()
    api = make_api(tmp_path, hotkey_service=svc,
                   capture_fn=lambda timeout, mode="listen": (63, "Fn / Globe"))
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
    api = make_api(tmp_path, hotkey_service=svc, capture_fn=lambda timeout, mode="listen": None)
    res = api.capture_hotkey()
    assert "error" in res
    assert svc.events == ["stop", "start"]



@pytest.mark.parametrize("configured, expected", [
    (None, "active"), ("listen", "listen"), ("active", "active"), ("grab", "active"),
])
def test_capture_hotkey_captures_with_the_configured_tap_mode(tmp_path, configured, expected):
    # An active-tap user may have no Input Monitoring: a listen-only capture
    # tap would never see the key and Change hotkey would time out.
    if configured is not None:
        (tmp_path / "config.json").write_text(json.dumps({"event_tap": configured}))
    modes = []
    api = make_api(tmp_path, capture_fn=lambda timeout, mode="listen":
                   modes.append(mode) or (63, "Fn / Globe"))
    api.capture_hotkey()
    assert modes == [expected]


def test_capture_next_key_passes_the_mode_to_the_tap(monkeypatch):
    from voisr import keytap
    from voisr.ui import capture_next_key
    seen = []
    monkeypatch.setattr(keytap, "capture_next_vk",
                        lambda timeout, mode="listen": seen.append(mode) or 63)
    assert capture_next_key(1.0, mode="active")[0] == 63
    assert seen == ["active"]

def test_tap_handler_returns_fast_while_previous_capture_is_finalizing(tmp_path):
    """H2: a press that lands while the previous recorder is still in stop()
    must not block the event-tap callback. macOS disables a tap whose
    callback stalls, and every key event in the gap (including the release
    that ends the dictation) is lost."""
    import threading
    import time

    (tmp_path / "config.json").write_text(json.dumps({"hotkey_vk": 105, "paste": False}))
    allow_stop = threading.Event()
    stop_entered = threading.Event()
    made = []

    class BlockingStopRecorder(FakeRecorder):
        def stop(self):
            stop_entered.set()
            assert allow_stop.wait(5)
            return super().stop()

    def recorder_factory():
        rec = BlockingStopRecorder() if not made else FakeRecorder()
        made.append(rec)
        return rec

    widget = FakeWidget()
    factory = cb_factory()
    api = make_api(tmp_path, recorder_factory=recorder_factory)
    svc = HotkeyService(api, widget=widget, hide_delay=0, listener_factory=factory)
    svc.start()
    listener = factory.made[-1]
    try:
        listener.handler(105, "down", 0)
        wait_for(lambda: widget.calls == ["recording"])
        listener.handler(105, "up", 0)
        assert stop_entered.wait(2)

        t0 = time.monotonic()
        listener.handler(105, "down", 0)       # second press while stop() is stuck
        elapsed = time.monotonic() - t0
        assert elapsed < 0.2, f"tap handler blocked for {elapsed:.2f}s"

        listener.handler(105, "up", 0)         # release must not be lost either
        allow_stop.set()
        wait_for(lambda: widget.calls.count("recording") == 2)
        # the first session's done/hide are suppressed by design (a newer
        # press bumped the epoch); the second runs to completion
        wait_for(lambda: widget.calls[-2:] == ["done", "hide"])
        assert len(made) == 2
        assert len(api.get_history()) == 2
    finally:
        allow_stop.set()
        svc.stop()
        api.close()


def wait_for(cond, timeout=3.0):
    import time
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert cond(), "condition not met before timeout"
